"""Excel -> Parquet conversion for one Điều chỉnh doanh thu (revenue
adjustment) Report.

Same shape as Master File: one output row per source row, no computation —
this data was never wired into the Dashboard's query engine (it's a
standalone record-keeping viewer, not an aggregation input) so every field
is just preserved as-is. Two of the 7 headers share the "Ngày hoàn thành..."
prefix, so this needs the same real exact-match-priority scoring as
app.master_to_parquet's detect_master_mapping — a simpler "first
substring-or-exact match wins" detector would be fragile here too.
"""
from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq

from .excel_to_parquet import read_excel_rows
from .mapping import score_headers
from .parsing import to_number

ADJUSTMENT_KEYWORDS = {
    "transactionId": ["ma giao dich"],
    "adjustmentDate": ["dieu chinh don hang"],
    "adjustmentType": ["loai dieu chinh"],
    "reason": ["ly do dieu chinh"],
    "amount": ["so tien dieu chinh"],
    "relatedOrderId": ["ma don hang lien quan"],
    "paymentCompletedDate": ["hoan thanh thanh toan"],
}


class AdjustmentMappingError(ValueError):
    """Raised when the uploaded file has neither "Mã giao dịch" nor "Mã đơn hàng liên quan"."""


def detect_adjustment_mapping(headers: list[str]) -> dict[str, str]:
    return score_headers(headers, ADJUSTMENT_KEYWORDS)


def adjustment_excel_to_parquet(file_like, sheet_name=0) -> tuple[bytes, int, dict]:
    """Returns (parquet_bytes, row_count, resolved_mapping).

    Every field except "amount" is preserved as plain text (dates included —
    this data isn't filtered/joined anywhere, so there's no need to parse
    them into real date values).

    "Mã giao dịch" isn't always present — a real TikTok-style export
    (confirmed with the user 2026-09-03, file "ĐIỀU CHỈNH.xlsx") has no
    such column at all, only "Mã đơn hàng liên quan". A row is kept as long
    as EITHER identifying column has a value (whichever the file actually
    has); the same "Mã đơn hàng liên quan" legitimately repeats across rows
    for separate adjustment events on the same order (verified against the
    real file — different dates/reasons/amounts), so it's never used to
    dedupe, only to decide "is this row real".
    """
    raw_rows, headers = read_excel_rows(file_like, sheet_name=sheet_name)
    mapping = detect_adjustment_mapping(headers)

    if "transactionId" not in mapping and "relatedOrderId" not in mapping:
        raise AdjustmentMappingError("Không tìm thấy cột Mã giao dịch hoặc Mã đơn hàng liên quan trong file.")

    txn_col = mapping.get("transactionId")
    date_col = mapping.get("adjustmentDate")
    type_col = mapping.get("adjustmentType")
    reason_col = mapping.get("reason")
    amount_col = mapping.get("amount")
    order_col = mapping.get("relatedOrderId")
    paid_col = mapping.get("paymentCompletedDate")

    def text(row, col):
        return str(row.get(col, "") or "").strip() if col else ""

    rows = []
    for row in raw_rows:
        txn_id = text(row, txn_col)
        related_order_id = text(row, order_col)
        if not txn_id and not related_order_id:
            continue
        rows.append({
            "transactionId": txn_id,
            "adjustmentDate": text(row, date_col),
            "adjustmentType": text(row, type_col),
            "reason": text(row, reason_col),
            "amount": to_number(row.get(amount_col)) if amount_col else 0.0,
            "relatedOrderId": related_order_id,
            "paymentCompletedDate": text(row, paid_col),
        })

    if not rows:
        raise AdjustmentMappingError(
            "Không có dòng dữ liệu hợp lệ nào (không đọc được Mã giao dịch/Mã đơn hàng liên quan ở bất kỳ dòng nào)."
        )

    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue(), len(rows), mapping
