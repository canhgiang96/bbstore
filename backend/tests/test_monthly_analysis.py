"""Router-level tests for monthly_analysis.py — app.db's PostgREST calls
and routers.dashboard's Report-gathering are monkeypatched, since a real
Supabase/R2 isn't available locally (same approach as test_report_crud.py).
"""
import pytest

from app import db
from app.routers import monthly_analysis


def _find_route(path, method):
    return next(r for r in monthly_analysis.router.routes if r.path == path and method in r.methods)


@pytest.mark.asyncio
async def test_monthly_analysis_joins_query_engine_result_with_saved_expenses(monkeypatch):
    async def fake_all_ready_reports():
        return []

    async def fake_all_dashboard_sources(reports):
        return [], [], [], [], {}, [], []

    def fake_run_monthly_analysis_query(*args, **kwargs):
        return [
            {"month": "2026-01", "gmv": 250000.0, "nmv": 250000.0, "loi_nhuan_gop": 246760.0},
            {"month": "2026-02", "gmv": 80000.0, "nmv": 80000.0, "loi_nhuan_gop": 78380.0},
        ]

    async def fake_pg_select(table, params=None):
        assert table == "monthly_expenses"
        return [{"month": "2026-01-01", "chi_phi_ban_hang": 100000.0, "chi_phi_quan_ly": 46760.0}]

    monkeypatch.setattr(monthly_analysis, "_all_ready_reports", fake_all_ready_reports)
    monkeypatch.setattr(monthly_analysis, "_all_dashboard_sources", fake_all_dashboard_sources)
    monkeypatch.setattr(monthly_analysis, "run_monthly_analysis_query", fake_run_monthly_analysis_query)
    monkeypatch.setattr(db, "pg_select", fake_pg_select)

    route = _find_route("/api/monthly-analysis", "GET")
    result = await route.endpoint(user={"id": "u1"})

    by_month = {r.month: r for r in result}
    # 2026-01 has a saved expense row -> Lợi nhuận nets it out.
    jan = by_month["2026-01"]
    assert jan.chiPhiBanHang == 100000.0
    assert jan.chiPhiQuanLy == 46760.0
    assert jan.loiNhuan == 246760.0 - 100000.0 - 46760.0

    # 2026-02 has no saved expense row -> defaults to 0, Lợi nhuận = Lợi nhuận gộp.
    feb = by_month["2026-02"]
    assert feb.chiPhiBanHang == 0
    assert feb.chiPhiQuanLy == 0
    assert feb.loiNhuan == 78380.0


@pytest.mark.asyncio
async def test_monthly_analysis_degrades_gracefully_when_expenses_table_missing(monkeypatch):
    # monthly_expenses may not exist yet (migration pending) — must not
    # break the whole endpoint, same best-effort pattern used for Combo/
    # Master File/Kênh AFF's parquet-path gathering.
    async def fake_all_ready_reports():
        return []

    async def fake_all_dashboard_sources(reports):
        return [], [], [], [], {}, [], []

    def fake_run_monthly_analysis_query(*args, **kwargs):
        return [{"month": "2026-01", "gmv": 100.0, "nmv": 100.0, "loi_nhuan_gop": 100.0}]

    async def fake_pg_select(table, params=None):
        raise Exception("relation \"monthly_expenses\" does not exist")

    monkeypatch.setattr(monthly_analysis, "_all_ready_reports", fake_all_ready_reports)
    monkeypatch.setattr(monthly_analysis, "_all_dashboard_sources", fake_all_dashboard_sources)
    monkeypatch.setattr(monthly_analysis, "run_monthly_analysis_query", fake_run_monthly_analysis_query)
    monkeypatch.setattr(db, "pg_select", fake_pg_select)

    route = _find_route("/api/monthly-analysis", "GET")
    result = await route.endpoint(user={"id": "u1"})

    assert result[0].chiPhiBanHang == 0
    assert result[0].loiNhuan == 100.0


@pytest.mark.asyncio
async def test_update_monthly_expense_inserts_when_no_existing_row(monkeypatch):
    calls = []

    async def fake_pg_update(table, params, data):
        calls.append(("update", table, params, data))
        return []  # no matching row -> caller should fall back to insert

    async def fake_pg_insert(table, data):
        calls.append(("insert", table, data))
        return data

    monkeypatch.setattr(db, "pg_update", fake_pg_update)
    monkeypatch.setattr(db, "pg_insert", fake_pg_insert)

    route = _find_route("/api/monthly-analysis/{month}", "PATCH")
    body = monthly_analysis.MonthlyExpenseUpdateRequest(chiPhiBanHang=1000.0, chiPhiQuanLy=2000.0)
    result = await route.endpoint(month="2026-01", body=body, user={"id": "u1"})

    assert result == {"ok": True}
    assert calls[0][0] == "update"
    assert calls[0][2] == {"month": "eq.2026-01-01"}
    assert calls[1][0] == "insert"
    assert calls[1][2]["month"] == "2026-01-01"
    assert calls[1][2]["chi_phi_ban_hang"] == 1000.0


@pytest.mark.asyncio
async def test_update_monthly_expense_updates_existing_row_without_inserting(monkeypatch):
    calls = []

    async def fake_pg_update(table, params, data):
        calls.append(("update", table, params, data))
        return [{"month": "2026-01-01", **data}]

    async def fake_pg_insert(table, data):
        calls.append(("insert", table, data))
        return data

    monkeypatch.setattr(db, "pg_update", fake_pg_update)
    monkeypatch.setattr(db, "pg_insert", fake_pg_insert)

    route = _find_route("/api/monthly-analysis/{month}", "PATCH")
    body = monthly_analysis.MonthlyExpenseUpdateRequest(chiPhiBanHang=1000.0, chiPhiQuanLy=2000.0)
    await route.endpoint(month="2026-01", body=body, user={"id": "u1"})

    assert len(calls) == 1
    assert calls[0][0] == "update"
