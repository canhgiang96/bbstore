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
