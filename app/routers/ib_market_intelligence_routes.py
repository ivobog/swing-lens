from __future__ import annotations

import csv
import io
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.ib_market_intelligence_tables import IBExecutionFill, IBTradeEpisode
from app.security import ROUTE_CLASS_LOCAL_ADMIN, require_local_admin, unsafe_route
from app.services.background_job_service import enqueue_job
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.ib_market_intelligence.evidence_hash import evidence_hash
from app.services.ib_market_intelligence.job_handlers import (
    IB_FLEX_IMPORT,
    IB_HISTOGRAM_FETCH,
    IB_INTELLIGENCE_HISTORICAL_REFRESH,
    IB_INTELLIGENCE_LIVE_SNAPSHOT,
    IB_INTELLIGENCE_REBUILD_FEATURES,
    IB_SCANNER_RUN,
)
from app.services.ib_market_intelligence.query_service import (
    histogram_detail,
    latest_features,
    operations,
    overview,
    scanner_runs,
    trade_journal,
)
from app.settings import Settings
from app.templates import templates

router = APIRouter(tags=["ib-market-intelligence"])
DbSession = Annotated[Session, Depends(get_db)]


class TickerJobRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=500)
    module: Literal["LIQUIDITY", "SHORT_PRESSURE", "VOLATILITY", "OPTIONS_ACTIVITY"]
    start_date: date | None = None
    end_date: date | None = None


class ScannerJobRequest(BaseModel):
    presets: list[str] = Field(default_factory=list, max_length=10)


class HistogramJobRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=100)
    period: str | None = None


class FlexJobRequest(BaseModel):
    query_type: Literal["TRADE_CONFIRMATIONS", "ACTIVITY"] = "TRADE_CONFIRMATIONS"
    dry_run: bool = False
    force: bool = False


class ExcludeFillRequest(BaseModel):
    excluded: bool = True
    reason: str = Field(min_length=3, max_length=500)


@router.get("/ib-intelligence", response_class=HTMLResponse)
def intelligence_page(request: Request, db: DbSession) -> HTMLResponse:
    _read_guard(request.app.state.settings)
    return templates.TemplateResponse(
        request,
        "ib_market_intelligence.html",
        {
            "active_nav": "ib-intelligence",
            "payload": overview(db),
        },
    )


@router.get("/ib-intelligence/scanner", response_class=HTMLResponse)
def scanner_page(request: Request, db: DbSession) -> HTMLResponse:
    _read_guard(request.app.state.settings)
    return templates.TemplateResponse(
        request,
        "ib_market_discovery.html",
        {
            "active_nav": "ib-intelligence",
            "payload": scanner_runs(db),
        },
    )


@router.get("/ib-intelligence/trade-journal", response_class=HTMLResponse)
def trade_journal_page(request: Request, db: DbSession) -> HTMLResponse:
    _read_guard(request.app.state.settings)
    return templates.TemplateResponse(
        request,
        "ib_trade_journal.html",
        {
            "active_nav": "ib-intelligence",
            "payload": trade_journal(db),
        },
    )


@router.get("/ib-intelligence/operations", response_class=HTMLResponse)
def operations_page(request: Request, db: DbSession) -> HTMLResponse:
    _read_guard(request.app.state.settings)
    config = load_ib_market_intelligence_config(settings=request.app.state.settings)
    return templates.TemplateResponse(
        request,
        "ib_intelligence_operations.html",
        {
            "active_nav": "ib-intelligence",
            "payload": operations(db),
            "config": config,
            "csrf_token": request.app.state.local_admin_csrf_token,
        },
    )


@router.get("/api/ib-intelligence/run/{run_id}")
def intelligence_run(run_id: int, db: DbSession) -> dict:
    match = next((row for row in operations(db)["runs"] if row["id"] == run_id), None)
    if match is None:
        raise HTTPException(404, "IB intelligence run not found")
    return match


