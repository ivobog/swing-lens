from datetime import date
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models.tables import (
    BackgroundJob,
    CombinedResult,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.services.bar_cache_service import DEFAULT_WHAT_TO_SHOW
from app.services.chart_data_service import build_ticker_chart_payload
from app.services.cockpit_sorting import cockpit_sort_key
from app.services.column_mapping_summary_service import summarize_run_column_mapping
from app.services.combined_decision import refresh_combined_results
from app.services.export_service import (
    EXPORT_TYPES,
    export_coverage_csv,
    export_fetch_plan_csv,
    export_fetch_results_csv,
    export_filename,
    export_mapping_csv,
    export_run_csv,
)
from app.services.fundamental_score_service import recalculate_run_fundamentals
from app.services.history_query_service import (
    DecisionFilters,
    RunFilters,
    paged_decisions,
    paged_runs,
)
from app.services.ib_connection import check_ib_connection
from app.services.ib_fetch_job_service import (
    FetchJobOptions,
    cancel_fetch_job,
    create_queued_fetch_run,
    get_fetch_progress,
    resume_fetch_job,
    submit_fetch_job,
)
from app.services.ib_fetch_plan_service import FetchPlan, build_fetch_plan, fetch_plan_to_dict
from app.services.ib_fetch_summary_service import (
    latest_ib_fetch_for_run,
)
from app.services.ohlcv_coverage_service import OhlcvCoverageSummary, summarize_run_ohlcv_coverage
from app.services.pipeline_service import (
    PIPELINE_TERMINAL_STATUSES,
    PipelineStatusDto,
    cancel_pipeline,
    get_pipeline_status,
    start_pipeline,
)
from app.services.score_card_view_service import build_score_cards
from app.services.technical_display_fields import technical_v4_details_by_ticker
from app.services.technical_score_service import score_run_technicals
from app.settings import get_settings
from app.templates import templates

router = APIRouter(tags=["runs"])
DbSession = Annotated[Session, Depends(get_db)]
TickerSubsetForm = Annotated[str, Form()]
IncludeBenchmarksForm = Annotated[bool, Form()]
WhatToShowForm = Annotated[list[str] | None, Form()]
ForceRefreshForm = Annotated[bool, Form()]
ForceFullBackfillForm = Annotated[bool, Form()]

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
    "earnings_quality_risk": "Earnings quality",
    "poor_cash_conversion": "Cash conversion",
    "high_accrual_risk": "Accrual risk",
    "capital_efficiency_deterioration": "Efficiency",
    "asset_growth_without_returns": "Asset growth",
    "balance_sheet_stress": "Balance sheet",
    "liquidity_buffer_weak": "Liquidity buffer",
    "forward_quality_weak": "Forward weak",
    "dividend_payout_risk": "Dividend payout",
    "sparse_fundamental_data": "Sparse data",
    "earnings_blocked": "Earnings block",
    "earnings_high_risk": "Earnings high",
    "earnings_medium_risk": "Earnings medium",
    "earnings_date_missing": "No earnings date",
    "earnings_date_unparseable": "Bad earnings date",
}


@router.get("/runs", response_class=HTMLResponse)
def runs_page(
    request: Request,
    db: DbSession,
    page: int = 1,
    page_size: int | None = None,
    status: str = "",
    from_date: str = "",
    to_date: str = "",
    search: str = "",
    sort: str = "uploaded_at",
    direction: str = "desc",
) -> HTMLResponse:
    settings = get_settings()
    effective_page_size = page_size or settings.runs_default_page_size
    filters = RunFilters(
        status=status or None,
        from_date=_parse_date_filter(from_date),
        to_date=_parse_date_filter(to_date),
        search=search.strip() or None,
        sort=sort,
        direction=direction,
    )
    runs_page_data = paged_runs(
        db,
        filters,
        page=page,
        page_size=effective_page_size,
        max_page_size=settings.history_max_page_size,
    )
    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "active_nav": "runs",
            "runs": runs_page_data.items,
            "page": runs_page_data,
            "filters": filters,
            "query_params": _pagination_query_params(request),
        },
    )


