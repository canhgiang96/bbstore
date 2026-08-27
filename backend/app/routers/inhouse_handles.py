"""ID Inhouse ("Người sáng tạo Handle") CRUD — a plain named list of the
shop's own TikTok Creator Handles (seeded with bbstores.vn/bbcongso/
bbstores_forlady, but addable/removable), used by the Dashboard's "Kênh
nhỏ" classification (see query_engine._aff_channel_join) to tell the
shop's own main-channel Order Channel activity apart from an outside
creator's. Same shape as sales_channels.py — see _named_list_crud.py.
"""
from __future__ import annotations

from ..models import InhouseHandleCreateRequest, InhouseHandleOut, InhouseHandleUpdateRequest
from ._named_list_crud import create_named_list_router

router = create_named_list_router(
    prefix="/api/inhouse-handles",
    tag="inhouse-handles",
    table="inhouse_creator_handles",
    not_found_message="Không tìm thấy ID Inhouse.",
    empty_name_message="Tên người sáng tạo (Handle) không được để trống.",
    duplicate_message=lambda name: f'ID Inhouse "{name}" đã tồn tại.',
    response_model=InhouseHandleOut,
    create_request_model=InhouseHandleCreateRequest,
    update_request_model=InhouseHandleUpdateRequest,
)