@router.get("/api/ib-intelligence/ticker/{ticker}")
def ticker_intelligence(ticker: str, db: DbSession) -> dict:
    return {"ticker": ticker.upper(), "features": latest_features(db, ticker=ticker)}


@router.get("/api/ib-intelligence/scanner/runs")
def scanner_runs_api(db: DbSession) -> dict:
    return scanner_runs(db)


@router.get("/api/ib-intelligence/scanner/candidates.csv")
def scanner_candidates_csv(db: DbSession) -> Response:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["Symbol", "IBConId", "BestRank", "UniverseSource", "DiscoveryReasons"])
    for row in scanner_runs(db)["candidate_pool"]:
        writer.writerow(
            [
                row["ticker"],
                row["ib_conid"],
                row["best_rank"],
                row["universe_source"],
                "; ".join(
                    f"{reason['scanner']}#{reason['rank']}" for reason in row["discovery_reasons"]
                ),
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ibkr_scanner_candidates.csv"},
    )


@router.get("/api/ib-intelligence/histogram/{ticker}")
def histogram_api(ticker: str, db: DbSession) -> dict:
    payload = histogram_detail(db, ticker)
    if payload is None:
        raise HTTPException(404, "Histogram evidence not found")
    return payload


@router.get("/api/ib-intelligence/trade-journal")
def trade_journal_api(
    db: DbSession,
    group_by: Annotated[str, Query()] = "setup_family",
) -> dict:
    return trade_journal(db, group_by=group_by, include_account=False)


@router.get("/api/ib-intelligence/operations")
def operations_api(db: DbSession) -> dict:
    return operations(db)


@router.post("/api/ib-intelligence/refresh")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues read-only IB historical intelligence",
    csrf_required=True,
    local_admin_required=True,
)
def queue_historical(request: Request, payload: TickerJobRequest, db: DbSession) -> dict:
    _admin_guard(request, payload.module)
    if payload.module == "OPTIONS_ACTIVITY":
        raise HTTPException(400, "Options Activity uses the live-snapshot operation")
    priority = 110 if payload.module == "LIQUIDITY" else 130
    return _enqueue(
        db,
        IB_INTELLIGENCE_HISTORICAL_REFRESH,
        payload.model_dump(),
        priority=priority,
    )


@router.post("/api/ib-intelligence/live-snapshot")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues bounded read-only IB market snapshots",
    csrf_required=True,
    local_admin_required=True,
)
def queue_live(request: Request, payload: TickerJobRequest, db: DbSession) -> dict:
    _admin_guard(request, payload.module)
    if payload.module == "LIQUIDITY":
        raise HTTPException(400, "Liquidity v1 uses historical BID_ASK refresh")
    return _enqueue(db, IB_INTELLIGENCE_LIVE_SNAPSHOT, payload.model_dump(), priority=110)


@router.post("/api/ib-intelligence/scanner/run")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues read-only IB scanner discovery",
    csrf_required=True,
    local_admin_required=True,
)
def queue_scanner(request: Request, payload: ScannerJobRequest, db: DbSession) -> dict:
    _admin_guard(request, "SCANNER")
    return _enqueue(db, IB_SCANNER_RUN, payload.model_dump(), priority=140)


@router.post("/api/ib-intelligence/histogram/fetch")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues read-only IB histogram requests",
    csrf_required=True,
    local_admin_required=True,
)
def queue_histogram(request: Request, payload: HistogramJobRequest, db: DbSession) -> dict:
    _admin_guard(request, "HISTOGRAM")
    return _enqueue(db, IB_HISTOGRAM_FETCH, payload.model_dump(), priority=120)


@router.post("/api/ib-intelligence/flex/import")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues reporting-only Flex import",
    csrf_required=True,
    local_admin_required=True,
)
def queue_flex(request: Request, payload: FlexJobRequest, db: DbSession) -> dict:
    _admin_guard(request, "FLEX")
    return _enqueue(db, IB_FLEX_IMPORT, payload.model_dump(), priority=130)