@router.get("/history", response_class=HTMLResponse)
def history_page(
    request: Request,
    db: DbSession,
    page: int = 1,
    page_size: int | None = None,
    from_date: str = "",
    to_date: str = "",
    decision: str = "",
    ticker: str = "",
    sector: str = "",
    min_score: str = "",
    has_warning: str = "",
    incomplete_only: bool = False,
) -> HTMLResponse:
    settings = get_settings()
    effective_page_size = page_size or settings.history_default_page_size
    filters = DecisionFilters(
        from_date=_parse_date_filter(from_date),
        to_date=_parse_date_filter(to_date),
        decision=decision or None,
        ticker=ticker.strip() or None,
        sector=sector or None,
        min_score=_parse_decimal_filter(min_score),
        has_warning=_parse_bool_filter(has_warning),
        incomplete_only=incomplete_only,
    )
    decisions_page_data = paged_decisions(
        db,
        filters,
        page=page,
        page_size=effective_page_size,
        max_page_size=settings.history_max_page_size,
    )
    recent_runs_page = paged_runs(
        db,
        RunFilters(),
        page=1,
        page_size=5,
        max_page_size=5,
    )
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "active_nav": "history",
            "run_summaries": recent_runs_page.items,
            "recent_decisions": decisions_page_data.items,
            "decisions_page": decisions_page_data,
            "filters": filters,
            "query_params": _pagination_query_params(request),
        },
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail_page(
    run_id: int,
    request: Request,
    db: DbSession,
    pipeline_id: int | None = None,
) -> HTMLResponse:
    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    rows = sorted(run.raw_company_rows, key=lambda row: row.row_number)
    fundamental_by_ticker = {score.ticker: score for score in run.fundamental_scores}
    technical_by_ticker = {score.ticker: score for score in run.technical_scores}
    combined_by_ticker = {result.ticker: result for result in run.combined_results}
    combined_results = sorted(run.combined_results, key=cockpit_sort_key)
    decision_counts = _decision_counts(combined_results)
    coverage = summarize_run_ohlcv_coverage(db, run.id)
    latest_fetch = latest_ib_fetch_for_run(db, run.id)
    settings = get_settings()
    latest_pipeline = _pipeline_status_for_run(db, run.id, pipeline_id)
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "active_nav": "runs",
            "run": run,
            "rows": rows,
            "raw_preview": rows[:10],
            "fundamental_by_ticker": fundamental_by_ticker,
            "technical_by_ticker": technical_by_ticker,
            "technical_details_by_ticker": technical_v4_details_by_ticker(
                run.technical_scores
            ),
            "combined_by_ticker": combined_by_ticker,
            "combined_results": combined_results,
            "decision_counts": decision_counts,
            "run_summary": _run_summary(run, rows, combined_results),
            "workflow_steps": _workflow_steps(
                run=run,
                rows=rows,
                technical_scores=run.technical_scores,
                combined_results=combined_results,
                coverage=coverage,
            ),
            "coverage": coverage,
            "latest_fetch": latest_fetch,
            "latest_pipeline": latest_pipeline,
            "ib_panel": {
                "host": settings.ib_host,
                "port": settings.ib_port,
                "client_id": settings.ib_client_id,
                "read_only": True,
                "full_backfill_duration": settings.ib_full_backfill_duration,
                "top_up_duration": settings.ib_top_up_duration,
                "refresh_duration": settings.ib_refresh_duration,
                "default_bar_size": settings.ib_default_bar_size,
                "requests_per_minute": settings.ib_requests_per_minute,
                "min_seconds_between_requests": settings.ib_min_seconds_between_requests,
                "benchmarks": ", ".join(settings.ib_benchmark_symbols),
                "required_daily_bars": settings.ib_required_daily_bars,
                "test_status": request.query_params.get("ib_status"),
                "test_message": request.query_params.get("ib_message"),
            },
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


@router.get("/runs/{run_id}/tickers/{ticker}/chart", response_class=HTMLResponse)
def ticker_chart_panel(
    run_id: int,
    ticker: str,
    request: Request,
    db: DbSession,
) -> HTMLResponse:
    context = _ticker_chart_context(db, run_id, ticker)
    return templates.TemplateResponse(
        request,
        "ticker_chart_panel.html",
        {
            "active_nav": "runs",
            **context,
        },
    )


@router.get("/api/runs/{run_id}/tickers/{ticker}/chart-data")
def ticker_chart_data(run_id: int, ticker: str, db: DbSession) -> dict[str, object]:
    context = _ticker_chart_context(db, run_id, ticker)
    return build_ticker_chart_payload(db, context["run"].id, context["ticker"])


@router.get("/runs/{run_id}/exports/{export_type}.csv")
def export_run_results(run_id: int, export_type: str, db: DbSession) -> Response:
    if export_type not in EXPORT_TYPES:
        raise HTTPException(status_code=404, detail="Export not found")

    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    filename = export_filename(run, export_type)
    if export_type == "ib-fetch-plan":
        content = export_fetch_plan_csv(
            build_fetch_plan(
                db,
                _unique_tickers(run.raw_company_rows),
                run_id=run_id,
                include_benchmarks=True,
                what_to_show_values=DEFAULT_WHAT_TO_SHOW,
            )
        )
    elif export_type == "ib-fetch-results":
        content = export_fetch_results_csv(latest_ib_fetch_for_run(db, run_id))
    elif export_type == "coverage":
        content = export_coverage_csv(summarize_run_ohlcv_coverage(db, run_id))
    elif export_type == "mapping":
        content = export_mapping_csv(summarize_run_column_mapping(run))
    else:
        coverage = summarize_run_ohlcv_coverage(db, run_id) if export_type == "combined" else None
        content = export_run_csv(run, export_type, coverage=coverage)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/runs/{run_id}/coverage", response_class=HTMLResponse)
