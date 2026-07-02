from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models.tables import UploadRun
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
        )
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    rows = sorted(run.raw_company_rows, key=lambda row: row.row_number)
    scores_by_ticker = {score.ticker: score for score in run.fundamental_scores}
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run": run,
            "rows": rows,
            "scores_by_ticker": scores_by_ticker,
        },
    )
