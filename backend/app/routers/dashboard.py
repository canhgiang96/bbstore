from __future__ import annotations

import asyncio
import io
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import db
from ..deps import get_current_user
from ..models import RowsOut, SummaryOut
from ..query_engine import (
    DETAIL_COLUMNS,
    GROUP_BY_COLUMNS,
    GROUP_SORT_COLUMNS,
    get_local_parquet_async,
    run_export_query,
    run_grouped_rows_query,
    run_rows_query,
    run_summary_query,
)

# Vietnamese export headers — duplicated from the frontend's TABLE_COLS
# labels (js/app.js) since the exported file must read correctly regardless
# of what hit the API.
DETAIL_COLUMN_LABELS = {
    "date": "Ngày", "orderId": "Mã đơn hàng", "sku": "SKU", "skuVariant": "SKU phân loại",
    "product": "Sản phẩm", "category": "Danh mục", "customer": "Khách hàng",
    "quantity": "Số lượng", "returnedQty": "SL hoàn trả", "soLuongThuc": "SL thực",
    "price": "Giá bán", "originalPrice": "Giá gốc", "revenue": "Doanh thu", "doanhSo": "Doanh số",
    "status": "Status", "trangThai": "Trạng thái", "discount": "Giảm giá", "voucher": "Voucher",
    "platformFee": "Phí sàn", "piship": "Phí Piship", "phiAff": "Phí AFF",
    "phanLoaiKho": "Phân loại kho", "phanLoaiMuc": "Phân loại mục", "phanLoaiSp": "Phân loại sản phẩm",
    "giaVon": "Giá vốn", "gmv": "GMV", "doanhThuThuan": "Doanh thu thuần", "nmv": "NMV",
    "loiNhuanGop": "Lợi nhuận gộp", "salesChannel": "Kênh bán hàng",
}
GROUP_BY_LABELS = {
    "sku": "SKU", "product": "Sản phẩm", "category": "Danh mục", "customer": "Khách hàng",
    "status": "Trạng thái", "warehouseType": "Phân loại kho", "itemGroup": "Phân loại mục",
    "productType": "Phân loại sản phẩm", "orderId": "Mã đơn hàng", "salesChannel": "Kênh bán hàng",
}
GROUP_AGG_LABELS = {
    "rowCount": "Số dòng", "quantity": "Số lượng", "returnedQty": "SL hoàn trả",
    "soLuongThuc": "SL thực", "doanhSo": "Doanh số", "discount": "Giảm giá", "voucher": "Voucher",
    "platformFee": "Phí sàn", "piship": "Phí Piship", "phiAff": "Phí AFF", "giaVon": "Giá vốn",
    "gmv": "GMV", "doanhThuThuan": "Doanh thu thuần", "nmv": "NMV", "loiNhuanGop": "Lợi nhuận gộp",
}

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


async def _download_all(reports: list) -> list:
    """Concurrently resolves every Report's local Parquet path (downloading
    from R2 in a thread pool where the local cache is cold) instead of
    downloading one at a time — see get_local_parquet_async. reports must
    already be filtered to ones with a parquet_key.
    """
    if not reports:
        return []
    return list(await asyncio.gather(*(get_local_parquet_async(r["id"], r["parquet_key"]) for r in reports)))


async def _all_ready_reports() -> list:
    return await db.pg_select(
        "reports", {"status": "eq.ready", "select": "id,parquet_key,sales_channel_id"}
    )


async def _all_ready_parquet_paths(reports: list | None = None) -> list:
    if reports is None:
        reports = await _all_ready_reports()
    return await _download_all([r for r in reports if r.get("parquet_key")])


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
    return await _download_all([r for r in reports if r.get("parquet_key")])


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
    return await _download_all([r for r in reports if r.get("parquet_key")])


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
    return await _download_all([r for r in reports if r.get("parquet_key")])


async def _fetch_sales_channels() -> list:
    try:
        return await db.pg_select("sales_channels", {"select": "id,name"})
    except Exception:  # noqa: BLE001 — channel tagging is best-effort, never worth 500ing the whole Dashboard for
        return []


async def _orders_paths_and_channel_groups(reports: list) -> tuple[list, dict]:
    """Resolves every ready Orders Report's local Parquet path ONCE and
    groups them by assigned Sales Channel name from that same resolved list
    (unassigned/orphaned-FK Reports group under "") — reusing
    _all_ready_parquet_paths' downloads instead of a second
    get_local_parquet_async pass over the same Reports, which on a cold
    cache would race two concurrent downloads to the same local file.
    """
    ready = [r for r in reports if r.get("parquet_key")]
    paths, channels = await asyncio.gather(_download_all(ready), _fetch_sales_channels())
    channel_names = {c["id"]: c["name"] for c in channels}
    groups: dict = {}
    for r, path in zip(ready, paths):
        name = channel_names.get(r.get("sales_channel_id"), "")
        groups.setdefault(name, []).append(path)
    return list(paths), groups


