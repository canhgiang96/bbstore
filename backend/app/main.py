from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db
from .routers import (
    adjustments_reports,
    aff_channel_reports,
    auth,
    cashflow_reports,
    combo_reports,
    dashboard,
    inhouse_handles,
    master_reports,
    monthly_analysis,
    reports,
    sales_channels,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await db.aclose_client()


app = FastAPI(title="BBStore Dashboard API", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(reports.router)
app.include_router(cashflow_reports.router)
app.include_router(combo_reports.router)
app.include_router(master_reports.router)
app.include_router(adjustments_reports.router)
app.include_router(aff_channel_reports.router)
app.include_router(sales_channels.router)
app.include_router(inhouse_handles.router)
app.include_router(dashboard.router)
app.include_router(dashboard.dashboard_router)
app.include_router(monthly_analysis.router)


@app.get("/api/health")
async def health():
    return {"ok": True}


# The frontend (index.html/css/js) is served as static files from the same
# service — see the plan's decision to retire GitHub Pages + the manual
# "Xuất báo cáo" publish flow now that there's a real backend.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
