"""Client for Supabase's PostgREST API.

Uses the service_role key over HTTP instead of a direct asyncpg connection,
so the only Supabase credentials the backend needs are SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY (no separate database password/connection string
to collect and manage). The service_role key bypasses Postgres RLS — that's
fine here since FastAPI's own route dependencies (see app/deps.py) are the
real authorization boundary; RLS is defense-in-depth only.
"""
from __future__ import annotations

import httpx

from .config import get_settings


def _headers(extra: dict | None = None) -> dict:
    s = get_settings()
    headers = {
        "apikey": s.supabase_service_role_key,
        "Authorization": f"Bearer {s.supabase_service_role_key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _rest_url(table: str) -> str:
    s = get_settings()
    return f"{s.supabase_url}/rest/v1/{table}"


async def pg_select(table: str, params: dict | None = None) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(_rest_url(table), headers=_headers(), params=params or {})
        r.raise_for_status()
        return r.json()


async def pg_select_one(table: str, params: dict) -> dict | None:
    rows = await pg_select(table, params)
    return rows[0] if rows else None


async def pg_insert(table: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        headers = _headers({"Prefer": "return=representation"})
        r = await client.post(_rest_url(table), headers=headers, json=data)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else {}


async def pg_update(table: str, params: dict, data: dict) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        headers = _headers({"Prefer": "return=representation"})
        r = await client.patch(_rest_url(table), headers=headers, params=params, json=data)
        r.raise_for_status()
        return r.json()


async def pg_delete(table: str, params: dict) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.delete(_rest_url(table), headers=_headers(), params=params)
        r.raise_for_status()
