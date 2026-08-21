import io
import os
import tempfile
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from openpyxl import Workbook

from app.excel_to_parquet import excel_to_parquet
from app.query_engine import run_rows_query, run_summary_query

HEADERS = [
    "Mã đơn hàng", "Ngày đặt hàng", "Trạng Thái Đơn Hàng", "Lý do hủy",
    "SKU phân loại hàng", "Tên sản phẩm", "Tên phân loại hàng",
    "Giá gốc", "Số lượng", "Số lượng sản phẩm được hoàn trả",
]

# Same 6-row dataset as test_excel_to_parquet, plus a category column so
# category filtering/breakdown can be exercised.
ROWS = [
    ["O1", "2026-02-01 00:01", "Đã hủy", "Giao hàng thất bại", "A100-1", "SP A", "Áo", 100000, 2, 0],
    ["O2", "2026-02-02 09:00", "Đã hủy", "Người mua đổi ý", "B200-2", "SP B", "Quần", 50000, 3, 0],
    ["O3", "2026-02-03 10:00", "Hoàn thành", "", "C300-1", "SP C", "Áo", 20000, 4, 4],
    ["O4", "2026-02-04 11:00", "Hoàn thành", "", "D400-3", "SP D", "Quần", 30000, 5, 2],
    ["O5", "2026-02-05 12:00", "Hoàn thành", "", "E500-1", "SP E", "Áo", 200000, 1, 0],
    ["O6", "2026-02-06 13:00", "Đang giao hàng", "", "F600-2", "SP F", "Quần", 80000, 2, 0],
]


@pytest.fixture
def parquet_path():
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for r in ROWS:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    parquet_bytes, row_count, mapping = excel_to_parquet(buf)
    assert row_count == 6

    fd, path = tempfile.mkstemp(suffix=".parquet")
    with os.fdopen(fd, "wb") as f:
        f.write(parquet_bytes)
    yield path
    os.remove(path)


def test_summary_kpis_match_expected(parquet_path):
    result = run_summary_query(parquet_path)
    kpis = result["kpis"]

    assert kpis["doanhSo"] == 940000
    assert kpis["gmv"] == 360000
    assert kpis["huyChuaXK"] == 150000
    assert kpis["huySauXK"] == 200000
    assert kpis["hoan"] == 230000
    assert kpis["rowCount"] == 6
    assert kpis["gmv"] + kpis["huyChuaXK"] + kpis["huySauXK"] + kpis["hoan"] == kpis["doanhSo"]


def test_summary_facets_unaffected_by_status_filter(parquet_path):
    result = run_summary_query(parquet_path, status="Hoàn thành")
    # Facets come from the whole report, not the filtered set.
    assert set(result["facets"]["statuses"]) == {
        "Hủy sau XK", "Hủy chưa XK", "Hoàn hàng", "Hoàn 1 phần", "Hoàn thành", "Đang giao",
    }
    assert set(result["facets"]["categories"]) == {"Áo", "Quần"}
    # But the KPI numbers themselves ARE filtered — only O5 has trangThai
    # exactly "Hoàn thành" (O3 is "Hoàn hàng", O4 is "Hoàn 1 phần" — distinct buckets).
    assert result["kpis"]["doanhSo"] == 200000


def test_summary_category_filter(parquet_path):
    result = run_summary_query(parquet_path, category="Áo")
    # O1 (100k*2=200k, Hủy sau XK), O3 (20k*4=80k, Hoàn hàng), O5 (200k*1=200k, Hoàn thành)
    assert result["kpis"]["doanhSo"] == 200000 + 80000 + 200000


def test_summary_to_date_is_inclusive_of_the_whole_day(parquet_path):
    # Regression: "Đến ngày" comes from <input type="date"> as a plain
    # "YYYY-MM-DD" (implicitly midnight). O1 is timestamped "2026-02-01
    # 00:01" — one minute past that midnight — so a naive "date" <= to_date
    # comparison would wrongly exclude it. to_date must mean end-of-day.
    result = run_summary_query(parquet_path, to_date="2026-02-01")
    assert result["kpis"]["rowCount"] == 1
    assert result["kpis"]["doanhSo"] == 200000  # O1 only: 100000 * 2

    # And it must not leak into the next day.
    result_before = run_summary_query(parquet_path, to_date="2026-01-31")
    assert result_before["kpis"]["rowCount"] == 0


def test_top_products_sorted_desc(parquet_path):
    result = run_summary_query(parquet_path)
    values = [p["value"] for p in result["topProducts"]]
    assert values == sorted(values, reverse=True)
    # SP A (100k*2) and SP E (200k*1) are tied at 200.000 for the top spot —
    # which one DuckDB returns first among ties isn't guaranteed, so just
    # confirm the top value itself is correct.
    assert result["topProducts"][0]["value"] == 200000
    assert result["topProducts"][0]["label"] in ("SP A", "SP E")


