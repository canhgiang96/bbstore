"""Điều chỉnh doanh thu (revenue adjustment) Report CRUD — built from
_report_crud's shared factory, pointed at the adjustments_reports table/R2
prefix and adjustment_excel_to_parquet(). Unlike Combo/Cashflow/Master File,
this data was never joined into the Orders Dashboard's query engine — it's
a standalone record-keeping viewer — so it also gets its own read-only
/rows endpoint below (the Report model has no manual per-row add/edit like
the IndexedDB manager this replaced) and a /channel PATCH like Đơn hàng/Dòng
tiền, for the user's own organizational tagging.
"""
from __future__ import annotations

import duckdb
from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from .. import db, storage
from ..adjustments_to_parquet import AdjustmentMappingError, adjustment_excel_to_parquet
from ..deps import get_current_user
from ..models import RowsOut
from ..query_engine import get_local_parquet_async
from ._report_crud import create_report_crud_router

ADJUSTMENT_COLUMNS = [
    "transactionId", "adjustmentDate", "adjustmentType", "reason",
    "amount", "relatedOrderId", "paymentCompletedDate",
]

router = create_report_crud_router(
    prefix="/api/adjustments-reports",
    tag="adjustments-reports",
    table="adjustments_reports",
    list_fields="id,name,uploaded_at,uploaded_by,row_count,status,error_message,sales_channel_id",
    original_key_fn=storage.adjustments_original_key,
    parquet_key_fn=storage.adjustments_parquet_key,
    converter=adjustment_excel_to_parquet,
    mapping_error=AdjustmentMappingError,
    has_channel=True,
)


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
