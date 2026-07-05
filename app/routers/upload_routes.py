from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models.tables import UploadRun
from app.services.ohlcv_coverage_service import summarize_run_ohlcv_coverage
from app.services.upload_service import UploadProcessingError, create_upload_run
from app.settings import get_settings
from app.templates import templates

router = APIRouter(tags=["upload"])
DbSession = Annotated[Session, Depends(get_db)]
CsvUpload = Annotated[UploadFile, File(...)]


def _recent_runs(db: Session) -> list[UploadRun]:
    return list(db.scalars(select(UploadRun).order_by(UploadRun.uploaded_at.desc()).limit(5)).all())


def _latest_run(db: Session) -> UploadRun | None:
    return db.scalar(
        select(UploadRun)
        .options(selectinload(UploadRun.combined_results))
        .order_by(UploadRun.uploaded_at.desc())
        .limit(1)
    )


@router.get("/", response_class=HTMLResponse)
def upload_page(request: Request, db: DbSession) -> HTMLResponse:
    settings = get_settings()
    latest_run = _latest_run(db)
    return templates.TemplateResponse(
        request,
        "upload.html",
        {
            "active_nav": "dashboard",
            "settings": settings,
            "ib_status": "Not tested",
            "dashboard": _dashboard_summary(db, latest_run),
            "latest_run": latest_run,
            "recent_runs": _recent_runs(db),
            "error": None,
        },
    )


@router.post("/uploads", response_class=HTMLResponse)
def upload_csv(
    request: Request,
    file: CsvUpload,
    db: DbSession,
):
    try:
        run = create_upload_run(db, file)
    except UploadProcessingError as exc:
        settings = get_settings()
        latest_run = _latest_run(db)
        return templates.TemplateResponse(
            request,
            "upload.html",
            {
                "active_nav": "dashboard",
                "settings": settings,
                "ib_status": "Not tested",
                "dashboard": _dashboard_summary(db, latest_run),
                "latest_run": latest_run,
                "recent_runs": _recent_runs(db),
                "error": str(exc),
            },
            status_code=400,
        )

    return RedirectResponse(url=f"/runs/{run.id}", status_code=303)


def _dashboard_summary(db: Session, latest_run: UploadRun | None) -> dict[str, object]:
    if not latest_run:
        return {
            "latest_run_id": None,
            "latest_status": "No runs",
            "row_count": 0,
            "combined_count": 0,
            "incomplete_count": 0,
            "strong_count": 0,
            "coverage_ready": 0,
            "coverage_total": 0,
            "next_action": "Upload a daily screener CSV.",
            "combined_export_url": None,
        }

    combined_results = latest_run.combined_results
    coverage = summarize_run_ohlcv_coverage(db, latest_run.id)
    combined_count = len(combined_results)
    incomplete_count = sum(not result.is_complete for result in combined_results)
    strong_count = sum(
        result.combined_decision == "Strong candidate" and result.is_complete
        for result in combined_results
    )

    return {
        "latest_run_id": latest_run.id,
        "latest_status": latest_run.status,
        "row_count": latest_run.row_count or 0,
        "combined_count": combined_count,
        "incomplete_count": incomplete_count,
        "strong_count": strong_count,
        "coverage_ready": coverage.ready_count,
        "coverage_total": coverage.total_tickers,
        "next_action": _next_action(latest_run, combined_count, coverage.ready_count),
        "combined_export_url": (
            f"/runs/{latest_run.id}/exports/combined.csv" if combined_count else None
        ),
    }


def _next_action(latest_run: UploadRun, combined_count: int, ready_count: int) -> str:
    if latest_run.status == "FAILED":
        return "Review the failed run, then upload a corrected CSV."
    if not ready_count:
        return "Fetch IB bars from the latest run."
    if not combined_count:
        return "Refresh the cockpit for the latest run."
    return "Review decisions or export the combined CSV."
