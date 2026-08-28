"""Business status derivation for Orders rows.

Port of deriveOrderStatus() and the per-row derived-field block inside
buildDashboardRecords() in ../../js/app.js. Keep in sync with that file —
the 6-branch priority order and the "4 breakdown KPIs sum to the total"
invariant both depend on this matching exactly.
"""
from __future__ import annotations

from .mapping import strip_diacritics
from .parsing import to_number


def derive_order_status(
    raw_status: str, cancel_reason: str, so_luong_thuc: float, returned_qty: float,
    quantity_known: bool = True, status_known: bool = True,
) -> str:
    status_norm = strip_diacritics(raw_status or "")
    reason_norm = strip_diacritics(cancel_reason or "")

    # "huy" detection only applies when there's real status text to read —
    # a file with no status column at all (e.g. the in-house POS/social/
    # web/Zalo export, confirmed with the user 2026-08-28: "các dòng trong
    # file không có đơn hủy") has no cancellation concept to detect at all.
    if status_known and "huy" in status_norm:
        # "giao hang that bai" is Shopee's phrasing; "giao goi hang that
        # bai" ("delivery of the package failed") is TikTok Shop's.
        failed_delivery = "giao hang that bai" in reason_norm or "giao goi hang that bai" in reason_norm
        return "Hủy sau XK" if failed_delivery else "Hủy chưa XK"
    # so_luong_thuc == 0 means "fully returned" only when we actually know
    # the quantity — if "Số lượng" wasn't mapped at all, quantity defaults
    # to 0 and so_luong_thuc is 0 for every row regardless of what really
    # happened, which would otherwise misclassify the whole report as
    # "Hoàn hàng" (zeroing GMV dashboard-wide) instead of falling through
    # to the status-text-based branches below. This branch (and the next)
    # are return-quantity-based, independent of whether a status column
    # exists at all, so they still apply when status_known is False.
    if quantity_known and so_luong_thuc == 0:
        return "Hoàn hàng"
    if returned_qty > 0 and so_luong_thuc > 0:
        return "Hoàn 1 phần"
    if not status_known:
        return "Hoàn thành"
    # "hoan thanh" is Shopee's phrasing; "hoan tat" ("Đã hoàn tất") is
    # TikTok Shop's for the same "completed" status.
    if "hoan thanh" in status_norm or "hoan tat" in status_norm:
        return "Hoàn thành"
    return "Đang giao"


def derive_row_fields(row: dict, mapping: dict) -> dict:
    """Given one raw Excel row (dict keyed by original header) and the
    resolved column mapping, returns the computed fields used by the
    Dashboard: sku, doanhSo, soLuongThuc, trangThai, plus the passthrough
    values needed to build a Parquet row (quantity, originalPrice, status,
    cancelReason, skuVariant).
    """
    def get(field_key, default=""):
        col = mapping.get(field_key)
        if not col:
            return default
        v = row.get(col, default)
        return v

    sku_variant = str(get("skuVariant", "") or "").strip()
    sku = sku_variant.split("-")[0] if sku_variant else ""

    quantity_known = bool(mapping.get("quantity"))
    quantity = to_number(get("quantity")) if quantity_known else 0.0
    if mapping.get("originalPrice"):
        original_price = to_number(get("originalPrice"))
    elif mapping.get("revenue") and quantity:
        # No per-unit price column at all (e.g. the in-house POS/social/
        # web/Zalo export, confirmed with the user 2026-08-28: it gives a
        # pre-computed per-line "Doanh thu" instead of "Giá gốc") — infer
        # an implied unit price so GMV/Giá vốn (which key off originalPrice)
        # still work; doanhSo = original_price * quantity below then comes
        # out exactly equal to the raw "Doanh thu" value, matching "Doanh
        # số = Doanh thu".
        original_price = to_number(get("revenue")) / quantity
    else:
        original_price = 0.0
    # abs(): Shopee/TikTok always store this as a non-negative count, but
    # the in-house POS/social/web/Zalo export stores it NEGATIVE instead
    # (confirmed against real file sale_report_28_08_2026_927871_1,
    # 2026-08-28 — e.g. "Số sản phẩm trả" = -1 for 1 unit returned).
    # Normalizing to non-negative here keeps so_luong_thuc's subtraction
    # below correct regardless of which convention the source file used.
    returned_qty = abs(to_number(get("returnedQty"))) if mapping.get("returnedQty") else 0.0
    so_luong_thuc = quantity - returned_qty
    doanh_so = original_price * quantity
    # "Doanh số hoàn" defaults to originalPrice x returnedQty (Shopee/
    # TikTok, no direct refund-amount column) but prefers a real per-line
    # refund amount when the file gives one directly (confirmed with the
    # user 2026-08-28: "Doanh số hoàn = Hoàn trả", not re-derived) — same
    # abs() normalization as returnedQty above, since the source file
    # stores "Hoàn trả" negative too.
    hoan_amount = abs(to_number(get("refundAmount"))) if mapping.get("refundAmount") else original_price * returned_qty

    status = str(get("status", "") or "").strip()
    cancel_reason = str(get("cancelReason", "") or "").strip()
    status_known = bool(mapping.get("status"))
    trang_thai = derive_order_status(
        status, cancel_reason, so_luong_thuc, returned_qty,
        quantity_known=quantity_known, status_known=status_known,
    )

    return {
        "skuVariant": sku_variant,
        "sku": sku,
        "quantity": quantity,
        "originalPrice": original_price,
        "returnedQty": returned_qty,
        "soLuongThuc": so_luong_thuc,
        "doanhSo": doanh_so,
        "hoanAmount": hoan_amount,
        "status": status,
        "cancelReason": cancel_reason,
        "trangThai": trang_thai,
    }


