import io
import os
import tempfile

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
