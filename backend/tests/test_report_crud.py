"""Router-level tests for _report_crud.py's shared CRUD factory — unlike
the pure-function tests elsewhere in this suite, these call the actual
FastAPI endpoint closures directly with app.db's PostgREST calls
monkeypatched, since a real Supabase isn't available in CI/locally.
"""
import httpx
import pytest
from fastapi import HTTPException

from app import db, storage
from app.routers import _report_crud
from app.routers._report_crud import create_report_crud_router


def _make_router(list_fields="id,name,uploaded_at,uploaded_by,row_count,status,error_message", **kwargs):
    return create_report_crud_router(
        prefix="/api/reports", tag="reports", table="reports",
        list_fields=list_fields,
        original_key_fn=lambda report_id, filename: "x",
        parquet_key_fn=lambda report_id: "y",
        converter=kwargs.pop("converter", lambda *a, **k: (b"", 0, {})),
        mapping_error=kwargs.pop("mapping_error", ValueError),
        **kwargs,
    )


def _find_route(router, method, path="/api/reports"):
    return next(r for r in router.routes if r.path == path and method in r.methods)


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
    # (end of test_list_reports_propagates_non_400_errors)


# --- POST /{report_id}/reconvert ---------------------------------------
# Lets an already-converted Report's stored Parquet catch up with a
# business-rule/formula fix (e.g. the Piship rate change, the Voucher
# proration fix) without the previous workaround of re-picking the same
# Kênh bán hàng just to trigger reconversion as a PATCH side effect.


@pytest.mark.asyncio
async def test_reconvert_report_success(monkeypatch):
    updates = []

    async def fake_pg_select_one(table, params=None):
        assert table == "reports"
        return {"id": "r1", "original_xlsx_key": "orig/r1.xlsx", "mapping": {"orderId": "Mã đơn hàng"}}

    async def fake_pg_update(table, params, data):
        updates.append((table, params, data))

    def fake_download_to_path(key, path):
        assert key == "orig/r1.xlsx"

    uploaded = {}

    def fake_upload_bytes(key, data, content_type):
        uploaded["key"] = key
        uploaded["data"] = data

    invalidated = []

    def fake_converter(path, mapping_override=None):
        assert mapping_override == {"orderId": "Mã đơn hàng"}
        return b"parquet-bytes", 7, {"orderId": "Mã đơn hàng"}

    monkeypatch.setattr(db, "pg_select_one", fake_pg_select_one)
    monkeypatch.setattr(db, "pg_update", fake_pg_update)
    monkeypatch.setattr(storage, "download_to_path", fake_download_to_path)
    monkeypatch.setattr(storage, "upload_bytes", fake_upload_bytes)
    monkeypatch.setattr(_report_crud, "invalidate_local_parquet_cache", lambda report_id: invalidated.append(report_id))

    router = _make_router(converter=fake_converter, supports_mapping_override=True)
    route = _find_route(router, "POST", "/api/reports/{report_id}/reconvert")

    result = await route.endpoint("r1", user={"id": "u1"})

    assert result == {"ok": True, "rowCount": 7}
    assert uploaded["key"] == "y"  # from parquet_key_fn stub
    assert invalidated == ["r1"]
    assert updates == [(
        "reports", {"id": "eq.r1"},
        {"status": "ready", "row_count": 7, "mapping": {"orderId": "Mã đơn hàng"}, "parquet_key": "y"},
    )]


