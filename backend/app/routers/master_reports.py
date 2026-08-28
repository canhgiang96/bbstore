"""Master File Report CRUD — built from _report_crud's shared factory,
pointed at the master_reports table/R2 prefix and master_excel_to_parquet().
Master File Reports exist solely to look up cost/category data for Orders
rows at query time (see query_engine.py) — there's no per-report dashboard
view for them.
"""
from __future__ import annotations

from .. import storage
from ..master_to_parquet import MasterMappingError, master_excel_to_parquet
from ._report_crud import create_report_crud_router

router = create_report_crud_router(
    prefix="/api/master-reports",
    tag="master-reports",
    table="master_reports",
    list_fields="id,name,uploaded_at,uploaded_by,row_count,status,error_message,locked",
    original_key_fn=storage.master_original_key,
    parquet_key_fn=storage.master_parquet_key,
    converter=master_excel_to_parquet,
    mapping_error=MasterMappingError,
)