def test_rows_pagination(parquet_path):
    page1 = run_rows_query(parquet_path, page=1, page_size=4)
    assert page1["total"] == 6
    assert len(page1["rows"]) == 4

    page2 = run_rows_query(parquet_path, page=2, page_size=4)
    assert len(page2["rows"]) == 2

    ids_seen = {r["orderId"] for r in page1["rows"]} | {r["orderId"] for r in page2["rows"]}
    assert ids_seen == {"O1", "O2", "O3", "O4", "O5", "O6"}


def test_rows_search(parquet_path):
    result = run_rows_query(parquet_path, search="sp e")
    assert result["total"] == 1
    assert result["rows"][0]["orderId"] == "O5"


def test_rows_sort_by_doanh_so_desc(parquet_path):
    result = run_rows_query(parquet_path, sort="doanhSo", sort_dir="desc", page_size=10)
    values = [r["doanhSo"] for r in result["rows"]]
    assert values == sorted(values, reverse=True)


def test_rows_sql_injection_attempt_on_sort_is_ignored(parquet_path):
    # Not a real vulnerability test (sort isn't parameterizable in DuckDB),
    # just confirms the whitelist silently falls back instead of erroring
    # or executing arbitrary SQL.
    result = run_rows_query(parquet_path, sort='"date"; DROP TABLE x; --', page_size=10)
    assert result["total"] == 6


# ---- Multi-Report aggregation (Dashboard no longer pins to one Report — see
# the "xem bằng bộ lọc" change: it queries every ready Report's Parquet
# together and the date/category/status filters narrow the combined set) ----

MARCH_HEADERS = HEADERS
MARCH_ROWS = [
    ["O7", "2026-03-01 08:00", "Hoàn thành", "", "G700-1", "SP G", "Áo", 90000, 2, 0],
    ["O8", "2026-03-02 09:00", "Đang giao hàng", "", "H800-1", "SP H", "Quần", 60000, 1, 0],
]


@pytest.fixture
def parquet_path_march():
    wb = Workbook()
    ws = wb.active
    ws.append(MARCH_HEADERS)
    for r in MARCH_ROWS:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    parquet_bytes, row_count, mapping = excel_to_parquet(buf)
    assert row_count == 2

    fd, path = tempfile.mkstemp(suffix=".parquet")
    with os.fdopen(fd, "wb") as f:
        f.write(parquet_bytes)
    yield path
    os.remove(path)


def test_summary_aggregates_across_multiple_reports(parquet_path, parquet_path_march):
    result = run_summary_query([parquet_path, parquet_path_march])
    # Feb total (940.000) + March total (90000*2 + 60000*1 = 240.000)
    assert result["kpis"]["doanhSo"] == 940000 + 240000
    assert result["kpis"]["rowCount"] == 6 + 2


def test_summary_date_filter_narrows_across_reports(parquet_path, parquet_path_march):
    # Only March rows should count when filtered to March.
    result = run_summary_query([parquet_path, parquet_path_march], from_date="2026-03-01", to_date="2026-03-31")
    assert result["kpis"]["doanhSo"] == 240000
    assert result["kpis"]["rowCount"] == 2


def test_rows_pagination_across_multiple_reports(parquet_path, parquet_path_march):
    result = run_rows_query([parquet_path, parquet_path_march], page_size=100)
    assert result["total"] == 8
    ids_seen = {r["orderId"] for r in result["rows"]}
    assert ids_seen == {"O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8"}


def test_summary_empty_source_list_returns_zeroed_result():
    result = run_summary_query([])
    assert result["kpis"]["doanhSo"] == 0
    assert result["kpis"]["rowCount"] == 0
    assert result["facets"]["categories"] == []


def test_rows_empty_source_list_returns_empty_page():
    result = run_rows_query([], page_size=15)
    assert result["total"] == 0
    assert result["rows"] == []


def test_summary_nmv_is_gmv_when_no_discount_or_voucher(parquet_path):
    # parquet_path has no "Người bán trợ giá"/"Mã giảm giá của Shop"/"Số
    # tiền người mua thanh toán" columns mapped, so discount/voucher are 0
    # for every row -> NMV should equal GMV exactly.
    result = run_summary_query(parquet_path)
    assert result["kpis"]["nmv"] == result["kpis"]["gmv"] == 360000


DISCOUNT_HEADERS = HEADERS + ["Người bán trợ giá", "Mã giảm giá của Shop", "Số tiền người mua thanh toán"]