@pytest.mark.asyncio
async def test_reconvert_report_missing_report_404(monkeypatch):
    async def fake_pg_select_one(table, params=None):
        return None

    monkeypatch.setattr(db, "pg_select_one", fake_pg_select_one)

    router = _make_router()
    route = _find_route(router, "POST", "/api/reports/{report_id}/reconvert")

    with pytest.raises(HTTPException) as exc_info:
        await route.endpoint("missing", user={"id": "u1"})

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_reconvert_report_processing_returns_409(monkeypatch):
    # original_xlsx_key is written at upload time, before the background
    # conversion task actually runs — a Report can be "processing" while
    # already having original_xlsx_key set. Firing /reconvert in that
    # window must not start a second, uncoordinated conversion.
    async def fake_pg_select_one(table, params=None):
        return {"id": "r1", "original_xlsx_key": "orig/r1.xlsx", "mapping": {}, "status": "processing"}

    monkeypatch.setattr(db, "pg_select_one", fake_pg_select_one)

    router = _make_router()
    route = _find_route(router, "POST", "/api/reports/{report_id}/reconvert")

    with pytest.raises(HTTPException) as exc_info:
        await route.endpoint("r1", user={"id": "u1"})

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_reconvert_report_does_not_pass_mapping_override_when_unsupported(monkeypatch):
    # Regression test: only excel_to_parquet (Orders) accepts a
    # mapping_override kwarg — the other 5 real converters (cashflow,
    # combo, master, adjustments, aff-channel) are plain (file_like,
    # sheet_name=0) with no **kwargs, so passing mapping_override to them
    # raised TypeError -> unhandled 500 before this was fixed. Uses a
    # converter with that exact restrictive signature (no supports_
    # mapping_override on the router, matching those 5 real report types)
    # to prove the kwarg is never sent.
    async def fake_pg_select_one(table, params=None):
        return {"id": "r1", "original_xlsx_key": "orig/r1.xlsx", "mapping": {"orderId": "Mã đơn hàng"}}

    async def fake_pg_update(table, params, data):
        pass

    def restrictive_converter(file_like, sheet_name=0):
        return b"x", 3, {}

    monkeypatch.setattr(db, "pg_select_one", fake_pg_select_one)
    monkeypatch.setattr(db, "pg_update", fake_pg_update)
    monkeypatch.setattr(storage, "download_to_path", lambda key, path: None)
    monkeypatch.setattr(storage, "upload_bytes", lambda key, data, content_type: None)
    monkeypatch.setattr(_report_crud, "invalidate_local_parquet_cache", lambda report_id: None)

    router = _make_router(converter=restrictive_converter)  # supports_mapping_override defaults False
    route = _find_route(router, "POST", "/api/reports/{report_id}/reconvert")

    result = await route.endpoint("r1", user={"id": "u1"})

    assert result == {"ok": True, "rowCount": 3}


@pytest.mark.asyncio
async def test_reconvert_report_resets_status_to_ready_from_failed(monkeypatch):
    # A previously-failed Report (status="failed", but original_xlsx_key
    # was already uploaded before the failure) whose reconvert now
    # succeeds must flip back to "ready" and get a parquet_key, or it stays
    # invisible to the Dashboard forever despite having valid data (see
    # routers/dashboard.py's _all_ready_*_parquet_paths, which filters on
    # both status=ready AND a truthy parquet_key).
    async def fake_pg_select_one(table, params=None):
        return {
            "id": "r1", "original_xlsx_key": "orig/r1.xlsx", "mapping": {},
            "status": "failed", "error_message": "Lỗi cũ",
        }

    updates = []

    async def fake_pg_update(table, params, data):
        updates.append(data)

    def fake_converter(path):
        return b"x", 5, {"orderId": "Mã đơn hàng"}

    monkeypatch.setattr(db, "pg_select_one", fake_pg_select_one)
    monkeypatch.setattr(db, "pg_update", fake_pg_update)
    monkeypatch.setattr(storage, "download_to_path", lambda key, path: None)
    monkeypatch.setattr(storage, "upload_bytes", lambda key, data, content_type: None)
    monkeypatch.setattr(_report_crud, "invalidate_local_parquet_cache", lambda report_id: None)

    router = _make_router(converter=fake_converter)
    route = _find_route(router, "POST", "/api/reports/{report_id}/reconvert")

    result = await route.endpoint("r1", user={"id": "u1"})

    assert result == {"ok": True, "rowCount": 5}
    assert updates == [{"status": "ready", "row_count": 5, "mapping": {"orderId": "Mã đơn hàng"}, "parquet_key": "y"}]


