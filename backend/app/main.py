from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routers import auth, dashboard, reports

app = FastAPI(title="BBStore Dashboard API")

app.include_router(auth.router)
app.include_router(reports.router)
app.include_router(dashboard.router)


@app.get("/api/health")
async def health():
    return {"ok": True}


# The frontend (index.html/css/js) is served as static files from the same
# service — see the plan's decision to retire GitHub Pages + the manual
# "Xuất báo cáo" publish flow now that there's a real backend.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
