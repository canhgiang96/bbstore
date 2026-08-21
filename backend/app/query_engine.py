"""DuckDB queries over one Report's data.parquet — backs GET /reports/{id}/summary
and GET /reports/{id}/rows. Mirrors the aggregation logic in js/app.js's
renderKPIs/sumDoanhSoWhere, renderTimelineChart/topN, and renderTable, but
computed server-side so the browser never needs the full row set.
"""
from __future__ import annotations

import os

import duckdb

from . import storage
from .config import get_settings

DETAIL_COLUMNS = [
    "date", "orderId", "sku", "skuVariant", "product", "category", "customer",
    "quantity", "returnedQty", "soLuongThuc", "price", "originalPrice",
    "revenue", "doanhSo", "status", "trangThai",
]

ALLOWED_SORT_COLUMNS = {
    "date", "orderId", "product", "category", "customer",
    "quantity", "doanhSo", "trangThai",
}

GMV_STATUSES_SQL = "('Hoàn thành', 'Đang giao')"
HOAN_STATUSES_SQL = "('Hoàn hàng', 'Hoàn 1 phần')"


def _connect():
    return duckdb.connect(database=":memory:")


def get_local_parquet(report_id: str, parquet_object_key: str) -> str:
    """Downloads a Report's data.parquet from R2 into a local cache dir (once
    per process — a report's Parquet is immutable once status=ready) and
    returns the local path DuckDB should read from.
    """
    s = get_settings()
    os.makedirs(s.parquet_cache_dir, exist_ok=True)
    local_path = os.path.join(s.parquet_cache_dir, f"{report_id}.parquet")
    if not os.path.exists(local_path):
        storage.download_to_path(parquet_object_key, local_path)
    return local_path


def invalidate_local_parquet_cache(report_id: str) -> None:
    """Call after a Report's Parquet is overwritten (remapping, see
    routers/reports.py's PATCH .../mapping) so the next query re-downloads
    the new file instead of serving the stale cached one.
    """
    s = get_settings()
    local_path = os.path.join(s.parquet_cache_dir, f"{report_id}.parquet")
    if os.path.exists(local_path):
        os.remove(local_path)


def _where_clause(from_date=None, to_date=None, category=None, status=None):
    clauses = []
    params: list = []
    if from_date:
        clauses.append('"date" >= ?')
        params.append(from_date)
    if to_date:
        clauses.append('"date" <= ?')
        params.append(to_date)
    if category:
        clauses.append('"category" = ?')
        params.append(category)
    if status:
        clauses.append('"trangThai" = ?')
        params.append(status)
    return (" AND ".join(clauses) if clauses else "1=1"), params


