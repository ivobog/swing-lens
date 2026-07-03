from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models.tables import CombinedResult, PriceBar, RawCompanyRow, TechnicalScore, UploadRun
from app.services.combined_decision import refresh_combined_results
from app.services.export_service import EXPORT_TYPES, export_filename, export_run_csv
from app.services.history_service import recent_decisions, summarize_runs
from app.services.technical_score_service import score_run_technicals
from app.templates import templates

router = APIRouter(tags=["runs"])
DbSession = Annotated[Session, Depends(get_db)]

WARNING_BADGE_LABELS = {
    "incomplete_data": "Incomplete",
    "missing_fundamental": "No fundamentals",
    "missing_technical": "No technicals",
    "value_trap_risk": "Value trap",
    "growth_trap_risk": "Growth trap",
    "liquidity_warning": "Liquidity",
    "ib_fetch_failed": "IB failed",
    "insufficient_history": "Short history",
    "missing_market_data": "No market",
    "missing_benchmark_data": "No benchmark",
    "technical_error": "Technical error",
    "low_technical_confidence": "Low confidence",
    "negative_free_cash_flow": "Negative FCF",
    "high_leverage": "High leverage",
    "weak_liquidity": "Weak liquidity",
    "extreme_valuation": "Extreme valuation",
    "share_dilution": "Dilution",
}


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, db: DbSession) -> HTMLResponse:
    runs = db.scalars(select(UploadRun).order_by(UploadRun.uploaded_at.desc())).all()
    return templates.TemplateResponse(request, "runs.html", {"runs": runs})


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request, db: DbSession) -> HTMLResponse:
    runs = db.scalars(
        select(UploadRun)
        .options(selectinload(UploadRun.combined_results))
        .order_by(UploadRun.uploaded_at.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "run_summaries": summarize_runs(list(runs)),
            "recent_decisions": recent_decisions(list(runs), limit=100),
        },
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail_page(
    run_id: int,
    request: Request,
    db: DbSession,
) -> HTMLResponse:
    run = _load_run(db, run_id)
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
    tickers = _unique_tickers(rows)
    price_bar_ticker_count = _price_bar_ticker_count(db, tickers)
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run": run,
            "rows": rows,
            "raw_preview": rows[:10],
            "fundamental_by_ticker": fundamental_by_ticker,
            "technical_by_ticker": technical_by_ticker,
            "combined_by_ticker": combined_by_ticker,
            "combined_results": combined_results,
            "decision_counts": decision_counts,
            "run_summary": _run_summary(run, rows, combined_results),
            "workflow_steps": _workflow_steps(
                run=run,
                rows=rows,
                technical_scores=run.technical_scores,
                combined_results=combined_results,
                price_bar_ticker_count=price_bar_ticker_count,
            ),
            "warning_badges_by_ticker": {
                result.ticker: _warning_badges(result.warning_flags_json or [])
                for result in combined_results
            },
            "decision_filters": sorted(decision_counts),
            "sector_filters": sorted(
                {result.sector for result in combined_results if result.sector}
            ),
        },
    )


