"""Report CRUD: upload (admin), list, get, mapping override (admin), delete
(admin). Excel -> Parquet conversion runs as a FastAPI BackgroundTask, in
the same process, on the same event loop — see app/excel_to_parquet.py for
the conversion itself. No queue/worker process for v1 (see the plan's
"background-job graduation condition").
"""
from __future__ import annotations

import io
import tempfile
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import db, storage
from ..deps import get_current_user, require_admin
from ..excel_to_parquet import MappingError, excel_to_parquet, get_original_headers
from ..models import ChannelUpdateRequest, MappingUpdateRequest, ReportCreatedOut, ReportDetailOut, ReportOut
from ..query_engine import invalidate_local_parquet_cache

router = APIRouter(prefix="/api/reports", tags=["reports"])

REPORT_LIST_FIELDS = "id,name,uploaded_at,uploaded_by,row_count,status,error_message,sales_channel_id"


async def _process_report(report_id: str, xlsx_bytes: bytes) -> None:
    try:
        # Both are blocking/CPU-bound (openpyxl parsing a potentially huge
        # sheet; boto3's R2 upload) — off the event loop so one big upload
        # doesn't freeze every other concurrent request while it runs.
        parquet_bytes, row_count, mapping = await run_in_threadpool(excel_to_parquet, io.BytesIO(xlsx_bytes))
        await run_in_threadpool(
            storage.upload_bytes, storage.parquet_key(report_id), parquet_bytes, "application/octet-stream"
        )
        await db.pg_update(
            "reports",
            {"id": f"eq.{report_id}"},
            {
                "status": "ready",
                "row_count": row_count,
                "mapping": mapping,
                "parquet_key": storage.parquet_key(report_id),
            },
        )
    except MappingError as e:
        await db.mark_failed("reports", report_id, str(e))
    except Exception as e:  # noqa: BLE001 — a bad file should fail the Report, not crash the worker
        await db.mark_failed("reports", report_id, f"Lỗi xử lý: {e}")


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

    # Off the event loop — boto3 is sync, and this upload would otherwise
    # block every other concurrent request until it finishes.
    await run_in_threadpool(
        storage.upload_bytes,
        storage.original_key(report_id, file.filename),
        xlsx_bytes,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    name = file.filename.rsplit(".", 1)[0]
    await db.pg_insert(
        "reports",
        {
            "id": report_id,
            "name": name,
            "original_filename": file.filename,
            "uploaded_by": user["id"],
            "status": "processing",
            "original_xlsx_key": storage.original_key(report_id, file.filename),
            "file_size_bytes": len(xlsx_bytes),
        },
    )

    background_tasks.add_task(_process_report, report_id, xlsx_bytes)
    return ReportCreatedOut(id=report_id, status="processing")


@router.get("", response_model=list[ReportOut])
async def list_reports(user: dict = Depends(get_current_user)):
    rows = await db.pg_select("reports", {"select": REPORT_LIST_FIELDS, "order": "uploaded_at.desc"})
    return [ReportOut(**r) for r in rows]


@router.get("/{report_id}", response_model=ReportDetailOut)
async def get_report(report_id: str, user: dict = Depends(get_current_user)):
    row = await db.pg_select_one("reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    return ReportDetailOut(**row)


@router.get("/{report_id}/headers")
async def get_headers(report_id: str, user: dict = Depends(require_admin)):
    """Original column headers for the "Chỉnh cột" dropdown — downloads the
    original.xlsx from R2 and reads just the header row.
    """
    row = await db.pg_select_one("reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
        await run_in_threadpool(storage.download_to_path, row["original_xlsx_key"], tmp.name)
        headers = await run_in_threadpool(get_original_headers, tmp.name)
    return {"headers": headers}


@router.patch("/{report_id}/mapping")
async def update_mapping(report_id: str, body: MappingUpdateRequest, user: dict = Depends(require_admin)):
    """The Parquet's columns are fixed at conversion time, so taking a new
    mapping means reconverting from the original file — not just editing
    stored metadata. See excel_to_parquet()'s mapping_override param.
    """
    row = await db.pg_select_one("reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")

    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
        await run_in_threadpool(storage.download_to_path, row["original_xlsx_key"], tmp.name)
        try:
            parquet_bytes, row_count, mapping = await run_in_threadpool(
                excel_to_parquet, tmp.name, mapping_override=body.mapping
            )
        except MappingError as e:
            raise HTTPException(status_code=400, detail=str(e))

    await run_in_threadpool(storage.upload_bytes, storage.parquet_key(report_id), parquet_bytes, "application/octet-stream")
    invalidate_local_parquet_cache(report_id)
    await db.pg_update(
        "reports", {"id": f"eq.{report_id}"}, {"mapping": mapping, "row_count": row_count}
    )
    return {"ok": True, "rowCount": row_count}


@router.patch("/{report_id}/channel")
async def update_channel(report_id: str, body: ChannelUpdateRequest, user: dict = Depends(require_admin)):
    row = await db.pg_select_one("reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    await db.pg_update("reports", {"id": f"eq.{report_id}"}, {"sales_channel_id": body.sales_channel_id})
    return {"ok": True}


@router.delete("/{report_id}", status_code=204)
async def delete_report(report_id: str, user: dict = Depends(require_admin)):
    row = await db.pg_select_one("reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    storage.delete_objects([row.get("original_xlsx_key"), row.get("parquet_key")])
    await db.pg_delete("reports", {"id": f"eq.{report_id}"})
