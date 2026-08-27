"""Factory for the "plain named list — admin add/rename/delete, no file
upload" CRUD pattern shared by sales_channels.py ("Kênh bán hàng") and
inhouse_handles.py ("ID Inhouse"). Mirrors _report_crud.py's factory for
the file-upload Report pattern, one level simpler since there's no R2/
parquet/background-processing involved at all — just 4 endpoints straight
over app.db's PostgREST helpers.

NOT itself an included router — main.py includes each concrete module's
`router`, not this one.
"""
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..deps import get_current_user, require_admin


def create_named_list_router(
    *,
    prefix: str,
    tag: str,
    table: str,
    not_found_message: str,
    empty_name_message: str,
    duplicate_message: Callable[[str], str],
    response_model,
    create_request_model,
    update_request_model,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    @router.get("", response_model=list[response_model])
    async def list_items(user: dict = Depends(get_current_user)):
        rows = await db.pg_select(table, {"order": "name.asc"})
        return [response_model(**r) for r in rows]

    @router.post("", response_model=response_model, status_code=201)
    async def create_item(body: create_request_model, user: dict = Depends(require_admin)):
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail=empty_name_message)
        existing = await db.pg_select_one(table, {"name": f"eq.{name}"})
        if existing:
            raise HTTPException(status_code=409, detail=duplicate_message(name))
        row = await db.pg_insert(table, {"name": name, "created_by": user["id"]})
        return response_model(**row)

    @router.patch("/{item_id}", response_model=response_model)
    async def update_item(item_id: str, body: update_request_model, user: dict = Depends(require_admin)):
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail=empty_name_message)
        existing = await db.pg_select_one(table, {"id": f"eq.{item_id}"})
        if not existing:
            raise HTTPException(status_code=404, detail=not_found_message)
        duplicate = await db.pg_select_one(table, {"name": f"eq.{name}"})
        if duplicate and duplicate["id"] != item_id:
            raise HTTPException(status_code=409, detail=duplicate_message(name))
        rows = await db.pg_update(table, {"id": f"eq.{item_id}"}, {"name": name})
        return response_model(**rows[0])

    @router.delete("/{item_id}", status_code=204)
    async def delete_item(item_id: str, user: dict = Depends(require_admin)):
        existing = await db.pg_select_one(table, {"id": f"eq.{item_id}"})
        if not existing:
            raise HTTPException(status_code=404, detail=not_found_message)
        await db.pg_delete(table, {"id": f"eq.{item_id}"})

    return router
