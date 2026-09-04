"""Regression tests for app.derive, covering all 6 status branches in
priority order. Values mirror the synthetic 6-row test dataset used to
verify the JS deriveOrderStatus() during Phase 1 development (see the
KPI sums: Doanh số 940.000, GMV 360.000, hủy chưa XK 150.000, hủy sau XK
200.000, hoàn 230.000 — 150.000 + 200.000 + 230.000 + 360.000 = 940.000).
"""
from datetime import date, datetime

from app.derive import (
    channel_has_piship,
    compute_discount,
    compute_piship_fee,
    compute_platform_fee,
    compute_voucher,
    derive_order_status,
    derive_row_fields,
    normalize_combined_sales_channel,
)

MAPPING = {
    "quantity": "Số lượng",
    "originalPrice": "Giá gốc",
    "returnedQty": "Số lượng sản phẩm được hoàn trả",
    "status": "Trạng Thái Đơn Hàng",
    "cancelReason": "Lý do hủy",
    "skuVariant": "SKU phân loại hàng",
}


def row(sl, gia, hoan_tra, trang_thai, ly_do=""):
    return {
        "Số lượng": sl,
        "Giá gốc": gia,
        "Số lượng sản phẩm được hoàn trả": hoan_tra,
        "Trạng Thái Đơn Hàng": trang_thai,
        "Lý do hủy": ly_do,
        "SKU phân loại hàng": "A100-1",
    }


def test_huy_sau_xk_priority_over_everything_else():
    assert derive_order_status("Đã hủy", "Giao hàng thất bại", 2, 0) == "Hủy sau XK"


def test_huy_chua_xk_other_cancel_reason():
    assert derive_order_status("Đã hủy", "Người mua đổi ý", 3, 0) == "Hủy chưa XK"


def test_hoan_hang_full_return_beats_completed_status():
    # so_luong_thuc == 0 must win even though the raw status says Hoàn thành.
    assert derive_order_status("Hoàn thành", "", 0, 4) == "Hoàn hàng"


def test_hoan_1_phan_partial_return():
    assert derive_order_status("Hoàn thành", "", 3, 2) == "Hoàn 1 phần"


def test_hoan_thanh_no_return():
    assert derive_order_status("Hoàn thành", "", 1, 0) == "Hoàn thành"


def test_dang_giao_fallback():
    assert derive_order_status("Đang giao hàng", "", 2, 0) == "Đang giao"


def test_tiktok_status_and_cancel_reason_phrasing():
    # TikTok Shop's Vietnamese status/reason values differ slightly from
    # Shopee's for the same two states — confirmed against a real TikTok
    # order export (2026-08-26): "Đã hoàn tất" instead of "Hoàn thành",
    # and "Giao gói hàng thất bại" instead of "Giao hàng thất bại".
    assert derive_order_status("Đã hoàn tất", "", 1, 0) == "Hoàn thành"
    assert derive_order_status("Đã hủy", "Giao gói hàng thất bại", 2, 0) == "Hủy sau XK"
    # Still correctly falls through to Hoàn hàng/Hoàn 1 phần by quantity,
    # same as Shopee — TikTok's "Đã hoàn tất" doesn't override a real return.
    assert derive_order_status("Đã hoàn tất", "", 0, 4) == "Hoàn hàng"
    assert derive_order_status("Đã hoàn tất", "", 3, 2) == "Hoàn 1 phần"


def test_six_row_kpi_reconciliation():
    rows = [
        row(2, 100000, 0, "Đã hủy", "Giao hàng thất bại"),   # Hủy sau XK: 200.000
        row(3, 50000, 0, "Đã hủy", "Người mua đổi ý"),        # Hủy chưa XK: 150.000
        row(4, 20000, 4, "Hoàn thành"),                        # Hoàn hàng: 80.000
        row(5, 30000, 2, "Hoàn thành"),                        # Hoàn 1 phần: 150.000
        row(1, 200000, 0, "Hoàn thành"),                       # Hoàn thành: 200.000
        row(2, 80000, 0, "Đang giao hàng"),                    # Đang giao: 160.000
    ]
    derived = [derive_row_fields(r, MAPPING) for r in rows]

    by_status = {}
    for d in derived:
        by_status.setdefault(d["trangThai"], 0)
        by_status[d["trangThai"]] += d["doanhSo"]

    total = sum(d["doanhSo"] for d in derived)
    assert total == 940000
    assert by_status["Hủy sau XK"] == 200000
    assert by_status["Hủy chưa XK"] == 150000
    assert by_status["Hoàn hàng"] == 80000
    assert by_status["Hoàn 1 phần"] == 150000
    gmv = by_status.get("Hoàn thành", 0) + by_status.get("Đang giao", 0)
    assert gmv == 360000
    hoan = by_status.get("Hoàn hàng", 0) + by_status.get("Hoàn 1 phần", 0)
    huy_chua_xk = by_status.get("Hủy chưa XK", 0)
    huy_sau_xk = by_status.get("Hủy sau XK", 0)
    assert gmv + huy_chua_xk + huy_sau_xk + hoan == total


def test_hoan_hang_requires_a_real_zero_not_an_unmapped_quantity():
    # Regression: so_luong_thuc == 0 used to trigger "Hoàn hàng" even when
    # it's 0 only because "Số lượng" wasn't mapped at all (quantity
    # defaults to 0 for every row) — misclassifying an entire report as
    # fully-returned and zeroing GMV report-wide. A genuine full return
    # (quantity_known=True) still correctly wins over "Hoàn thành".
    assert derive_order_status("Hoàn thành", "", 0, 0, quantity_known=False) == "Hoàn thành"
    assert derive_order_status("Đang giao hàng", "", 0, 0, quantity_known=False) == "Đang giao"
    assert derive_order_status("Hoàn thành", "", 0, 4, quantity_known=True) == "Hoàn hàng"


