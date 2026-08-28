"""Report CRUD: upload (admin), list, get, mapping override (admin), delete
(admin). Excel -> Parquet conversion runs as a FastAPI BackgroundTask, in
the same process, on the same event loop — see app/excel_to_parquet.py for
the conversion itself. No queue/worker process for v1 (see the plan's
"background-job graduation condition").

The upload/list/get/channel/delete endpoints come from _report_crud's
shared factory; only the "Chỉnh cột" headers/mapping-override endpoints
below are Orders-specific.
"""
from __future__ import annotations

import tempfile

from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from .. import db, storage
from ..deps import require_admin
from ..excel_to_parquet import MappingError, excel_to_parquet, get_original_headers
from ..models import MappingUpdateRequest
from ..query_engine import invalidate_local_parquet_cache
from ._report_crud import convert_with_backpressure, create_report_crud_router

router = create_report_crud_router(
    prefix="/api/reports",
    tag="reports",
    table="reports",
    list_fields="id,name,uploaded_at,uploaded_by,row_count,status,error_message,sales_channel_id,locked",
    original_key_fn=storage.original_key,
    parquet_key_fn=storage.parquet_key,
    converter=excel_to_parquet,
    mapping_error=MappingError,
    has_channel=True,
    # excel_to_parquet accepts sales_channel_name to gate Phí Piship
    # (Shopee-only) — see derive.channel_has_piship.
    channel_aware_converter=True,
)


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

    # Reconverting must keep gating Phí Piship by this Report's own
    # already-assigned channel (if any), same as the initial upload did —
    # otherwise re-mapping a TikTok Report would silently reapply Shopee's
    # default-on Piship.
    sales_channel_name = None
    if row.get("sales_channel_id"):
        channel_row = await db.pg_select_one("sales_channels", {"id": f"eq.{row['sales_channel_id']}"})
        sales_channel_name = channel_row["name"] if channel_row else None

    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
        await run_in_threadpool(storage.download_to_path, row["original_xlsx_key"], tmp.name)
        try:
            parquet_bytes, row_count, mapping = await convert_with_backpressure(
                excel_to_parquet, tmp.name, mapping_override=body.mapping, sales_channel_name=sales_channel_name
            )
        except MappingError as e:
            raise HTTPException(status_code=400, detail=str(e))

    await run_in_threadpool(storage.upload_bytes, storage.parquet_key(report_id), parquet_bytes, "application/octet-stream")
    invalidate_local_parquet_cache(report_id)
    await db.pg_update(
        "reports", {"id": f"eq.{report_id}"}, {"mapping": mapping, "row_count": row_count}
    )
    return {"ok": True, "rowCount": row_count}
