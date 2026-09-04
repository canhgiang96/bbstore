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

import asyncio
import os

import duckdb
from starlette.concurrency import run_in_threadpool

from . import storage
from .config import get_settings

DETAIL_COLUMNS = [
    "date", "orderId", "sku", "skuVariant", "product", "category", "customer",
    "quantity", "returnedQty", "soLuongThuc", "price", "originalPrice",
    "revenue", "doanhSo", "status", "trangThai", "discount", "voucher",
    "platformFee", "piship", "phiAff", "phanLoaiKho", "phanLoaiMuc",
    "phanLoaiSp", "giaVon", "gmv", "doanhThuThuan", "nmv", "loiNhuanGop",
    "salesChannel", "kenhNho",
]

ALLOWED_SORT_COLUMNS = {
    "date", "orderId", "product", "category", "customer",
    "quantity", "doanhSo", "trangThai", "gmv", "doanhThuThuan", "nmv", "loiNhuanGop",
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
    "salesChannel": "salesChannel", "kenhNho": "kenhNho",
}

# Sortable aggregate columns for run_grouped_rows_query's result set — same
# whitelist-not-parameterize pattern as ALLOWED_SORT_COLUMNS.
GROUP_SORT_COLUMNS = {
    "groupValue": "group_value", "rowCount": "row_count", "quantity": "quantity",
    "returnedQty": "returned_qty", "soLuongThuc": "so_luong_thuc", "doanhSo": "doanh_so",
    "discount": "discount", "voucher": "voucher", "platformFee": "platform_fee",
    "piship": "piship", "phiAff": "phi_aff", "giaVon": "gia_von",
    "gmv": "gmv", "doanhThuThuan": "doanh_thu_thuan", "nmv": "nmv", "loiNhuanGop": "loi_nhuan_gop",
}

GMV_STATUSES_SQL = "('Hoàn thành', 'Đang giao', 'Hoàn 1 phần')"

