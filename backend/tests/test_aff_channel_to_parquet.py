import io

import pyarrow.parquet as pq
from openpyxl import Workbook

from app.aff_channel_to_parquet import AffChannelMappingError, aff_channel_excel_to_parquet

# A trimmed slice of real TikTok "affiliate_orders_*.xlsx" headers (2026-08-27)
# — only "ID đơn hàng" and "ID SKU" matter to the join, everything else is
# ignored by this converter.
HEADERS = ["ID đơn hàng", "ID sản phẩm", "ID SKU", "Tên người dùng nhà sáng tạo", "Loại nội dung", "Trạng thái đơn hàng"]

ROWS = [
    ["O1", "P1", "1730315401307982614", "creator_a", "Video", "Đã quyết toán"],
    ["O2", "P2", "1730007230586850070", "creator_b", "Chương trình Lưu lượng truy cập bên ngoài", "Không đủ điều kiện"],
]


def make_xlsx_bytes(headers=HEADERS, rows=ROWS):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_aff_channel_excel_to_parquet_captures_order_and_sku_id():
    parquet_bytes, row_count, mapping = aff_channel_excel_to_parquet(make_xlsx_bytes())
    assert row_count == 2
    assert mapping["orderId"] == "ID đơn hàng"
    assert mapping["skuId"] == "ID SKU"

    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    pairs = set(zip(df["orderId"], df["skuId"]))
    assert pairs == {("O1", "1730315401307982614"), ("O2", "1730007230586850070")}


def test_counts_regardless_of_eligibility_status():
    # Confirmed with the user 2026-08-27: a "Không đủ điều kiện" (not
    # eligible for commission) row still counts as a Kênh AFF match — this
    # is a channel-attribution classification, not a commission payout
    # figure, so both statuses are kept.
    parquet_bytes, row_count, _ = aff_channel_excel_to_parquet(make_xlsx_bytes())
    assert row_count == 2
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert set(df["orderId"]) == {"O1", "O2"}


def test_duplicate_order_sku_pairs_are_deduped():
    rows = ROWS + [["O1", "P1", "1730315401307982614", "creator_a", "Video", "Đã quyết toán"]]
    parquet_bytes, row_count, _ = aff_channel_excel_to_parquet(make_xlsx_bytes(rows=rows))
    assert row_count == 2  # the repeated (O1, sku) pair collapses to one


def test_missing_order_id_column_raises():
    headers = ["ID SKU", "Tên người dùng nhà sáng tạo"]
    rows = [["1730315401307982614", "creator_a"]]
    try:
        aff_channel_excel_to_parquet(make_xlsx_bytes(headers, rows))
        assert False, "expected AffChannelMappingError"
    except AffChannelMappingError:
        pass


def test_missing_sku_id_column_raises():
    headers = ["ID đơn hàng", "Tên người dùng nhà sáng tạo"]
    rows = [["O1", "creator_a"]]
    try:
        aff_channel_excel_to_parquet(make_xlsx_bytes(headers, rows))
        assert False, "expected AffChannelMappingError"
    except AffChannelMappingError:
        pass


def test_blank_order_id_or_sku_id_rows_are_skipped():
    rows = [["", "P1", "1730315401307982614", "creator_a", "Video", "Đã quyết toán"], ["O2", "P2", "", "creator_b", "Video", "Đã quyết toán"]]
    try:
        aff_channel_excel_to_parquet(make_xlsx_bytes(rows=rows))
        assert False, "expected AffChannelMappingError (no valid rows left)"
    except AffChannelMappingError:
        pass
