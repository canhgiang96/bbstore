"""DuckDB queries over Report data.parquet files — backs the Dashboard's
summary/rows endpoints. `parquet_source` accepts either a single local path
(one Report) or a list of paths (aggregated across every ready Report,
since the Dashboard no longer pins to one Report — filtering by date/
category/status is how the user narrows the view instead). Mirrors the
aggregation logic in the old client-side js/app.js's renderKPIs/
sumDoanhSoWhere, renderTimelineChart/topN, and renderTable, computed
server-side so the browser never needs the full row set.
"""
from __future__ import annotations

import os

import duckdb

from . import storage
from .config import get_settings

DETAIL_COLUMNS = [
    "date", "orderId", "sku", "skuVariant", "product", "category", "customer",
    "quantity", "returnedQty", "soLuongThuc", "price", "originalPrice",
    "revenue", "doanhSo", "status", "trangThai", "discount", "voucher",
    "platformFee", "piship", "phiAff",
]

# Columns that may be absent on Reports converted before they existed —
# queried via COALESCE(..., 0) when present, or a literal 0 when the
# column is missing from every file in parquet_source (see
# _available_columns / col_or_zero). "phiAff" isn't a literal Orders column
# at all — it's computed via the Cashflow join (see _cashflow_join) — so it
# isn't in this set even though it gets the same "default to 0" treatment.
OPTIONAL_NUMERIC_COLUMNS = {"discount", "voucher", "platformFee", "piship"}

ALLOWED_SORT_COLUMNS = {
    "date", "orderId", "product", "category", "customer",
    "quantity", "doanhSo", "trangThai",
}

GMV_STATUSES_SQL = "('Hoàn thành', 'Đang giao', 'Hoàn 1 phần')"

EMPTY_SUMMARY = {
    "kpis": {
        "doanhSo": 0, "gmv": 0, "huyChuaXK": 0, "huySauXK": 0, "hoan": 0,
        "discount": 0, "voucher": 0, "platformFee": 0, "piship": 0, "phiAff": 0,
        "nmv": 0, "rowCount": 0,
    },
    "timeline": [],
    "topProducts": [],
    "categoryBreakdown": [],
    "topCustomers": [],
    "facets": {"categories": [], "statuses": []},
}


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


def _is_empty_source(parquet_source) -> bool:
    return isinstance(parquet_source, (list, tuple)) and len(parquet_source) == 0


def _where_clause(from_date=None, to_date=None, category=None, status=None):
    clauses = []
    params: list = []
    if from_date:
        # Explicit CAST — DuckDB rejects an implicit TIMESTAMP/VARCHAR
        # comparison here (BinderException), even though the mirrored
        # to_date clause below apparently gets inferred fine on its own.
        clauses.append('"date" >= CAST(? AS DATE)')
        params.append(from_date)
    if to_date:
        # to_date arrives as a plain "YYYY-MM-DD" (from <input type="date">),
        # which would otherwise mean midnight — excluding every order placed
        # later that same day, since "date" carries a real time-of-day.
        clauses.append('"date" < (CAST(? AS DATE) + INTERVAL 1 DAY)')
        params.append(to_date)
    if category:
        clauses.append('"category" = ?')
        params.append(category)
    if status:
        clauses.append('"trangThai" = ?')
        params.append(status)
    return (" AND ".join(clauses) if clauses else "1=1"), params


def _available_columns(con, parquet_source) -> set:
    """Reports converted before "discount"/"voucher" existed don't have
    those columns in their Parquet schema. union_by_name=true lets DuckDB
    read a set of Reports with differing schemas together (missing columns
    come back NULL) — but only when there's at least one file that DOES
    have the column; a lone old-schema Report still needs the caller to
    fall back to a literal 0, hence checking availability up front instead
    of just always referencing the column name.
    """
    cur = con.execute("SELECT * FROM read_parquet(?, union_by_name=true) LIMIT 0", [parquet_source])
    return {d[0] for d in cur.description}


