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
