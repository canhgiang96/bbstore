import io

import pyarrow.parquet as pq
from openpyxl import Workbook

from app.adjustments_to_parquet import (
    AdjustmentMappingError,
    adjustment_excel_to_parquet,
    detect_adjustment_mapping,
)

HEADERS = [
    "Mã giao dịch", "Ngày hoàn thành điều chỉnh đơn hàng", "Loại điều chỉnh | Mô tả",
    "Lý do điều chỉnh", "Số tiền điều chỉnh", "Mã đơn hàng liên quan", "Ngày hoàn thành thanh toán",
]

ROWS = [
    ["TXN1", "2026-02-01", "Hoàn tiền", "Hàng lỗi", -50000, "OID1", "2026-02-03"],
    ["TXN2", "2026-02-02", "Phạt", "Giao trễ", -20000, "OID2", "2026-02-04"],
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


def test_adjustment_excel_to_parquet_reads_all_fields():
    parquet_bytes, row_count, mapping = adjustment_excel_to_parquet(make_xlsx_bytes())
    assert row_count == 2
    assert mapping["transactionId"] == "Mã giao dịch"
    assert mapping["adjustmentDate"] == "Ngày hoàn thành điều chỉnh đơn hàng"
    assert mapping["paymentCompletedDate"] == "Ngày hoàn thành thanh toán"
    assert mapping["adjustmentType"] == "Loại điều chỉnh | Mô tả"
    assert mapping["reason"] == "Lý do điều chỉnh"
    assert mapping["amount"] == "Số tiền điều chỉnh"
    assert mapping["relatedOrderId"] == "Mã đơn hàng liên quan"

    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    t1 = df[df["transactionId"] == "TXN1"].iloc[0]
    assert t1["adjustmentType"] == "Hoàn tiền"
    assert t1["reason"] == "Hàng lỗi"
    assert t1["amount"] == -50000
    assert t1["relatedOrderId"] == "OID1"


def test_adjustment_dates_disambiguated_regardless_of_header_order():
    # Both date headers share the "Ngày hoàn thành..." prefix — the
    # detector must not conflate them in either header order.
    reordered = ["Mã giao dịch", "Ngày hoàn thành thanh toán", "Ngày hoàn thành điều chỉnh đơn hàng"]
    mapping = detect_adjustment_mapping(reordered)
    assert mapping["adjustmentDate"] == "Ngày hoàn thành điều chỉnh đơn hàng"
    assert mapping["paymentCompletedDate"] == "Ngày hoàn thành thanh toán"

    normal_order = ["Mã giao dịch", "Ngày hoàn thành điều chỉnh đơn hàng", "Ngày hoàn thành thanh toán"]
    mapping2 = detect_adjustment_mapping(normal_order)
    assert mapping2["adjustmentDate"] == "Ngày hoàn thành điều chỉnh đơn hàng"
    assert mapping2["paymentCompletedDate"] == "Ngày hoàn thành thanh toán"


def test_missing_both_identifying_columns_raises():
    # Neither "Mã giao dịch" nor "Mã đơn hàng liên quan" — no way to tell
    # a real row from a blank one, must reject.
    headers = ["Lý do điều chỉnh", "Số tiền điều chỉnh"]
    rows = [["Hàng lỗi", -1000]]
    try:
        adjustment_excel_to_parquet(make_xlsx_bytes(headers, rows))
        assert False, "expected AdjustmentMappingError"
    except AdjustmentMappingError:
        pass


# Real TikTok-style export (confirmed with the user 2026-09-03, file
# "ĐIỀU CHỈNH.xlsx") — no "Mã giao dịch" column at all, only "Mã đơn hàng
# liên quan", which legitimately repeats across separate adjustment events
# for the same order (different dates/reasons/amounts each time).
NO_TRANSACTION_ID_HEADERS = [
    "Ngày hoàn thành điều chỉnh đơn hàng", "Loại điều chỉnh | Mô tả",
    "Lý do điều chỉnh", "Số tiền điều chỉnh", "Mã đơn hàng liên quan", "Ngày hoàn thành thanh toán",
]

NO_TRANSACTION_ID_ROWS = [
    ["2026-01-01", "Trả hàng/ Hoàn tiền", "", -429952, "2512222YMRHBDE", "2025-12-27"],
    # Same "Mã đơn hàng liên quan" as above, but a distinct adjustment event.
    ["2026-01-11", "Chương trình Marketing", "", -32194, "2512222YMRHBDE", "2026-01-10"],
    ["", "", "", 0, "", ""],  # fully blank -> skipped
]


def test_file_without_transaction_id_column_converts_using_related_order_id():
    parquet_bytes, row_count, mapping = adjustment_excel_to_parquet(
        make_xlsx_bytes(NO_TRANSACTION_ID_HEADERS, NO_TRANSACTION_ID_ROWS)
    )
    assert row_count == 2  # the fully-blank row is skipped
    assert "transactionId" not in mapping
    assert mapping["relatedOrderId"] == "Mã đơn hàng liên quan"

    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert (df["transactionId"] == "").all()
    # Both rows for the same order kept as separate records, not deduped —
    # they're genuinely different adjustment events (verified against the
    # real file: different dates/reasons/amounts per row).
    assert df["relatedOrderId"].tolist() == ["2512222YMRHBDE", "2512222YMRHBDE"]
    assert df["amount"].tolist() == [-429952, -32194]


def test_blank_transaction_id_rows_are_skipped():
    rows = [["", "2026-02-01", "", "", -1000, "", ""], ["TXN2", "2026-02-02", "Phạt", "Giao trễ", -20000, "OID2", "2026-02-04"]]
    parquet_bytes, row_count, _ = adjustment_excel_to_parquet(make_xlsx_bytes(HEADERS, rows))
    assert row_count == 1
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert df["transactionId"].tolist() == ["TXN2"]


def test_only_transaction_id_column_present_still_converts():
    headers = ["Mã giao dịch"]
    rows = [["TXN1"], ["TXN2"]]
    parquet_bytes, row_count, mapping = adjustment_excel_to_parquet(make_xlsx_bytes(headers, rows))
    assert row_count == 2
    assert set(mapping) == {"transactionId"}
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert df["amount"].tolist() == [0.0, 0.0]
    assert df["adjustmentDate"].tolist() == ["", ""]
