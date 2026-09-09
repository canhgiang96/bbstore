import pytest

from app import db
from app.routers import dashboard as dashboard_mod


@pytest.mark.asyncio
async def test_all_dashboard_sources_includes_adjustment_paths(monkeypatch):
    # Regression test for the "Số tiền đã thu"/"Còn lại" feature (2026-09-09):
    # _all_dashboard_sources' return tuple grew from 7 to 8 elements (added
    # adjustment_paths) — every one of its 4 callers (summary/rows/grouped/
    # export) unpacks it positionally, so a mismatched count there would be
    # a silent ValueError at request time, never caught by unit tests that
    # only exercise query_engine directly.
    async def fake_pg_select(table, params=None):
        if table == "adjustments_reports":
            assert params == {"status": "eq.ready", "select": "id,parquet_key"}
            return [{"id": "a1", "parquet_key": "adj/a1.parquet"}]
        return []

    async def fake_get_local_parquet_async(report_id, parquet_key):
        return f"/tmp/{report_id}.parquet"

    monkeypatch.setattr(db, "pg_select", fake_pg_select)
    monkeypatch.setattr(dashboard_mod, "get_local_parquet_async", fake_get_local_parquet_async)

    (
        paths, cashflow_paths, combo_paths, master_paths,
        channel_paths, aff_paths, inhouse_handles, adjustment_paths,
    ) = await dashboard_mod._all_dashboard_sources([])

    assert adjustment_paths == ["/tmp/a1.parquet"]
    assert paths == []
    assert cashflow_paths == combo_paths == master_paths == aff_paths == []
    assert inhouse_handles == []
    assert channel_paths == {}


@pytest.mark.asyncio
async def test_all_ready_adjustments_parquet_paths_swallows_errors(monkeypatch):
    # Same best-effort fallback as Cashflow/Combo/Master File/Kênh AFF — the
    # adjustments_reports table may not exist yet right after this ships,
    # before the Supabase migration has run; must degrade to [], never 500
    # the whole Dashboard.
    async def fake_pg_select(table, params=None):
        raise RuntimeError("table does not exist")

    monkeypatch.setattr(db, "pg_select", fake_pg_select)

    result = await dashboard_mod._all_ready_adjustments_parquet_paths()
    assert result == []
