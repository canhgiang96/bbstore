"""Excel -> Parquet conversion for one Combo Report.

Unlike Cashflow (one output row per source row), this UNPIVOTS: each source
row describes up to 3 sub-SKUs (SKU1/SKU2/SKU3) that make up one combo
product, so it becomes up to 3 output rows — one per non-blank sub-SKU — used
by query_engine.py to explode a matching Orders row into its components.
"""
from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq

from .excel_to_parquet import read_excel_rows
from .mapping import normalize_header
from .parsing import to_number

COMBO_KEYWORDS = {
    "skuCombo": ["sku combo"],
    "sku1": ["sku1"],
    "sku2": ["sku2"],
    "sku3": ["sku3"],
    "ratio1": ["ti le sku 1"],
    "ratio2": ["ti le sku 2"],
    "ratio3": ["ti le sku 3"],
}

SUB_SKU_SLOTS = [
    ("sku1", "ratio1", 1),
    ("sku2", "ratio2", 2),
    ("sku3", "ratio3", 3),
]


class ComboMappingError(ValueError):
    """Raised when the uploaded file is missing the SKU COMBO column."""


def detect_combo_mapping(headers: list[str]) -> dict[str, str]:
    normalized = [(h, normalize_header(h)) for h in headers]
    result: dict[str, str] = {}
    for field, keywords in COMBO_KEYWORDS.items():
        for h, n in normalized:
            for w in keywords:
                if n == w or w in n:
                    result[field] = h
                    break
            if field in result:
                break
    return result


def combo_excel_to_parquet(file_like, sheet_name=0) -> tuple[bytes, int, dict]:
    """Returns (parquet_bytes, row_count, resolved_mapping).

    Each output row is {"skuCombo": ..., "subSku": ..., "ratio": ..., "slot": 1|2|3}
    — one per non-blank SKU1/SKU2/SKU3 on a source row. "Tỉ lệ SKU n" is
    already a decimal fraction in the source file (0.5 == 50%), used as-is
    (confirmed with the user — no /100 conversion).
    """
    raw_rows, headers = read_excel_rows(file_like, sheet_name=sheet_name)
    mapping = detect_combo_mapping(headers)

    if "skuCombo" not in mapping:
        raise ComboMappingError("Không tìm thấy cột SKU COMBO trong file.")

    combo_col = mapping["skuCombo"]

    rows = []
    for row in raw_rows:
        sku_combo = str(row.get(combo_col, "") or "").strip()
        if not sku_combo:
            continue
        for sku_field, ratio_field, slot in SUB_SKU_SLOTS:
            sku_col = mapping.get(sku_field)
            if not sku_col:
                continue
            sub_sku = str(row.get(sku_col, "") or "").strip()
            if not sub_sku:
                continue
            ratio_col = mapping.get(ratio_field)
            ratio = to_number(row.get(ratio_col)) if ratio_col else 0.0
            rows.append({"skuCombo": sku_combo, "subSku": sub_sku, "ratio": ratio, "slot": slot})

    if not rows:
        raise ComboMappingError("Không có dòng dữ liệu hợp lệ nào (không đọc được SKU COMBO ở bất kỳ dòng nào).")

    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue(), len(rows), mapping
