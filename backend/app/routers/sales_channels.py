"""Sales Channel ("Kênh bán hàng") CRUD — a plain named list (Shopee,
Lazada, TikTok Shop, ...), not a file-upload Report, so this talks to
Supabase directly via app.db's PostgREST helpers with no R2/parquet
involved at all.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..deps import get_current_user, require_admin
from ..models import SalesChannelCreateRequest, SalesChannelOut, SalesChannelUpdateRequest

router = APIRouter(prefix="/api/sales-channels", tags=["sales-channels"])


@router.get("", response_model=list[SalesChannelOut])
async def list_sales_channels(user: dict = Depends(get_current_user)):
    rows = await db.pg_select("sales_channels", {"order": "name.asc"})
    return [SalesChannelOut(**r) for r in rows]


@router.post("", response_model=SalesChannelOut, status_code=201)
async def create_sales_channel(body: SalesChannelCreateRequest, user: dict = Depends(require_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tên kênh bán hàng không được để trống.")
    existing = await db.pg_select_one("sales_channels", {"name": f"eq.{name}"})
    if existing:
        raise HTTPException(status_code=409, detail=f'Kênh bán hàng "{name}" đã tồn tại.')
    row = await db.pg_insert("sales_channels", {"name": name, "created_by": user["id"]})
    return SalesChannelOut(**row)


@router.patch("/{channel_id}", response_model=SalesChannelOut)
async def update_sales_channel(
    channel_id: str, body: SalesChannelUpdateRequest, user: dict = Depends(require_admin)
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tên kênh bán hàng không được để trống.")
    existing = await db.pg_select_one("sales_channels", {"id": f"eq.{channel_id}"})
    if not existing:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh bán hàng.")
    duplicate = await db.pg_select_one("sales_channels", {"name": f"eq.{name}"})
    if duplicate and duplicate["id"] != channel_id:
        raise HTTPException(status_code=409, detail=f'Kênh bán hàng "{name}" đã tồn tại.')
    rows = await db.pg_update("sales_channels", {"id": f"eq.{channel_id}"}, {"name": name})
    return SalesChannelOut(**rows[0])


@router.delete("/{channel_id}", status_code=204)
async def delete_sales_channel(channel_id: str, user: dict = Depends(require_admin)):
    existing = await db.pg_select_one("sales_channels", {"id": f"eq.{channel_id}"})
    if not existing:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh bán hàng.")
    await db.pg_delete("sales_channels", {"id": f"eq.{channel_id}"})
