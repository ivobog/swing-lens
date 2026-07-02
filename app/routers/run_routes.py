from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models.tables import UploadRun
from app.services.combined_decision import refresh_combined_results
from app.services.technical_score_service import score_run_technicals
from app.templates import templates

router = APIRouter(tags=["runs"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, db: DbSession) -> HTMLResponse:
    runs = db.scalars(select(UploadRun).order_by(UploadRun.uploaded_at.desc())).all()
    return templates.TemplateResponse(request, "runs.html", {"runs": runs})


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail_page(
    run_id: int,
    request: Request,
    db: DbSession,
) -> HTMLResponse:
    run = db.scalar(
        select(UploadRun)
        .where(UploadRun.id == run_id)
        .options(
            selectinload(UploadRun.raw_company_rows),
            selectinload(UploadRun.fundamental_scores),
            selectinload(UploadRun.technical_scores),
            selectinload(UploadRun.combined_results),
        )
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    rows = sorted(run.raw_company_rows, key=lambda row: row.row_number)
    fundamental_by_ticker = {score.ticker: score for score in run.fundamental_scores}
    technical_by_ticker = {score.ticker: score for score in run.technical_scores}
    combined_by_ticker = {result.ticker: result for result in run.combined_results}
    combined_results = sorted(
        run.combined_results,
        key=lambda result: result.final_rank or 0,
    )
    decision_counts = _decision_counts(combined_results)
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run": run,
            "rows": rows,
            "fundamental_by_ticker": fundamental_by_ticker,
            "technical_by_ticker": technical_by_ticker,
            "combined_by_ticker": combined_by_ticker,
            "combined_results": combined_results,
            "decision_counts": decision_counts,
        },
    )


@router.post("/runs/{run_id}/combined-results")
def refresh_combined_results_action(run_id: int, db: DbSession) -> RedirectResponse:
    run_exists = db.scalar(select(UploadRun.id).where(UploadRun.id == run_id))
    if not run_exists:
        raise HTTPException(status_code=404, detail="Run not found")

    score_run_technicals(db, run_id)
    refresh_combined_results(db, run_id)
    db.commit()
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


def _decision_counts(results: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        decision = result.combined_decision or "Unclassified"
        counts[decision] = counts.get(decision, 0) + 1
    return counts