DISCOUNT_ROWS = [
    # Both rows share Mã đơn hàng "D1" — a single 2-line order. Both are
    # "Hoàn thành" (counts toward GMV/NMV). Shop voucher (10.000) is
    # repeated on both lines and must be prorated 40/60 by paid amount.
    ["D1", "2026-02-01 00:01", "Hoàn thành", "", "A100-1", "SP A", "Áo", 100000, 2, 0, 4000, 10000, 400000],
    ["D1", "2026-02-01 00:01", "Hoàn thành", "", "B200-1", "SP B", "Quần", 150000, 1, 0, 1500, 10000, 600000],
]


@pytest.fixture
def parquet_path_with_discounts():
    wb = Workbook()
    ws = wb.active
    ws.append(DISCOUNT_HEADERS)
    for r in DISCOUNT_ROWS:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    parquet_bytes, row_count, mapping = excel_to_parquet(buf)
    assert row_count == 2
    assert "sellerSubsidy" in mapping and "shopVoucher" in mapping

    fd, path = tempfile.mkstemp(suffix=".parquet")
    with os.fdopen(fd, "wb") as f:
        f.write(parquet_bytes)
    yield path
    os.remove(path)


def test_summary_nmv_nets_out_discount_and_voucher(parquet_path_with_discounts):
    result = run_summary_query(parquet_path_with_discounts)
    kpis = result["kpis"]
    # doanhSo: D1 = 100000*2 = 200000, D2 = 150000*1 = 150000 -> 350000 total, all GMV.
    assert kpis["doanhSo"] == kpis["gmv"] == 350000
    # discount: D1 = 4000/2*2 = 4000, D2 = 1500/1*1 = 1500 -> 5500.
    # voucher: D1 ratio 400000/1000000=0.4 -> 10000*0.4/2*2=4000;
    #          D2 ratio 600000/1000000=0.6 -> 10000*0.6/1*1=6000 -> 10000.
    assert kpis["nmv"] == 350000 - 5500 - 10000
    assert kpis["discount"] == 5500
    assert kpis["voucher"] == 10000


def _write_raw_parquet(rows: list[dict]) -> str:
    """Builds a Parquet file directly (bypassing excel_to_parquet) so the
    schema has exactly the given columns — used to simulate a Report that
    was converted before "discount"/"voucher" existed.
    """
    table = pa.Table.from_pylist(rows)
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    pq.write_table(table, path)
    return path


@pytest.fixture
def old_schema_parquet_path():
    # No "discount"/"voucher" keys at all — mirrors a pre-feature Report.
    rows = [
        {"date": datetime(2026, 2, 1), "orderId": "OLD1", "sku": "X1",
         "skuVariant": "X1-1", "product": "SP X", "category": "Áo", "customer": "(Không rõ)",
         "quantity": 2.0, "returnedQty": 0.0, "soLuongThuc": 2.0, "price": 0.0, "originalPrice": 100000.0,
         "revenue": 0.0, "doanhSo": 200000.0, "status": "Hoàn thành", "trangThai": "Hoàn thành"},
    ]
    path = _write_raw_parquet(rows)
    yield path
    os.remove(path)


def test_summary_old_schema_report_without_discount_columns_does_not_error(old_schema_parquet_path):
    result = run_summary_query(old_schema_parquet_path)
    assert result["kpis"]["doanhSo"] == 200000
    # No discount/voucher columns at all -> NMV falls back to GMV.
    assert result["kpis"]["nmv"] == result["kpis"]["gmv"] == 200000


def test_rows_old_schema_report_without_discount_columns_returns_zero(old_schema_parquet_path):
    result = run_rows_query(old_schema_parquet_path, page_size=10)
    assert result["total"] == 1
    assert result["rows"][0]["discount"] == 0
    assert result["rows"][0]["voucher"] == 0


def test_summary_mixed_old_and_new_schema_reports_via_union_by_name(
    old_schema_parquet_path, parquet_path_with_discounts
):
    # The Dashboard aggregate endpoint always passes a list of every ready
    # Report's Parquet — some old (no discount/voucher columns), some new.
    # This must not error, and the old Report's rows must contribute 0 to
    # discount/voucher instead of poisoning the whole aggregate with NULLs.
    result = run_summary_query([old_schema_parquet_path, parquet_path_with_discounts])
    assert result["kpis"]["rowCount"] == 1 + 2
    assert result["kpis"]["doanhSo"] == 200000 + 350000
    assert result["kpis"]["nmv"] == (200000 - 0 - 0) + (350000 - 5500 - 10000)


def test_rows_mixed_old_and_new_schema_reports_via_union_by_name(
    old_schema_parquet_path, parquet_path_with_discounts
):
    result = run_rows_query([old_schema_parquet_path, parquet_path_with_discounts], page_size=10)
    assert result["total"] == 3
    by_sku = {r["skuVariant"]: r for r in result["rows"]}
    assert by_sku["X1-1"]["discount"] == 0
    assert by_sku["X1-1"]["voucher"] == 0
    assert by_sku["A100-1"]["discount"] == 4000