@pytest.mark.asyncio
async def test_reconvert_report_converter_crash_becomes_400(monkeypatch):
    # Mirrors _process_report's broad safety net (a bad/corrupt file must
    # never crash the request) — before this, only mapping_error was
    # caught, so any other converter exception propagated as an unhandled
    # 500.
    async def fake_pg_select_one(table, params=None):
        return {"id": "r1", "original_xlsx_key": "orig/r1.xlsx", "mapping": {}}

    def crashing_converter(path):
        raise ValueError("dữ liệu hỏng")

    monkeypatch.setattr(db, "pg_select_one", fake_pg_select_one)
    monkeypatch.setattr(storage, "download_to_path", lambda key, path: None)

    router = _make_router(converter=crashing_converter)
    route = _find_route(router, "POST", "/api/reports/{report_id}/reconvert")

    with pytest.raises(HTTPException) as exc_info:
        await route.endpoint("r1", user={"id": "u1"})

    assert exc_info.value.status_code == 400
    assert "dữ liệu hỏng" in exc_info.value.detail


@pytest.mark.asyncio
async def test_reconvert_report_without_original_file_409(monkeypatch):
    async def fake_pg_select_one(table, params=None):
        return {"id": "r1", "original_xlsx_key": None, "mapping": {}}

    monkeypatch.setattr(db, "pg_select_one", fake_pg_select_one)

    router = _make_router()
    route = _find_route(router, "POST", "/api/reports/{report_id}/reconvert")

    with pytest.raises(HTTPException) as exc_info:
        await route.endpoint("r1", user={"id": "u1"})

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_reconvert_report_mapping_error_becomes_400(monkeypatch):
    class FakeMappingError(ValueError):
        pass

    async def fake_pg_select_one(table, params=None):
        return {"id": "r1", "original_xlsx_key": "orig/r1.xlsx", "mapping": {}}

    def fake_converter(path, mapping_override=None):
        raise FakeMappingError("Không tìm thấy cột Mã đơn hàng")

    monkeypatch.setattr(db, "pg_select_one", fake_pg_select_one)
    monkeypatch.setattr(storage, "download_to_path", lambda key, path: None)

    router = _make_router(converter=fake_converter, mapping_error=FakeMappingError)
    route = _find_route(router, "POST", "/api/reports/{report_id}/reconvert")

    with pytest.raises(HTTPException) as exc_info:
        await route.endpoint("r1", user={"id": "u1"})

    assert exc_info.value.status_code == 400
    assert "Mã đơn hàng" in exc_info.value.detail


@pytest.mark.asyncio
async def test_reconvert_report_channel_aware_passes_current_channel_name(monkeypatch):
    async def fake_pg_select_one(table, params=None):
        if table == "reports":
            return {
                "id": "r1", "original_xlsx_key": "orig/r1.xlsx", "mapping": {},
                "sales_channel_id": "c1",
            }
        assert table == "sales_channels"
        assert params == {"id": "eq.c1"}
        return {"id": "c1", "name": "Shopee"}

    async def fake_pg_update(table, params, data):
        pass

    seen_kwargs = {}

    def fake_converter(path, mapping_override=None, sales_channel_name=None):
        seen_kwargs["sales_channel_name"] = sales_channel_name
        return b"x", 1, {}

    monkeypatch.setattr(db, "pg_select_one", fake_pg_select_one)
    monkeypatch.setattr(db, "pg_update", fake_pg_update)
    monkeypatch.setattr(storage, "download_to_path", lambda key, path: None)
    monkeypatch.setattr(storage, "upload_bytes", lambda key, data, content_type: None)
    monkeypatch.setattr(_report_crud, "invalidate_local_parquet_cache", lambda report_id: None)

    router = _make_router(converter=fake_converter, has_channel=True, channel_aware_converter=True)
    route = _find_route(router, "POST", "/api/reports/{report_id}/reconvert")

    result = await route.endpoint("r1", user={"id": "u1"})

    assert result == {"ok": True, "rowCount": 1}
    assert seen_kwargs["sales_channel_name"] == "Shopee"
