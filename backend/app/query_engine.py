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
    "platformFee", "piship", "phiAff", "phanLoaiKho", "phanLoaiMuc",
    "phanLoaiSp", "giaVon",
]

ALLOWED_SORT_COLUMNS = {
    "date", "orderId", "product", "category", "customer",
    "quantity", "doanhSo", "trangThai",
}

# Allow-list mapping the Detail-table's "Group theo" dropdown keys to their
# actual orders_working column — also reused for the group-row drill-down
# filter (group_by/group_value on run_rows_query) and for the export
# endpoint. Never interpolate a client-supplied column name directly; always
# go through this dict first.
GROUP_BY_COLUMNS = {
    "sku": "sku", "product": "product", "category": "category",
    "customer": "customer", "status": "trangThai",
    "warehouseType": "phanLoaiKho", "itemGroup": "phanLoaiMuc",
    "productType": "phanLoaiSp", "orderId": "orderId",
}

# Sortable aggregate columns for run_grouped_rows_query's result set — same
# whitelist-not-parameterize pattern as ALLOWED_SORT_COLUMNS.
GROUP_SORT_COLUMNS = {
    "groupValue": "group_value", "rowCount": "row_count", "quantity": "quantity",
    "returnedQty": "returned_qty", "soLuongThuc": "so_luong_thuc", "doanhSo": "doanh_so",
    "discount": "discount", "voucher": "voucher", "platformFee": "platform_fee",
    "piship": "piship", "phiAff": "phi_aff", "giaVon": "gia_von",
}

GMV_STATUSES_SQL = "('Hoàn thành', 'Đang giao', 'Hoàn 1 phần')"