EMPTY_SUMMARY = {
    "kpis": {
        "doanhSo": 0, "gmv": 0, "huyChuaXK": 0, "huySauXK": 0, "hoan": 0,
        "discount": 0, "voucher": 0, "platformFee": 0, "piship": 0, "phiAff": 0,
        "doanhThuThuan": 0, "nmv": 0, "giaVon": 0, "loiNhuanGop": 0, "rowCount": 0,
        "doanhSoOrders": 0, "huyChuaXKOrders": 0, "huySauXKOrders": 0, "hoanOrders": 0,
        "gmvOrders": 0, "doanhThuThuanOrders": 0, "nmvOrders": 0, "pishipOrders": 0, "phiAffOrders": 0,
    },
    "timeline": [],
    "topProducts": [],
    "categoryBreakdown": [],
    "topCustomers": [],
    "facets": {
        "categories": [], "statuses": [],
        "warehouseTypes": [], "itemGroups": [], "productTypes": [], "salesChannels": [],
        "kenhNho": [],
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


_download_locks: dict[str, asyncio.Lock] = {}


async def get_local_parquet_async(report_id: str, parquet_object_key: str) -> str:
    """Thread-pooled wrapper — get_local_parquet's R2 download (boto3, sync)
    would otherwise block the event loop for every OTHER concurrent request
    while a cold cache re-downloads a Report's Parquet. Callers that need
    several Reports' Parquets (see routers/dashboard.py's
    _all_ready_*_parquet_paths) should asyncio.gather() a list of these
    instead of awaiting them one at a time, so multiple downloads happen
    concurrently too.

    Also coalesces concurrent downloads of the SAME report across separate
    HTTP requests: the frontend fires /summary and /rows together on every
    filter change, and on a cold cache both would otherwise independently
    download the same Parquet from R2. A per-report_id lock makes the
    second caller wait for the first's download instead of duplicating it
    (the os.path.exists check after acquiring the lock is what turns that
    wait into a cache hit).
    """
    s = get_settings()
    local_path = os.path.join(s.parquet_cache_dir, f"{report_id}.parquet")
    if os.path.exists(local_path):
        return local_path
    lock = _download_locks.setdefault(report_id, asyncio.Lock())
    async with lock:
        if not os.path.exists(local_path):
            await run_in_threadpool(get_local_parquet, report_id, parquet_object_key)
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


def _in_clause(column: str, values, params: list):
    """Builds '"col" IN (?, ?, ...)' from a single value or a list of
    values, appending the placeholders' params in place. Returns None (no
    clause) when values is empty/falsy, after dropping any blank entries.
    """
    vals = values if isinstance(values, (list, tuple)) else ([values] if values else [])
    vals = [v for v in vals if v]
    if not vals:
        return None
    params.extend(vals)
    return f'"{column}" IN ({", ".join(["?"] * len(vals))})'


def _where_clause(
    from_date=None, to_date=None, category=None, status=None,
    warehouse_type=None, item_group=None, product_type=None, sku=None,
    sales_channel=None, kenh_nho=None,
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
    # Each of these accepts either a single value or a list — the Detail-table
    # filter bar's "Trạng thái"/"Phân loại kho"/"Phân loại mục"/"Phân loại
    # sản phẩm"/"Kênh bán hàng"/"Kênh nhỏ" pickers are multi-select, so a
    # filter can now mean "any of these values" (SQL IN), not just one exact
    # match.
    for column, values in [
        ("trangThai", status), ("phanLoaiKho", warehouse_type),
        ("phanLoaiMuc", item_group), ("phanLoaiSp", product_type),
        ("salesChannel", sales_channel), ("kenhNho", kenh_nho),
    ]:
        in_clause = _in_clause(column, values, params)
        if in_clause:
            clauses.append(in_clause)
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


def _cashflow_agg_join(con, available: set, cashflow_source) -> tuple[str, list, str, str]:
    """Returns (join_sql, join_params, aff_expr, platform_fee_expr) to LEFT
    JOIN per-order Phí AFF (and, for TikTok Cashflow Reports, Phí sàn) from
    ready Cashflow Reports into an Orders query whose FROM clause is
    aliased "o". Both expressions are always safe to use unconditionally —
    each is a literal "0" when there's no cashflow data yet, when this
    Orders Report predates the "orderPaidRatio" column (same backward-compat
    pattern as discount/voucher/platformFee/piship), or — for
    platform_fee_expr — when no uploaded Cashflow Report has a "platformFee"
    column yet (Shopee's own Cashflow Reports never do; that fee comes from
    the Orders file itself instead — see excel_to_parquet.py).

    The GROUP BY in the subquery guards against the same Mã đơn hàng
    appearing in more than one uploaded Cashflow Report — summed once
    before the join, not double-counted per Orders line.
    """
    order_ratio_col = 'COALESCE(o."orderPaidRatio", 0)' if "orderPaidRatio" in available else "0"
    if not cashflow_source:
        return "", [], "0", "0"
    cashflow_available = _available_columns(con, cashflow_source)
    has_platform_fee = "platformFee" in cashflow_available
    platform_fee_select = ', SUM("platformFee") AS cf_platform_fee' if has_platform_fee else ""
    join_sql = (
        'LEFT JOIN ('
        f'SELECT "orderId" AS cf_order_id, SUM("phiAff") AS cf_phi_aff{platform_fee_select} '
        'FROM read_parquet(?, union_by_name=true) GROUP BY "orderId"'
        ') cf ON o."orderId" = cf.cf_order_id'
    )
    aff_expr = f'({order_ratio_col} * COALESCE(cf.cf_phi_aff, 0))'
    platform_fee_expr = f'({order_ratio_col} * COALESCE(cf.cf_platform_fee, 0))' if has_platform_fee else "0"
    return join_sql, [cashflow_source], aff_expr, platform_fee_expr


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


def _aff_channel_join(
    aff_source, inhouse_handles: list, sku_id_col: str, creator_handle_col: str
) -> tuple[str, list, str, str]:
    """Returns (join_sql, join_params, aff_matched_expr, is_inhouse_expr) —
    the two building blocks _build_orders_working's "Kênh nhỏ" CASE
    expression needs, both added as LEFT JOINs against "o" (never fan out
    rows — the Kênh AFF Report is already DISTINCT-ed per (orderId, skuId)
    at conversion time, see aff_channel_to_parquet.py, and the inhouse-
    handles lookup is just a small list).

    sku_id_col/creator_handle_col are the caller's already-resolved (via
    `available`, same backward-compat pattern as discount_col etc.)
    references to o."skuId"/o."creatorHandle" — passed in rather than
    hardcoded here so a globally-absent column becomes a literal "NULL"
    that safely never matches, instead of a DuckDB "column not found" error.

    Both expressions are plain booleans with NO "?" placeholders of their
    own — every param this function needs lives in join_sql instead — so
    the caller can drop aff_matched_expr/is_inhouse_expr into the outer
    SELECT list without worrying about param-position ordering relative to
    the FROM clause's other joins.
    """
    join_sql = ""
    join_params: list = []
    aff_matched_expr = "FALSE"
    if aff_source:
        join_sql += (
            'LEFT JOIN (SELECT DISTINCT "orderId" AS aff_order_id, "skuId" AS aff_sku_id '
            'FROM read_parquet(?, union_by_name=true)) aff '
            f'ON o."orderId" = aff.aff_order_id AND {sku_id_col} = aff.aff_sku_id '
        )
        join_params.append(aff_source)
        aff_matched_expr = "aff.aff_order_id IS NOT NULL"
    is_inhouse_expr = "FALSE"
    if inhouse_handles:
        join_sql += (
            'LEFT JOIN (SELECT UNNEST(CAST(? AS VARCHAR[])) AS handle) ih '
            f'ON LOWER(TRIM({creator_handle_col})) = ih.handle '
        )
        join_params.append([h.lower() for h in inhouse_handles])
        is_inhouse_expr = "ih.handle IS NOT NULL"
    return join_sql, join_params, aff_matched_expr, is_inhouse_expr


def _channel_tagged_source_sql(parquet_source, channel_groups: dict | None) -> tuple[str, list]:
    """Returns (select_sql, params) for the base row source of
    orders_working, tagging every row with which Sales Channel its Orders
    Report was assigned to (or '' when unassigned/unknown).

    Each Orders Report's Parquet is just a path in parquet_source — there's
    no per-row marker for which Report (and therefore which channel) a row
    came from. Rather than recovering that from filenames (fragile), the
    caller groups Report paths by channel name in Python (channel_groups:
    {channel_name: [paths]}) and this unions one tagged read_parquet() call
    per channel — DuckDB's UNION ALL BY NAME (already relied on elsewhere in
    this file for cross-Report schema differences) keeps that safe even
    when different Reports' Parquets have slightly different optional
    columns.
    """
    if not channel_groups:
        return 'SELECT *, \'\' AS "salesChannel" FROM read_parquet(?, union_by_name=true)', [parquet_source]

    all_paths = list(parquet_source) if isinstance(parquet_source, (list, tuple)) else [parquet_source]
    covered: set = set()
    parts: list[str] = []
    params: list = []
    for channel_name, paths in channel_groups.items():
        paths_here = [p for p in paths if p in all_paths]
        if not paths_here:
            continue
        covered.update(paths_here)
        parts.append('SELECT *, ? AS "salesChannel" FROM read_parquet(?, union_by_name=true)')
        params.extend([channel_name, paths_here])
    unassigned = [p for p in all_paths if p not in covered]
    if unassigned:
        parts.append('SELECT *, \'\' AS "salesChannel" FROM read_parquet(?, union_by_name=true)')
        params.append(unassigned)
    if not parts:
        return 'SELECT *, \'\' AS "salesChannel" FROM read_parquet(?, union_by_name=true)', [parquet_source]
    return " UNION ALL BY NAME ".join(parts), params


def _base_date_filter_sql(from_date, to_date) -> tuple[str, list]:
    """Same from/to semantics as _where_clause's date clause, but meant to
    run against the raw per-Report source BEFORE the Combo/Cashflow/Master
    joins — pruning to the requested range there (instead of only in the
    outer WHERE, after joining/exploding every historical row) is what lets
    a date-scoped view (e.g. the Dashboard's "Tháng này" default) actually
    scan less data rather than just display less of it.
    """
    clauses = []
    params: list = []
    if from_date:
        clauses.append('"date" >= CAST(? AS DATE)')
        params.append(from_date)
    if to_date:
        clauses.append('"date" < (CAST(? AS DATE) + INTERVAL 1 DAY)')
        params.append(to_date)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


def _build_orders_working(
    con, parquet_source, available: set, combo_source, cashflow_source, master_source,
    aff_source=None, inhouse_handles=None,
    channel_source: dict | None = None, from_date=None, to_date=None,
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
    sku_id_col = 'o."skuId"' if "skuId" in available else "NULL"
    creator_handle_col = 'o."creatorHandle"' if "creatorHandle" in available else "NULL"
    content_channel_col = 'o."contentChannel"' if "contentChannel" in available else "NULL"
    # "hoanAmount" is persisted at conversion time for every Orders Report
    # going forward (originalPrice x returnedQty by default, or a real
    # per-line refund amount when the file gives one directly — see
    # excel_to_parquet.build_dashboard_rows/derive.derive_row_fields) — a
    # Report converted before this existed falls back to recomputing the
    # old formula here, same backward-compat pattern as discount/voucher.
    hoan_amount_col = '"hoanAmount"' if "hoanAmount" in available else '("originalPrice" * "returnedQty")'
    # "channelOverride" — a per-row Kênh bán hàng recovered from the
    # combined 31 LVS/HARA/WEBSITE/ZALO file's own "Kênh bán hàng" column
    # (see derive.normalize_combined_sales_channel); "" for every other
    # channel, and simply absent (pre-feature Reports) most of the time.
    channel_override_col = '"channelOverride"' if "channelOverride" in available else "NULL"

    combo_join_sql, combo_params, sku_variant_expr, ratio_expr, slot_expr = _combo_join(combo_source)
    cashflow_join_sql, cashflow_params, aff_expr, cashflow_platform_fee_expr = _cashflow_agg_join(
        con, available, cashflow_source
    )
    master_join_sql, master_params, muc_expr, phan_loai_sp_expr, phan_loai_kho_expr, gia_von_expr = _master_join(
        master_source, sku_variant_expr
    )
    aff_channel_join_sql, aff_channel_params, aff_matched_expr, is_inhouse_expr = _aff_channel_join(
        aff_source, inhouse_handles or [], sku_id_col, creator_handle_col
    )
    # "Kênh nhỏ" — TikTok-only (LIVE/VIDEO/PSA/AFF), stays NULL for every
    # other channel. Confirmed with the user 2026-08-27: a Kênh AFF match
    # always wins (AFF) regardless of eligibility status; a blank/"0"
    # Creator Handle is the main channel (PSA); an admin-managed "ID
    # Inhouse" handle maps by Order Channel; anything else is an outside
    # creator (AFF).
    kenh_nho_expr = f"""CASE
        WHEN LOWER(TRIM(o."salesChannel")) != 'tiktok' THEN NULL
        WHEN {aff_matched_expr} THEN 'AFF'
        WHEN {creator_handle_col} IS NULL OR TRIM({creator_handle_col}) IN ('', '0') THEN 'PSA'
        WHEN {is_inhouse_expr} THEN CASE {content_channel_col}
            WHEN 'Videos' THEN 'VIDEO' WHEN 'Product cards' THEN 'PSA' WHEN 'LIVE' THEN 'LIVE'
            ELSE NULL
        END
        ELSE 'AFF'
    END"""
    # Phí sàn can come from the Orders file itself (Shopee) and/or from a
    # Cashflow Report (TikTok) — see _cashflow_agg_join. Combined here so
    # every downstream use (the persisted "platformFee" column, nmv) stays
    # in sync automatically regardless of which channel(s) contributed.
    combined_platform_fee_col = f"({platform_fee_col} + {cashflow_platform_fee_expr})"
    channel_source_sql, channel_params = _channel_tagged_source_sql(parquet_source, channel_source)
    date_filter_sql, date_filter_params = _base_date_filter_sql(from_date, to_date)

    # Combo-exploded children report "combo" for the 3 category labels
    # instead of a Master File lookup (confirmed with the user) — Giá vốn
    # still looks up normally via the child's own sub-SKU.
    is_combo_child = f"({slot_expr} IS NOT NULL)"
    warehouse_expr = f"CASE WHEN {is_combo_child} THEN 'combo' ELSE {phan_loai_kho_expr} END"
    item_group_expr = f"CASE WHEN {is_combo_child} THEN 'combo' ELSE {muc_expr} END"
    product_type_expr = f"CASE WHEN {is_combo_child} THEN 'combo' ELSE {phan_loai_sp_expr} END"

    # Per-row GMV/Doanh thu thuần/NMV/Lợi nhuận gộp — mirror run_summary_query's
    # KPI formulas exactly (see totals_sql below) so that SUM()-ing these
    # columns over any filtered/grouped subset always reconciles with the
    # KPI cards for that same subset. Only GMV-status rows count towards
    # GMV/discount/voucher/Giá vốn in THESE composite formulas (same
    # CASE-WHEN scoping as the KPI totals) — scoped_discount_row_expr/
    # scoped_voucher_row_expr/scoped_gia_von_row_expr exist ONLY for that,
    # never assigned to the persisted "discount"/"voucher"/"giaVon"
    # columns themselves. Confirmed with the user 2026-08-29: this is a
    # deliberate, NOT a bug — Tổng quan intentionally shows the GMV-funnel
    # view (only orders that actually count towards GMV), while Dữ liệu
    # chi tiết intentionally shows the raw, unscoped source data (every
    # row's own discount/voucher/giá vốn regardless of trạng thái) — the
    # two tabs are meant to disagree whenever a cancelled/returned order
    # carries a nonzero discount. Phí sàn/Piship/Phí AFF are NOT
    # status-scoped either way (piship already excludes "Hủy chưa XK" via
    # its own CASE above), matching the KPIs.
    piship_row_expr = (
        f"CASE WHEN ({slot_expr} IS NULL OR {slot_expr} = 1) AND o.\"trangThai\" != 'Hủy chưa XK' "
        f"THEN {piship_col} ELSE 0 END"
    )
    phi_aff_row_expr = f"(({aff_expr}) * {ratio_expr})"
    gmv_row_expr = (
        f'CASE WHEN o."trangThai" IN {GMV_STATUSES_SQL} '
        f'THEN o."originalPrice" * {ratio_expr} * o."soLuongThuc" ELSE 0 END'
    )
    scoped_discount_row_expr = (
        f'CASE WHEN o."trangThai" IN {GMV_STATUSES_SQL} THEN {discount_col} * {ratio_expr} ELSE 0 END'
    )
    scoped_voucher_row_expr = (
        f'CASE WHEN o."trangThai" IN {GMV_STATUSES_SQL} THEN {voucher_col} * {ratio_expr} ELSE 0 END'
    )
    doanh_thu_thuan_row_expr = f"({gmv_row_expr} - {scoped_discount_row_expr} - {scoped_voucher_row_expr})"
    nmv_row_expr = (
        f"({doanh_thu_thuan_row_expr} - {combined_platform_fee_col} * {ratio_expr} "
        f"- {piship_row_expr} - {phi_aff_row_expr})"
    )
    scoped_gia_von_row_expr = (
        f'CASE WHEN o."trangThai" IN {GMV_STATUSES_SQL} THEN o."soLuongThuc" * {gia_von_expr} ELSE 0 END'
    )
    loi_nhuan_gop_row_expr = f"({nmv_row_expr} - {scoped_gia_von_row_expr})"

    create_sql = f"""
        CREATE OR REPLACE TEMP TABLE orders_working AS
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
          {combined_platform_fee_col} * {ratio_expr} AS "platformFee",
          CASE WHEN ({slot_expr} IS NULL OR {slot_expr} = 1) AND o."trangThai" != 'Hủy chưa XK' THEN {piship_col} ELSE 0 END AS "piship",
          ({aff_expr}) * {ratio_expr} AS "phiAff",
          {warehouse_expr} AS "phanLoaiKho",
          {item_group_expr} AS "phanLoaiMuc",
          {product_type_expr} AS "phanLoaiSp",
          o."soLuongThuc" * {gia_von_expr} AS "giaVon",
          {hoan_amount_col} * {ratio_expr} AS "hoanAmount",
          {gmv_row_expr} AS "gmv",
          {doanh_thu_thuan_row_expr} AS "doanhThuThuan",
          {nmv_row_expr} AS "nmv",
          {loi_nhuan_gop_row_expr} AS "loiNhuanGop",
          COALESCE(NULLIF({channel_override_col}, ''), o."salesChannel") AS "salesChannel",
          {kenh_nho_expr} AS "kenhNho"
        FROM (SELECT * FROM ({channel_source_sql}) t {date_filter_sql}) o
        {combo_join_sql}
        {cashflow_join_sql}
        {master_join_sql}
        {aff_channel_join_sql}
    """
    con.execute(
        create_sql,
        [
            *channel_params, *date_filter_params, *combo_params, *cashflow_params, *master_params,
            *aff_channel_params,
        ],
    )


def _prepare_orders_working(
    con, parquet_source, from_date, to_date, category, status, warehouse_type,
    item_group, product_type, sku, sales_channel,
    combo_source, cashflow_source, master_source, channel_source,
    kenh_nho=None, aff_source=None, inhouse_handles=None,
) -> tuple[str, list, set]:
    """Shared setup for every run_*_query function below: builds the WHERE
    clause for the requested filters and materializes orders_working on
    `con` for them to query. Factored out because all four functions need
    the identical two steps in the identical order. Also returns `available`
    (which columns exist across parquet_source) so callers that need to
    rebuild orders_working again on the same connection — see
    run_summary_query's unscoped facets rebuild — don't have to re-probe it.
    """
    where_sql, params = _where_clause(
        from_date, to_date, category, status, warehouse_type, item_group, product_type, sku,
        sales_channel, kenh_nho,
    )
    available = _available_columns(con, parquet_source)
    _build_orders_working(
        con, parquet_source, available, combo_source, cashflow_source, master_source,
        aff_source, inhouse_handles, channel_source,
        from_date=from_date, to_date=to_date,
    )
    return where_sql, params, available


def run_summary_query(
    parquet_source, from_date=None, to_date=None, category=None, status=None,
    cashflow_source=None, combo_source=None, master_source=None,
    warehouse_type=None, item_group=None, product_type=None, sku=None,
    channel_source=None, sales_channel=None,
    kenh_nho=None, aff_source=None, inhouse_handles=None,
) -> dict:
    if _is_empty_source(parquet_source):
        return EMPTY_SUMMARY

    con = _connect()
    try:
        where_sql, params, available = _prepare_orders_working(
            con, parquet_source, from_date, to_date, category, status, warehouse_type,
            item_group, product_type, sku, sales_channel,
            combo_source, cashflow_source, master_source, channel_source,
            kenh_nho, aff_source, inhouse_handles,
        )

        # *_orders columns are COUNT(DISTINCT "orderId") over exactly the
        # same row set each KPI's own SUM/CASE above draws from, so "how
        # many orders make up this number" always matches what's actually
        # summed — not just COUNT(*) (a row is one SKU line, not one
        # order). NMV nets out platformFee/piship/phiAff, which (like
        # their SUMs above) aren't status-scoped, so nmv_orders is the
        # union of GMV-status orders and any order with a non-zero fee.
        totals_sql = f"""
            SELECT
              COALESCE(SUM("doanhSo"), 0) AS total,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "originalPrice" * "soLuongThuc" ELSE 0 END), 0) AS gmv,
              COALESCE(SUM(CASE WHEN "trangThai" = 'Hủy chưa XK' THEN "doanhSo" ELSE 0 END), 0) AS huy_chua_xk,
              COALESCE(SUM(CASE WHEN "trangThai" = 'Hủy sau XK' THEN "doanhSo" ELSE 0 END), 0) AS huy_sau_xk,
              COALESCE(SUM("hoanAmount"), 0) AS hoan,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "discount" ELSE 0 END), 0) AS discount,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "voucher" ELSE 0 END), 0) AS voucher,
              COALESCE(SUM("platformFee"), 0) AS platform_fee,
              COALESCE(SUM("piship"), 0) AS piship,
              COALESCE(SUM("phiAff"), 0) AS phi_aff,
              COALESCE(SUM(CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "giaVon" ELSE 0 END), 0) AS gia_von,
              COUNT(*) AS row_count,
              COUNT(DISTINCT "orderId") AS doanh_so_orders,
              COUNT(DISTINCT CASE WHEN "trangThai" IN {GMV_STATUSES_SQL} THEN "orderId" END) AS gmv_orders,
              COUNT(DISTINCT CASE WHEN "trangThai" = 'Hủy chưa XK' THEN "orderId" END) AS huy_chua_xk_orders,
              COUNT(DISTINCT CASE WHEN "trangThai" = 'Hủy sau XK' THEN "orderId" END) AS huy_sau_xk_orders,
              COUNT(DISTINCT CASE WHEN "returnedQty" > 0 THEN "orderId" END) AS hoan_orders,
              COUNT(DISTINCT CASE WHEN "trangThai" IN {GMV_STATUSES_SQL}
                OR "platformFee" != 0 OR "piship" != 0 OR "phiAff" != 0 THEN "orderId" END) AS nmv_orders,
              COUNT(DISTINCT CASE WHEN "piship" != 0 THEN "orderId" END) AS piship_orders,
              COUNT(DISTINCT CASE WHEN "phiAff" != 0 THEN "orderId" END) AS phi_aff_orders
            FROM orders_working
            WHERE {where_sql}
        """
        (
            total, gmv, huy_chua_xk, huy_sau_xk, hoan, discount, voucher,
            platform_fee, piship, phi_aff, gia_von, row_count,
            doanh_so_orders, gmv_orders, huy_chua_xk_orders, huy_sau_xk_orders, hoan_orders,
            nmv_orders, piship_orders, phi_aff_orders,
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

        # Facets ignore every filter, INCLUDING the date range — user
        # confirmed 2026-09-03 (after "Tháng trước" showed every dropdown
        # as empty for a month with genuinely zero orders) that the filter
        # checklists should always list every value that has EVER existed
        # across all uploaded data, not just whatever falls inside the
        # currently-selected period. orders_working was already
        # materialized date-scoped above for the KPIs/timeline/top_n
        # queries; when a date range was actually given, rebuild it here
        # (CREATE OR REPLACE, same connection, reusing the `available` set
        # _prepare_orders_working already probed) with from_date/to_date
        # cleared just for this one query — every query above this point
        # already ran against the scoped version. Skipped when no date
        # range was requested in the first place, since orders_working is
        # already the full unscoped set in that case (rebuilding would be
        # byte-for-byte identical — pure waste).
        if from_date or to_date:
            _build_orders_working(
                con, parquet_source, available, combo_source, cashflow_source, master_source,
                aff_source, inhouse_handles, channel_source, from_date=None, to_date=None,
            )
        facets_sql = """
            SELECT
              list(DISTINCT "category") AS categories,
              list(DISTINCT "trangThai") AS statuses,
              list(DISTINCT "phanLoaiKho") AS warehouse_types,
              list(DISTINCT "phanLoaiMuc") AS item_groups,
              list(DISTINCT "phanLoaiSp") AS product_types,
              list(DISTINCT "salesChannel") AS sales_channels,
              list(DISTINCT "kenhNho") AS kenh_nho_values
            FROM orders_working
        """
        (
            categories, statuses, warehouse_types, item_groups, product_types, sales_channels, kenh_nho_values,
        ) = con.execute(facets_sql).fetchone()

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
                "doanhSoOrders": doanh_so_orders,
                "huyChuaXKOrders": huy_chua_xk_orders,
                "huySauXKOrders": huy_sau_xk_orders,
                "hoanOrders": hoan_orders,
                "gmvOrders": gmv_orders,
                "doanhThuThuanOrders": gmv_orders,  # same row scope as GMV
                "nmvOrders": nmv_orders,
                "pishipOrders": piship_orders,
                "phiAffOrders": phi_aff_orders,
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
                "salesChannels": sorted(s for s in (sales_channels or []) if s),
                "kenhNho": sorted(k for k in (kenh_nho_values or []) if k),
            },
        }
    finally:
        con.close()


def _apply_path_filters(where_sql: str, params: list, path_filters) -> str:
    """Appends one equality filter per (group_by_key, value) pair in
    path_filters — the ancestor chain for a node in the Detail-table's
    nested/hierarchical "Group theo" view (see run_grouped_rows_query and
    run_rows_query). Each key is checked against the same GROUP_BY_COLUMNS
    allow-list used to build the GROUP BY itself before being interpolated.
    """
    for group_by_key, value in (path_filters or []):
        if group_by_key in GROUP_BY_COLUMNS and value is not None:
            where_sql += f' AND "{GROUP_BY_COLUMNS[group_by_key]}" = ?'
            params.append(value)
    return where_sql


def _apply_search_filter(where_sql: str, params: list, search: str | None) -> str:
    """Appends a case-insensitive LIKE filter across product/category/
    customer/orderId/sku/skuVariant, matching the "Tìm kiếm" box in the
    Detail-table sub-tab (run_rows_query, run_grouped_rows_query,
    run_export_query all offer this same search).
    """
    if not search:
        return where_sql
    where_sql += (
        ' AND (lower("product") LIKE ? OR lower("category") LIKE ? OR '
        'lower("customer") LIKE ? OR lower("orderId") LIKE ? OR '
        'lower("sku") LIKE ? OR lower("skuVariant") LIKE ?)'
    )
    like = f"%{search.lower()}%"
    params.extend([like] * 6)
    return where_sql


def run_rows_query(
    parquet_source,
    from_date=None, to_date=None, category=None, status=None,
    search=None, sort="date", sort_dir="asc", page=1, page_size=15,
    cashflow_source=None, combo_source=None, master_source=None,
    warehouse_type=None, item_group=None, product_type=None, sku=None,
    path_filters=None, channel_source=None, sales_channel=None,
    kenh_nho=None, aff_source=None, inhouse_handles=None,
) -> dict:
    page = max(1, page)
    if _is_empty_source(parquet_source):
        return {"rows": [], "total": 0, "page": page, "pageSize": page_size}

    con = _connect()
    try:
        where_sql, params, _available = _prepare_orders_working(
            con, parquet_source, from_date, to_date, category, status, warehouse_type,
            item_group, product_type, sku, sales_channel,
            combo_source, cashflow_source, master_source, channel_source,
            kenh_nho, aff_source, inhouse_handles,
        )

        # Drill-down request from a (possibly nested) group node in the
        # Detail-table's grouped view — narrows to exactly that node's
        # underlying raw rows, one equality filter per ancestor level.
        where_sql = _apply_path_filters(where_sql, params, path_filters)
        where_sql = _apply_search_filter(where_sql, params, search)

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
          COALESCE(SUM("giaVon"), 0) AS gia_von,
          COALESCE(SUM("gmv"), 0) AS gmv,
          COALESCE(SUM("doanhThuThuan"), 0) AS doanh_thu_thuan,
          COALESCE(SUM("nmv"), 0) AS nmv,
          COALESCE(SUM("loiNhuanGop"), 0) AS loi_nhuan_gop
        FROM orders_working WHERE {where_sql}
        GROUP BY "{group_col}"
    """


def _grouped_row_dict(r: dict) -> dict:
    return {
        "groupValue": r["group_value"], "rowCount": r["row_count"],
        "quantity": r["quantity"], "returnedQty": r["returned_qty"],
        "soLuongThuc": r["so_luong_thuc"], "doanhSo": r["doanh_so"],
        "discount": r["discount"], "voucher": r["voucher"],
        "platformFee": r["platform_fee"], "piship": r["piship"],
        "phiAff": r["phi_aff"], "giaVon": r["gia_von"],
        "gmv": r["gmv"], "doanhThuThuan": r["doanh_thu_thuan"],
        "nmv": r["nmv"], "loiNhuanGop": r["loi_nhuan_gop"],
    }


def run_grouped_rows_query(
    parquet_source,
    from_date=None, to_date=None, category=None, status=None,
    search=None, group_by="sku", sort="doanhSo", sort_dir="desc", page=1, page_size=15,
    cashflow_source=None, combo_source=None, master_source=None,
    warehouse_type=None, item_group=None, product_type=None, sku=None,
    path_filters=None, channel_source=None, sales_channel=None,
    kenh_nho=None, aff_source=None, inhouse_handles=None,
) -> dict:
    """Server-side group-by-column aggregation over orders_working — the
    "Group theo" mode of the Detail-table sub-tab. Never loads the raw row
    set into Python; DuckDB does the GROUP BY over however many hundreds of
    thousands of rows match the filters, and only the (much smaller) group
    result page comes back.

    path_filters (list of (group_by_key, value) pairs) narrows to a specific
    ancestor combination for nested/hierarchical grouping — e.g. grouping by
    "warehouseType" within the "Áo" category node of a "category" ->
    "warehouseType" hierarchy passes path_filters=[("category", "Áo")].
    """
    page = max(1, page)
    if _is_empty_source(parquet_source) or group_by not in GROUP_BY_COLUMNS:
        return {"rows": [], "total": 0, "page": page, "pageSize": page_size}

    group_col = GROUP_BY_COLUMNS[group_by]

    con = _connect()
    try:
        where_sql, params, _available = _prepare_orders_working(
            con, parquet_source, from_date, to_date, category, status, warehouse_type,
            item_group, product_type, sku, sales_channel,
            combo_source, cashflow_source, master_source, channel_source,
            kenh_nho, aff_source, inhouse_handles,
        )
        where_sql = _apply_path_filters(where_sql, params, path_filters)
        where_sql = _apply_search_filter(where_sql, params, search)

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
        rows = [_grouped_row_dict(r) for r in raw_rows]

        return {"rows": rows, "total": total, "page": page, "pageSize": page_size}
    finally:
        con.close()


def run_export_query(
    parquet_source,
    from_date=None, to_date=None, category=None, status=None,
    search=None, group_by=None, sort=None, sort_dir="asc",
    cashflow_source=None, combo_source=None, master_source=None,
    warehouse_type=None, item_group=None, product_type=None, sku=None,
    channel_source=None, sales_channel=None,
    kenh_nho=None, aff_source=None, inhouse_handles=None,
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
        where_sql, params, _available = _prepare_orders_working(
            con, parquet_source, from_date, to_date, category, status, warehouse_type,
            item_group, product_type, sku, sales_channel,
            combo_source, cashflow_source, master_source, channel_source,
            kenh_nho, aff_source, inhouse_handles,
        )
        where_sql = _apply_search_filter(where_sql, params, search)

        if group_by and group_by in GROUP_BY_COLUMNS:
            group_col = GROUP_BY_COLUMNS[group_by]
            sort_col = GROUP_SORT_COLUMNS.get(sort, "doanh_so")
            sort_dir_sql = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
            sql = f"{_grouped_agg_sql(where_sql, group_col)} ORDER BY {sort_col} {sort_dir_sql}"
            cursor = con.execute(sql, params)
            col_names = [d[0] for d in cursor.description]
            raw_rows = [dict(zip(col_names, r)) for r in cursor.fetchall()]
            return [_grouped_row_dict(r) for r in raw_rows]

        sort_col = sort if sort in ALLOWED_SORT_COLUMNS else "date"
        sort_dir_sql = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
        cols_sql = ", ".join(f'"{c}"' for c in DETAIL_COLUMNS)
        sql = f'SELECT {cols_sql} FROM orders_working WHERE {where_sql} ORDER BY "{sort_col}" {sort_dir_sql}'
        cursor = con.execute(sql, params)
        col_names = [d[0] for d in cursor.description]
        return [dict(zip(col_names, r)) for r in cursor.fetchall()]
    finally:
        con.close()


def run_monthly_analysis_query(
    parquet_source, cashflow_source=None, combo_source=None, master_source=None,
    channel_source=None, aff_source=None, inhouse_handles=None,
) -> list[dict]:
    """Doanh thu thuần/NMV/Lợi nhuận gộp summed per calendar month across
    ALL of history — deliberately unfiltered (no date/status/warehouse/
    channel/etc. scoping), unlike every other run_*_query above. "Phân
    tích tháng" is a whole-business trend view (confirmed with the user
    2026-08-28), not a scoped one, so this reuses _build_orders_working
    over every ready Report exactly like the others, just skips
    _where_clause/_apply_path_filters/_apply_search_filter entirely.

    Doanh thu thuần (not GMV) is the table's leading revenue figure — the
    user's reference spreadsheet used GMV, but explicitly asked for it to
    be replaced with Doanh thu thuần here (2026-08-28).
    """
    if _is_empty_source(parquet_source):
        return []

    con = _connect()
    try:
        available = _available_columns(con, parquet_source)
        _build_orders_working(
            con, parquet_source, available, combo_source, cashflow_source, master_source,
            aff_source, inhouse_handles, channel_source,
        )
        sql = """
            SELECT
              strftime("date", '%Y-%m') AS month,
              COALESCE(SUM("doanhThuThuan"), 0) AS doanh_thu_thuan,
              COALESCE(SUM("nmv"), 0) AS nmv,
              COALESCE(SUM("loiNhuanGop"), 0) AS loi_nhuan_gop
            FROM orders_working
            GROUP BY month
            ORDER BY month
        """
        cursor = con.execute(sql)
        col_names = [d[0] for d in cursor.description]
        return [dict(zip(col_names, r)) for r in cursor.fetchall()]
    finally:
        con.close()
