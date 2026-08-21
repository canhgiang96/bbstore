from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db
from ..deps import get_current_user
from ..models import RowsOut, SummaryOut
from ..query_engine import get_local_parquet, run_rows_query, run_summary_query

router = APIRouter(prefix="/api/reports", tags=["dashboard"])

# The Dashboard aggregates every ready Report instead of pinning to one —
# see dashboard_router below. Kept under a separate prefix/router since it
# isn't scoped to a single report_id.
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


async def _get_ready_report(report_id: str) -> dict:
    row = await db.pg_select_one("reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    if row["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Report đang ở trạng thái {row['status']}, chưa sẵn sàng.")
    return row


async def _all_ready_parquet_paths() -> list:
    reports = await db.pg_select("reports", {"status": "eq.ready", "select": "id,parquet_key"})
    return [get_local_parquet(r["id"], r["parquet_key"]) for r in reports if r.get("parquet_key")]


async def _all_ready_cashflow_parquet_paths() -> list:
    """Every ready Cashflow (Dòng tiền) Report's Parquet — joined into the
    Orders query at query time for Phí AFF (see query_engine._cashflow_join),
    not at Orders-conversion time, so uploading Dòng tiền later still applies
    to Orders Reports that were converted before it existed.

    Swallows errors (e.g. the cashflow_reports table not existing yet, right
    after this ships and before the Supabase migration has been run) so the
    Orders Dashboard itself never breaks because of this — it just degrades
    to phiAff=0, same as "no cashflow data uploaded yet".
    """
    try:
        reports = await db.pg_select("cashflow_reports", {"status": "eq.ready", "select": "id,parquet_key"})
    except Exception:  # noqa: BLE001 — Phí AFF is best-effort, never worth 500ing the whole Dashboard for
        return []
    return [get_local_parquet(r["id"], r["parquet_key"]) for r in reports if r.get("parquet_key")]


async def _all_ready_combo_parquet_paths() -> list:
    """Every ready Combo Report's Parquet — used to explode matching Orders
    rows into their sub-SKU components at query time (see
    query_engine._combo_join / _build_orders_working). Same rationale as
    Cashflow: joined at query time (not Orders-conversion time) so uploading
    Combo data later still applies to already-converted Orders Reports, and
    the same best-effort []-on-error fallback (the combo_reports table may
    not exist yet right after this ships, before the Supabase migration).
    """
    try:
        reports = await db.pg_select("combo_reports", {"status": "eq.ready", "select": "id,parquet_key"})
    except Exception:  # noqa: BLE001 — combo explosion is best-effort, never worth 500ing the whole Dashboard for
        return []
    return [get_local_parquet(r["id"], r["parquet_key"]) for r in reports if r.get("parquet_key")]


async def _all_ready_master_parquet_paths() -> list:
    """Every ready Master File Report's Parquet — used to look up cost/
    category data for Orders rows by parent SKU at query time (see
    query_engine._master_join). Same query-time rationale and best-effort
    []-on-error fallback as Cashflow/Combo.
    """
    try:
        reports = await db.pg_select("master_reports", {"status": "eq.ready", "select": "id,parquet_key"})
    except Exception:  # noqa: BLE001 — cost/category lookup is best-effort, never worth 500ing the whole Dashboard for
        return []
    return [get_local_parquet(r["id"], r["parquet_key"]) for r in reports if r.get("parquet_key")]


@dashboard_router.get("/summary", response_model=SummaryOut)
async def dashboard_summary(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    warehouseType: Optional[str] = None,
    itemGroup: Optional[str] = None,
    productType: Optional[str] = None,
    sku: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    paths = await _all_ready_parquet_paths()
    cashflow_paths = await _all_ready_cashflow_parquet_paths()
    combo_paths = await _all_ready_combo_parquet_paths()
    master_paths = await _all_ready_master_parquet_paths()
    return run_summary_query(
        paths, from_date=from_, to_date=to, category=category, status=status,
        cashflow_source=cashflow_paths, combo_source=combo_paths, master_source=master_paths,
        warehouse_type=warehouseType, item_group=itemGroup, product_type=productType, sku=sku,
    )


@dashboard_router.get("/rows", response_model=RowsOut)
async def dashboard_rows(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    warehouseType: Optional[str] = None,
    itemGroup: Optional[str] = None,
    productType: Optional[str] = None,
    sku: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "date",
    sort_dir: str = "asc",
    page: int = 1,
    pageSize: int = 15,
    user: dict = Depends(get_current_user),
):
    paths = await _all_ready_parquet_paths()
    cashflow_paths = await _all_ready_cashflow_parquet_paths()
    combo_paths = await _all_ready_combo_parquet_paths()
    master_paths = await _all_ready_master_parquet_paths()
    return run_rows_query(
        paths, from_date=from_, to_date=to, category=category, status=status,
        search=search, sort=sort, sort_dir=sort_dir, page=page, page_size=pageSize,
        cashflow_source=cashflow_paths, combo_source=combo_paths, master_source=master_paths,
        warehouse_type=warehouseType, item_group=itemGroup, product_type=productType, sku=sku,
    )


@router.get("/{report_id}/summary", response_model=SummaryOut)
async def summary(
    report_id: str,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    warehouseType: Optional[str] = None,
    itemGroup: Optional[str] = None,
    productType: Optional[str] = None,
    sku: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    report = await _get_ready_report(report_id)
    path = get_local_parquet(report_id, report["parquet_key"])
    cashflow_paths = await _all_ready_cashflow_parquet_paths()
    combo_paths = await _all_ready_combo_parquet_paths()
    master_paths = await _all_ready_master_parquet_paths()
    return run_summary_query(
        path, from_date=from_, to_date=to, category=category, status=status,
        cashflow_source=cashflow_paths, combo_source=combo_paths, master_source=master_paths,
        warehouse_type=warehouseType, item_group=itemGroup, product_type=productType, sku=sku,
    )


@router.get("/{report_id}/rows", response_model=RowsOut)
async def rows(
    report_id: str,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    warehouseType: Optional[str] = None,
    itemGroup: Optional[str] = None,
    productType: Optional[str] = None,
    sku: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "date",
    sort_dir: str = "asc",
    page: int = 1,
    pageSize: int = 15,
    user: dict = Depends(get_current_user),
):
    report = await _get_ready_report(report_id)
    path = get_local_parquet(report_id, report["parquet_key"])
    cashflow_paths = await _all_ready_cashflow_parquet_paths()
    combo_paths = await _all_ready_combo_parquet_paths()
    master_paths = await _all_ready_master_parquet_paths()
    return run_rows_query(
        path, from_date=from_, to_date=to, category=category, status=status,
        search=search, sort=sort, sort_dir=sort_dir, page=page, page_size=pageSize,
        cashflow_source=cashflow_paths, combo_source=combo_paths, master_source=master_paths,
        warehouse_type=warehouseType, item_group=itemGroup, product_type=productType, sku=sku,
    )