EMPTY_SUMMARY = {
    "kpis": {
        "doanhSo": 0, "gmv": 0, "huyChuaXK": 0, "huySauXK": 0, "hoan": 0,
        "discount": 0, "voucher": 0, "platformFee": 0, "piship": 0, "phiAff": 0,
        "doanhThuThuan": 0, "nmv": 0, "giaVon": 0, "loiNhuanGop": 0, "rowCount": 0,
    },
    "timeline": [],
    "topProducts": [],
    "categoryBreakdown": [],
    "topCustomers": [],
    "facets": {
        "categories": [], "statuses": [],
        "warehouseTypes": [], "itemGroups": [], "productTypes": [],
    },
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


def _where_clause(
    from_date=None, to_date=None, category=None, status=None,
    warehouse_type=None, item_group=None, product_type=None, sku=None,
):
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
    if warehouse_type:
        clauses.append('"phanLoaiKho" = ?')
        params.append(warehouse_type)
    if item_group:
        clauses.append('"phanLoaiMuc" = ?')
        params.append(item_group)
    if product_type:
        clauses.append('"phanLoaiSp" = ?')
        params.append(product_type)
    if sku:
        clauses.append('(lower("sku") LIKE ? OR lower("skuVariant") LIKE ?)')
        like = f"%{sku.lower()}%"
        params.extend([like, like])
    return (" AND ".join(clauses) if clauses else "1=1"), params


def _available_columns(con, parquet_source) -> set:
    """Reports converted before "discount"/"voucher"/"orderPaidRatio" existed
    don't have those columns in their Parquet schema. union_by_name=true lets
    DuckDB read a set of Reports with differing schemas together (missing
    columns come back NULL) — but only when there's at least one file that
    DOES have the column; a lone old-schema Report still needs the caller to
    fall back to a literal 0, hence checking availability up front instead
    of just always referencing the column name.
    """
    cur = con.execute("SELECT * FROM read_parquet(?, union_by_name=true) LIMIT 0", [parquet_source])
    return {d[0] for d in cur.description}


def _combo_join(combo_source) -> tuple[str, list, str, str, str]:
    """Returns (join_sql, join_params, sku_variant_expr, ratio_expr,
    slot_expr) to LEFT JOIN Combo sub-SKUs into an Orders query whose FROM
    clause is aliased "o". When combo_source is empty, the expressions
    reference only "o" (no "cm" join exists), so they're always safe to use
    unconditionally in the SELECT list.

    A matching row explodes 1:many (once per non-blank SKU1/SKU2/SKU3 the
    Combo file has for that SKU COMBO) — that's what makes this different
    from _cashflow_agg_join, which only ever adds a column.
    """
    if not combo_source:
        return "", [], 'o."skuVariant"', "1", "NULL"
    join_sql = 'LEFT JOIN read_parquet(?, union_by_name=true) cm ON o."skuVariant" = cm."skuCombo"'
    sku_variant_expr = 'COALESCE(cm."subSku", o."skuVariant")'
    return join_sql, [combo_source], sku_variant_expr, "COALESCE(cm.ratio, 1)", "cm.slot"


def _cashflow_agg_join(available: set, cashflow_source) -> tuple[str, list, str]:
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


def _master_join(master_source, sku_variant_expr: str) -> tuple[str, list, str, str, str, str]:
    """Returns (join_sql, join_params, muc_expr, phan_loai_sp_expr,
    phan_loai_kho_expr, gia_von_expr) to LEFT JOIN Master File cost/category
    data into an Orders query, aliased "mf" — joined on the parent SKU of
    the ALREADY-exploded skuVariant (post Combo explosion, if any), matching
    the confirmed "combo children still look up their own Giá vốn" rule.

    A parent SKU can appear on more than one Master File row (color/size
    variants each have their own "SKU phân loại" but share "SKU") — ANY_VALUE
    picks one consistently per field rather than fanning out rows, unlike
    the Combo join. When master_source is empty, the 4 expressions are
    literal defaults ('' / 0) that don't reference "mf" at all — same
    unconditionally-safe pattern as _combo_join/_cashflow_agg_join, since no
    "mf" alias exists in the query in that case.
    """
    if not master_source:
        return "", [], "''", "''", "''", "0"
    join_sql = (
        'LEFT JOIN ('
        'SELECT "sku" AS mf_sku, ANY_VALUE("muc") AS mf_muc, '
        'ANY_VALUE("phanLoaiSp") AS mf_phan_loai_sp, ANY_VALUE("phanLoaiKho") AS mf_phan_loai_kho, '
        'ANY_VALUE("giaVon") AS mf_gia_von '
        'FROM read_parquet(?, union_by_name=true) GROUP BY "sku"'
        f") mf ON split_part({sku_variant_expr}, '-', 1) = mf.mf_sku"
    )
    return (
        join_sql, [master_source],
        "COALESCE(mf.mf_muc, '')", "COALESCE(mf.mf_phan_loai_sp, '')",
        "COALESCE(mf.mf_phan_loai_kho, '')", "COALESCE(mf.mf_gia_von, 0)",
    )


def _build_orders_working(
    con, parquet_source, available: set, combo_source, cashflow_source, master_source,
) -> None:
    """Materializes a TEMP TABLE "orders_working" combining the Combo
    explosion and the Phí AFF join exactly once per call — every query below
    (totals, timeline, top_n, facets, count, the paginated rows select) then
    just reads FROM orders_working instead of each repeating both joins.

    Combo explosion changes the row SET itself (1 Orders row -> 0..3 rows),
    unlike Phí AFF which only adds a column — that's why this needs to
    happen once, up front, rather than per-query like _cashflow_agg_join
    used to be applied directly. All backward-compat COALESCE-or-0 handling
    for discount/voucher/platformFee/piship is resolved here too, so
    everything downstream can just reference the plain column name.
    """
    discount_col = 'COALESCE("discount", 0)' if "discount" in available else "0"
    voucher_col = 'COALESCE("voucher", 0)' if "voucher" in available else "0"
    platform_fee_col = 'COALESCE("platformFee", 0)' if "platformFee" in available else "0"
    piship_col = 'COALESCE("piship", 0)' if "piship" in available else "0"

    combo_join_sql, combo_params, sku_variant_expr, ratio_expr, slot_expr = _combo_join(combo_source)
    cashflow_join_sql, cashflow_params, aff_expr = _cashflow_agg_join(available, cashflow_source)
    master_join_sql, master_params, muc_expr, phan_loai_sp_expr, phan_loai_kho_expr, gia_von_expr = _master_join(
        master_source, sku_variant_expr
    )

    # Combo-exploded children report "combo" for the 3 category labels
    # instead of a Master File lookup (confirmed with the user) — Giá vốn
    # still looks up normally via the child's own sub-SKU.
    is_combo_child = f"({slot_expr} IS NOT NULL)"
    warehouse_expr = f"CASE WHEN {is_combo_child} THEN 'combo' ELSE {phan_loai_kho_expr} END"
    item_group_expr = f"CASE WHEN {is_combo_child} THEN 'combo' ELSE {muc_expr} END"
    product_type_expr = f"CASE WHEN {is_combo_child} THEN 'combo' ELSE {phan_loai_sp_expr} END"

    create_sql = f"""
        CREATE TEMP TABLE orders_working AS
        SELECT
          o."date" AS "date",
          o."orderId" AS "orderId",
          {sku_variant_expr} AS "skuVariant",
          split_part({sku_variant_expr}, '-', 1) AS "sku",
          o."product" AS "product",
          o."category" AS "category",
          o."customer" AS "customer",
          o."quantity" AS "quantity",
          o."returnedQty" AS "returnedQty",
          o."soLuongThuc" AS "soLuongThuc",
          o."price" * {ratio_expr} AS "price",
          o."originalPrice" * {ratio_expr} AS "originalPrice",
          o."revenue" * {ratio_expr} AS "revenue",
          o."doanhSo" * {ratio_expr} AS "doanhSo",
          o."status" AS "status",
          o."trangThai" AS "trangThai",
          {discount_col} * {ratio_expr} AS "discount",
          {voucher_col} * {ratio_expr} AS "voucher",
          {platform_fee_col} * {ratio_expr} AS "platformFee",
          CASE WHEN {slot_expr} IS NULL OR {slot_expr} = 1 THEN {piship_col} ELSE 0 END AS "piship",
          ({aff_expr}) * {ratio_expr} AS "phiAff",
          {warehouse_expr} AS "phanLoaiKho",
          {item_group_expr} AS "phanLoaiMuc",
          {product_type_expr} AS "phanLoaiSp",
          o."soLuongThuc" * {gia_von_expr} AS "giaVon"
        FROM read_parquet(?, union_by_name=true) o
        {combo_join_sql}
        {cashflow_join_sql}
        {master_join_sql}
    """
    con.execute(create_sql, [*[parquet_source], *combo_params, *cashflow_params, *master_params])


def run_summary_query(
    parquet_source, from_date=None, to_date=None, category=None, status=None,
    cashflow_source=None, combo_source=None, master_source=None,
    warehouse_type=None, item_group=None, product_type=None, sku=None,
) -> dict:
    if _is_empty_source(parquet_source):
        return EMPTY_SUMMARY

    con = _connect()
    try:
        where_sql, params = _where_clause(
            from_date, to_date, category, status, warehouse_type, item_group, product_type, sku,
        )
        available = _available_columns(con, parquet_source)
        _build_orders_working(con, parquet_source, available, combo_source, cashflow_source, master_source)

        totals_sql = f"""
            SELECT
              COALESCE(SUM("doanhSo"), 0) AS total,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "originalPrice" * "soLuongThuc" ELSE 0 END), 0) AS gmv,
              COALESCE(SUM(CASE WHEN "trangThai" = 'Hủy chưa XK' THEN "doanhSo" ELSE 0 END), 0) AS huy_chua_xk,
              COALESCE(SUM(CASE WHEN "trangThai" = 'Hủy sau XK' THEN "doanhSo" ELSE 0 END), 0) AS huy_sau_xk,
              COALESCE(SUM("originalPrice" * "returnedQty"), 0) AS hoan,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "discount" ELSE 0 END), 0) AS discount,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "voucher" ELSE 0 END), 0) AS voucher,
              COALESCE(SUM("platformFee"), 0) AS platform_fee,
              COALESCE(SUM("piship"), 0) AS piship,
              COALESCE(SUM("phiAff"), 0) AS phi_aff,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "giaVon" ELSE 0 END), 0) AS gia_von,
              COUNT(*) AS row_count
            FROM orders_working
            WHERE {where_sql}
        """
        (
            total, gmv, huy_chua_xk, huy_sau_xk, hoan, discount, voucher,
            platform_fee, piship, phi_aff, gia_von, row_count,
        ) = con.execute(totals_sql, params).fetchone()
        doanh_thu_thuan = gmv - discount - voucher
        nmv = doanh_thu_thuan - platform_fee - piship - phi_aff
        loi_nhuan_gop = nmv - gia_von

        timeline_sql = f"""
            SELECT strftime("date", '%Y-%m') AS month, SUM("doanhSo") AS value
            FROM orders_working WHERE {where_sql}
            GROUP BY month ORDER BY month
        """
        timeline = con.execute(timeline_sql, params).fetchall()

        def top_n(column: str, n: int = 8):
            sql = f"""
                SELECT "{column}" AS label, SUM("doanhSo") AS value
                FROM orders_working WHERE {where_sql}
                GROUP BY label ORDER BY value DESC LIMIT {n}
            """
            return con.execute(sql, params).fetchall()

        top_products = top_n("product")
        category_breakdown = top_n("category")
        top_customers = top_n("customer")

        # Facets are computed over every row (no filters applied) so the
        # dropdown options don't shrink as the user filters.
        facets_sql = """
            SELECT
              list(DISTINCT "category") AS categories,
              list(DISTINCT "trangThai") AS statuses,
              list(DISTINCT "phanLoaiKho") AS warehouse_types,
              list(DISTINCT "phanLoaiMuc") AS item_groups,
              list(DISTINCT "phanLoaiSp") AS product_types
            FROM orders_working
        """
        categories, statuses, warehouse_types, item_groups, product_types = con.execute(facets_sql).fetchone()

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
                "doanhThuThuan": doanh_thu_thuan,
                "nmv": nmv,
                "giaVon": gia_von,
                "loiNhuanGop": loi_nhuan_gop,
                "rowCount": row_count,
            },
            "timeline": [{"month": m, "value": v} for m, v in timeline],
            "topProducts": [{"label": l, "value": v} for l, v in top_products],
            "categoryBreakdown": [{"label": l, "value": v} for l, v in category_breakdown],
            "topCustomers": [{"label": l, "value": v} for l, v in top_customers],
            "facets": {
                "categories": sorted(c for c in (categories or []) if c is not None),
                "statuses": [s for s in (statuses or []) if s is not None],
                "warehouseTypes": sorted(w for w in (warehouse_types or []) if w),
                "itemGroups": sorted(g for g in (item_groups or []) if g),
                "productTypes": sorted(p for p in (product_types or []) if p),
            },
        }
    finally:
        con.close()