@router.get("/runs/{run_id}/exports/{export_type}.csv")
def export_run_results(run_id: int, export_type: str, db: DbSession) -> Response:
    if export_type not in EXPORT_TYPES:
        raise HTTPException(status_code=404, detail="Export not found")

    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    filename = export_filename(run, export_type)
    return Response(
        content=export_run_csv(run, export_type),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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


def _run_summary(
    run: UploadRun,
    rows: list[RawCompanyRow],
    combined_results: list[CombinedResult],
) -> dict[str, object]:
    incomplete_count = sum(not result.is_complete for result in combined_results)
    warning_count = sum(result.has_warning for result in combined_results)
    strong_count = sum(
        result.combined_decision == "Strong candidate" and result.is_complete
        for result in combined_results
    )
    duplicate_ticker_count = len(rows) - len(_unique_tickers(rows))
    top_complete = next(
        (result for result in combined_results if result.is_complete),
        None,
    )
    raw_column_count = max((len(row.raw_json) for row in rows), default=0)
    return {
        "row_count": run.row_count or len(rows),
        "combined_count": len(combined_results),
        "incomplete_count": incomplete_count,
        "warning_count": warning_count,
        "strong_count": strong_count,
        "duplicate_ticker_count": duplicate_ticker_count,
        "raw_column_count": raw_column_count,
        "top_complete": top_complete,
    }


def _workflow_steps(
    run: UploadRun,
    rows: list[RawCompanyRow],
    technical_scores: list[TechnicalScore],
    combined_results: list[CombinedResult],
    price_bar_ticker_count: int,
) -> list[dict[str, str]]:
    ticker_count = len(_unique_tickers(rows))
    technical_warning_count = sum(
        score.insufficient_data or score.technical_confidence in {"low", "error"}
        for score in technical_scores
    )
    return [
        _workflow_step(
            "CSV uploaded",
            "failed" if run.status == "FAILED" else "completed" if rows else "not-started",
            "Raw rows are stored." if rows else "Waiting for a valid CSV.",
        ),
        _workflow_step(
            "Fundamentals scored",
            "completed" if run.fundamental_scores else "not-started",
            f"{len(run.fundamental_scores)} score rows.",
        ),
        _workflow_step(
            "IB bars fetched",
            _coverage_status(price_bar_ticker_count, ticker_count),
            f"{price_bar_ticker_count} of {ticker_count} tickers have cached bars.",
        ),
        _workflow_step(
            "Technicals scored",
            _technical_step_status(technical_scores, technical_warning_count),
            (
                f"{len(technical_scores)} technical rows; {technical_warning_count} warnings."
                if technical_scores
                else "Fetch bars, then refresh the cockpit."
            ),
        ),
        _workflow_step(
            "Combined cockpit",
            "completed" if combined_results else "not-started",
            f"{len(combined_results)} combined rows.",
        ),
        _workflow_step(
            "Export/review",
            "completed" if combined_results else "not-started",
            "CSV exports are ready." if combined_results else "Generate combined results first.",
        ),
    ]


def _workflow_step(label: str, status: str, message: str) -> dict[str, str]:
    return {
        "label": label,
        "status": status,
        "message": message,
        "status_label": status.replace("-", " ").title(),
    }


def _coverage_status(covered_count: int, ticker_count: int) -> str:
    if ticker_count == 0 or covered_count == 0:
        return "not-started"
    if covered_count < ticker_count:
        return "warning"
    return "completed"


def _technical_step_status(
    technical_scores: list[TechnicalScore],
    warning_count: int,
) -> str:
    if warning_count:
        return "warning"
    if technical_scores:
        return "completed"
    return "not-started"


def _warning_badges(flags: list[str]) -> list[dict[str, str]]:
    return [
        {
            "flag": flag,
            "label": WARNING_BADGE_LABELS.get(flag, flag.replace("_", " ").title()),
            "tone": _warning_tone(flag),
        }
        for flag in flags
    ]


def _warning_tone(flag: str) -> str:
    if flag in {
        "value_trap_risk",
        "growth_trap_risk",
        "technical_error",
        "ib_fetch_failed",
        "negative_free_cash_flow",
        "high_leverage",
    }:
        return "danger"
    if flag in {"missing_fundamental", "missing_technical", "incomplete_data"}:
        return "warning"
    return "muted"


def _unique_tickers(rows: list[RawCompanyRow]) -> list[str]:
    seen: set[str] = set()
    tickers: list[str] = []
    for row in rows:
        ticker = row.ticker.upper()
        if ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers


def _price_bar_ticker_count(db: Session, tickers: list[str]) -> int:
    if not tickers:
        return 0
    return len(
        db.scalars(
            select(PriceBar.ticker).where(PriceBar.ticker.in_(tickers)).distinct()
        ).all()
    )


def _load_run(db: Session, run_id: int) -> UploadRun | None:
    return db.scalar(
        select(UploadRun)
        .where(UploadRun.id == run_id)
        .options(
            selectinload(UploadRun.raw_company_rows),
            selectinload(UploadRun.fundamental_scores),
            selectinload(UploadRun.technical_scores),
            selectinload(UploadRun.combined_results),
        )
    )
