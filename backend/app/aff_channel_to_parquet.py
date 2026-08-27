"""Excel -> Parquet conversion for one Kênh AFF Report (TikTok's
`affiliate_orders_*.xlsx` export).

Supplies the (orderId, skuId) pairs the Dashboard's query-time join uses to
classify a TikTok Orders line into "Kênh nhỏ" = AFF (see
query_engine._aff_channel_join and derive.KENH_NHO_* below) — any Orders
line whose (orderId, skuId) appears in ANY uploaded Kênh AFF Report is AFF,
unconditionally, regardless of this file's own "Trạng thái đơn hàng" (even
"Không đủ điều kiện" rows count — confirmed with the user 2026-08-27: this
is a channel-attribution classification, not a commission-payout figure).

skuId here is TikTok's own internal numeric SKU id ("ID SKU") — NOT the
same value space as the Orders file's skuVariant/Seller SKU field. This was
confirmed against real files: for the same order line, Seller SKU was
"V3609-2" while ID SKU was "1730315401307982614" — only the latter matches
this file's "ID SKU" column, so the Orders file needs its OWN new "SKU ID"
mapping field (see mapping.FIELDS) to join against.

This is intentionally a small, separate module rather than reusing
app.mapping's FIELDS/detect_mapping() — that machinery is Orders-specific
(it requires a "date" column, which this file doesn't have at all).
"""
from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq

from .excel_to_parquet import read_excel_rows
from .mapping import score_headers

AFF_CHANNEL_KEYWORDS = {
    "orderId": ["id don hang"],
    "skuId": ["id sku"],
}


class AffChannelMappingError(ValueError):
    """Raised when the uploaded file is missing the order id or SKU id column."""


def detect_aff_channel_mapping(headers: list[str]) -> dict[str, str]:
    return score_headers(headers, AFF_CHANNEL_KEYWORDS)


def aff_channel_excel_to_parquet(file_like, sheet_name=0) -> tuple[bytes, int, dict]:
    """Returns (parquet_bytes, row_count, resolved_mapping).

    Each output row is {"orderId": ..., "skuId": ...} — deduped (a single
    order+SKU can appear more than once per file, e.g. once per settlement
    adjustment) since the join only ever checks existence, never sums
    anything from this file.
    """
    raw_rows, headers = read_excel_rows(file_like, sheet_name=sheet_name)
    mapping = detect_aff_channel_mapping(headers)

    if "orderId" not in mapping:
        raise AffChannelMappingError("Không tìm thấy cột ID đơn hàng trong file.")
    if "skuId" not in mapping:
        raise AffChannelMappingError("Không tìm thấy cột ID SKU trong file.")

    order_col = mapping["orderId"]
    sku_col = mapping["skuId"]

    seen: set[tuple[str, str]] = set()
    for row in raw_rows:
        order_id = str(row.get(order_col, "") or "").strip()
        sku_id = str(row.get(sku_col, "") or "").strip()
        if not order_id or not sku_id:
            continue
        seen.add((order_id, sku_id))

    if not seen:
        raise AffChannelMappingError(
            "Không có dòng dữ liệu hợp lệ nào (không đọc được ID đơn hàng/ID SKU ở bất kỳ dòng nào)."
        )

    rows = [{"orderId": order_id, "skuId": sku_id} for order_id, sku_id in seen]
    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue(), len(rows), mapping