def run_coverage_page(run_id: int, request: Request, db: DbSession) -> HTMLResponse:
    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    coverage = summarize_run_ohlcv_coverage(db, run_id)
    return templates.TemplateResponse(
        request,
        "coverage.html",
        {
            "active_nav": "runs",
            "run": run,
            "coverage": coverage,
            "coverage_actions": _coverage_actions(coverage),
        },
    )


@router.get("/runs/{run_id}/mapping", response_class=HTMLResponse)
def run_mapping_page(run_id: int, request: Request, db: DbSession) -> HTMLResponse:
    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    return templates.TemplateResponse(
        request,
        "mapping.html",
        {
            "active_nav": "runs",
            "run": run,
            "mapping": summarize_run_column_mapping(run),
        },
    )


@router.post("/runs/{run_id}/combined-results")
def refresh_combined_results_action(run_id: int, db: DbSession) -> RedirectResponse:
    run_exists = db.scalar(select(UploadRun.id).where(UploadRun.id == run_id))
    if not run_exists:
        raise HTTPException(status_code=404, detail="Run not found")

    refresh_combined_results(db, run_id)
    db.commit()
    return _redirect_with_query(
        run_id,
        {
            "ib_status": "combined-refreshed",
            "ib_message": "Combined cockpit rebuilt from existing fundamentals and technicals.",
        },
    )


@router.post("/runs/{run_id}/fundamentals/recalculate")
def recalculate_fundamentals_action(run_id: int, db: DbSession) -> RedirectResponse:
    _require_run(db, run_id)
    scores = recalculate_run_fundamentals(db, run_id)
    combined = refresh_combined_results(db, run_id)
    db.commit()
    return _redirect_with_query(
        run_id,
        {
            "ib_status": "fundamentals-refreshed",
            "ib_message": (
                f"Recalculated {len(scores)} fundamental score rows and rebuilt "
                f"{len(combined)} combined rows."
            ),
        },
    )


@router.post("/runs/{run_id}/technicals/refresh")
def refresh_technicals_action(run_id: int, db: DbSession) -> RedirectResponse:
    _require_run(db, run_id)
    scores = score_run_technicals(db, run_id)
    combined = refresh_combined_results(db, run_id)
    db.commit()
    return _redirect_with_query(
        run_id,
        {
            "ib_status": "technicals-refreshed",
            "ib_message": (
                f"Refreshed {len(scores)} technical score rows from cached OHLCV "
                f"and rebuilt {len(combined)} combined rows."
            ),
        },
    )


@router.post("/runs/{run_id}/pipeline")
def run_full_pipeline_action(run_id: int, db: DbSession) -> RedirectResponse:
    settings = get_settings()
    if settings.use_durable_pipeline:
        try:
            pipeline = start_pipeline(db, run_id)
            db.commit()
            return RedirectResponse(
                url=f"/runs/{run_id}/pipeline/{pipeline.id}",
                status_code=303,
            )
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception:
            db.rollback()
            raise

    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    tickers = _unique_tickers(run.raw_company_rows)
    if not tickers:
        return _redirect_with_query(
            run_id,
            {
                "ib_status": "pipeline-failed",
                "ib_message": "No uploaded tickers are available for this run.",
            },
        )

    fundamental_scores = recalculate_run_fundamentals(db, run_id)
    plan = build_fetch_plan(
        db=db,
        tickers=tickers,
        run_id=run_id,
        include_benchmarks=True,
        what_to_show_values=DEFAULT_WHAT_TO_SHOW,
    )
    technical_scores = score_run_technicals(db, run_id)
    combined_results = refresh_combined_results(db, run_id)

    message = (
        f"Recalculated {len(fundamental_scores)} fundamentals, refreshed "
        f"{len(technical_scores)} technicals, and rebuilt {len(combined_results)} "
        "combined rows."
    )
    status = "pipeline-refreshed"
    if plan.estimated_request_count:
        options = FetchJobOptions(include_benchmarks=True)
        fetch_run = create_queued_fetch_run(db, plan, options)
        db.commit()
        submit_fetch_job(fetch_run.id, plan, options)
        status = "pipeline-queued"
        message = (
            f"IB fetch {fetch_run.id} queued for {plan.estimated_request_count} requests. "
            f"{message} Refresh technicals and combined again after fetch completion."
        )
    else:
        db.commit()

    return _redirect_with_query(
        run_id,
        {
            "ib_status": status,
            "ib_message": message,
        },
    )


