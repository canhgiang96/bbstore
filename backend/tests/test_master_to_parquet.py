import io

import pyarrow.parquet as pq
from openpyxl import Workbook

from app.master_to_parquet import MasterMappingError, detect_master_mapping, master_excel_to_parquet

HEADERS = ["SKU", "SKU phân loại", "Màu", "Size", "Mục", "Phân loại SP", "Phân loại kho ONL", "Gía vốn"]

ROWS = [
    ["A100", "A100-1", "Đen", "M", "Áo", "Áo thun", "Kho HN", 50000],
    ["B200", "B200-1", "Trắng", "S", "Quần", "Quần short", "Kho HCM", 30000],
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


def test_master_excel_to_parquet_reads_all_fields():
    parquet_bytes, row_count, mapping = master_excel_to_parquet(make_xlsx_bytes())
    assert row_count == 2
    assert mapping["sku"] == "SKU"
    assert mapping["itemGroup"] == "Mục"
    assert mapping["productType"] == "Phân loại SP"
    assert mapping["warehouseType"] == "Phân loại kho ONL"
    assert mapping["unitCost"] == "Gía vốn"

    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    a100 = df[df["sku"] == "A100"].iloc[0]
    assert a100["muc"] == "Áo"
    assert a100["phanLoaiSp"] == "Áo thun"
    assert a100["phanLoaiKho"] == "Kho HN"
    assert a100["giaVon"] == 50000


def test_sku_disambiguated_from_sku_phan_loai_regardless_of_header_order():
    # "SKU" is a substring of "SKU phân loại" — the detector must still pick
    # the bare "SKU" column, not "SKU phân loại", in EITHER header order.
    reordered = ["SKU phân loại", "SKU", "Mục"]
    mapping = detect_master_mapping(reordered)
    assert mapping["sku"] == "SKU"

    normal_order = ["SKU", "SKU phân loại", "Mục"]
    mapping2 = detect_master_mapping(normal_order)
    assert mapping2["sku"] == "SKU"


def test_giá_von_spelling_also_matches():
    headers = ["SKU", "Giá vốn"]  # correct spelling, vs "Gía vốn" (typo) in HEADERS
    rows = [["A100", 12345]]
    _, _, mapping = master_excel_to_parquet(make_xlsx_bytes(headers, rows))
    assert mapping["unitCost"] == "Giá vốn"


def test_missing_sku_column_raises():
    headers = ["Mục", "Phân loại SP"]
    rows = [["Áo", "Áo thun"]]
    try:
        master_excel_to_parquet(make_xlsx_bytes(headers, rows))
        assert False, "expected MasterMappingError"
    except MasterMappingError:
        pass


def test_blank_sku_rows_are_skipped():
    rows = [["", "", "", "", "Áo", "Áo thun", "Kho HN", 50000], ["B200", "B200-1", "Trắng", "S", "Quần", "Quần short", "Kho HCM", 30000]]
    parquet_bytes, row_count, _ = master_excel_to_parquet(make_xlsx_bytes(HEADERS, rows))
    assert row_count == 1
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert df["sku"].tolist() == ["B200"]