def _cashflow_join(available: set, cashflow_source) -> tuple[str, list, str]:
    """Returns (join_sql, join_params, aff_expr) to LEFT JOIN per-order Phí
    AFF from ready Cashflow Reports into an Orders query whose FROM clause
    is aliased "o". aff_expr is always safe to use unconditionally — it's a
    literal "0" when there's no cashflow data yet, or when this Orders
    Report predates the "orderPaidRatio" column (same backward-compat
    pattern as discount/voucher/platformFee/piship).

    The GROUP BY in the subquery guards against the same Mã đơn hàng
    appearing in more than one uploaded Cashflow Report — summed once
    before the join, not double-counted per Orders line.
    """
    order_ratio_col = 'COALESCE(o."orderPaidRatio", 0)' if "orderPaidRatio" in available else "0"
    if not cashflow_source:
        return "", [], "0"
    join_sql = (
        'LEFT JOIN ('
        'SELECT "orderId" AS cf_order_id, SUM("phiAff") AS cf_phi_aff '
        'FROM read_parquet(?, union_by_name=true) GROUP BY "orderId"'
        ') cf ON o."orderId" = cf.cf_order_id'
    )
    aff_expr = f'({order_ratio_col} * COALESCE(cf.cf_phi_aff, 0))'
    return join_sql, [cashflow_source], aff_expr


