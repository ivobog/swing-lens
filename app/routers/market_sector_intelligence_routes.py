from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
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

router = APIRouter(prefix="/api/market-sector-intelligence", tags=["market-sector-intelligence"])


class SectorRefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/refresh", status_code=202)
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN, reason="Explicit fixed-cohort MSI market-data acquisition",
    mutation_capability=MutationCapability.NORMAL_ONLY, operation="http.msi.sector_refresh",
    csrf_required=True, local_admin_required=True,
)
def market_sector_refresh(
    request: Request, body: SectorRefreshBody, db: Annotated[Session, Depends(get_db)],
):
    origin = request.headers.get("origin")
    if (origin != str(request.base_url).rstrip("/")
            or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"):
        raise HTTPException(status_code=403, detail={"code": "SAME_ORIGIN_REQUIRED"})
    try:
        payload = refresh.enqueue_sector_refresh(db, settings=request.app.state.settings)
        db.commit()
        return payload
    except refresh.SectorRefreshRejected as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "message": exc.message},
            headers={"Retry-After": str(refresh.RETRY_SECONDS)},
        ) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/refresh/active")
def market_sector_refresh_active(request: Request, db: Annotated[Session, Depends(get_db)]):
    with db.no_autoflush:
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


@router.get("/charts/price-series")
def market_sector_price_series(
    db: Annotated[Session, Depends(get_db)],
    horizon: int = 126,
    symbols: Annotated[str | None, Query(max_length=128)] = None,
):
    try:
        config = load_market_sector_intelligence_config()
    except MarketSectorIntelligenceConfigError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "MARKET_SECTOR_CONFIGURATION_INVALID"}
        ) from exc
    try:
        return MarketSectorChartService().build(db, config=config, symbols=symbols, horizon=horizon)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_CHART_SELECTION"}) from exc


@router.get("/coverage")
def market_sector_coverage(db: Annotated[Session, Depends(get_db)]):
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
    return MarketSectorIntelligenceCoverageService().build(db, config=config)


@router.get("/rotation/preview")
def market_sector_rotation_preview(db: Annotated[Session, Depends(get_db)]):
    try:
        config = load_market_sector_intelligence_config()
    except MarketSectorIntelligenceConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "MARKET_SECTOR_CONFIGURATION_INVALID", "message": str(exc)},
        ) from exc
    return MarketSectorRotationService().preview(db, config=config)


@router.get("/rotation/latest")
def market_sector_rotation_latest(db: Annotated[Session, Depends(get_db)]):
    return _snapshot_or_404(MarketSectorRotationRepository().latest(db))


@router.get("/rotation/history")
def market_sector_rotation_history(
    db: Annotated[Session, Depends(get_db)], limit: Annotated[int, Query(ge=1, le=100)] = 30
):
    return MarketSectorRotationRepository().history(db, limit=limit)


@router.get("/rotation/snapshots/{snapshot_id}")
def market_sector_rotation_detail(snapshot_id: int, db: Annotated[Session, Depends(get_db)]):
    return _snapshot_or_404(MarketSectorRotationRepository().detail(db, snapshot_id))


def _snapshot_or_404(payload):
    if payload is None:
        raise HTTPException(status_code=404, detail={"code": "NO_ROTATION_SNAPSHOT"})
    return payload


@router.get("/technicals/preview")
def market_sector_technicals_preview(db: Annotated[Session, Depends(get_db)]):
    return _technical_preview(db)


@router.get("/technicals/preview/{etf_symbol}")
def market_sector_technicals_detail(etf_symbol: str, db: Annotated[Session, Depends(get_db)]):
    return _technical_preview(db, etf_symbol=etf_symbol)


def _technical_preview(db, *, etf_symbol=None):
    try:
        config = load_market_sector_intelligence_config()
        symbol = etf_symbol.strip().upper() if etf_symbol is not None else None
        if symbol is not None and symbol not in {sector.etf_symbol for sector in config.sectors}:
            raise HTTPException(status_code=404, detail={"code": "UNSUPPORTED_SECTOR_ETF"})
        payload = MarketSectorTechnicalService().preview(db, config=config)
    except (MarketSectorIntelligenceConfigError, TechnicalConfigurationError) as exc:
        raise HTTPException(
            status_code=503, detail={"code": "MARKET_SECTOR_CONFIGURATION_INVALID"},
        ) from exc
    if symbol is None:
        return payload
    return {
        **{key: value for key, value in payload.items() if key != "rows"},
        "row": next(row for row in payload["rows"] if row["etf_symbol"] == symbol),
    }
