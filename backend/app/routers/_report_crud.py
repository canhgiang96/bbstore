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

import asyncio
import io
import tempfile
import uuid
from typing import Callable, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import db, storage
from ..deps import get_current_user, require_admin
from ..models import ChannelUpdateRequest, LockUpdateRequest, ReportCreatedOut, ReportDetailOut, ReportOut
from ..query_engine import invalidate_local_parquet_cache

# Excel-to-parquet conversion (openpyxl + pandas) is the single most
# memory-hungry step in the whole upload pipeline, and the app runs on a
# single memory-constrained instance (no worker pool) — several large
# files converting at once (e.g. a few upload requests landing seconds
# apart) can stack their peak memory and tip the process into an OOM
# kill. This semaphore is shared across ALL 5 Report types (Orders,
# Cashflow, Combo, Master File, Điều chỉnh doanh thu) so at most one
# conversion runs at a time process-wide — later ones simply wait their
# turn instead of piling on memory pressure together.
#
# Created lazily (on first use, inside a coroutine) rather than at import
# time: asyncio.Semaphore() built before any event loop is running isn't
# safe on every Python version (older asyncio eagerly bound it to
# whatever loop happened to exist at construction, which can be a
# different loop than the one that later runs the app). No await happens
# between the None-check and the assignment, so this can't race even
# though the module could be imported from multiple contexts.
_conversion_semaphore: asyncio.Semaphore | None = None