def run_summary_query(
    parquet_source, from_date=None, to_date=None, category=None, status=None, cashflow_source=None,
) -> dict:
    if _is_empty_source(parquet_source):
        return EMPTY_SUMMARY

    con = _connect()
    try:
        where_sql, params = _where_clause(from_date, to_date, category, status)
        available = _available_columns(con, parquet_source)
        discount_col = 'COALESCE("discount", 0)' if "discount" in available else "0"
        voucher_col = 'COALESCE("voucher", 0)' if "voucher" in available else "0"
        platform_fee_col = 'COALESCE("platformFee", 0)' if "platformFee" in available else "0"
        piship_col = 'COALESCE("piship", 0)' if "piship" in available else "0"
        cf_join_sql, cf_join_params, aff_expr = _cashflow_join(available, cashflow_source)

        totals_sql = f"""
            SELECT
              COALESCE(SUM("doanhSo"), 0) AS total,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "originalPrice" * "soLuongThuc" ELSE 0 END), 0) AS gmv,
              COALESCE(SUM(CASE WHEN "trangThai" = 'Hủy chưa XK' THEN "doanhSo" ELSE 0 END), 0) AS huy_chua_xk,
              COALESCE(SUM(CASE WHEN "trangThai" = 'Hủy sau XK' THEN "doanhSo" ELSE 0 END), 0) AS huy_sau_xk,
              COALESCE(SUM("originalPrice" * "returnedQty"), 0) AS hoan,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN {discount_col} ELSE 0 END), 0) AS discount,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN {voucher_col} ELSE 0 END), 0) AS voucher,
              COALESCE(SUM({platform_fee_col}), 0) AS platform_fee,
              COALESCE(SUM({piship_col}), 0) AS piship,
              COALESCE(SUM({aff_expr}), 0) AS phi_aff,
              COUNT(*) AS row_count
            FROM read_parquet(?, union_by_name=true) o
            {cf_join_sql}
            WHERE {where_sql}
        """
        total, gmv, huy_chua_xk, huy_sau_xk, hoan, discount, voucher, platform_fee, piship, phi_aff, row_count = con.execute(
            totals_sql, [parquet_source, *cf_join_params, *params]
        ).fetchone()
        nmv = gmv - discount - voucher

        timeline_sql = f"""
            SELECT strftime("date", '%Y-%m') AS month, SUM("doanhSo") AS value
            FROM read_parquet(?, union_by_name=true) WHERE {where_sql}
            GROUP BY month ORDER BY month
        """
        timeline = con.execute(timeline_sql, [parquet_source, *params]).fetchall()

        def top_n(column: str, n: int = 8):
            sql = f"""
                SELECT "{column}" AS label, SUM("doanhSo") AS value
                FROM read_parquet(?, union_by_name=true) WHERE {where_sql}
                GROUP BY label ORDER BY value DESC LIMIT {n}
            """
            return con.execute(sql, [parquet_source, *params]).fetchall()

        top_products = top_n("product")
        category_breakdown = top_n("category")
        top_customers = top_n("customer")

        # Facets are computed over every row (no filters applied) so the
        # dropdown options don't shrink as the user filters.
        facets_sql = """
            SELECT
              list(DISTINCT "category") AS categories,
              list(DISTINCT "trangThai") AS statuses
            FROM read_parquet(?, union_by_name=true)
        """
        categories, statuses = con.execute(facets_sql, [parquet_source]).fetchone()

        return {
            "kpis": {
                "doanhSo": total,
                "gmv": gmv,
                "huyChuaXK": huy_chua_xk,
                "huySauXK": huy_sau_xk,
                "hoan": hoan,
                "discount": discount,
                "voucher": voucher,
                "platformFee": platform_fee,
                "piship": piship,
                "phiAff": phi_aff,
                "nmv": nmv,
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
    parquet_source,
    from_date=None, to_date=None, category=None, status=None,
    search=None, sort="date", sort_dir="asc", page=1, page_size=15,
    cashflow_source=None,
) -> dict:
    page = max(1, page)
    if _is_empty_source(parquet_source):
        return {"rows": [], "total": 0, "page": page, "pageSize": page_size}

    con = _connect()
    try:
        where_sql, params = _where_clause(from_date, to_date, category, status)
        available = _available_columns(con, parquet_source)
        cf_join_sql, cf_join_params, aff_expr = _cashflow_join(available, cashflow_source)

        if search:
            where_sql += (
                ' AND (lower("product") LIKE ? OR lower("category") LIKE ? OR '
                'lower("customer") LIKE ? OR lower("orderId") LIKE ? OR '
                'lower("sku") LIKE ? OR lower("skuVariant") LIKE ?)'
            )
            like = f"%{search.lower()}%"
            params.extend([like] * 6)

        total = con.execute(
            f'SELECT COUNT(*) FROM read_parquet(?, union_by_name=true) o WHERE {where_sql}',
            [parquet_source, *params],
        ).fetchone()[0]

        # Column/direction are whitelisted, not parameterized — DuckDB can't
        # bind identifiers, so this is the injection guard.
        sort_col = sort if sort in ALLOWED_SORT_COLUMNS else "date"
        sort_dir_sql = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
        offset = (page - 1) * page_size

        def col_expr(c: str) -> str:
            if c == "phiAff":
                return f'{aff_expr} AS "phiAff"'
            if c in OPTIONAL_NUMERIC_COLUMNS:
                return f'COALESCE("{c}", 0) AS "{c}"' if c in available else f'CAST(0 AS DOUBLE) AS "{c}"'
            return f'"{c}"'

        cols_sql = ", ".join(col_expr(c) for c in DETAIL_COLUMNS)
        rows_sql = f"""
            SELECT {cols_sql}
            FROM read_parquet(?, union_by_name=true) o
            {cf_join_sql}
            WHERE {where_sql}
            ORDER BY "{sort_col}" {sort_dir_sql}
            LIMIT ? OFFSET ?
        """
        # .fetchall() (not .fetchdf()) so values come back as plain Python
        # types (int/float/str/datetime) — a pandas DataFrame would give us
        # numpy/pandas types that FastAPI's JSON encoder can choke on.
        cursor = con.execute(rows_sql, [parquet_source, *cf_join_params, *params, page_size, offset])
        col_names = [d[0] for d in cursor.description]
        rows = [dict(zip(col_names, r)) for r in cursor.fetchall()]

        return {"rows": rows, "total": total, "page": page, "pageSize": page_size}
    finally:
        con.close()
