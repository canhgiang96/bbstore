"""Business status derivation for Orders rows.

Port of deriveOrderStatus() and the per-row derived-field block inside
buildDashboardRecords() in ../../js/app.js. Keep in sync with that file —
the 6-branch priority order and the "4 breakdown KPIs sum to the total"
invariant both depend on this matching exactly.
"""
from __future__ import annotations

from .mapping import strip_diacritics
from .parsing import to_number


def derive_order_status(raw_status: str, cancel_reason: str, so_luong_thuc: float, returned_qty: float) -> str:
    status_norm = strip_diacritics(raw_status or "")
    reason_norm = strip_diacritics(cancel_reason or "")

    if "huy" in status_norm:
        return "Hủy sau XK" if "giao hang that bai" in reason_norm else "Hủy chưa XK"
    if so_luong_thuc == 0:
        return "Hoàn hàng"
    if returned_qty > 0 and so_luong_thuc > 0:
        return "Hoàn 1 phần"
    if "hoan thanh" in status_norm:
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

    quantity = to_number(get("quantity")) if mapping.get("quantity") else 0.0
    original_price = to_number(get("originalPrice")) if mapping.get("originalPrice") else 0.0
    returned_qty = to_number(get("returnedQty")) if mapping.get("returnedQty") else 0.0
    so_luong_thuc = quantity - returned_qty
    doanh_so = original_price * quantity

    status = str(get("status", "") or "").strip()
    cancel_reason = str(get("cancelReason", "") or "").strip()
    trang_thai = derive_order_status(status, cancel_reason, so_luong_thuc, returned_qty)

    return {
        "skuVariant": sku_variant,
        "sku": sku,
        "quantity": quantity,
        "originalPrice": original_price,
        "returnedQty": returned_qty,
        "soLuongThuc": so_luong_thuc,
        "doanhSo": doanh_so,
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