def run_rows_query(
    parquet_source,
    from_date=None, to_date=None, category=None, status=None,
    search=None, sort="date", sort_dir="asc", page=1, page_size=15,
    cashflow_source=None, combo_source=None, master_source=None,
    warehouse_type=None, item_group=None, product_type=None, sku=None,
    group_by=None, group_value=None,
) -> dict:
    page = max(1, page)
    if _is_empty_source(parquet_source):
        return {"rows": [], "total": 0, "page": page, "pageSize": page_size}

    con = _connect()
    try:
        where_sql, params = _where_clause(
            from_date, to_date, category, status, warehouse_type, item_group, product_type, sku,
        )
        available = _available_columns(con, parquet_source)
        _build_orders_working(con, parquet_source, available, combo_source, cashflow_source, master_source)

        # Drill-down request from a group row in the Detail-table's grouped
        # view (see run_grouped_rows_query) — narrows to exactly that
        # group's underlying rows. group_by is checked against the same
        # allow-list used to build the GROUP BY itself.
        if group_by and group_by in GROUP_BY_COLUMNS and group_value is not None:
            where_sql += f' AND "{GROUP_BY_COLUMNS[group_by]}" = ?'
            params.append(group_value)

        if search:
            where_sql += (
                ' AND (lower("product") LIKE ? OR lower("category") LIKE ? OR '
                'lower("customer") LIKE ? OR lower("orderId") LIKE ? OR '
                'lower("sku") LIKE ? OR lower("skuVariant") LIKE ?)'
            )
            like = f"%{search.lower()}%"
            params.extend([like] * 6)

        total = con.execute(
            f'SELECT COUNT(*) FROM orders_working WHERE {where_sql}', params
        ).fetchone()[0]

        # Column/direction are whitelisted, not parameterized — DuckDB can't
        # bind identifiers, so this is the injection guard.
        sort_col = sort if sort in ALLOWED_SORT_COLUMNS else "date"
        sort_dir_sql = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
        offset = (page - 1) * page_size

        cols_sql = ", ".join(f'"{c}"' for c in DETAIL_COLUMNS)
        rows_sql = f"""
            SELECT {cols_sql}
            FROM orders_working WHERE {where_sql}
            ORDER BY "{sort_col}" {sort_dir_sql}
            LIMIT ? OFFSET ?
        """
        # .fetchall() (not .fetchdf()) so values come back as plain Python
        # types (int/float/str/datetime) — a pandas DataFrame would give us
        # numpy/pandas types that FastAPI's JSON encoder can choke on.
        cursor = con.execute(rows_sql, [*params, page_size, offset])
        col_names = [d[0] for d in cursor.description]
        rows = [dict(zip(col_names, r)) for r in cursor.fetchall()]

        return {"rows": rows, "total": total, "page": page, "pageSize": page_size}
    finally:
        con.close()


