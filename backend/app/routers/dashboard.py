from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db
from ..deps import get_current_user
from ..models import RowsOut, SummaryOut
from ..query_engine import get_local_parquet, run_rows_query, run_summary_query

router = APIRouter(prefix="/api/reports", tags=["dashboard"])


async def _get_ready_report(report_id: str) -> dict:
    row = await db.pg_select_one("reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    if row["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Report đang ở trạng thái {row['status']}, chưa sẵn sàng.")
    return row


@router.get("/{report_id}/summary", response_model=SummaryOut)
async def summary(
    report_id: str,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    report = await _get_ready_report(report_id)
    path = get_local_parquet(report_id, report["parquet_key"])
    return run_summary_query(path, from_date=from_, to_date=to, category=category, status=status)


@router.get("/{report_id}/rows", response_model=RowsOut)
async def rows(
    report_id: str,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "date",
    sort_dir: str = "asc",
    page: int = 1,
    pageSize: int = 15,
    user: dict = Depends(get_current_user),
):
    report = await _get_ready_report(report_id)
    path = get_local_parquet(report_id, report["parquet_key"])
    return run_rows_query(
        path, from_date=from_, to_date=to, category=category, status=status,
        search=search, sort=sort, sort_dir=sort_dir, page=page, page_size=pageSize,
    )
