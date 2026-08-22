"""Factory for the "upload -> process in background -> list -> get ->
[channel PATCH] -> delete" Report CRUD pattern shared by reports.py,
cashflow_reports.py, combo_reports.py, master_reports.py and
adjustments_reports.py. Each of those files just calls
create_report_crud_router() with its own table name, R2 key functions and
Excel-to-parquet converter, then adds whatever extra endpoints are unique to
it (e.g. reports.py's "Chỉnh cột" headers/mapping-override, or
adjustments_reports.py's read-only /rows browser).

NOT itself an included router — main.py includes each concrete module's
`router`, not this one.
"""
from __future__ import annotations

import io
import uuid
from typing import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import db, storage
from ..deps import get_current_user, require_admin
from ..models import ChannelUpdateRequest, ReportCreatedOut, ReportDetailOut, ReportOut


def create_report_crud_router(
    *,
    prefix: str,
    tag: str,
    table: str,
    list_fields: str,
    original_key_fn: Callable[[str, str], str],
    parquet_key_fn: Callable[[str], str],
    converter: Callable,
    mapping_error: type[Exception],
    has_channel: bool = False,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    async def _process_report(report_id: str, xlsx_bytes: bytes) -> None:
        try:
            # Both are blocking/CPU-bound (openpyxl parsing a potentially
            # huge sheet; boto3's R2 upload) — off the event loop so one big
            # upload doesn't freeze every other concurrent request.
            parquet_bytes, row_count, mapping = await run_in_threadpool(converter, io.BytesIO(xlsx_bytes))
            await run_in_threadpool(
                storage.upload_bytes, parquet_key_fn(report_id), parquet_bytes, "application/octet-stream"
            )
            await db.pg_update(
                table,
                {"id": f"eq.{report_id}"},
                {
                    "status": "ready",
                    "row_count": row_count,
                    "mapping": mapping,
                    "parquet_key": parquet_key_fn(report_id),
                },
            )
        except mapping_error as e:
            await db.mark_failed(table, report_id, str(e))
        except Exception as e:  # noqa: BLE001 — a bad file should fail the Report, not crash the worker
            await db.mark_failed(table, report_id, f"Lỗi xử lý: {e}")

    @router.post("", response_model=ReportCreatedOut, status_code=202)
    async def create_report(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        user: dict = Depends(require_admin),
    ):
        if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
            raise HTTPException(status_code=400, detail="Chỉ chấp nhận file .xlsx, .xls, .csv")

        report_id = str(uuid.uuid4())
        xlsx_bytes = await file.read()

        # Off the event loop — boto3 is sync, and this upload would
        # otherwise block every other concurrent request until it finishes.
        await run_in_threadpool(
            storage.upload_bytes,
            original_key_fn(report_id, file.filename),
            xlsx_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        name = file.filename.rsplit(".", 1)[0]
        await db.pg_insert(
            table,
            {
                "id": report_id,
                "name": name,
                "original_filename": file.filename,
                "uploaded_by": user["id"],
                "status": "processing",
                "original_xlsx_key": original_key_fn(report_id, file.filename),
                "file_size_bytes": len(xlsx_bytes),
            },
        )

        background_tasks.add_task(_process_report, report_id, xlsx_bytes)
        return ReportCreatedOut(id=report_id, status="processing")

    @router.get("", response_model=list[ReportOut])
    async def list_reports(user: dict = Depends(get_current_user)):
        rows = await db.pg_select(table, {"select": list_fields, "order": "uploaded_at.desc"})
        return [ReportOut(**r) for r in rows]

    @router.get("/{report_id}", response_model=ReportDetailOut)
    async def get_report(report_id: str, user: dict = Depends(get_current_user)):
        row = await db.pg_select_one(table, {"id": f"eq.{report_id}"})
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
        return ReportDetailOut(**row)

    if has_channel:

        @router.patch("/{report_id}/channel")
        async def update_channel(report_id: str, body: ChannelUpdateRequest, user: dict = Depends(require_admin)):
            row = await db.pg_select_one(table, {"id": f"eq.{report_id}"})
            if not row:
                raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
            await db.pg_update(table, {"id": f"eq.{report_id}"}, {"sales_channel_id": body.sales_channel_id})
            return {"ok": True}

    @router.delete("/{report_id}", status_code=204)
    async def delete_report(report_id: str, user: dict = Depends(require_admin)):
        row = await db.pg_select_one(table, {"id": f"eq.{report_id}"})
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
        storage.delete_objects([row.get("original_xlsx_key"), row.get("parquet_key")])
        await db.pg_delete(table, {"id": f"eq.{report_id}"})

    return router