def compute_discount(seller_subsidy: float, quantity: float, so_luong_thuc: float) -> float:
    """Giảm giá (per unit) = Người bán trợ giá / Số lượng.
    Giảm giá trên dashboard = Giảm giá x Số lượng thực.
    """
    if not quantity:
        return 0.0
    return (seller_subsidy / quantity) * so_luong_thuc


def compute_voucher(shop_voucher: float, order_paid_ratio: float, quantity: float, so_luong_thuc: float) -> float:
    """Mã giảm giá của Shop is an order-level amount Shopee repeats on every
    line of a multi-line order, so it's prorated by order_paid_ratio — this
    line's share of "Số tiền người mua thanh toán" summed across the whole
    order (1.0 when the order has only one line).

    Voucher (per unit) = Mã giảm giá của Shop x order_paid_ratio / Số lượng.
    Voucher trên dashboard = Voucher x Số lượng thực.
    """
    if not quantity:
        return 0.0
    return (shop_voucher * order_paid_ratio / quantity) * so_luong_thuc


PISHIP_FEE_PER_ORDER = 1620


def compute_platform_fee(fixed_fee: float, service_fee: float, transaction_fee: float, order_paid_ratio: float) -> float:
    """Phí sàn = (Phí cố định + Phí dịch vụ + Phí xử lý giao dịch), an
    order-level total Shopee repeats on every line, prorated by
    order_paid_ratio the same way Voucher is — but NOT scaled by Số lượng
    thực, since a platform fee already incurred isn't refunded by a return.
    """
    return (fixed_fee + service_fee + transaction_fee) * order_paid_ratio


def compute_piship_fee(is_first_line_of_order: bool) -> float:
    """Phí Piship là một khoản phí cố định 1.620 cho mỗi đơn hàng (không
    nhân theo số dòng sản phẩm) — assigned to just the first surviving line
    of each order so summing rows gives the correct per-order total instead
    of double-counting it once per line.
    """
    return PISHIP_FEE_PER_ORDER if is_first_line_of_order else 0.0


# Piship is Shopee's own delivery-partner fee scheme — it doesn't apply to
# other channels (confirmed with the user: TikTok has no Piship-equivalent
# at all). A channel not in this set defaults to "no Piship" rather than
# "has Piship", since that's a Shopee-specific concept new channels are
# very unlikely to share.
PISHIP_CHANNEL_NAMES = {"shopee"}


def channel_has_piship(sales_channel_name: str | None) -> bool:
    """True if Piship should be computed for this Report's rows.

    No channel selected at all (None) defaults to True — preserving the
    original always-on behavior for the many Reports uploaded before
    per-channel Piship gating existed (all of them Shopee, per the actual
    upload history), so an admin who doesn't bother picking a channel for
    a Shopee file doesn't silently lose Phí Piship.
    """
    if sales_channel_name is None:
        return True
    return sales_channel_name.strip().lower() in PISHIP_CHANNEL_NAMES


# 31 LVS/HARA/WEBSITE/ZALO share one combined "sale_report_*.xlsx" export
# (confirmed with the user 2026-08-28, real file
# sale_report_28_08_2026_927871_1) with a per-row "Kênh bán hàng" column
# whose raw values are the exact keys below — mapping them here lets all 4
# be uploaded as a single Report instead of 4 separate ones, with each
# row's real Kênh bán hàng recovered at conversion time (see
# excel_to_parquet.build_dashboard_rows) rather than needing one upload-
# time channel pick per file.
COMBINED_SALES_CHANNEL_MAP = {
    "harasocial": "HARA",
    "pos": "31 LVS",
    "web": "WEBSITE",
}


def normalize_combined_sales_channel(raw_value: str) -> str:
    """Maps one row's raw "Kênh bán hàng" text (from the combined 31 LVS/
    HARA/WEBSITE/ZALO file) to the matching Kênh bán hàng name used
    elsewhere in the system, or "" when the value isn't one of the known
    ones (e.g. blank, or a file that doesn't use this scheme at all —
    Piship/other per-channel gating then falls back to the Report's own
    upload-time channel pick, see query_engine._build_orders_working's
    COALESCE).
    """
    norm = strip_diacritics(raw_value or "").strip()
    if not norm:
        return ""
    if "zalo" in norm:
        return "ZALO"
    return COMBINED_SALES_CHANNEL_MAP.get(norm, "")