@router.post("/api/ib-intelligence/rebuild-features")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="queues deterministic intelligence feature rebuild",
    csrf_required=True,
    local_admin_required=True,
)
def queue_rebuild(request: Request, payload: TickerJobRequest, db: DbSession) -> dict:
    _admin_guard(request, payload.module)
    return _enqueue(db, IB_INTELLIGENCE_REBUILD_FEATURES, payload.model_dump(), priority=150)


@router.post("/api/ib-intelligence/trade-journal/fills/{fill_id}/exclude")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="marks local journal evidence excluded without deleting broker evidence",
    csrf_required=True,
    local_admin_required=True,
)
def exclude_fill(
    fill_id: int,
    request: Request,
    payload: ExcludeFillRequest,
    db: DbSession,
) -> dict:
    _admin_guard(request, "FLEX")
    fill = db.get(IBExecutionFill, fill_id)
    if fill is None:
        raise HTTPException(404, "Execution fill not found")
    fill.is_excluded = payload.excluded
    fill.exclusion_reason = payload.reason if payload.excluded else None
    affected = 0
    for episode in db.query(IBTradeEpisode).all():
        if fill_id in (episode.fill_ids_json or []):
            episode.is_excluded = payload.excluded
            affected += 1
    db.commit()
    return {
        "fill_id": fill.id,
        "excluded": fill.is_excluded,
        "affected_episodes": affected,
        "broker_evidence_deleted": False,
    }


def _enqueue(db: Session, job_type: str, payload: dict, *, priority: int) -> dict:
    normalized_tickers = sorted(set(payload.get("tickers") or []))
    semantic = {
        **payload,
        "tickers": normalized_tickers,
        "effective_date": date.today().isoformat(),
    }
    config_hash = load_ib_market_intelligence_config().config_hash
    request_key = f"{job_type}:{evidence_hash({'config_hash': config_hash, 'scope': semantic})}"
    job = enqueue_job(
        db,
        job_type=job_type,
        payload=payload,
        priority=priority,
        request_key=request_key,
        coalesce=True,
    )
    db.commit()
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "request_key": job.request_key,
        "coalesced": bool(getattr(job, "_coalesced", False)),
    }


def _read_guard(settings: Settings) -> None:
    if not settings.ib_market_intelligence_enabled:
        raise HTTPException(404, "IB Market Intelligence is disabled")


def _admin_guard(request: Request, module: str) -> Settings:
    settings: Settings = request.app.state.settings
    require_local_admin(
        request,
        enabled=settings.ib_market_intelligence_enabled,
        disabled_message="IB Market Intelligence is disabled",
        local_only_message="IB Market Intelligence operations are local-only",
        csrf_message="A valid local-admin CSRF token is required",
        csrf_required=True,
    )
    enabled = {
        "LIQUIDITY": settings.ib_liquidity_enabled,
        "SHORT_PRESSURE": settings.ib_short_pressure_enabled,
        "VOLATILITY": settings.ib_volatility_intelligence_enabled,
        "OPTIONS_ACTIVITY": settings.ib_options_activity_enabled,
        "SCANNER": settings.ib_scanner_enabled,
        "HISTOGRAM": settings.ib_histogram_enabled,
        "FLEX": settings.ib_flex_journal_enabled,
    }.get(module, False)
    config = load_ib_market_intelligence_config(settings=settings)
    section_name = {
        "LIQUIDITY": "liquidity",
        "SHORT_PRESSURE": "short_pressure",
        "VOLATILITY": "volatility",
        "OPTIONS_ACTIVITY": "options_activity",
        "SCANNER": "scanner",
        "HISTOGRAM": "histogram",
        "FLEX": "flex",
    }.get(module)
    config_enabled = bool(
        config.section("engine").get("enabled", False)
        and section_name
        and config.section(section_name).get("enabled", False)
    )
    if not enabled or not config_enabled:
        raise HTTPException(409, f"{module} is disabled")
    return settings
