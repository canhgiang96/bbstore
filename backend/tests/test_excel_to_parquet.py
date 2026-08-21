import io

import pyarrow.parquet as pq
from openpyxl import Workbook

from app.excel_to_parquet import MappingError, excel_to_parquet, get_original_headers

HEADERS = [
    "Mã đơn hàng", "Ngày đặt hàng", "Trạng Thái Đơn Hàng", "Lý do hủy",
    "SKU phân loại hàng", "Tên sản phẩm", "Giá gốc", "Số lượng",
    "Số lượng sản phẩm được hoàn trả",
]

ROWS = [
    ["O1", "2026-02-01 00:01", "Đã hủy", "Giao hàng thất bại", "A100-1", "SP A", 100000, 2, 0],
    ["O2", "2026-02-02 09:00", "Đã hủy", "Người mua đổi ý", "B200-2", "SP B", 50000, 3, 0],
    ["O3", "2026-02-03 10:00", "Hoàn thành", "", "C300-1", "SP C", 20000, 4, 4],
    ["O4", "2026-02-04 11:00", "Hoàn thành", "", "D400-3", "SP D", 30000, 5, 2],
    ["O5", "2026-02-05 12:00", "Hoàn thành", "", "E500-1", "SP E", 200000, 1, 0],
    ["O6", "2026-02-06 13:00", "Đang giao hàng", "", "F600-2", "SP F", 80000, 2, 0],
]


def make_xlsx_bytes():
    wb = Workbook()
    ws = wb.active
    ws.title = "orders"
    ws.append(HEADERS)
    for r in ROWS:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_excel_to_parquet_end_to_end():
    parquet_bytes, row_count, mapping = excel_to_parquet(make_xlsx_bytes())

    assert row_count == 6
    assert mapping["date"] == "Ngày đặt hàng"
    assert mapping["status"] == "Trạng Thái Đơn Hàng"
    assert mapping["originalPrice"] == "Giá gốc"
    assert mapping["orderId"] == "Mã đơn hàng"

    table = pq.read_table(io.BytesIO(parquet_bytes))
    df = table.to_pandas()
    assert len(df) == 6

    total_doanh_so = df["doanhSo"].sum()
    assert total_doanh_so == 940000

    by_status = df.groupby("trangThai")["doanhSo"].sum().to_dict()
    assert by_status["Hủy sau XK"] == 200000
    assert by_status["Hủy chưa XK"] == 150000
    assert by_status["Hoàn hàng"] == 80000
    assert by_status["Hoàn 1 phần"] == 150000
    gmv = by_status.get("Hoàn thành", 0) + by_status.get("Đang giao", 0)
    assert gmv == 360000

    # SKU parent-code derivation survived the full pipeline.
    row_a = df[df["orderId"] == "O1"].iloc[0]
    assert row_a["skuVariant"] == "A100-1"
    assert row_a["sku"] == "A100"


def test_missing_date_column_raises():
    wb = Workbook()
    ws = wb.active
    ws.append(["Sản phẩm", "Số lượng", "Giá gốc"])
    ws.append(["A", 1, 1000])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    try:
        excel_to_parquet(buf)
        assert False, "expected MappingError"
    except MappingError:
        pass


def test_get_original_headers():
    headers = get_original_headers(make_xlsx_bytes())
    assert headers == HEADERS


def test_mapping_override_reconverts_with_chosen_columns():
    # Regression for the "Chỉnh cột" PATCH endpoint: since the Parquet's
    # columns are fixed at conversion time, overriding the mapping must
    # actually reconvert the file with the admin's chosen columns — not
    # just relabel stored metadata. Here we deliberately map "product" to
    # the SKU column instead of "Tên sản phẩm" to prove the override wins
    # over auto-detection.
    override = {
        "date": "Ngày đặt hàng",
        "orderId": "Mã đơn hàng",
        "product": "SKU phân loại hàng",  # would never be auto-detected as product
        "quantity": "Số lượng",
        "price": "Giá gốc",
        "originalPrice": "Giá gốc",
        "returnedQty": "Số lượng sản phẩm được hoàn trả",
        "status": "Trạng Thái Đơn Hàng",
        "cancelReason": "Lý do hủy",
    }
    parquet_bytes, row_count, mapping = excel_to_parquet(make_xlsx_bytes(), mapping_override=override)
    assert row_count == 6
    assert mapping["product"] == "SKU phân loại hàng"

    table = pq.read_table(io.BytesIO(parquet_bytes))
    df = table.to_pandas()
    row_a = df[df["orderId"] == "O1"].iloc[0]
    assert row_a["product"] == "A100-1"  # took the overridden column, not "SP A"


def test_mapping_override_missing_date_still_raises():
    try:
        excel_to_parquet(make_xlsx_bytes(), mapping_override={"product": "Tên sản phẩm"})
        assert False, "expected MappingError"
    except MappingError:
        pass


DISCOUNT_HEADERS = HEADERS + ["Người bán trợ giá", "Mã giảm giá của Shop", "Số tiền người mua thanh toán"]

DISCOUNT_ROWS = [
    # MULTI1: 2-line order. Shopee repeats the order-level shop voucher
    # (10.000) on every line; it must be prorated by each line's share of
    # "Số tiền người mua thanh toán" (300.000 + 700.000 = 1.000.000 total).
    ["MULTI1", "2026-02-01 00:01", "Hoàn thành", "", "A100-1", "SP A", 150000, 2, 0, 4000, 10000, 300000],
    ["MULTI1", "2026-02-01 00:01", "Hoàn thành", "", "B200-1", "SP B", 700000, 1, 0, 6000, 10000, 700000],
    # SINGLE1: 1-line order -> ratio must be exactly 100%.
    ["SINGLE1", "2026-02-02 09:00", "Hoàn thành", "", "C300-1", "SP C", 50000, 1, 0, 1000, 5000, 50000],
]


