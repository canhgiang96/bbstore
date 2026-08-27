"""Sales Channel ("Kênh bán hàng") CRUD — a plain named list (Shopee,
Lazada, TikTok Shop, ...), not a file-upload Report, so this talks to
Supabase directly via app.db's PostgREST helpers with no R2/parquet
involved at all. See _named_list_crud.py for the shared factory (also used
by inhouse_handles.py's "ID Inhouse" list).
"""
from __future__ import annotations

from ..models import SalesChannelCreateRequest, SalesChannelOut, SalesChannelUpdateRequest
from ._named_list_crud import create_named_list_router

router = create_named_list_router(
    prefix="/api/sales-channels",
    tag="sales-channels",
    table="sales_channels",
    not_found_message="Không tìm thấy kênh bán hàng.",
    empty_name_message="Tên kênh bán hàng không được để trống.",
    duplicate_message=lambda name: f'Kênh bán hàng "{name}" đã tồn tại.',
    response_model=SalesChannelOut,
    create_request_model=SalesChannelCreateRequest,
    update_request_model=SalesChannelUpdateRequest,
)