def run_summary_query(parquet_path, from_date=None, to_date=None, category=None, status=None) -> dict:
    con = _connect()
    try:
        where_sql, params = _where_clause(from_date, to_date, category, status)

        totals_sql = f"""
            SELECT
              COALESCE(SUM("doanhSo"), 0) AS total,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "doanhSo" ELSE 0 END), 0) AS gmv,
              COALESCE(SUM(CASE WHEN "trangThai" = 'Hủy chưa XK' THEN "doanhSo" ELSE 0 END), 0) AS huy_chua_xk,
              COALESCE(SUM(CASE WHEN "trangThai" = 'Hủy sau XK' THEN "doanhSo" ELSE 0 END), 0) AS huy_sau_xk,
              COALESCE(SUM(CASE WHEN "trangThai" IN {HOAN_STATUSES_SQL} THEN "doanhSo" ELSE 0 END), 0) AS hoan,
              COUNT(*) AS row_count
            FROM read_parquet(?) WHERE {where_sql}
        """
        total, gmv, huy_chua_xk, huy_sau_xk, hoan, row_count = con.execute(
            totals_sql, [parquet_path, *params]
        ).fetchone()

        timeline_sql = f"""
            SELECT strftime("date", '%Y-%m') AS month, SUM("doanhSo") AS value
            FROM read_parquet(?) WHERE {where_sql}
            GROUP BY month ORDER BY month
        """
        timeline = con.execute(timeline_sql, [parquet_path, *params]).fetchall()

        def top_n(column: str, n: int = 8):
            sql = f"""
                SELECT "{column}" AS label, SUM("doanhSo") AS value
                FROM read_parquet(?) WHERE {where_sql}
                GROUP BY label ORDER BY value DESC LIMIT {n}
            """
            return con.execute(sql, [parquet_path, *params]).fetchall()

        top_products = top_n("product")
        category_breakdown = top_n("category")
        top_customers = top_n("customer")

        # Facets are computed over the whole Report (no filters applied) so the
        # dropdown options don't shrink as the user filters — matches
        # initDashboardFilters() populating from dash.records, not dash.filtered.
        facets_sql = """
            SELECT
              list(DISTINCT "category") AS categories,
              list(DISTINCT "trangThai") AS statuses
            FROM read_parquet(?)
        """
        categories, statuses = con.execute(facets_sql, [parquet_path]).fetchone()

        return {
            "kpis": {
                "doanhSo": total,
                "gmv": gmv,
                "huyChuaXK": huy_chua_xk,
                "huySauXK": huy_sau_xk,
                "hoan": hoan,
                "rowCount": row_count,
            },
            "timeline": [{"month": m, "value": v} for m, v in timeline],
            "topProducts": [{"label": l, "value": v} for l, v in top_products],
            "categoryBreakdown": [{"label": l, "value": v} for l, v in category_breakdown],
            "topCustomers": [{"label": l, "value": v} for l, v in top_customers],
            "facets": {
                "categories": sorted(c for c in (categories or []) if c is not None),
                "statuses": [s for s in (statuses or []) if s is not None],
            },
        }
    finally:
        con.close()


def run_rows_query(
    parquet_path,
    from_date=None, to_date=None, category=None, status=None,
    search=None, sort="date", sort_dir="asc", page=1, page_size=15,
) -> dict:
    con = _connect()
    try:
        where_sql, params = _where_clause(from_date, to_date, category, status)

        if search:
            where_sql += (
                ' AND (lower("product") LIKE ? OR lower("category") LIKE ? OR '
                'lower("customer") LIKE ? OR lower("orderId") LIKE ? OR '
                'lower("sku") LIKE ? OR lower("skuVariant") LIKE ?)'
            )
            like = f"%{search.lower()}%"
            params.extend([like] * 6)

        total = con.execute(
            f'SELECT COUNT(*) FROM read_parquet(?) WHERE {where_sql}', [parquet_path, *params]
        ).fetchone()[0]

        # Column/direction are whitelisted, not parameterized — DuckDB can't
        # bind identifiers, so this is the injection guard.
        sort_col = sort if sort in ALLOWED_SORT_COLUMNS else "date"
        sort_dir_sql = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
        page = max(1, page)
        offset = (page - 1) * page_size

        cols_sql = ", ".join(f'"{c}"' for c in DETAIL_COLUMNS)
        rows_sql = f"""
            SELECT {cols_sql}
            FROM read_parquet(?) WHERE {where_sql}
            ORDER BY "{sort_col}" {sort_dir_sql}
            LIMIT ? OFFSET ?
        """
        # .fetchall() (not .fetchdf()) so values come back as plain Python
        # types (int/float/str/datetime) — a pandas DataFrame would give us
        # numpy/pandas types that FastAPI's JSON encoder can choke on.
        cursor = con.execute(rows_sql, [parquet_path, *params, page_size, offset])
        col_names = [d[0] for d in cursor.description]
        rows = [dict(zip(col_names, r)) for r in cursor.fetchall()]

        return {"rows": rows, "total": total, "page": page, "pageSize": page_size}
    finally:
        con.close()