@router.get("/runs/{run_id}/pipeline/{pipeline_id}", response_class=HTMLResponse)
def run_pipeline_progress_page(
    run_id: int,
    pipeline_id: int,
    request: Request,
    db: DbSession,
) -> HTMLResponse:
    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    status = _require_pipeline_for_run(db, pipeline_id, run_id)
    return templates.TemplateResponse(
        request,
        "pipeline_progress.html",
        {
            "active_nav": "runs",
            "run": run,
            "pipeline": _pipeline_status_payload(db, status),
            "terminal_statuses": sorted(PIPELINE_TERMINAL_STATUSES),
            "status_url": f"/runs/{run_id}/pipeline/{pipeline_id}/status",
        },
    )


@router.get("/runs/{run_id}/pipeline/{pipeline_id}/status")
def run_pipeline_status(run_id: int, pipeline_id: int, db: DbSession) -> dict[str, object]:
    status = _require_pipeline_for_run(db, pipeline_id, run_id)
    return _pipeline_status_payload(db, status)


@router.post("/runs/{run_id}/pipeline/{pipeline_id}/cancel")
def cancel_run_pipeline_action(
    run_id: int,
    pipeline_id: int,
    db: DbSession,
) -> RedirectResponse:
    _require_run(db, run_id)
    try:
        existing = get_pipeline_status(db, pipeline_id)
        if existing.upload_run_id != run_id:
            raise HTTPException(status_code=404, detail="Pipeline run not found for this run.")
        cancel_pipeline(db, pipeline_id)
        db.commit()
        return RedirectResponse(
            url=f"/runs/{run_id}/pipeline/{pipeline_id}",
            status_code=303,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/ib/test")
def test_run_ib_connection_action(run_id: int, db: DbSession) -> RedirectResponse:
    run_exists = db.scalar(select(UploadRun.id).where(UploadRun.id == run_id))
    if not run_exists:
        raise HTTPException(status_code=404, detail="Run not found")

    status = check_ib_connection()
    return _redirect_with_query(
        run_id,
        {
            "ib_status": "connected" if status.connected else "disconnected",
            "ib_message": status.message,
        },
    )


@router.get("/runs/{run_id}/ib/plan", response_model=None)
def preview_run_ib_fetch_plan(
    run_id: int,
    request: Request,
    db: DbSession,
    ticker_subset: str = "",
    include_benchmarks: bool = False,
    force_refresh: bool = False,
    force_full_backfill: bool = False,
    what_to_show: Annotated[list[str] | None, Query()] = None,
    format: str = "html",
) -> HTMLResponse | dict[str, object]:
    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    tickers = _tickers_from_fetch_form(ticker_subset) or _unique_tickers(run.raw_company_rows)
    if not tickers:
        raise HTTPException(status_code=400, detail="No uploaded tickers are available.")

    what_to_show_values = _what_to_show_values(what_to_show)
    plan = build_fetch_plan(
        db,
        tickers,
        run_id=run_id,
        include_benchmarks=include_benchmarks,
        force_refresh=force_refresh,
        force_full_backfill=force_full_backfill,
        what_to_show_values=what_to_show_values,
    )
    if format == "json":
        return fetch_plan_to_dict(plan)

    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "ib_fetch_plan.html",
        {
            "active_nav": "runs",
            "run": run,
            "plan": plan,
            "action_counts": _fetch_plan_action_counts(plan),
            "request_options": _fetch_request_options(
                ticker_subset=ticker_subset,
                include_benchmarks=include_benchmarks,
                force_refresh=force_refresh,
                force_full_backfill=force_full_backfill,
                what_to_show=what_to_show_values,
            ),
            "estimated_duration_label": _estimated_fetch_duration_label(plan, settings),
            "json_url": _fetch_plan_json_url(
                run_id=run_id,
                ticker_subset=ticker_subset,
                include_benchmarks=include_benchmarks,
                force_refresh=force_refresh,
                force_full_backfill=force_full_backfill,
                what_to_show=what_to_show,
            ),
        },
    )


