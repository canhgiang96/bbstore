"""Excel -> Parquet conversion for one Cashflow (Dòng tiền) Report.

Cashflow only ever needs two columns for the Dashboard's Phí AFF join: the
order id to match against Orders rows, and the AFF (affiliate marketing
commission) fee. This is intentionally a small, separate module rather than
reusing app.mapping's FIELDS/KEYWORDS/detect_mapping() — that machinery is
Orders-specific (it requires a "date" column, which Cashflow doesn't need at
all for this purpose).
"""
from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq

from .excel_to_parquet import read_excel_rows
from .mapping import normalize_header
from .parsing import to_number

CASHFLOW_KEYWORDS = {
    "orderId": ["ma don hang"],
    "phiAff": ["phi hoa hong tiep thi lien ket"],
}


class CashflowMappingError(ValueError):
    """Raised when the uploaded file is missing the order id or Phí AFF column."""


def detect_cashflow_mapping(headers: list[str]) -> dict[str, str]:
    normalized = [(h, normalize_header(h)) for h in headers]
    result: dict[str, str] = {}
    for field, keywords in CASHFLOW_KEYWORDS.items():
        for h, n in normalized:
            for w in keywords:
                if n == w or w in n:
                    result[field] = h
                    break
            if field in result:
                break
    return result


def cashflow_excel_to_parquet(file_like, sheet_name=0) -> tuple[bytes, int, dict]:
    """Returns (parquet_bytes, row_count, resolved_mapping).

    Each output row is {"orderId": ..., "phiAff": ...} — phiAff is the
    negated raw "Phí hoa hồng Tiếp thị liên kết" value (stored negative in
    the source file; the Dashboard wants it positive). One row per source
    row — the user confirmed each Mã đơn hàng appears exactly once per file.
    """
    raw_rows, headers = read_excel_rows(file_like, sheet_name=sheet_name)
    mapping = detect_cashflow_mapping(headers)

    if "orderId" not in mapping:
        raise CashflowMappingError("Không tìm thấy cột Mã đơn hàng trong file.")
    if "phiAff" not in mapping:
        raise CashflowMappingError("Không tìm thấy cột Phí hoa hồng Tiếp thị liên kết trong file.")

    order_col = mapping["orderId"]
    aff_col = mapping["phiAff"]

    rows = []
    for row in raw_rows:
        order_id = str(row.get(order_col, "") or "").strip()
        if not order_id:
            continue
        rows.append({"orderId": order_id, "phiAff": -to_number(row.get(aff_col))})

    if not rows:
        raise CashflowMappingError("Không có dòng dữ liệu hợp lệ nào (không đọc được Mã đơn hàng ở bất kỳ dòng nào).")

    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue(), len(rows), mapping