async def convert_with_backpressure(converter: Callable, *args, **kwargs):
    global _conversion_semaphore
    if _conversion_semaphore is None:
        _conversion_semaphore = asyncio.Semaphore(1)
    async with _conversion_semaphore:
        return await run_in_threadpool(converter, *args, **kwargs)


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
    channel_aware_converter: bool = False,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    async def _process_report(report_id: str, xlsx_bytes: bytes, sales_channel_id: str | None) -> None:
        try:
            # Both are blocking/CPU-bound (openpyxl parsing a potentially
            # huge sheet; boto3's R2 upload) — off the event loop so one big
            # upload doesn't freeze every other concurrent request. The
            # conversion itself is additionally rate-limited process-wide —
            # see _CONVERSION_SEMAPHORE above.
            if channel_aware_converter and sales_channel_id:
                # Only Orders' converter (excel_to_parquet) actually reads
                # this — it gates Phí Piship (Shopee-only, see
                # derive.channel_has_piship). Looked up by id -> name here
                # so the frontend only ever has to send the id it already
                # has, same as the post-upload channel PATCH does.
                channel_row = await db.pg_select_one("sales_channels", {"id": f"eq.{sales_channel_id}"})
                channel_name = channel_row["name"] if channel_row else None
                parquet_bytes, row_count, mapping = await convert_with_backpressure(
                    converter, io.BytesIO(xlsx_bytes), sales_channel_name=channel_name
                )
            else:
                parquet_bytes, row_count, mapping = await convert_with_backpressure(converter, io.BytesIO(xlsx_bytes))
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
            await db.mark_failed(table, report_id, str(e) or repr(e))
        except Exception as e:  # noqa: BLE001 — a bad file should fail the Report, not crash the worker
            # str(e) can be "" for some exceptions raised with no message
            # (e.g. a bare StopIteration) — repr(e) still names the type in
            # that case, so the Report never shows a blank error.
            await db.mark_failed(table, report_id, f"Lỗi xử lý: {str(e) or repr(e)}")

    @router.post("", response_model=ReportCreatedOut, status_code=202)
    async def create_report(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        sales_channel_id: Optional[str] = Form(None),
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
        insert_data = {
            "id": report_id,
            "name": name,
            "original_filename": file.filename,
            "uploaded_by": user["id"],
            "status": "processing",
            "original_xlsx_key": original_key_fn(report_id, file.filename),
            "file_size_bytes": len(xlsx_bytes),
        }
        # Picking the channel at upload time (rather than only via the
        # post-upload PATCH) lets channel_aware_converter's business rules
        # (e.g. Piship gating) apply during the conversion itself.
        if has_channel and sales_channel_id:
            insert_data["sales_channel_id"] = sales_channel_id
        await db.pg_insert(table, insert_data)

        background_tasks.add_task(_process_report, report_id, xlsx_bytes, sales_channel_id if has_channel else None)
        return ReportCreatedOut(id=report_id, status="processing")

    @router.get("", response_model=list[ReportOut])
    async def list_reports(user: dict = Depends(get_current_user)):
        try:
            rows = await db.pg_select(table, {"select": list_fields, "order": "uploaded_at.desc"})
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400 or "locked" not in list_fields:
                raise
            # "locked" is a recently-added column — a deploy can land before
            # its Supabase migration has actually been run, and PostgREST
            # rejects the whole select= list (not just the unknown column)
            # in that case. Retry without it rather than 500ing the entire
            # list — ReportOut.locked defaults to False either way.
            safe_fields = ",".join(f for f in list_fields.split(",") if f != "locked")
            rows = await db.pg_select(table, {"select": safe_fields, "order": "uploaded_at.desc"})
        return [ReportOut(**r) for r in rows]

    @router.get("/{report_id}", response_model=ReportDetailOut)
    async def get_report(report_id: str, user: dict = Depends(get_current_user)):
        row = await db.pg_select_one(table, {"id": f"eq.{report_id}"})
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
        return ReportDetailOut(**row)

    async def _reconvert_report(report_id: str, row: dict, channel_name: str | None) -> dict:
        """Re-downloads this Report's original .xlsx and reconverts it with
        its already-resolved column mapping — shared by update_channel
        (Piship gating needs to apply under a newly-picked channel) and the
        explicit /reconvert endpoint below (a business-rule/formula fix
        ships and an already-converted Report's stored Parquet needs to
        catch up, without faking a channel change just to trigger it).
        """
        if not row.get("original_xlsx_key"):
            raise HTTPException(status_code=409, detail="Report chưa có file gốc để chuyển đổi lại.")
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
            await run_in_threadpool(storage.download_to_path, row["original_xlsx_key"], tmp.name)
            kwargs = {"mapping_override": row.get("mapping")}
            if channel_aware_converter:
                kwargs["sales_channel_name"] = channel_name
            try:
                parquet_bytes, row_count, mapping = await convert_with_backpressure(converter, tmp.name, **kwargs)
            except mapping_error as e:
                raise HTTPException(status_code=400, detail=str(e) or repr(e))
        await run_in_threadpool(
            storage.upload_bytes, parquet_key_fn(report_id), parquet_bytes, "application/octet-stream"
        )
        invalidate_local_parquet_cache(report_id)
        await db.pg_update(table, {"id": f"eq.{report_id}"}, {"mapping": mapping, "row_count": row_count})
        return {"ok": True, "rowCount": row_count}

    @router.post("/{report_id}/reconvert")
    async def reconvert_report(report_id: str, user: dict = Depends(require_admin)):
        """Re-runs the converter on this Report's already-uploaded original
        file, using its existing column mapping and (if applicable) its
        current Kênh bán hàng — for when a business-rule/formula fix ships
        (e.g. Phí Piship's rate change, Voucher's proration formula,
        2026-09-03) and an already-converted Report's stored Parquet needs
        to catch up. Previously the only way to force this was to re-pick
        the same channel in the Kênh bán hàng dropdown (which happens to
        also reconvert) — this is the same reconversion, just direct.
        """
        row = await db.pg_select_one(table, {"id": f"eq.{report_id}"})
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
        channel_name = None
        if channel_aware_converter and row.get("sales_channel_id"):
            channel_row = await db.pg_select_one("sales_channels", {"id": f"eq.{row['sales_channel_id']}"})
            channel_name = channel_row["name"] if channel_row else None
        return await _reconvert_report(report_id, row, channel_name)

    if has_channel:

        @router.patch("/{report_id}/channel")
        async def update_channel(report_id: str, body: ChannelUpdateRequest, user: dict = Depends(require_admin)):
            row = await db.pg_select_one(table, {"id": f"eq.{report_id}"})
            if not row:
                raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
            await db.pg_update(table, {"id": f"eq.{report_id}"}, {"sales_channel_id": body.sales_channel_id})

            if channel_aware_converter and row.get("original_xlsx_key"):
                # Phí Piship (Shopee-only, see derive.channel_has_piship) is
                # gated at conversion time, not query time — a channel
                # assigned only via this PATCH (rather than at upload) must
                # reconvert the file now with the same column mapping it
                # already resolved, or the stored Parquet keeps whatever
                # Piship value the old channel (or no channel) produced.
                channel_row = (
                    await db.pg_select_one("sales_channels", {"id": f"eq.{body.sales_channel_id}"})
                    if body.sales_channel_id
                    else None
                )
                channel_name = channel_row["name"] if channel_row else None
                await _reconvert_report(report_id, row, channel_name)

            return {"ok": True}

    @router.patch("/{report_id}/lock")
    async def update_lock(report_id: str, body: LockUpdateRequest, user: dict = Depends(require_admin)):
        row = await db.pg_select_one(table, {"id": f"eq.{report_id}"})
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
        await db.pg_update(table, {"id": f"eq.{report_id}"}, {"locked": body.locked})
        return {"ok": True}

    @router.delete("/{report_id}", status_code=204)
    async def delete_report(report_id: str, user: dict = Depends(require_admin)):
        row = await db.pg_select_one(table, {"id": f"eq.{report_id}"})
        if not row:
            raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
        if row.get("locked"):
            raise HTTPException(status_code=409, detail="Report đã bị khóa — mở khóa trước khi xóa.")
        storage.delete_objects([row.get("original_xlsx_key"), row.get("parquet_key")])
        await db.pg_delete(table, {"id": f"eq.{report_id}"})

    return router
