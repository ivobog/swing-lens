import logging
from datetime import UTC, datetime
from functools import wraps
from time import monotonic
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.security import ROUTE_CLASS_LOCAL_ADMIN, unsafe_route
from app.services import market_sector_refresh_service as refresh
from app.services.market_sector_intelligence_chart_service import MarketSectorChartService
from app.services.market_sector_intelligence_config import (
    MarketSectorIntelligenceConfigError,
    load_market_sector_intelligence_config,
)
from app.services.market_sector_intelligence_coverage import MarketSectorIntelligenceCoverageService
from app.services.market_sector_intelligence_rotation_repository import (
    MarketSectorRotationRepository,
)
from app.services.market_sector_intelligence_rotation_service import MarketSectorRotationService
from app.services.market_sector_intelligence_technical_service import (
    MarketSectorTechnicalService,
    TechnicalConfigurationError,
)
from app.services.runtime_mutation_authority import MutationCapability
from app.services.sector_read_budget import panel_budget


class SectorObservedRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        @wraps(original)
        async def observed(request):
            from app.observability.logging import log_event

            started, correlation, status = monotonic(), uuid4().hex, 500
            try:
                response = await original(request)
                status = response.status_code
                response.headers["X-MSI-Correlation-ID"] = correlation
                return response
            except HTTPException as exc:
                status = exc.status_code
                raise
            except RequestValidationError:
                status = 422
                raise
            finally:
                log_event(
                    logging.getLogger(__name__),
                    "sector.http.finished",
                    route=self.path,
                    method=request.method,
                    status=status,
                    duration_ms=round((monotonic() - started) * 1000, 2),
                    correlation_id=correlation,
                )

        return observed


router = APIRouter(
    prefix="/api/market-sector-intelligence",
    tags=["market-sector-intelligence"],
    route_class=SectorObservedRoute,
)


def _read_cutoff(as_of):
    if as_of is None:
        return {}
    if as_of.tzinfo is None or as_of > datetime.now(UTC):
        raise HTTPException(422, detail={"code": "INVALID_SECTOR_READ_CUTOFF"})
    from app.services.market_clock_service import MarketClockService

    return {"cutoff": MarketClockService().cutoff_for(as_of, reason="SECTOR_PAGE_FROZEN_READ")}


class SectorRefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9-]+$"
    )


@router.post("/refresh", status_code=202)
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="Explicit fixed-cohort MSI market-data acquisition",
    mutation_capability=MutationCapability.NORMAL_ONLY,
    operation="http.msi.sector_refresh",
    csrf_required=True,
    local_admin_required=True,
)
def market_sector_refresh(
    request: Request,
    body: SectorRefreshBody,
    db: Annotated[Session, Depends(get_db)],
):
    origin = request.headers.get("origin")
    if (
        origin != str(request.base_url).rstrip("/")
        or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
    ):
        raise HTTPException(status_code=403, detail={"code": "SAME_ORIGIN_REQUIRED"})
    try:
        payload = refresh.enqueue_sector_refresh(
            db, settings=request.app.state.settings, request_id=body.request_id
        )
        db.commit()
        return payload
    except refresh.SectorRefreshRejected as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
            headers={"Retry-After": str(refresh.RETRY_SECONDS)},
        ) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/refresh/active")
def market_sector_refresh_active(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    request_id: str | None = Query(default=None, max_length=64),
):
    with db.no_autoflush:
        if request_id:
            job = refresh.find_refresh_request(db, request_id, settings=request.app.state.settings)
            if job:
                return refresh.sector_refresh_status(db, job, coalesced=True)
        job = refresh.active_sector_refresh(db, settings=request.app.state.settings)
        return refresh.sector_refresh_status(db, job, coalesced=True) if job else None


@router.get("/refresh/{job_id}")
def market_sector_refresh_status(
    request: Request, job_id: int, db: Annotated[Session, Depends(get_db)],
):
    with db.no_autoflush:
        job = refresh.find_sector_refresh(db, job_id, settings=request.app.state.settings)
        if job is None:
            raise HTTPException(status_code=404, detail={"code": "SECTOR_REFRESH_NOT_FOUND"})
        return refresh.sector_refresh_status(db, job)


