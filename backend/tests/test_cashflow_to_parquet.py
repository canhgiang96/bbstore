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


def test_shopee_style_file_has_zero_platform_fee():
    # Shopee's Phí sàn comes from the Orders file itself (fixedFee/
    # serviceFee/transactionFee), not from Cashflow — platformFee must
    # stay 0 for a Shopee-shaped Cashflow file.
    parquet_bytes, _, _ = cashflow_excel_to_parquet(make_xlsx_bytes())
    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    assert (df["platformFee"] == 0).all()


# TikTok's "income" export headers/values (confirmed with the user against
# a real file, 2026-08-26/27) — Phí AFF = Hoa hồng liên kết + Hoa hồng
# liên kết Quảng cáo cửa hàng; Phí sàn = Tổng phí minus those two minus
# the two withheld-tax columns. All stored negative in the source file.
TIKTOK_HEADERS = [
    "ID đơn hàng/điều chỉnh", "Loại giao dịch", "Tổng phí",
    "Hoa hồng liên kết", "Hoa hồng liên kết Quảng cáo cửa hàng",
    "Thuế GTGT do TikTok Shop khấu trừ", "Thuế TNCN do TikTok Shop khấu trừ",
]

TIKTOK_ROWS = [
    # T1: no affiliate involved — Phí sàn = toàn bộ Tổng phí (trừ thuế).
    ["T1", "Đơn hàng", -98793, 0, 0, -3247, -1624],
    # T2: has affiliate commission from both sources.
    ["T2", "Đơn hàng", -78660, -17860, -3637, -3247, -1624],
    # T3: a "Khoản bồi hoàn của nền tảng" adjustment row — must be excluded.
    ["T3", "Khoản bồi hoàn của nền tảng", 50000, 0, 0, 0, 0],
]


def make_tiktok_xlsx_bytes():
    return make_xlsx_bytes(TIKTOK_HEADERS, TIKTOK_ROWS)


def test_tiktok_style_file_computes_phi_aff_and_platform_fee():
    parquet_bytes, row_count, mapping = cashflow_excel_to_parquet(make_tiktok_xlsx_bytes())
    assert row_count == 2  # T3 excluded (not a real order transaction)
    assert mapping["orderId"] == "ID đơn hàng/điều chỉnh"
    assert mapping["affiliateCommission"] == "Hoa hồng liên kết"
    assert mapping["affiliateAdsCommission"] == "Hoa hồng liên kết Quảng cáo cửa hàng"
    assert mapping["totalFee"] == "Tổng phí"

    df = pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()
    by_order = df.set_index("orderId")

    t1 = by_order.loc["T1"]
    assert t1["phiAff"] == 0
    assert t1["platformFee"] == 98793 - 3247 - 1624

    t2 = by_order.loc["T2"]
    assert t2["phiAff"] == 17860 + 3637
    assert t2["platformFee"] == 78660 - 17860 - 3637 - 3247 - 1624

    assert "T3" not in by_order.index
