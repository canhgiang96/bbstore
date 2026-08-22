"""Điều chỉnh doanh thu (revenue adjustment) Report CRUD — mirrors
app/routers/master_reports.py, pointed at the adjustments_reports table/R2
prefix and adjustment_excel_to_parquet(). Unlike Combo/Cashflow/Master File,
this data was never joined into the Orders Dashboard's query engine — it's
a standalone record-keeping viewer — so it also gets its own read-only
/rows endpoint (the Report model has no manual per-row add/edit like the
IndexedDB manager this replaced) and a /channel PATCH like Đơn hàng/Dòng
tiền, for the user's own organizational tagging.
"""
from __future__ import annotations

import io
import uuid

import duckdb
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import db, storage
from ..adjustments_to_parquet import AdjustmentMappingError, adjustment_excel_to_parquet
from ..deps import get_current_user, require_admin
from ..models import ChannelUpdateRequest, ReportCreatedOut, ReportDetailOut, ReportOut, RowsOut
from ..query_engine import get_local_parquet_async

router = APIRouter(prefix="/api/adjustments-reports", tags=["adjustments-reports"])

ADJUSTMENTS_REPORT_LIST_FIELDS = "id,name,uploaded_at,uploaded_by,row_count,status,error_message,sales_channel_id"
ADJUSTMENT_COLUMNS = [
    "transactionId", "adjustmentDate", "adjustmentType", "reason",
    "amount", "relatedOrderId", "paymentCompletedDate",
]


async def _process_adjustments_report(report_id: str, xlsx_bytes) -> None:
    try:
        parquet_bytes, row_count, mapping = await run_in_threadpool(
            adjustment_excel_to_parquet, io.BytesIO(xlsx_bytes)
        )
        await run_in_threadpool(
            storage.upload_bytes, storage.adjustments_parquet_key(report_id), parquet_bytes, "application/octet-stream"
        )
        await db.pg_update(
            "adjustments_reports",
            {"id": f"eq.{report_id}"},
            {
                "status": "ready",
                "row_count": row_count,
                "mapping": mapping,
                "parquet_key": storage.adjustments_parquet_key(report_id),
            },
        )
    except AdjustmentMappingError as e:
        await db.pg_update(
            "adjustments_reports", {"id": f"eq.{report_id}"}, {"status": "failed", "error_message": str(e)}
        )
    except Exception as e:  # noqa: BLE001 — a bad file should fail the Report, not crash the worker
        await db.pg_update(
            "adjustments_reports", {"id": f"eq.{report_id}"}, {"status": "failed", "error_message": f"Lỗi xử lý: {e}"}
        )


@router.post("", response_model=ReportCreatedOut, status_code=202)
async def create_adjustments_report(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: dict = Depends(require_admin),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file .xlsx, .xls, .csv")

    report_id = str(uuid.uuid4())
    xlsx_bytes = await file.read()

    await run_in_threadpool(
        storage.upload_bytes,
        storage.adjustments_original_key(report_id, file.filename),
        xlsx_bytes,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    name = file.filename.rsplit(".", 1)[0]
    await db.pg_insert(
        "adjustments_reports",
        {
            "id": report_id,
            "name": name,
            "original_filename": file.filename,
            "uploaded_by": user["id"],
            "status": "processing",
            "original_xlsx_key": storage.adjustments_original_key(report_id, file.filename),
            "file_size_bytes": len(xlsx_bytes),
        },
    )

    background_tasks.add_task(_process_adjustments_report, report_id, xlsx_bytes)
    return ReportCreatedOut(id=report_id, status="processing")


@router.get("", response_model=list[ReportOut])
async def list_adjustments_reports(user: dict = Depends(get_current_user)):
    rows = await db.pg_select(
        "adjustments_reports", {"select": ADJUSTMENTS_REPORT_LIST_FIELDS, "order": "uploaded_at.desc"}
    )
    return [ReportOut(**r) for r in rows]


@router.get("/{report_id}", response_model=ReportDetailOut)
async def get_adjustments_report(report_id: str, user: dict = Depends(get_current_user)):
    row = await db.pg_select_one("adjustments_reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    return ReportDetailOut(**row)


@router.get("/{report_id}/rows", response_model=RowsOut)
async def get_adjustments_rows(
    report_id: str, page: int = 1, pageSize: int = 15, user: dict = Depends(get_current_user)
):
    """Read-only, unfiltered paginated browse of one Report's rows — there's
    no Dashboard join for this data, so no filters/sort/search needed, just
    a way to see what was uploaded (replacing the old IndexedDB manager's
    row-browsing view, minus its manual add/edit which doesn't fit the
    Report model).
    """
    row = await db.pg_select_one("adjustments_reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    if row["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Report đang ở trạng thái {row['status']}, chưa sẵn sàng.")

    path = await get_local_parquet_async(report_id, row["parquet_key"])
    page = max(1, page)

    def _query_page():
        con = duckdb.connect(database=":memory:")
        try:
            total = con.execute("SELECT COUNT(*) FROM read_parquet(?)", [path]).fetchone()[0]
            offset = (page - 1) * pageSize
            cols_sql = ", ".join(f'"{c}"' for c in ADJUSTMENT_COLUMNS)
            cursor = con.execute(
                f'SELECT {cols_sql} FROM read_parquet(?) LIMIT ? OFFSET ?', [path, pageSize, offset]
            )
            col_names = [d[0] for d in cursor.description]
            rows = [dict(zip(col_names, r)) for r in cursor.fetchall()]
            return {"rows": rows, "total": total, "page": page, "pageSize": pageSize}
        finally:
            con.close()

    return await run_in_threadpool(_query_page)


@router.patch("/{report_id}/channel")
async def update_channel(report_id: str, body: ChannelUpdateRequest, user: dict = Depends(require_admin)):
    row = await db.pg_select_one("adjustments_reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    await db.pg_update("adjustments_reports", {"id": f"eq.{report_id}"}, {"sales_channel_id": body.sales_channel_id})
    return {"ok": True}


@router.delete("/{report_id}", status_code=204)
async def delete_adjustments_report(report_id: str, user: dict = Depends(require_admin)):
    row = await db.pg_select_one("adjustments_reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    storage.delete_objects([row.get("original_xlsx_key"), row.get("parquet_key")])
    await db.pg_delete("adjustments_reports", {"id": f"eq.{report_id}"})
