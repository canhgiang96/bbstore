"""Monthly P&L-style analysis ("Phân tích tháng") — Doanh thu thuần/NMV/
Lợi nhuận gộp summed per calendar month across ALL history (deliberately
unfiltered, not scoped by the Dashboard's Thời gian/Trạng thái/Kênh bán
hàng/etc filters — confirmed with the user 2026-08-28), joined with
manually-entered monthly Chi phí bán hàng/Chi phí quản lý (company-level
operating expenses no uploaded Excel file carries) to compute Lợi nhuận
and the standard ratio columns the user's own reference spreadsheet uses
(which led with GMV — the user explicitly asked for Doanh thu thuần here
instead, 2026-08-28).

Reuses routers.dashboard's Report-gathering helpers rather than
duplicating them — this endpoint needs the exact same "every ready
Report's Parquet + supporting sources" set the Dashboard's own /summary
does, just run through a different, unfiltered query.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import db
from ..deps import get_current_user, require_admin
from ..models import MonthlyAnalysisOut, MonthlyExpenseUpdateRequest
from ..query_engine import run_monthly_analysis_query
from .dashboard import _all_dashboard_sources, _all_ready_reports

router = APIRouter(prefix="/api/monthly-analysis", tags=["monthly-analysis"])


@router.get("", response_model=list[MonthlyAnalysisOut])
async def monthly_analysis(user: dict = Depends(get_current_user)):
    reports = await _all_ready_reports()
    paths, cashflow_paths, combo_paths, master_paths, channel_paths, aff_paths, inhouse_handles = (
        await _all_dashboard_sources(reports)
    )
    monthly = run_monthly_analysis_query(
        paths,
        cashflow_source=cashflow_paths, combo_source=combo_paths, master_source=master_paths,
        channel_source=channel_paths, aff_source=aff_paths, inhouse_handles=inhouse_handles,
    )

    try:
        expense_rows = await db.pg_select("monthly_expenses", {"select": "month,chi_phi_ban_hang,chi_phi_quan_ly"})
    except Exception:  # noqa: BLE001 — the monthly_expenses table may not exist yet (migration pending)
        expense_rows = []
    # "month" comes back from Supabase as a full "YYYY-MM-DD" date (always
    # the 1st) — keyed here by its "YYYY-MM" prefix to match the "date" ->
    # month grouping DuckDB's strftime produced above.
    expense_by_month = {row["month"][:7]: row for row in expense_rows}

    result = []
    for row in monthly:
        expense = expense_by_month.get(row["month"], {})
        chi_phi_ban_hang = float(expense.get("chi_phi_ban_hang") or 0)
        chi_phi_quan_ly = float(expense.get("chi_phi_quan_ly") or 0)
        result.append(MonthlyAnalysisOut(
            month=row["month"],
            doanhThuThuan=row["doanh_thu_thuan"],
            nmv=row["nmv"],
            loiNhuanGop=row["loi_nhuan_gop"],
            chiPhiBanHang=chi_phi_ban_hang,
            chiPhiQuanLy=chi_phi_quan_ly,
            loiNhuan=row["loi_nhuan_gop"] - chi_phi_ban_hang - chi_phi_quan_ly,
        ))
    return result


@router.patch("/{month}")
async def update_monthly_expense(month: str, body: MonthlyExpenseUpdateRequest, user: dict = Depends(require_admin)):
    """month is "YYYY-MM" (from the editable table cell's row) — upserted
    against monthly_expenses' "month" date column (always the 1st), since
    PostgREST has no built-in upsert-by-primary-key helper in app.db.
    """
    month_date = f"{month}-01"
    data = {
        "chi_phi_ban_hang": body.chiPhiBanHang,
        "chi_phi_quan_ly": body.chiPhiQuanLy,
        "updated_by": user["id"],
    }
    updated = await db.pg_update("monthly_expenses", {"month": f"eq.{month_date}"}, data)
    if not updated:
        await db.pg_insert("monthly_expenses", {"month": month_date, **data})
    return {"ok": True}