@router.post("/refresh/{job_id}/cancel", status_code=202)
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="Cancel the fixed-cohort MSI operation",
    mutation_capability=MutationCapability.NORMAL_ONLY,
    operation="http.msi.sector_refresh_cancel",
    csrf_required=True,
    local_admin_required=True,
)
def market_sector_refresh_cancel(
    request: Request,
    job_id: int,
    body: SectorRefreshBody,
    db: Annotated[Session, Depends(get_db)],
):
    if request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail={"code": "SAME_ORIGIN_REQUIRED"})
    job = refresh.find_sector_refresh(db, job_id, settings=request.app.state.settings)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "SECTOR_REFRESH_NOT_FOUND"})
    from app.services.ib_fetch_job_service import cancel_fetch_job

    try:
        cancel_fetch_job(db, int(job.payload_json["fetch_run_id"]), expected_job_id=job.id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, detail={"code": "REFRESH_CANCEL_TARGET_CHANGED"}) from exc
    db.commit()
    return refresh.sector_refresh_status(db, job)


@router.get("/charts/price-series")
@panel_budget
def market_sector_price_series(
    db: Annotated[Session, Depends(get_db)],
    horizon: int = 126,
    symbols: Annotated[str | None, Query(max_length=128)] = None,
    as_of: datetime | None = None,
):
    try:
        config = load_market_sector_intelligence_config()
    except MarketSectorIntelligenceConfigError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "MARKET_SECTOR_CONFIGURATION_INVALID"}
        ) from exc
    try:
        return MarketSectorChartService().build(
            db, config=config, symbols=symbols, horizon=horizon, **_read_cutoff(as_of)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_CHART_SELECTION"}) from exc


@router.get("/coverage")
@panel_budget
def market_sector_coverage(db: Annotated[Session, Depends(get_db)], as_of: datetime | None = None):
    try:
        config = load_market_sector_intelligence_config()
    except MarketSectorIntelligenceConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MARKET_SECTOR_CONFIGURATION_INVALID",
                "message": str(exc),
            },
        ) from exc
    return MarketSectorIntelligenceCoverageService().build(db, config=config, **_read_cutoff(as_of))


@router.get("/rotation/preview")
@panel_budget
def market_sector_rotation_preview(
    db: Annotated[Session, Depends(get_db)], as_of: datetime | None = None
):
    try:
        config = load_market_sector_intelligence_config()
    except MarketSectorIntelligenceConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "MARKET_SECTOR_CONFIGURATION_INVALID", "message": str(exc)},
        ) from exc
    return MarketSectorRotationService().preview(db, config=config, **_read_cutoff(as_of))


@router.get("/rotation/latest")
@panel_budget
def market_sector_rotation_latest(db: Annotated[Session, Depends(get_db)]):
    return _snapshot_or_404(MarketSectorRotationRepository().latest(db))


@router.get("/rotation/history")
@panel_budget
def market_sector_rotation_history(
    db: Annotated[Session, Depends(get_db)], limit: Annotated[int, Query(ge=1, le=100)] = 30
):
    return MarketSectorRotationRepository().history(db, limit=limit)


@router.get("/rotation/snapshots/{snapshot_id}")
@panel_budget
def market_sector_rotation_detail(snapshot_id: int, db: Annotated[Session, Depends(get_db)]):
    return _snapshot_or_404(MarketSectorRotationRepository().detail(db, snapshot_id))


def _snapshot_or_404(payload):
    if payload is None:
        raise HTTPException(status_code=404, detail={"code": "NO_ROTATION_SNAPSHOT"})
    return payload


@router.get("/technicals/preview")
@panel_budget
def market_sector_technicals_preview(
    db: Annotated[Session, Depends(get_db)], as_of: datetime | None = None
):
    return _technical_preview(db, as_of=as_of)


@router.get("/technicals/preview/{etf_symbol}")
@panel_budget
def market_sector_technicals_detail(
    etf_symbol: str, db: Annotated[Session, Depends(get_db)], as_of: datetime | None = None
):
    return _technical_preview(db, etf_symbol=etf_symbol, as_of=as_of)


def _technical_preview(db, *, etf_symbol=None, as_of=None):
    try:
        config = load_market_sector_intelligence_config()
        symbol = etf_symbol.strip().upper() if etf_symbol is not None else None
        if symbol is not None and symbol not in {sector.etf_symbol for sector in config.sectors}:
            raise HTTPException(status_code=404, detail={"code": "UNSUPPORTED_SECTOR_ETF"})
        payload = MarketSectorTechnicalService().preview(db, config=config, **_read_cutoff(as_of))
    except (MarketSectorIntelligenceConfigError, TechnicalConfigurationError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "MARKET_SECTOR_CONFIGURATION_INVALID"},
        ) from exc
    if symbol is None:
        return payload
    return {
        **{key: value for key, value in payload.items() if key != "rows"},
        "row": next(row for row in payload["rows"] if row["etf_symbol"] == symbol),
    }
