import io

import pyarrow.parquet as pq
from openpyxl import Workbook

from app.cashflow_to_parquet import CashflowMappingError, cashflow_excel_to_parquet

HEADERS = ["Mã giao dịch", "Mã đơn hàng", "Phí hoa hồng Tiếp thị liên kết", "Ghi chú"]

ROWS = [
    ["T1", "O1", -5000, ""],
    ["T2", "O2", -3200, ""],
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


def test_cashflow_excel_to_parquet_flips_negative_to_positive():
    parquet_bytes, row_count, mapping = cashflow_excel_to_parquet(make_xlsx_bytes())
    assert row_count == 2
    assert mapping["orderId"] == "Mã đơn hàng"
    assert mapping["phiAff"] == "Phí hoa hồng Tiếp thị liên kết"

    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    by_order = df.set_index("orderId")["phiAff"].to_dict()
    assert by_order["O1"] == 5000
    assert by_order["O2"] == 3200


def test_missing_order_id_column_raises():
    headers = ["Mã giao dịch", "Phí hoa hồng Tiếp thị liên kết"]
    rows = [["T1", -1000]]
    try:
        cashflow_excel_to_parquet(make_xlsx_bytes(headers, rows))
        assert False, "expected CashflowMappingError"
    except CashflowMappingError:
        pass


def test_missing_phi_aff_column_raises():
    headers = ["Mã giao dịch", "Mã đơn hàng"]
    rows = [["T1", "O1"]]
    try:
        cashflow_excel_to_parquet(make_xlsx_bytes(headers, rows))
        assert False, "expected CashflowMappingError"
    except CashflowMappingError:
        pass


def test_blank_order_id_rows_are_skipped():
    headers = HEADERS
    rows = [["T1", "", -1000, ""], ["T2", "O2", -2000, ""]]
    parquet_bytes, row_count, _ = cashflow_excel_to_parquet(make_xlsx_bytes(headers, rows))
    assert row_count == 1
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert df["orderId"].tolist() == ["O2"]