async def _all_dashboard_sources(reports: list) -> tuple[list, list, list, list, dict]:
    """Fetches every supporting dataset needed by the Dashboard's query
    engine concurrently instead of one sequential await per dataset — each
    is an independent Supabase (+ R2 download) round trip, so awaiting them
    one at a time was pure added latency on every single Dashboard request.
    """
    (paths, channel_paths), cashflow_paths, combo_paths, master_paths = await asyncio.gather(
        _orders_paths_and_channel_groups(reports),
        _all_ready_cashflow_parquet_paths(),
        _all_ready_combo_parquet_paths(),
        _all_ready_master_parquet_paths(),
    )
    return paths, cashflow_paths, combo_paths, master_paths, channel_paths


@dashboard_router.get("/summary", response_model=SummaryOut)
async def dashboard_summary(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: list[str] = Query([]),
    warehouseType: list[str] = Query([]),
    itemGroup: list[str] = Query([]),
    productType: list[str] = Query([]),
    sku: Optional[str] = None,
    salesChannel: list[str] = Query([]),
    user: dict = Depends(get_current_user),
):
    reports = await _all_ready_reports()
    paths, cashflow_paths, combo_paths, master_paths, channel_paths = await _all_dashboard_sources(reports)
    # DuckDB building/querying orders_working is sync and can take real time
    # over hundreds of thousands of rows — off the event loop so it doesn't
    # freeze every other concurrent request for the duration.
    return await run_in_threadpool(
        run_summary_query, paths,
        from_date=from_, to_date=to, category=category, status=status,
        cashflow_source=cashflow_paths, combo_source=combo_paths, master_source=master_paths,
        warehouse_type=warehouseType, item_group=itemGroup, product_type=productType, sku=sku,
        channel_source=channel_paths, sales_channel=salesChannel,
    )


def _zip_path_filters(path_by: list[str], path_value: list[str]) -> list[tuple[str, str]]:
    """Zips the repeated ?pathBy=&pathValue= query params into (key, value)
    pairs for the nested/hierarchical "Group theo" drill-down — each pair
    narrows to one ancestor level (e.g. pathBy=category&pathValue=Áo).
    """
    if len(path_by) != len(path_value):
        raise HTTPException(status_code=400, detail="pathBy và pathValue phải có cùng số lượng.")
    for key in path_by:
        if key not in GROUP_BY_COLUMNS:
            raise HTTPException(status_code=400, detail=f"pathBy không hợp lệ: {key}")
    return list(zip(path_by, path_value))


@dashboard_router.get("/rows", response_model=RowsOut)
async def dashboard_rows(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: list[str] = Query([]),
    warehouseType: list[str] = Query([]),
    itemGroup: list[str] = Query([]),
    productType: list[str] = Query([]),
    sku: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "date",
    sort_dir: str = "asc",
    page: int = 1,
    pageSize: int = 15,
    pathBy: list[str] = Query([]),
    pathValue: list[str] = Query([]),
    salesChannel: list[str] = Query([]),
    user: dict = Depends(get_current_user),
):
    path_filters = _zip_path_filters(pathBy, pathValue)
    reports = await _all_ready_reports()
    paths, cashflow_paths, combo_paths, master_paths, channel_paths = await _all_dashboard_sources(reports)
    return await run_in_threadpool(
        run_rows_query, paths,
        from_date=from_, to_date=to, category=category, status=status,
        search=search, sort=sort, sort_dir=sort_dir, page=page, page_size=pageSize,
        cashflow_source=cashflow_paths, combo_source=combo_paths, master_source=master_paths,
        warehouse_type=warehouseType, item_group=itemGroup, product_type=productType, sku=sku,
        path_filters=path_filters, channel_source=channel_paths, sales_channel=salesChannel,
    )


@dashboard_router.get("/rows/grouped", response_model=RowsOut)
async def dashboard_rows_grouped(
    groupBy: str,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: list[str] = Query([]),
    warehouseType: list[str] = Query([]),
    itemGroup: list[str] = Query([]),
    productType: list[str] = Query([]),
    sku: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "doanhSo",
    sortDir: str = "desc",
    page: int = 1,
    pageSize: int = 15,
    pathBy: list[str] = Query([]),
    pathValue: list[str] = Query([]),
    salesChannel: list[str] = Query([]),
    user: dict = Depends(get_current_user),
):
    if groupBy not in GROUP_BY_COLUMNS:
        raise HTTPException(status_code=400, detail=f"groupBy không hợp lệ: {groupBy}")
    path_filters = _zip_path_filters(pathBy, pathValue)
    reports = await _all_ready_reports()
    paths, cashflow_paths, combo_paths, master_paths, channel_paths = await _all_dashboard_sources(reports)
    return await run_in_threadpool(
        run_grouped_rows_query, paths,
        from_date=from_, to_date=to, category=category, status=status,
        search=search, group_by=groupBy, sort=sort, sort_dir=sortDir, page=page, page_size=pageSize,
        cashflow_source=cashflow_paths, combo_source=combo_paths, master_source=master_paths,
        warehouse_type=warehouseType, item_group=itemGroup, product_type=productType, sku=sku,
        path_filters=path_filters, channel_source=channel_paths, sales_channel=salesChannel,
    )


