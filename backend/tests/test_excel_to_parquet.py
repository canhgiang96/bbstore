import io
import re
import zipfile

import pyarrow.parquet as pq
from openpyxl import Workbook

from app.excel_to_parquet import MappingError, excel_to_parquet, get_original_headers, read_excel_rows

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

    # skuVariant survives the full pipeline; the parent "sku" code is
    # deliberately not persisted (query_engine.py recomputes it from
    # skuVariant at query time — see test_derive.py and test_query_engine.py).
    row_a = df[df["orderId"] == "O1"].iloc[0]
    assert row_a["skuVariant"] == "A100-1"
    assert "sku" not in df.columns


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


def _make_one_row_tag_per_cell_xlsx_bytes():
    """Builds an .xlsx whose worksheet XML has ONE <row> wrapper per
    individual cell instead of one <row> per actual spreadsheet row (seen
    verbatim in a real TikTok Shop order export). openpyxl's read_only
    mode streams by <row> element and ends up keeping just a single
    column's worth of cells per real row instead of raising anything —
    this reproduces that malformed structure from a normal openpyxl file.
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["Order ID", "Order Status", "Quantity"])
    ws.append(["O1", "Đã hủy", 2])
    ws.append(["O2", "Hoàn thành", 1])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    malformed_sheet_data = (
        "<sheetData>"
        '<row r="1"><c r="A1" t="str"><v>Order ID</v></c></row>'
        '<row r="1"><c r="B1" t="str"><v>Order Status</v></c></row>'
        '<row r="1"><c r="C1" t="str"><v>Quantity</v></c></row>'
        '<row r="2"><c r="A2" t="str"><v>O1</v></c></row>'
        '<row r="2"><c r="B2" t="str"><v>Đã hủy</v></c></row>'
        '<row r="2"><c r="C2" t="n"><v>2</v></c></row>'
        '<row r="3"><c r="A3" t="str"><v>O2</v></c></row>'
        '<row r="3"><c r="B3" t="str"><v>Hoàn thành</v></c></row>'
        '<row r="3"><c r="C3" t="n"><v>1</v></c></row>'
        "</sheetData>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(buf) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                text = re.sub(r"<sheetData>.*</sheetData>", malformed_sheet_data, data.decode("utf-8"), flags=re.S)
                data = text.encode("utf-8")
            zout.writestr(item, data)
    out.seek(0)
    return out


def test_read_excel_rows_recovers_from_one_row_tag_per_cell_export():
    # Regression: a malformed export (one <row> XML wrapper per cell
    # instead of per real row) made openpyxl's fast read_only mode
    # silently collapse every row down to just its first column, with no
    # exception raised — read_excel_rows must detect the suspiciously
    # narrow (<=1 column) result and retry without that fast path.
    rows, headers = read_excel_rows(_make_one_row_tag_per_cell_xlsx_bytes())
    assert headers == ["Order ID", "Order Status", "Quantity"]
    assert len(rows) == 2
    assert rows[0]["Order ID"] == "O1"
    assert rows[0]["Order Status"] == "Đã hủy"
    assert rows[0]["Quantity"] == 2
    assert rows[1]["Order ID"] == "O2"


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


def test_missing_orderid_column_raises():
    # "Mã đơn hàng" is required: Piship (flat fee per order, first line
    # only) and Voucher/Phí sàn proration both depend on being able to
    # group rows into orders — without it there's no way to compute these
    # correctly, so the upload is rejected rather than guessed at.
    override = {
        "date": "Ngày đặt hàng",
        "price": "Giá gốc",
        "quantity": "Số lượng",
        "originalPrice": "Giá gốc",
        "returnedQty": "Số lượng sản phẩm được hoàn trả",
        "status": "Trạng Thái Đơn Hàng",
        "cancelReason": "Lý do hủy",
        "skuVariant": "SKU phân loại hàng",
        "product": "Tên sản phẩm",
        # "orderId" deliberately omitted.
    }
    try:
        excel_to_parquet(make_xlsx_bytes(), mapping_override=override)
        assert False, "expected MappingError"
    except MappingError as e:
        assert "Mã đơn hàng" in str(e)


def test_piship_assigned_only_to_first_line_of_each_order():
    parquet_bytes, row_count, mapping = excel_to_parquet(make_xlsx_bytes())
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    # ROWS above is 6 distinct single-line orders (O1..O6) -> each is its
    # own order's first (and only) line, so each still gets 1.620.
    assert (df["piship"] == 1620).all()


def test_missing_required_column_lists_all_missing_fields():
    # Every field marked required=True in mapping.FIELDS must be present;
    # a file missing several of them should name all of them, not just
    # the first one found.
    try:
        excel_to_parquet(make_xlsx_bytes(), mapping_override={"date": "Ngày đặt hàng"})
        assert False, "expected MappingError"
    except MappingError as e:
        msg = str(e)
        for label in ["Mã đơn hàng", "Số lượng", "Giá gốc", "SL sản phẩm hoàn trả", "Trạng thái đơn hàng", "Lý do hủy"]:
            assert label in msg, f"expected {label!r} in error message: {msg!r}"


def test_voucher_and_platform_fee_fall_back_to_quantity_share_when_buyerpaidamount_not_mapped():
    # Regression: when "Số tiền người mua thanh toán" isn't mapped,
    # order_paid_ratio used to always be 0.0 (dividing by a total that's
    # never populated), silently zeroing Voucher and Phí sàn for the whole
    # report even though the source columns had real values. It should
    # instead fall back to prorating by each line's share of the order's
    # total quantity.
    override = {
        "date": "Ngày đặt hàng",
        "orderId": "Mã đơn hàng",
        "price": "Giá gốc",
        "quantity": "Số lượng",
        "originalPrice": "Giá gốc",
        "returnedQty": "Số lượng sản phẩm được hoàn trả",
        "status": "Trạng Thái Đơn Hàng",
        "cancelReason": "Lý do hủy",
        "skuVariant": "SKU phân loại hàng",
        "product": "Tên sản phẩm",
        "sellerSubsidy": "Người bán trợ giá",
        "shopVoucher": "Mã giảm giá của Shop",
        "fixedFee": "Phí cố định",
        "serviceFee": "Phí dịch vụ",
        "transactionFee": "Phí xử lý giao dịch",
        # "buyerPaidAmount" deliberately omitted.
    }
    parquet_bytes, row_count, mapping = excel_to_parquet(make_fee_xlsx_bytes(), mapping_override=override)
    assert "buyerPaidAmount" not in mapping
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()

    line1 = df[(df["orderId"] == "F1") & (df["skuVariant"] == "A100-1")].iloc[0]  # quantity 2
    line2 = df[(df["orderId"] == "F1") & (df["skuVariant"] == "B200-1")].iloc[0]  # quantity 1
    single = df[df["orderId"] == "F2"].iloc[0]  # quantity 1, only line in its order

    # F1's 2 lines split 2:1 by quantity (order total quantity = 3) instead
    # of collapsing to 0.
    assert line1["orderPaidRatio"] == 2 / 3
    assert line2["orderPaidRatio"] == 1 / 3
    assert line1["platformFee"] == 3500 * (2 / 3)
    assert line2["platformFee"] == 3500 * (1 / 3)
    assert line1["voucher"] == 10000 * (2 / 3)
    assert line2["voucher"] == 10000 * (1 / 3)

    # A single-line order still gets ratio 1.0 either way.
    assert single["orderPaidRatio"] == 1.0
    assert single["platformFee"] == 1000
    assert single["voucher"] == 5000


def test_platform_fee_and_piship_default_to_zero_when_columns_absent():
    parquet_bytes, row_count, mapping = excel_to_parquet(make_xlsx_bytes())
    assert "fixedFee" not in mapping
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert (df["platformFee"] == 0).all()
    # Piship is still assigned per-order regardless of whether fee columns
    # exist — one row per distinct orderId in this dataset (O1..O6) should
    # each get 1620 (all single-line orders here).
    assert (df["piship"] == 1620).all()
