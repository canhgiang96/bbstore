"""Excel -> Parquet conversion for one Cashflow (Dòng tiền) Report.

Supplies per-order Phí AFF (both channels) and Phí sàn (TikTok only —
Shopee's Phí sàn is computed from the Orders file itself, see
excel_to_parquet.py) for the Dashboard's query-time join. Shopee's file has
one already-combined "Phí hoa hồng Tiếp thị liên kết" column; TikTok's
"income" export instead has separate affiliate-commission and
platform-fee-decomposition columns that need to be summed/subtracted (see
CASHFLOW_KEYWORDS and the formulas below — confirmed with the user against
a real TikTok income export, 2026-08-26/27):
    Phí AFF      = Hoa hồng liên kết + Hoa hồng liên kết Quảng cáo cửa hàng
    Phí sàn      = Tổng phí - Hoa hồng liên kết - Hoa hồng liên kết Quảng
                   cáo cửa hàng - Thuế GTGT do TikTok Shop khấu trừ -
                   Thuế TNCN do TikTok Shop khấu trừ
Both channels store these as negative amounts; the Dashboard wants them
positive, same as Shopee's phiAff always has.

For a fully-refunded TikTok order (its rows' "Tổng doanh thu" nets to 0),
the affiliate-commission columns are treated as 0 in both formulas above —
see _REVENUE_EPSILON's comment for why.

This is intentionally a small, separate module rather than reusing
app.mapping's FIELDS/detect_mapping() — that machinery is Orders-specific
(it requires a "date" column, which Cashflow doesn't need at all here).
"""
from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq

from .excel_to_parquet import read_excel_rows
from .mapping import score_headers
from .parsing import to_number

CASHFLOW_KEYWORDS = {
    "orderId": ["ma don hang", "id don hang dieu chinh"],
    "transactionType": ["loai giao dich"],
    "phiAff": ["phi hoa hong tiep thi lien ket"],
    "affiliateCommission": ["hoa hong lien ket"],
    "affiliateAdsCommission": ["hoa hong lien ket quang cao cua hang"],
    "totalFee": ["tong phi"],
    "vatWithheld": ["thue gtgt do tiktok shop khau tru"],
    "pitWithheld": ["thue tncn do tiktok shop khau tru"],
    "totalRevenue": ["tong doanh thu"],
}

# On a full return, TikTok's own settlement rows for an order net "Tổng
# doanh thu" to 0 (a positive charge row followed by its negative reversal)
# — but the affiliate-commission columns don't always get a matching
# reversal row, so summing them as-is leaves a nonzero Phí AFF for an order
# that earned nothing. Confirmed with the user (2026-08-27, real order
# 582572544565151151: charge row had Hoa hồng liên kết -26294, its return
# row had 0, net doanh thu 0): when an order's rows net to 0 revenue, its
# affiliate-commission columns are treated as 0 too — the money doesn't
# disappear, it just stops being categorized as Phí AFF and stays inside
# Phí sàn (Tổng phí) instead, since total fee is unaffected by this rule.
_REVENUE_EPSILON = 0.5

# TikTok's "income" export marks a handful of rows (platform compensation,
# not a real order) with a different value here — confirmed with the user
# to exclude those from Phí sàn/Phí AFF, matching only real order rows.
_CASHFLOW_ORDER_TRANSACTION_TYPE = "Đơn hàng"


class CashflowMappingError(ValueError):
    """Raised when the uploaded file is missing the order id or every Phí AFF/Phí sàn source column."""


def detect_cashflow_mapping(headers: list[str]) -> dict[str, str]:
    # score_headers (not the simpler first_match_mapping used by
    # Combo/Master File) because "Hoa hồng liên kết" is a substring of
    # several other TikTok columns ("...Quảng cáo cửa hàng",
    # "...trước thuế TNCN") — needs real exact-match-priority scoring to
    # disambiguate, the same class of collision Master File's "SKU" vs
    # "SKU phân loại" already solves.
    return score_headers(headers, CASHFLOW_KEYWORDS)


def cashflow_excel_to_parquet(file_like, sheet_name=0) -> tuple[bytes, int, dict]:
    """Returns (parquet_bytes, row_count, resolved_mapping).

    Each output row is {"orderId": ..., "phiAff": ..., "platformFee": ...}
    — "platformFee" is 0 for a Shopee-style file (its Phí sàn already comes
    from the Orders file) and only nonzero when the TikTok-style component
    columns were detected instead of Shopee's single combined column. One
    row per source row — the user confirmed each Mã đơn hàng appears
    exactly once per file.
    """
    raw_rows, headers = read_excel_rows(file_like, sheet_name=sheet_name)
    mapping = detect_cashflow_mapping(headers)

    if "orderId" not in mapping:
        raise CashflowMappingError("Không tìm thấy cột Mã đơn hàng trong file.")
    has_direct_aff = "phiAff" in mapping
    has_tiktok_components = "affiliateCommission" in mapping or "affiliateAdsCommission" in mapping
    if not has_direct_aff and not has_tiktok_components:
        raise CashflowMappingError("Không tìm thấy cột Phí hoa hồng Tiếp thị liên kết trong file.")

    order_col = mapping["orderId"]
    type_col = mapping.get("transactionType")
    revenue_col = mapping.get("totalRevenue")

    included_rows = []
    order_revenue_totals: dict[str, float] = {}
    for row in raw_rows:
        if type_col and str(row.get(type_col, "") or "").strip() not in ("", _CASHFLOW_ORDER_TRANSACTION_TYPE):
            continue
        order_id = str(row.get(order_col, "") or "").strip()
        if not order_id:
            continue
        included_rows.append((order_id, row))
        if revenue_col:
            order_revenue_totals[order_id] = order_revenue_totals.get(order_id, 0.0) + to_number(row.get(revenue_col))

    rows = []
    for order_id, row in included_rows:
        if has_direct_aff:
            phi_aff = -to_number(row.get(mapping["phiAff"]))
            platform_fee = 0.0
        else:
            order_fully_refunded = revenue_col and abs(order_revenue_totals.get(order_id, 1.0)) < _REVENUE_EPSILON
            if order_fully_refunded:
                aff1 = aff2 = 0.0
            else:
                aff1 = to_number(row.get(mapping["affiliateCommission"])) if "affiliateCommission" in mapping else 0.0
                aff2 = to_number(row.get(mapping["affiliateAdsCommission"])) if "affiliateAdsCommission" in mapping else 0.0
            total_fee = to_number(row.get(mapping["totalFee"])) if "totalFee" in mapping else 0.0
            vat = to_number(row.get(mapping["vatWithheld"])) if "vatWithheld" in mapping else 0.0
            pit = to_number(row.get(mapping["pitWithheld"])) if "pitWithheld" in mapping else 0.0
            phi_aff = -(aff1 + aff2)
            platform_fee = -(total_fee - aff1 - aff2 - vat - pit)

        rows.append({"orderId": order_id, "phiAff": phi_aff, "platformFee": platform_fee})

    if not rows:
        raise CashflowMappingError("Không có dòng dữ liệu hợp lệ nào (không đọc được Mã đơn hàng ở bất kỳ dòng nào).")

    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue(), len(rows), mapping