def make_discount_xlsx_bytes():
    wb = Workbook()
    ws = wb.active
    ws.title = "orders"
    ws.append(DISCOUNT_HEADERS)
    for r in DISCOUNT_ROWS:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_discount_and_voucher_prorated_across_multi_line_order():
    parquet_bytes, row_count, mapping = excel_to_parquet(make_discount_xlsx_bytes())
    assert row_count == 3
    assert mapping["sellerSubsidy"] == "Người bán trợ giá"
    assert mapping["shopVoucher"] == "Mã giảm giá của Shop"
    assert mapping["buyerPaidAmount"] == "Số tiền người mua thanh toán"

    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()

    line1 = df[(df["orderId"] == "MULTI1") & (df["skuVariant"] == "A100-1")].iloc[0]
    line2 = df[(df["orderId"] == "MULTI1") & (df["skuVariant"] == "B200-1")].iloc[0]
    single = df[df["orderId"] == "SINGLE1"].iloc[0]

    # discount = (Người bán trợ giá / Số lượng) x Số lượng thực — no returns
    # here so Số lượng thực == Số lượng.
    assert line1["discount"] == 4000 / 2 * 2
    assert line2["discount"] == 6000 / 1 * 1

    # line1 is 300.000/1.000.000 = 30% of the order's paid amount; line2 is 70%.
    assert line1["voucher"] == 10000 * 0.3 / 2 * 2
    assert line2["voucher"] == 10000 * 0.7 / 1 * 1

    # Single-line order -> ratio is exactly 100%, so voucher == the shop's
    # full voucher value for that line (no proration needed).
    assert single["voucher"] == 5000
    assert single["discount"] == 1000

    # orderPaidRatio is persisted per row (not just used transiently) so the
    # query-time Phí AFF join can reuse the exact same proration.
    assert line1["orderPaidRatio"] == 0.3
    assert line2["orderPaidRatio"] == 0.7
    assert single["orderPaidRatio"] == 1.0


def test_discount_and_voucher_default_to_zero_when_columns_absent():
    # Existing Reports converted before this feature has no "Người bán trợ
    # giá" / "Mã giảm giá của Shop" / "Số tiền người mua thanh toán"
    # columns — must not error, discount/voucher should just be 0.
    parquet_bytes, row_count, mapping = excel_to_parquet(make_xlsx_bytes())
    assert "sellerSubsidy" not in mapping
    assert "shopVoucher" not in mapping
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert (df["discount"] == 0).all()
    assert (df["voucher"] == 0).all()


FEE_HEADERS = DISCOUNT_HEADERS + ["Phí cố định", "Phí dịch vụ", "Phí xử lý giao dịch"]

FEE_ROWS = [
    # F1: 2-line order. Fee columns hold the SAME order-level total on both
    # lines (1000+2000+500=3500), prorated by paid-amount ratio (40/60).
    ["F1", "2026-02-01 00:01", "Hoàn thành", "", "A100-1", "SP A", 100000, 2, 0, 4000, 10000, 400000, 1000, 2000, 500],
    ["F1", "2026-02-01 00:01", "Hoàn thành", "", "B200-1", "SP B", 150000, 1, 0, 1500, 10000, 600000, 1000, 2000, 500],
    # F2: single-line order -> ratio 100%, and the only (first) line for Piship.
    ["F2", "2026-02-02 09:00", "Hoàn thành", "", "C300-1", "SP C", 50000, 1, 0, 1000, 5000, 50000, 500, 300, 200],
]


def make_fee_xlsx_bytes():
    wb = Workbook()
    ws = wb.active
    ws.title = "orders"
    ws.append(FEE_HEADERS)
    for r in FEE_ROWS:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_platform_fee_prorated_and_piship_assigned_to_first_line_only():
    parquet_bytes, row_count, mapping = excel_to_parquet(make_fee_xlsx_bytes())
    assert row_count == 3
    assert mapping["fixedFee"] == "Phí cố định"
    assert mapping["serviceFee"] == "Phí dịch vụ"
    assert mapping["transactionFee"] == "Phí xử lý giao dịch"

    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    line1 = df[(df["orderId"] == "F1") & (df["skuVariant"] == "A100-1")].iloc[0]
    line2 = df[(df["orderId"] == "F1") & (df["skuVariant"] == "B200-1")].iloc[0]
    single = df[df["orderId"] == "F2"].iloc[0]

    # (1000+2000+500)=3500 total order fee, prorated 40%/60% by paid amount.
    assert line1["platformFee"] == 3500 * 0.4
    assert line2["platformFee"] == 3500 * 0.6
    assert single["platformFee"] == 1000  # 500+300+200, ratio 100%

    # Piship (1.620/order, flat) goes to only the first surviving line.
    assert line1["piship"] == 1620
    assert line2["piship"] == 0
    assert single["piship"] == 1620


def test_platform_fee_and_piship_default_to_zero_when_columns_absent():
    parquet_bytes, row_count, mapping = excel_to_parquet(make_xlsx_bytes())
    assert "fixedFee" not in mapping
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert (df["platformFee"] == 0).all()
    # Piship is still assigned per-order regardless of whether fee columns
    # exist — one row per distinct orderId in this dataset (O1..O6) should
    # each get 1620 (all single-line orders here).
    assert (df["piship"] == 1620).all()