def _grouped_agg_sql(where_sql: str, group_col: str) -> str:
    return f"""
        SELECT
          "{group_col}" AS group_value,
          COUNT(*) AS row_count,
          COALESCE(SUM("quantity"), 0) AS quantity,
          COALESCE(SUM("returnedQty"), 0) AS returned_qty,
          COALESCE(SUM("soLuongThuc"), 0) AS so_luong_thuc,
          COALESCE(SUM("doanhSo"), 0) AS doanh_so,
          COALESCE(SUM("discount"), 0) AS discount,
          COALESCE(SUM("voucher"), 0) AS voucher,
          COALESCE(SUM("platformFee"), 0) AS platform_fee,
          COALESCE(SUM("piship"), 0) AS piship,
          COALESCE(SUM("phiAff"), 0) AS phi_aff,
          COALESCE(SUM("giaVon"), 0) AS gia_von
        FROM orders_working WHERE {where_sql}
        GROUP BY "{group_col}"
    """


def run_grouped_rows_query(
    parquet_source,
    from_date=None, to_date=None, category=None, status=None,
    search=None, group_by="sku", sort="doanhSo", sort_dir="desc", page=1, page_size=15,
    cashflow_source=None, combo_source=None, master_source=None,
    warehouse_type=None, item_group=None, product_type=None, sku=None,
) -> dict:
    """Server-side group-by-column aggregation over orders_working — the
    "Group theo" mode of the Detail-table sub-tab. Never loads the raw row
    set into Python; DuckDB does the GROUP BY over however many hundreds of
    thousands of rows match the filters, and only the (much smaller) group
    result page comes back.
    """
    page = max(1, page)
    if _is_empty_source(parquet_source) or group_by not in GROUP_BY_COLUMNS:
        return {"rows": [], "total": 0, "page": page, "pageSize": page_size}

    group_col = GROUP_BY_COLUMNS[group_by]

    con = _connect()
    try:
        where_sql, params = _where_clause(
            from_date, to_date, category, status, warehouse_type, item_group, product_type, sku,
        )
        available = _available_columns(con, parquet_source)
        _build_orders_working(con, parquet_source, available, combo_source, cashflow_source, master_source)

        if search:
            where_sql += (
                ' AND (lower("product") LIKE ? OR lower("category") LIKE ? OR '
                'lower("customer") LIKE ? OR lower("orderId") LIKE ? OR '
                'lower("sku") LIKE ? OR lower("skuVariant") LIKE ?)'
            )
            like = f"%{search.lower()}%"
            params.extend([like] * 6)

        total = con.execute(
            f'SELECT COUNT(DISTINCT "{group_col}") FROM orders_working WHERE {where_sql}', params
        ).fetchone()[0]

        sort_col = GROUP_SORT_COLUMNS.get(sort, "doanh_so")
        sort_dir_sql = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
        offset = (page - 1) * page_size

        rows_sql = f"""
            {_grouped_agg_sql(where_sql, group_col)}
            ORDER BY {sort_col} {sort_dir_sql}
            LIMIT ? OFFSET ?
        """
        cursor = con.execute(rows_sql, [*params, page_size, offset])
        col_names = [d[0] for d in cursor.description]
        raw_rows = [dict(zip(col_names, r)) for r in cursor.fetchall()]
        rows = [
            {
                "groupValue": r["group_value"], "rowCount": r["row_count"],
                "quantity": r["quantity"], "returnedQty": r["returned_qty"],
                "soLuongThuc": r["so_luong_thuc"], "doanhSo": r["doanh_so"],
                "discount": r["discount"], "voucher": r["voucher"],
                "platformFee": r["platform_fee"], "piship": r["piship"],
                "phiAff": r["phi_aff"], "giaVon": r["gia_von"],
            }
            for r in raw_rows
        ]

        return {"rows": rows, "total": total, "page": page, "pageSize": page_size}
    finally:
        con.close()


