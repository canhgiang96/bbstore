"""Router-level tests for _report_crud.py's shared CRUD factory — unlike
the pure-function tests elsewhere in this suite, these call the actual
FastAPI endpoint closures directly with app.db's PostgREST calls
monkeypatched, since a real Supabase isn't available in CI/locally.
"""
import httpx
import pytest

from app import db
from app.routers._report_crud import create_report_crud_router


def _make_router(list_fields):
    return create_report_crud_router(
        prefix="/api/reports", tag="reports", table="reports",
        list_fields=list_fields,
        original_key_fn=lambda report_id, filename: "x",
        parquet_key_fn=lambda report_id: "y",
        converter=lambda *a, **k: (b"", 0, {}),
        mapping_error=ValueError,
    )


def _find_route(router, method):
    return next(r for r in router.routes if r.path == "/api/reports" and method in r.methods)


def _postgrest_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.supabase.co/rest/v1/reports")
    response = httpx.Response(status_code, request=request, json={"message": message})
    return httpx.HTTPStatusError(message, request=request, response=response)


@pytest.mark.asyncio
async def test_list_reports_retries_without_locked_column_when_migration_not_run(monkeypatch):
    # A deploy adding "locked" to list_fields can land before its Supabase
    # migration is actually run — PostgREST then rejects the whole select=
    # list (not just the unknown column), which must degrade to the
    # pre-migration column set instead of 500ing the entire Report list.
    calls = []

    async def fake_pg_select(table, params=None):
        calls.append(params.get("select"))
        if params.get("select") and "locked" in params["select"]:
            raise _postgrest_error(400, "column reports.locked does not exist")
        return [{
            "id": "r1", "name": "Test", "uploaded_at": "2026-01-01T00:00:00Z",
            "uploaded_by": "u1", "row_count": 5, "status": "ready", "error_message": None,
        }]

    monkeypatch.setattr(db, "pg_select", fake_pg_select)

    router = _make_router("id,name,uploaded_at,uploaded_by,row_count,status,error_message,locked")
    list_route = _find_route(router, "GET")

    reports = await list_route.endpoint(user={"id": "u1"})

    assert len(calls) == 2
    assert "locked" in calls[0]
    assert "locked" not in calls[1]
    assert len(reports) == 1
    assert reports[0].locked is False  # ReportOut's default, column wasn't available


@pytest.mark.asyncio
async def test_list_reports_does_not_swallow_unrelated_errors(monkeypatch):
    # A 400 for a reason OTHER than the "locked" column (or a table with
    # "locked" not even in its list_fields) must still propagate, not
    # silently be treated as "retry without locked and hope for the best".
    async def fake_pg_select(table, params=None):
        raise _postgrest_error(400, "some other malformed request")

    monkeypatch.setattr(db, "pg_select", fake_pg_select)

    router = _make_router("id,name,uploaded_at,uploaded_by,row_count,status,error_message,locked")
    list_route = _find_route(router, "GET")

    with pytest.raises(httpx.HTTPStatusError):
        await list_route.endpoint(user={"id": "u1"})


@pytest.mark.asyncio
async def test_list_reports_propagates_non_400_errors(monkeypatch):
    async def fake_pg_select(table, params=None):
        raise _postgrest_error(500, "internal server error")

    monkeypatch.setattr(db, "pg_select", fake_pg_select)

    router = _make_router("id,name,uploaded_at,uploaded_by,row_count,status,error_message,locked")
    list_route = _find_route(router, "GET")

    with pytest.raises(httpx.HTTPStatusError):
        await list_route.endpoint(user={"id": "u1"})