@router.post("/runs/{run_id}/ib/fetch")
def fetch_run_ib_bars_action(
    run_id: int,
    db: DbSession,
    ticker_subset: TickerSubsetForm = "",
    include_benchmarks: IncludeBenchmarksForm = False,
    what_to_show: WhatToShowForm = None,
    force_refresh: ForceRefreshForm = False,
    force_full_backfill: ForceFullBackfillForm = False,
) -> RedirectResponse:
    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    tickers = _tickers_from_fetch_form(ticker_subset) or _unique_tickers(run.raw_company_rows)
    if not tickers:
        return _redirect_with_query(
            run_id,
            {
                "ib_status": "disconnected",
                "ib_message": "No uploaded tickers are available for this run.",
            },
        )

    what_to_show_values = _what_to_show_values(what_to_show)
    plan = build_fetch_plan(
        db=db,
        tickers=tickers,
        run_id=run_id,
        include_benchmarks=include_benchmarks,
        force_refresh=force_refresh,
        force_full_backfill=force_full_backfill,
        what_to_show_values=what_to_show_values,
    )

    try:
        options = FetchJobOptions(
            include_benchmarks=include_benchmarks,
            force_refresh=force_refresh,
            force_full_backfill=force_full_backfill,
        )
        fetch_run = create_queued_fetch_run(db, plan, options)
        db.commit()
        submit_fetch_job(fetch_run.id, plan, options)
        return _redirect_to_fetch_progress(
            run_id=run_id,
            fetch_run_id=fetch_run.id,
            params={
                "ib_status": "fetch-queued",
                "ib_message": f"IB fetch {fetch_run.id} queued.",
            },
        )
    except Exception as exc:
        db.rollback()
        return _redirect_with_query(
            run_id,
            {
                "ib_status": "fetch-failed",
                "ib_message": str(exc),
            },
        )


@router.get("/runs/{run_id}/ib/fetch/{fetch_run_id}/progress")
def run_ib_fetch_progress(run_id: int, fetch_run_id: int, db: DbSession) -> dict[str, object]:
    _require_run(db, run_id)
    progress = get_fetch_progress(db, fetch_run_id)
    if progress["run_id"] != run_id:
        raise HTTPException(status_code=404, detail="IB fetch run not found for this run.")
    return progress


@router.get("/runs/{run_id}/ib/fetches/{fetch_run_id}", response_class=HTMLResponse)
def run_ib_fetch_progress_page(
    run_id: int,
    fetch_run_id: int,
    request: Request,
    db: DbSession,
) -> HTMLResponse:
    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    progress = get_fetch_progress(db, fetch_run_id)
    if progress["run_id"] != run_id:
        raise HTTPException(status_code=404, detail="IB fetch run not found for this run.")
    return templates.TemplateResponse(
        request,
        "fetch_progress.html",
        {
            "active_nav": "runs",
            "run": run,
            "progress": progress,
            "terminal_statuses": ["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"],
            "status_url": f"/runs/{run_id}/ib/fetches/{fetch_run_id}/status",
        },
    )


@router.get("/runs/{run_id}/ib/fetches/{fetch_run_id}/status")
def run_ib_fetch_status(run_id: int, fetch_run_id: int, db: DbSession) -> dict[str, object]:
    return run_ib_fetch_progress(run_id=run_id, fetch_run_id=fetch_run_id, db=db)


@router.get("/runs/{run_id}/ib/fetches/{fetch_run_id}/failed.csv")
def export_failed_fetch_items(run_id: int, fetch_run_id: int, db: DbSession) -> Response:
    progress = run_ib_fetch_progress(run_id=run_id, fetch_run_id=fetch_run_id, db=db)
    lines = ["ticker,what_to_show,status,error_message"]
    for item in progress["items"]:
        if item["status"] == "FAILED":
            lines.append(
                ",".join(
                    [
                        _csv_cell(item["ticker"]),
                        _csv_cell(item["what_to_show"]),
                        _csv_cell(item["status"]),
                        _csv_cell(item["error_message"] or ""),
                    ]
                )
            )
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="run-{run_id}-fetch-{fetch_run_id}-failed.csv"'
            ),
        },
    )


