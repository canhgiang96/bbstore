"""Regression tests for app.derive, covering all 6 status branches in
priority order. Values mirror the synthetic 6-row test dataset used to
verify the JS deriveOrderStatus() during Phase 1 development (see the
KPI sums: Doanh số 940.000, GMV 360.000, hủy chưa XK 150.000, hủy sau XK
200.000, hoàn 230.000 — 150.000 + 200.000 + 230.000 + 360.000 = 940.000).
"""
from app.derive import compute_discount, compute_voucher, derive_order_status, derive_row_fields

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
    assert compute_voucher(shop_voucher=20000, order_paid_ratio=1.0, quantity=2, so_luong_thuc=2) == 20000


def test_compute_voucher_prorated_across_multi_line_order():
    # Dòng này chiếm 30% tổng số tiền thanh toán của đơn.
    voucher = compute_voucher(shop_voucher=20000, order_paid_ratio=0.3, quantity=2, so_luong_thuc=2)
    assert voucher == 6000


def test_compute_voucher_zero_quantity_is_safe():
    assert compute_voucher(shop_voucher=20000, order_paid_ratio=1.0, quantity=0, so_luong_thuc=0) == 0
