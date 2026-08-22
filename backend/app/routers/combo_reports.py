"""Combo Report CRUD — mirrors app/routers/cashflow_reports.py exactly, just
pointed at the combo_reports table/R2 prefix and combo_excel_to_parquet().
Combo Reports exist solely to explode matching Orders rows into their
sub-SKU components at query time (see query_engine.py) — there's no
per-report dashboard view for them.
"""
from __future__ import annotations

import io
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import db, storage
from ..combo_to_parquet import ComboMappingError, combo_excel_to_parquet
from ..deps import get_current_user, require_admin
from ..models import ReportCreatedOut, ReportDetailOut, ReportOut

router = APIRouter(prefix="/api/combo-reports", tags=["combo-reports"])

COMBO_REPORT_LIST_FIELDS = "id,name,uploaded_at,uploaded_by,row_count,status,error_message"


async def _process_combo_report(report_id: str, xlsx_bytes) -> None:
    try:
        parquet_bytes, row_count, mapping = await run_in_threadpool(combo_excel_to_parquet, io.BytesIO(xlsx_bytes))
        await run_in_threadpool(
            storage.upload_bytes, storage.combo_parquet_key(report_id), parquet_bytes, "application/octet-stream"
        )
        await db.pg_update(
            "combo_reports",
            {"id": f"eq.{report_id}"},
            {
                "status": "ready",
                "row_count": row_count,
                "mapping": mapping,
                "parquet_key": storage.combo_parquet_key(report_id),
            },
        )
    except ComboMappingError as e:
        await db.pg_update("combo_reports", {"id": f"eq.{report_id}"}, {"status": "failed", "error_message": str(e)})
    except Exception as e:  # noqa: BLE001 — a bad file should fail the Report, not crash the worker
        await db.pg_update(
            "combo_reports", {"id": f"eq.{report_id}"}, {"status": "failed", "error_message": f"Lỗi xử lý: {e}"}
        )


@router.post("", response_model=ReportCreatedOut, status_code=202)
async def create_combo_report(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: dict = Depends(require_admin),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file .xlsx, .xls, .csv")

    report_id = str(uuid.uuid4())
    xlsx_bytes = await file.read()

    await run_in_threadpool(
        storage.upload_bytes,
        storage.combo_original_key(report_id, file.filename),
        xlsx_bytes,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    name = file.filename.rsplit(".", 1)[0]
    await db.pg_insert(
        "combo_reports",
        {
            "id": report_id,
            "name": name,
            "original_filename": file.filename,
            "uploaded_by": user["id"],
            "status": "processing",
            "original_xlsx_key": storage.combo_original_key(report_id, file.filename),
            "file_size_bytes": len(xlsx_bytes),
        },
    )

    background_tasks.add_task(_process_combo_report, report_id, xlsx_bytes)
    return ReportCreatedOut(id=report_id, status="processing")


@router.get("", response_model=list[ReportOut])
async def list_combo_reports(user: dict = Depends(get_current_user)):
    rows = await db.pg_select("combo_reports", {"select": COMBO_REPORT_LIST_FIELDS, "order": "uploaded_at.desc"})
    return [ReportOut(**r) for r in rows]


@router.get("/{report_id}", response_model=ReportDetailOut)
async def get_combo_report(report_id: str, user: dict = Depends(get_current_user)):
    row = await db.pg_select_one("combo_reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    return ReportDetailOut(**row)


@router.delete("/{report_id}", status_code=204)
async def delete_combo_report(report_id: str, user: dict = Depends(require_admin)):
    row = await db.pg_select_one("combo_reports", {"id": f"eq.{report_id}"})
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy Report.")
    storage.delete_objects([row.get("original_xlsx_key"), row.get("parquet_key")])
    await db.pg_delete("combo_reports", {"id": f"eq.{report_id}"})