def run_export_query(
    parquet_source,
    from_date=None, to_date=None, category=None, status=None,
    search=None, group_by=None, sort=None, sort_dir="asc",
    cashflow_source=None, combo_source=None, master_source=None,
    warehouse_type=None, item_group=None, product_type=None, sku=None,
) -> list[dict]:
    """Pulls the ENTIRE result set matching the current filters (no LIMIT/
    OFFSET) for the Excel export — grouped aggregate rows when group_by is
    set, otherwise every detail row. A single columnar DuckDB scan, not a
    per-page loop, so this stays fast even at hundreds of thousands of rows.
    """
    if _is_empty_source(parquet_source):
        return []

    con = _connect()
    try:
        where_sql, params = _where_clause(
            from_date, to_date, category, status, warehouse_type, item_group, product_type, sku,
        )
        available = _available_columns(con, parquet_source)
        _build_orders_working(con, parquet_source, available, combo_source, cashflow_source, master_source)

        if search:
            where_sql += (
                ' AND (lower("product") LIKE ? OR lower("category") LIKE ? OR '
                'lower("customer") LIKE ? OR lower("orderId") LIKE ? OR '
                'lower("sku") LIKE ? OR lower("skuVariant") LIKE ?)'
            )
            like = f"%{search.lower()}%"
            params.extend([like] * 6)

        if group_by and group_by in GROUP_BY_COLUMNS:
            group_col = GROUP_BY_COLUMNS[group_by]
            sort_col = GROUP_SORT_COLUMNS.get(sort, "doanh_so")
            sort_dir_sql = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
            sql = f"{_grouped_agg_sql(where_sql, group_col)} ORDER BY {sort_col} {sort_dir_sql}"
            cursor = con.execute(sql, params)
            col_names = [d[0] for d in cursor.description]
            raw_rows = [dict(zip(col_names, r)) for r in cursor.fetchall()]
            return [
                {
                    "groupValue": r["group_value"], "rowCount": r["row_count"],
                    "quantity": r["quantity"], "returnedQty": r["returned_qty"],
                    "soLuongThuc": r["so_luong_thuc"], "doanhSo": r["doanh_so"],
                    "discount": r["discount"], "voucher": r["voucher"],
                    "platformFee": r["platform_fee"], "piship": r["piship"],
                    "phiAff": r["phi_aff"], "giaVon": r["gia_von"],
                }
                for r in raw_rows
            ]

        sort_col = sort if sort in ALLOWED_SORT_COLUMNS else "date"
        sort_dir_sql = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
        cols_sql = ", ".join(f'"{c}"' for c in DETAIL_COLUMNS)
        sql = f'SELECT {cols_sql} FROM orders_working WHERE {where_sql} ORDER BY "{sort_col}" {sort_dir_sql}'
        cursor = con.execute(sql, params)
        col_names = [d[0] for d in cursor.description]
        return [dict(zip(col_names, r)) for r in cursor.fetchall()]
    finally:
        con.close()
