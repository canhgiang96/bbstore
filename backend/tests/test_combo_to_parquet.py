import io

import pyarrow.parquet as pq
from openpyxl import Workbook

from app.combo_to_parquet import ComboMappingError, combo_excel_to_parquet

HEADERS = ["PHÂN LOẠI", "Tỉ lệ SKU 1", "Tỉ lệ SKU 2", "Tỉ lệ SKU 3", "SKU1", "SKU2", "SKU3", "SKU COMBO"]

ROWS = [
    # 2-component combo (SKU3 blank).
    ["Combo áo quần", 0.4, 0.6, "", "A100-1", "B200-1", "", "COMBO-AQ"],
    # 3-component combo.
    ["Combo 3 món", 0.2, 0.3, 0.5, "C300-1", "D400-1", "E500-1", "COMBO-3"],
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


def test_combo_excel_to_parquet_unpivots_non_blank_skus():
    parquet_bytes, row_count, mapping = combo_excel_to_parquet(make_xlsx_bytes())
    assert row_count == 5  # 2 (COMBO-AQ) + 3 (COMBO-3)
    assert mapping["skuCombo"] == "SKU COMBO"
    assert mapping["sku1"] == "SKU1"
    assert mapping["ratio1"] == "Tỉ lệ SKU 1"

    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()

    aq = df[df["skuCombo"] == "COMBO-AQ"].sort_values("slot")
    assert list(aq["subSku"]) == ["A100-1", "B200-1"]
    assert list(aq["ratio"]) == [0.4, 0.6]
    assert list(aq["slot"]) == [1, 2]

    combo3 = df[df["skuCombo"] == "COMBO-3"].sort_values("slot")
    assert list(combo3["subSku"]) == ["C300-1", "D400-1", "E500-1"]
    assert list(combo3["ratio"]) == [0.2, 0.3, 0.5]
    assert list(combo3["slot"]) == [1, 2, 3]


def test_missing_sku_combo_column_raises():
    headers = ["SKU1", "SKU2", "Tỉ lệ SKU 1", "Tỉ lệ SKU 2"]
    rows = [["A100-1", "B200-1", 0.5, 0.5]]
    try:
        combo_excel_to_parquet(make_xlsx_bytes(headers, rows))
        assert False, "expected ComboMappingError"
    except ComboMappingError:
        pass


def test_blank_sku_combo_rows_are_skipped():
    rows = [["", 0.5, 0.5, "", "A100-1", "B200-1", "", ""], ["Combo B", 1.0, "", "", "C300-1", "", "", "COMBO-B"]]
    parquet_bytes, row_count, _ = combo_excel_to_parquet(make_xlsx_bytes(HEADERS, rows))
    assert row_count == 1
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert df["skuCombo"].tolist() == ["COMBO-B"]


def test_row_with_no_sub_skus_contributes_nothing():
    rows = [["Combo empty", "", "", "", "", "", "", "COMBO-EMPTY"], ["Combo B", 1.0, "", "", "C300-1", "", "", "COMBO-B"]]
    parquet_bytes, row_count, _ = combo_excel_to_parquet(make_xlsx_bytes(HEADERS, rows))
    assert row_count == 1
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert df["skuCombo"].tolist() == ["COMBO-B"]