@dashboard_router.get("/export")
async def dashboard_export(
    columns: str,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: list[str] = Query([]),
    warehouseType: list[str] = Query([]),
    itemGroup: list[str] = Query([]),
    productType: list[str] = Query([]),
    sku: Optional[str] = None,
    search: Optional[str] = None,
    groupBy: Optional[str] = None,
    sort: Optional[str] = None,
    sortDir: str = "asc",
    salesChannel: list[str] = Query([]),
    user: dict = Depends(get_current_user),
):
    """Exports every row/group matching the current Detail-table view (not
    just the on-screen page) to an .xlsx file — a single DuckDB scan via
    run_export_query, streamed back as a StreamingResponse so nothing huge
    sits in memory as a second copy longer than necessary.
    """
    col_keys = [c for c in columns.split(",") if c]
    if groupBy is not None and groupBy not in GROUP_BY_COLUMNS:
        raise HTTPException(status_code=400, detail=f"groupBy không hợp lệ: {groupBy}")

    allowed_cols = set(GROUP_SORT_COLUMNS) if groupBy else set(DETAIL_COLUMNS)
    col_keys = [c for c in col_keys if c in allowed_cols]
    if not col_keys:
        raise HTTPException(status_code=400, detail="Không có cột hợp lệ để xuất.")

    reports = await _all_ready_reports()
    paths, cashflow_paths, combo_paths, master_paths, channel_paths = await _all_dashboard_sources(reports)
    rows = await run_in_threadpool(
        run_export_query, paths,
        from_date=from_, to_date=to, category=category, status=status,
        search=search, group_by=groupBy, sort=sort, sort_dir=sortDir,
        cashflow_source=cashflow_paths, combo_source=combo_paths, master_source=master_paths,
        warehouse_type=warehouseType, item_group=itemGroup, product_type=productType, sku=sku,
        channel_source=channel_paths, sales_channel=salesChannel,
    )

    labels = {**GROUP_AGG_LABELS, "groupValue": GROUP_BY_LABELS.get(groupBy, "Nhóm")} if groupBy else DETAIL_COLUMN_LABELS
    buf = await run_in_threadpool(rows_to_xlsx_bytes, rows, col_keys, labels)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="du-lieu-chi-tiet.xlsx"'},
    )


def rows_to_xlsx_bytes(rows: list[dict], col_keys: list[str], labels: dict[str, str]) -> io.BytesIO:
    """Builds an in-memory .xlsx from a list of row dicts, keeping only
    col_keys (in order) and renaming them to their Vietnamese labels. Split
    out from dashboard_export so it's directly unit-testable without going
    through FastAPI/Supabase.
    """
    df = pd.DataFrame(rows)[col_keys] if rows else pd.DataFrame(columns=col_keys)
    df.columns = [labels.get(c, c) for c in col_keys]
    buf = io.BytesIO()
    df.to_excel(buf, engine="openpyxl", index=False)
    buf.seek(0)
    return buf


@router.get("/{report_id}/summary", response_model=SummaryOut)
async def summary(
    report_id: str,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    category: Optional[str] = None,
    status: list[str] = Query([]),
    warehouseType: list[str] = Query([]),
    itemGroup: list[str] = Query([]),
    productType: list[str] = Query([]),
    sku: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    report = await _get_ready_report(report_id)
    path, cashflow_paths, combo_paths, master_paths = await asyncio.gather(
        get_local_parquet_async(report_id, report["parquet_key"]),
        _all_ready_cashflow_parquet_paths(),
        _all_ready_combo_parquet_paths(),
        _all_ready_master_parquet_paths(),
    )
    return await run_in_threadpool(
        run_summary_query,
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
    status: list[str] = Query([]),
    warehouseType: list[str] = Query([]),
    itemGroup: list[str] = Query([]),
    productType: list[str] = Query([]),
    sku: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "date",
    sort_dir: str = "asc",
    page: int = 1,
    pageSize: int = 15,
    user: dict = Depends(get_current_user),
):
    report = await _get_ready_report(report_id)
    path, cashflow_paths, combo_paths, master_paths = await asyncio.gather(
        get_local_parquet_async(report_id, report["parquet_key"]),
        _all_ready_cashflow_parquet_paths(),
        _all_ready_combo_parquet_paths(),
        _all_ready_master_parquet_paths(),
    )
    return await run_in_threadpool(
        run_rows_query,
        path, from_date=from_, to_date=to, category=category, status=status,
        search=search, sort=sort, sort_dir=sort_dir, page=page, page_size=pageSize,
        cashflow_source=cashflow_paths, combo_source=combo_paths, master_source=master_paths,
        warehouse_type=warehouseType, item_group=itemGroup, product_type=productType, sku=sku,
    )
