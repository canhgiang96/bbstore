"""Cashflow (Dòng tiền) Report CRUD — built from _report_crud's shared
factory, pointed at the cashflow_reports table/R2 prefix and the smaller
cashflow_excel_to_parquet() conversion. Cashflow Reports exist solely to
supply Phí AFF for the Orders Dashboard's query-time join (see
query_engine.py) — there's no per-report dashboard view for them.
"""
from __future__ import annotations

from .. import storage
from ..cashflow_to_parquet import CashflowMappingError, cashflow_excel_to_parquet
from ._report_crud import create_report_crud_router

router = create_report_crud_router(
    prefix="/api/cashflow-reports",
    tag="cashflow-reports",
    table="cashflow_reports",
    list_fields="id,name,uploaded_at,uploaded_by,row_count,status,error_message,sales_channel_id",
    original_key_fn=storage.cashflow_original_key,
    parquet_key_fn=storage.cashflow_parquet_key,
    converter=cashflow_excel_to_parquet,
    mapping_error=CashflowMappingError,
    has_channel=True,
)
