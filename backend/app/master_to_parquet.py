"""Excel -> Parquet conversion for one Master File Report.

One output row per source row (like Cashflow, not unpivoted like Combo).
Unlike Cashflow/Combo's simpler "first substring-or-exact match in header
order wins" detector, this needs real exact-match-priority scoring (same
shape as app.mapping.detect_mapping) because "SKU" is a literal substring of
"SKU phân loại" — relying on header order to disambiguate would be fragile.
"""
from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq

from .excel_to_parquet import read_excel_rows
from .mapping import score_headers
from .parsing import to_number

MASTER_KEYWORDS = {
    "sku": ["sku"],
    "warehouseType": ["phan loai kho onl", "phan loai kho"],
    "itemGroup": ["muc"],
    "productType": ["phan loai sp"],
    "unitCost": ["gia von"],
}


class MasterMappingError(ValueError):
    """Raised when the uploaded file is missing the SKU column."""


def detect_master_mapping(headers: list[str]) -> dict[str, str]:
    return score_headers(headers, MASTER_KEYWORDS)


def master_excel_to_parquet(file_like, sheet_name=0) -> tuple[bytes, int, dict]:
    """Returns (parquet_bytes, row_count, resolved_mapping).

    Each output row is {"sku": ..., "muc": ..., "phanLoaiSp": ...,
    "phanLoaiKho": ..., "giaVon": ...} — "sku" is the parent SKU (this is
    Master File's own "SKU" column, already the parent-level code — not
    "SKU phân loại"). Rows with a blank SKU are skipped.
    """
    raw_rows, headers = read_excel_rows(file_like, sheet_name=sheet_name)
    mapping = detect_master_mapping(headers)

    if "sku" not in mapping:
        raise MasterMappingError("Không tìm thấy cột SKU trong file.")

    sku_col = mapping["sku"]
    warehouse_col = mapping.get("warehouseType")
    item_group_col = mapping.get("itemGroup")
    product_type_col = mapping.get("productType")
    unit_cost_col = mapping.get("unitCost")

    rows = []
    for row in raw_rows:
        sku = str(row.get(sku_col, "") or "").strip()
        if not sku:
            continue
        rows.append({
            "sku": sku,
            "muc": str(row.get(item_group_col, "") or "").strip() if item_group_col else "",
            "phanLoaiSp": str(row.get(product_type_col, "") or "").strip() if product_type_col else "",
            "phanLoaiKho": str(row.get(warehouse_col, "") or "").strip() if warehouse_col else "",
            "giaVon": to_number(row.get(unit_cost_col)) if unit_cost_col else 0.0,
        })

    if not rows:
        raise MasterMappingError("Không có dòng dữ liệu hợp lệ nào (không đọc được SKU ở bất kỳ dòng nào).")

    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue(), len(rows), mapping