@router.post("/runs/{run_id}/ib/fetch/{fetch_run_id}/cancel")
def cancel_run_ib_fetch_action(
    run_id: int,
    fetch_run_id: int,
    db: DbSession,
) -> RedirectResponse:
    _require_run(db, run_id)
    try:
        existing = get_fetch_progress(db, fetch_run_id)
        if existing["run_id"] != run_id:
            raise HTTPException(status_code=404, detail="IB fetch run not found for this run.")
        progress = cancel_fetch_job(db, fetch_run_id)
        db.commit()
        return _redirect_to_fetch_progress(
            run_id=run_id,
            fetch_run_id=fetch_run_id,
            params={
                "ib_status": "fetch-cancelled",
                "ib_message": progress["message"] or "IB fetch cancellation requested.",
            },
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/ib/fetch/{fetch_run_id}/retry-failed")
def retry_failed_run_ib_fetch_action(
    run_id: int,
    fetch_run_id: int,
    db: DbSession,
) -> RedirectResponse:
    return _resume_fetch_action(run_id=run_id, fetch_run_id=fetch_run_id, db=db, label="Retry")


@router.post("/runs/{run_id}/ib/fetch/{fetch_run_id}/resume")
def resume_run_ib_fetch_action(
    run_id: int,
    fetch_run_id: int,
    db: DbSession,
) -> RedirectResponse:
    return _resume_fetch_action(run_id=run_id, fetch_run_id=fetch_run_id, db=db, label="Resume")


def _resume_fetch_action(
    run_id: int,
    fetch_run_id: int,
    db: Session,
    label: str,
) -> RedirectResponse:
    _require_run(db, run_id)
    try:
        fetch_run, plan, options = resume_fetch_job(db, fetch_run_id)
        if fetch_run.run_id != run_id:
            raise HTTPException(status_code=404, detail="IB fetch run not found for this run.")
        db.commit()
        submit_fetch_job(fetch_run.id, plan, options)
        return _redirect_to_fetch_progress(
            run_id=run_id,
            fetch_run_id=fetch_run.id,
            params={
                "ib_status": "fetch-queued",
                "ib_message": f"{label} fetch {fetch_run.id} queued.",
            },
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    earnings_blocked_count = sum(
        result.earnings_risk_level == "blocked" for result in combined_results
    )
    earnings_high_risk_count = sum(
        result.earnings_risk_level == "high" for result in combined_results
    )
    earnings_medium_risk_count = sum(
        result.earnings_risk_level == "medium" for result in combined_results
    )
    earnings_unknown_count = sum(
        result.earnings_risk_level == "unknown" for result in combined_results
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
        "earnings_blocked_count": earnings_blocked_count,
        "earnings_high_risk_count": earnings_high_risk_count,
        "earnings_medium_risk_count": earnings_medium_risk_count,
        "earnings_unknown_count": earnings_unknown_count,
        "duplicate_ticker_count": duplicate_ticker_count,
        "raw_column_count": raw_column_count,
        "top_complete": top_complete,
    }


def _workflow_steps(
    run: UploadRun,
    rows: list[RawCompanyRow],
    technical_scores: list[TechnicalScore],
    combined_results: list[CombinedResult],
    coverage: OhlcvCoverageSummary,
) -> list[dict[str, str]]:
    technical_warning_count = sum(
        score.insufficient_data or score.technical_confidence in {"low", "error"}
        for score in technical_scores
    )
    return [
        _workflow_step(
            "Uploaded",
            "failed" if run.status == "FAILED" else "completed" if rows else "not-started",
            "Raw rows are stored." if rows else "Waiting for a valid CSV.",
        ),
        _workflow_step(
            "Fundamentals scored",
            "completed" if run.fundamental_scores else "not-started",
            f"{len(run.fundamental_scores)} score rows.",
        ),
        _workflow_step(
            "Column mapping checked",
            "completed" if rows else "not-started",
            "Raw columns mapped to canonical scoring fields." if rows else "Upload a CSV first.",
        ),
        _workflow_step(
            "IB fetch planned",
            "completed" if rows else "not-started",
            "Fetch plan can be previewed." if rows else "Upload tickers first.",
        ),
        _workflow_step(
            "OHLCV ready",
            _coverage_status(coverage),
            (
                f"{coverage.ready_count} ready, {coverage.insufficient_count} short, "
                f"{coverage.stale_count} stale, {coverage.missing_count} missing."
            ),
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
            "Cockpit ready",
            "completed" if combined_results else "not-started",
            f"{len(combined_results)} combined rows.",
        ),
        _workflow_step(
            "Export ready",
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


def _coverage_status(coverage: OhlcvCoverageSummary) -> str:
    if coverage.total_tickers == 0:
        return "not-started"
    if (
        coverage.ready_count == 0
        and coverage.insufficient_count == 0
        and coverage.stale_count == 0
        and coverage.missing_volume_count == 0
        and coverage.failed_contract_count == 0
    ):
        return "not-started"
    if coverage.ready_count < coverage.total_tickers:
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
        "high_accrual_risk",
        "poor_cash_conversion",
        "balance_sheet_stress",
        "dividend_payout_risk",
        "earnings_blocked",
    }:
        return "danger"
    if flag in {
        "missing_fundamental",
        "missing_technical",
        "incomplete_data",
        "earnings_quality_risk",
        "capital_efficiency_deterioration",
        "asset_growth_without_returns",
        "forward_quality_weak",
        "sparse_fundamental_data",
        "earnings_high_risk",
        "earnings_medium_risk",
        "earnings_date_missing",
        "earnings_date_unparseable",
    }:
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


def _tickers_from_fetch_form(ticker_subset: str) -> list[str]:
    symbols = ticker_subset.replace("\n", ",").replace(" ", ",").split(",")
    seen: set[str] = set()
    tickers: list[str] = []
    for symbol in symbols:
        ticker = symbol.strip().upper()
        if ticker and ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers


def _what_to_show_values(values: list[str] | None) -> tuple[str, ...]:
    allowed = set(DEFAULT_WHAT_TO_SHOW)
    normalized = tuple(value for value in values or DEFAULT_WHAT_TO_SHOW if value in allowed)
    return normalized or DEFAULT_WHAT_TO_SHOW


def _fetch_request_options(
    ticker_subset: str,
    include_benchmarks: bool,
    force_refresh: bool,
    force_full_backfill: bool,
    what_to_show: tuple[str, ...],
) -> dict[str, object]:
    return {
        "ticker_subset": ticker_subset,
        "include_benchmarks": include_benchmarks,
        "force_refresh": force_refresh,
        "force_full_backfill": force_full_backfill,
        "what_to_show": list(what_to_show),
        "include_adjusted": "ADJUSTED_LAST" in what_to_show,
        "include_trades": "TRADES" in what_to_show,
    }


def _estimated_fetch_duration_label(plan: FetchPlan, settings) -> str:
    request_count = plan.estimated_request_count
    if request_count <= 0:
        return "No IB requests expected."

    seconds_from_gap = request_count * float(settings.ib_min_seconds_between_requests)
    seconds_from_rate = request_count / float(settings.ib_requests_per_minute) * 60
    estimated_seconds = max(seconds_from_gap, seconds_from_rate)
    if estimated_seconds < 60:
        return f"About {round(estimated_seconds)} seconds minimum."

    estimated_minutes = estimated_seconds / 60
    if estimated_minutes < 60:
        return f"About {round(estimated_minutes, 1)} minutes minimum."

    estimated_hours = estimated_minutes / 60
    return f"About {round(estimated_hours, 1)} hours minimum."


def _fetch_plan_action_counts(plan: FetchPlan) -> list[dict[str, object]]:
    labels = {
        "SKIP": "Skip",
        "TOP_UP_RECENT": "Top-up",
        "REFRESH_RECENT": "Refresh",
        "FULL_BACKFILL": "Backfill",
        "FORCE_REFRESH": "Force full",
        "CONTRACT_RESOLUTION_REQUIRED": "Resolve",
        "UNSUPPORTED": "Unsupported",
        "FAILED": "Failed",
    }
    counts: dict[str, int] = {action: 0 for action in labels}
    for item in plan.items:
        counts[item.action.value] = counts.get(item.action.value, 0) + 1
    return [
        {
            "action": action,
            "label": label,
            "count": counts[action],
        }
        for action, label in labels.items()
        if counts[action]
    ]


def _fetch_plan_json_url(
    run_id: int,
    ticker_subset: str,
    include_benchmarks: bool,
    force_refresh: bool,
    force_full_backfill: bool,
    what_to_show: list[str] | None,
) -> str:
    params: list[tuple[str, str]] = [
        ("format", "json"),
        ("include_benchmarks", "true" if include_benchmarks else "false"),
        ("force_refresh", "true" if force_refresh else "false"),
        ("force_full_backfill", "true" if force_full_backfill else "false"),
    ]
    if ticker_subset:
        params.append(("ticker_subset", ticker_subset))
    for value in what_to_show or []:
        params.append(("what_to_show", value))
    return f"/runs/{run_id}/ib/plan?{urlencode(params)}"


def _coverage_actions(coverage: OhlcvCoverageSummary) -> dict[str, str]:
    statuses = {
        "missing": {"missing"},
        "stale": {"stale"},
        "short": {"insufficient", "missing_volume"},
        "failed": {"contract_failed"},
        "not_ready": {"missing", "stale", "insufficient", "missing_volume", "contract_failed"},
    }
    return {
        name: ", ".join(
            item.ticker
            for item in coverage.items
            if item.status in target_statuses
        )
        for name, target_statuses in statuses.items()
    }


def _pipeline_status_for_run(
    db: Session,
    run_id: int,
    pipeline_id: int | None,
) -> dict[str, object] | None:
    if pipeline_id is None:
        return None
    status = _require_pipeline_for_run(db, pipeline_id, run_id)
    return _pipeline_status_payload(db, status)


def _require_pipeline_for_run(
    db: Session,
    pipeline_id: int,
    run_id: int,
) -> PipelineStatusDto:
    try:
        status = get_pipeline_status(db, pipeline_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if status.upload_run_id != run_id:
        raise HTTPException(status_code=404, detail="Pipeline run not found for this run.")
    return status


def _pipeline_status_payload(
    db: Session,
    status: PipelineStatusDto,
) -> dict[str, object]:
    steps = [
        {
            "step_name": step.step_name,
            "label": step.step_name.replace("_", " ").title(),
            "step_order": step.step_order,
            "status": step.status,
            "started_at": step.started_at,
            "completed_at": step.completed_at,
            "message": step.message,
            "error_message": step.error_message,
            "retry_count": step.retry_count,
        }
        for step in status.steps
    ]
    completed_steps = sum(step["status"] in {"COMPLETED", "SKIPPED"} for step in steps)
    total_steps = len(steps)
    job = db.get(BackgroundJob, status.background_job_id) if status.background_job_id else None
    return {
        "pipeline_run_id": status.pipeline_run_id,
        "upload_run_id": status.upload_run_id,
        "status": status.status,
        "current_step": status.current_step,
        "current_step_label": (
            status.current_step.replace("_", " ").title() if status.current_step else ""
        ),
        "requested_by": status.requested_by,
        "started_at": status.started_at,
        "completed_at": status.completed_at,
        "created_at": status.created_at,
        "message": status.message,
        "error_message": status.error_message,
        "background_job_id": status.background_job_id,
        "job_status": job.status if job else None,
        "job_cancel_requested": bool(job.requested_cancel) if job else False,
        "completed_steps": completed_steps,
        "total_steps": total_steps,
        "percentage": round((completed_steps / total_steps) * 100, 1) if total_steps else 0.0,
        "steps": steps,
    }


def _pagination_query_params(request: Request) -> str:
    params = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != "page" and value not in {"", "None"}
    ]
    return urlencode(params)


def _parse_date_filter(value: str) -> date | None:
    value = value.strip()
    return date.fromisoformat(value) if value else None


def _parse_decimal_filter(value: str) -> Decimal | None:
    value = value.strip()
    return Decimal(value) if value else None


def _parse_bool_filter(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    return None


def _redirect_with_query(run_id: int, params: dict[str, str]) -> RedirectResponse:
    safe_params = {key: value.replace("\n", " ").strip()[:500] for key, value in params.items()}
    return RedirectResponse(
        url=f"/runs/{run_id}?{urlencode(safe_params)}",
        status_code=303,
    )


def _redirect_to_fetch_progress(
    run_id: int,
    fetch_run_id: int,
    params: dict[str, str],
) -> RedirectResponse:
    safe_params = {key: value.replace("\n", " ").strip()[:500] for key, value in params.items()}
    return RedirectResponse(
        url=f"/runs/{run_id}/ib/fetches/{fetch_run_id}?{urlencode(safe_params)}",
        status_code=303,
    )


def _ticker_chart_context(db: Session, run_id: int, ticker: str) -> dict[str, object]:
    run = _load_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    normalized_ticker = ticker.upper()
    raw_row = _by_ticker(run.raw_company_rows).get(normalized_ticker)
    if raw_row is None:
        raise HTTPException(status_code=404, detail="Ticker not found in this run.")

    fundamental = _by_ticker(run.fundamental_scores).get(normalized_ticker)
    technical = _by_ticker(run.technical_scores).get(normalized_ticker)
    combined = _by_ticker(run.combined_results).get(normalized_ticker)
    company_name = _first_context_value(
        getattr(combined, "company_name", None),
        raw_row.company_name,
    )
    sector = _first_context_value(
        getattr(combined, "sector", None),
        raw_row.sector,
    )

    return {
        "run": run,
        "ticker": normalized_ticker,
        "raw_row": raw_row,
        "fundamental": fundamental,
        "technical": technical,
        "combined": combined,
        "company_name": company_name,
        "sector": sector,
        "chart_data_url": f"/api/runs/{run.id}/tickers/{normalized_ticker}/chart-data",
        "back_url": f"/runs/{run.id}",
        "score_cards": build_score_cards(raw_row, fundamental, technical, combined),
    }


def _by_ticker(rows: list) -> dict[str, object]:
    return {row.ticker.upper(): row for row in rows}


def _first_context_value(*values: object) -> object:
    return next((value for value in values if value is not None and value != ""), None)


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


def _csv_cell(value: object) -> str:
    text = str(value).replace('"', '""')
    return f'"{text}"' if any(char in text for char in [",", '"', "\n"]) else text


def _require_run(db: Session, run_id: int) -> None:
    run_exists = db.scalar(select(UploadRun.id).where(UploadRun.id == run_id))
    if not run_exists:
        raise HTTPException(status_code=404, detail="Run not found")
