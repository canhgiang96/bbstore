"""Combo Report CRUD — built from _report_crud's shared factory, pointed at
the combo_reports table/R2 prefix and combo_excel_to_parquet(). Combo
Reports exist solely to explode matching Orders rows into their sub-SKU
components at query time (see query_engine.py) — there's no per-report
dashboard view for them.
"""
from __future__ import annotations

from .. import storage
from ..combo_to_parquet import ComboMappingError, combo_excel_to_parquet
from ._report_crud import create_report_crud_router

router = create_report_crud_router(
    prefix="/api/combo-reports",
    tag="combo-reports",
    table="combo_reports",
    list_fields="id,name,uploaded_at,uploaded_by,row_count,status,error_message,locked",
    original_key_fn=storage.combo_original_key,
    parquet_key_fn=storage.combo_parquet_key,
    converter=combo_excel_to_parquet,
    mapping_error=ComboMappingError,
)
