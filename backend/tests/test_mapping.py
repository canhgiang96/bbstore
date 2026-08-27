"""Regression tests for app.mapping against known behavior of the JS
detectMapping() in ../../js/app.js — these header lists and expected
mappings were captured from real Shopee order exports during Phase 1
development, including two bugs that were fixed there and must stay fixed
here.
"""
from app.mapping import detect_mapping, normalize_header, strip_diacritics

REAL_ORDER_HEADERS = [
    "Mã đơn hàng", "Mã Kiện Hàng", "Ngày đặt hàng", "Trạng Thái Đơn Hàng",
    "Sản Phẩm Bán Chạy", "Lý do hủy", "Nhận xét từ Người mua", "Mã vận đơn",
    "Đơn Vị Vận Chuyển", "Phương thức giao hàng", "Loại đơn hàng",
    "Ngày giao hàng dự kiến", "Ngày gửi hàng", "Thời gian giao hàng",
    "Trạng thái Trả hàng/Hoàn tiền", "SKU sản phẩm", "Tên sản phẩm",
    "Cân nặng sản phẩm", "Tổng cân nặng", "SKU phân loại hàng",
    "Tên phân loại hàng", "Giá gốc", "Người bán trợ giá",
    "Được Shopee trợ giá", "Tổng số tiền được người bán trợ giá",
    "Giá ưu đãi", "Số lượng", "Số lượng sản phẩm được hoàn trả",
    "Tổng số tiền Người mua thanh toán", "Tổng giá trị đơn hàng (VND)",
]


def test_strip_diacritics():
    assert strip_diacritics("Đơn Giá") == "don gia"
    assert strip_diacritics("Số Lượng") == "so luong"


def test_normalize_header_collapses_punctuation():
    assert normalize_header("Tổng giá trị đơn hàng (VND)") == "tong gia tri don hang vnd"


def test_real_order_file_mapping():
    m = detect_mapping(REAL_ORDER_HEADERS)
    assert m["date"] == "Ngày đặt hàng"
    assert m["status"] == "Trạng Thái Đơn Hàng"
    assert m["orderId"] == "Mã đơn hàng"
    assert m["quantity"] == "Số lượng"
    assert m["cancelReason"] == "Lý do hủy"
    assert m["returnedQty"] == "Số lượng sản phẩm được hoàn trả"
    assert m["originalPrice"] == "Giá gốc"
    assert m["skuVariant"] == "SKU phân loại hàng"

    # Regression: "product" must pick the name column, not the SKU column
    # that happens to also contain the substring "san pham".
    assert m["product"] == "Tên sản phẩm"

    # Regression: "price" should prefer the discounted selling price over
    # the original list price when both exist.
    assert m["price"] == "Giá ưu đãi"

    # Regression: revenue should prefer "Tổng giá trị đơn hàng" (merchandise
    # value) over "Tổng số tiền ... thanh toán" (includes buyer-paid shipping).
    assert m["revenue"] == "Tổng giá trị đơn hàng (VND)"


def test_no_category_column_is_left_unmapped():
    # Regression: when there's no real category-like column, an identifier
    # column (e.g. "SKU phân loại hàng") must NOT be picked just because it's
    # the only substring match — a negative-scored candidate should be
    # rejected rather than selected.
    headers = ["Ngày đặt hàng", "Tên sản phẩm", "SKU phân loại hàng", "Số lượng", "Giá gốc"]
    m = detect_mapping(headers)
    assert "category" not in m


def test_category_prefers_full_name_column_over_sku_column():
    headers = ["Tên phân loại hàng", "SKU phân loại hàng"]
    m = detect_mapping(headers)
    assert m["category"] == "Tên phân loại hàng"


def test_required_date_field_always_wins_on_exact_match():
    headers = ["Ngày đặt hàng", "Ngày giao hàng dự kiến", "Ngày gửi hàng"]
    m = detect_mapping(headers)
    assert m["date"] == "Ngày đặt hàng"


# Captured verbatim from a real TikTok Shop order export (2026-08-26) — the
# same field keys as Shopee's REAL_ORDER_HEADERS above, but with TikTok's
# English column names instead of Shopee's Vietnamese ones.
TIKTOK_ORDER_HEADERS = [
    "Order ID", "Order Status", "Order Substatus", "Cancelation/Return Type",
    "Normal or Pre-order", "SKU ID", "Seller SKU", "Product Name", "Variation",
    "Quantity", "Sku Quantity of return", "SKU Unit Original Price",
    "SKU Subtotal Before Discount", "SKU Platform Discount", "SKU Seller Discount",
    "SKU Subtotal After Discount", "Shipping Fee After Discount",
    "Original Shipping Fee", "Shipping Fee Seller Discount",
    "Shipping Fee Platform Discount", "Payment platform discount", "Taxes",
    "Order Amount", "Order Refund Amount", "Created Time", "Paid Time",
    "RTS Time", "Shipped Time", "Delivered Time", "Cancelled Time", "Cancel By",
    "Cancel Reason", "Fulfillment Type", "Warehouse Name", "Tracking ID",
    "Delivery Option", "Shipping Provider Name", "Buyer Message",
    "Buyer Username", "Recipient", "Phone #", "Country", "Province", "District",
    "Commune", "Detail Address", "Additional address information",
    "Payment Method", "Weight(kg)", "Product Category", "Package ID",
    "Seller Note", "Checked Status", "Checked Marked by", "Order Channel",
    "Creator Handle",
]


def test_tiktok_order_file_mapping():
    m = detect_mapping(TIKTOK_ORDER_HEADERS)
    assert m["date"] == "Created Time"
    assert m["status"] == "Order Status"
    assert m["orderId"] == "Order ID"
    assert m["quantity"] == "Quantity"
    assert m["cancelReason"] == "Cancel Reason"
    assert m["returnedQty"] == "Sku Quantity of return"
    assert m["originalPrice"] == "SKU Unit Original Price"
    assert m["skuVariant"] == "Seller SKU"
    assert m["sellerSubsidy"] == "SKU Seller Discount"
    assert m["product"] == "Product Name"
    assert m["category"] == "Product Category"
    # Feed the Dashboard's "Kênh nhỏ" classification (see
    # query_engine._aff_channel_join) — skuId is TikTok's own internal
    # numeric SKU id, deliberately a SEPARATE field from skuVariant/Seller
    # SKU (confirmed against real files: they're different value spaces).
    assert m["skuId"] == "SKU ID"
    assert m["creatorHandle"] == "Creator Handle"
    assert m["contentChannel"] == "Order Channel"
    # TikTok's Orders file has no Voucher/Phí sàn/Phí AFF/Piship-equivalent
    # columns (per the user, those come from the Dòng tiền-equivalent file
    # instead) — must stay unmapped rather than false-matching something.
    assert "shopVoucher" not in m
    assert "buyerPaidAmount" not in m
    assert "fixedFee" not in m
    assert "serviceFee" not in m
    assert "transactionFee" not in m