def test_derive_row_fields_unmapped_quantity_does_not_force_full_return():
    mapping_no_quantity = {k: v for k, v in MAPPING.items() if k != "quantity"}
    row_no_quantity = {
        "Giá gốc": 100000,
        "Số lượng sản phẩm được hoàn trả": 0,
        "Trạng Thái Đơn Hàng": "Hoàn thành",
        "Lý do hủy": "",
        "SKU phân loại hàng": "A100-1",
    }
    fields = derive_row_fields(row_no_quantity, mapping_no_quantity)
    assert fields["trangThai"] == "Hoàn thành"


def test_sku_parent_strips_variant_suffix():
    fields = derive_row_fields(row(1, 1000, 0, "Hoàn thành"), MAPPING)
    assert fields["skuVariant"] == "A100-1"
    assert fields["sku"] == "A100"


def test_compute_discount_scales_by_actual_quantity():
    # Người bán trợ giá 10.000 / Số lượng 4 = 2.500/đơn vị; 1 đơn vị bị hoàn
    # nên Số lượng thực chỉ còn 3 -> Giảm giá trên dashboard = 2.500 x 3.
    assert compute_discount(seller_subsidy=10000, quantity=4, so_luong_thuc=3) == 7500


def test_compute_discount_zero_quantity_is_safe():
    assert compute_discount(seller_subsidy=10000, quantity=0, so_luong_thuc=0) == 0


def test_compute_voucher_single_line_order_full_ratio():
    # Đơn chỉ có 1 dòng sản phẩm -> tỉ lệ = 100%.
    assert compute_voucher(shop_voucher=20000, voucher_ratio=1.0) == 20000


def test_compute_voucher_prorated_across_multi_line_order():
    # Dòng này chiếm 30% tỉ trọng (đã tính theo Số lượng thực) của đơn.
    voucher = compute_voucher(shop_voucher=20000, voucher_ratio=0.3)
    assert voucher == 6000


def test_compute_voucher_zero_ratio_is_safe():
    assert compute_voucher(shop_voucher=20000, voucher_ratio=0.0) == 0


def test_compute_platform_fee_prorated_by_order_paid_ratio():
    # (Phí cố định + Phí dịch vụ + Phí xử lý giao dịch) x tỉ lệ của đơn.
    fee = compute_platform_fee(fixed_fee=1000, service_fee=2000, transaction_fee=500, order_paid_ratio=0.4)
    assert fee == 3500 * 0.4


def test_compute_platform_fee_not_scaled_by_returns():
    # Phí sàn không giảm theo Số lượng thực dù đơn có hoàn hàng — hàm này
    # không nhận quantity/so_luong_thuc ở tất cả, nên không thể bị ảnh hưởng.
    fee_full_order = compute_platform_fee(1000, 2000, 500, order_paid_ratio=1.0)
    assert fee_full_order == 3500


def test_compute_piship_fee_first_line_gets_full_amount():
    assert compute_piship_fee(is_first_line_of_order=True) == 1620


def test_compute_piship_fee_other_lines_get_zero():
    assert compute_piship_fee(is_first_line_of_order=False) == 0


# Confirmed with the user 2026-09-03: Shopee raised Piship from 1.620 to
# 2.700 starting exactly 23/05/2026, compared against the order's own date.
def test_compute_piship_fee_uses_old_rate_before_change_date():
    assert compute_piship_fee(True, datetime(2026, 5, 22)) == 1620


def test_compute_piship_fee_uses_new_rate_on_change_date():
    assert compute_piship_fee(True, datetime(2026, 5, 23)) == 2700


def test_compute_piship_fee_uses_new_rate_after_change_date():
    assert compute_piship_fee(True, datetime(2026, 9, 3)) == 2700


def test_compute_piship_fee_defaults_to_old_rate_when_date_unknown():
    # No date column mapped/parseable — don't silently assume the newer
    # rate, matching quantity_known/status_known's caution elsewhere.
    assert compute_piship_fee(True, None) == 1620
    assert compute_piship_fee(True) == 1620


def test_compute_piship_fee_accepts_plain_date_not_just_datetime():
    assert compute_piship_fee(True, date(2026, 5, 23)) == 2700


def test_channel_has_piship_none_defaults_to_true():
    # No channel picked at upload time -> preserve old always-on behavior.
    assert channel_has_piship(None) is True


def test_channel_has_piship_shopee_case_and_whitespace_insensitive():
    assert channel_has_piship("Shopee") is True
    assert channel_has_piship(" shopee ") is True


def test_channel_has_piship_false_for_other_channels():
    assert channel_has_piship("TikTok Shop") is False
    assert channel_has_piship("") is False


def test_normalize_combined_sales_channel_known_values():
    assert normalize_combined_sales_channel("HaraSocial") == "HARA"
    assert normalize_combined_sales_channel("POS") == "31 LVS"
    assert normalize_combined_sales_channel("Web") == "WEBSITE"


def test_normalize_combined_sales_channel_zalo_matches_by_substring():
    # Zalo is checked separately (not via COMBINED_SALES_CHANNEL_MAP) since
    # the raw value varies more than the other 3 (e.g. "Zalo OA").
    assert normalize_combined_sales_channel("Zalo") == "ZALO"
    assert normalize_combined_sales_channel("Zalo OA") == "ZALO"


def test_normalize_combined_sales_channel_unknown_or_blank_returns_empty():
    assert normalize_combined_sales_channel("") == ""
    assert normalize_combined_sales_channel("Lazada") == ""
