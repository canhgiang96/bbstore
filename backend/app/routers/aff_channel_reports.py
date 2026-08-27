"""Kênh AFF Report CRUD — built from _report_crud's shared factory, pointed
at the aff_channel_reports table/R2 prefix and aff_channel_excel_to_parquet().
Kênh AFF Reports (TikTok's affiliate_orders_*.xlsx export) exist solely to
supply (orderId, skuId) pairs for the Orders Dashboard's "Kênh nhỏ"
classification at query time (see query_engine._aff_channel_join) — no
per-report dashboard view of their own, no Kênh bán hàng concept either
(this Report type is inherently TikTok-only).
"""
from __future__ import annotations

from .. import storage
from ..aff_channel_to_parquet import AffChannelMappingError, aff_channel_excel_to_parquet
from ._report_crud import create_report_crud_router

router = create_report_crud_router(
    prefix="/api/aff-channel-reports",
    tag="aff-channel-reports",
    table="aff_channel_reports",
    list_fields="id,name,uploaded_at,uploaded_by,row_count,status,error_message",
    original_key_fn=storage.aff_channel_original_key,
    parquet_key_fn=storage.aff_channel_parquet_key,
    converter=aff_channel_excel_to_parquet,
    mapping_error=AffChannelMappingError,
)
