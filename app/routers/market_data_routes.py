from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tables import BackgroundJob
from app.security import ROUTE_CLASS_PUBLIC_LOCAL, unsafe_route
from app.services.background_job_service import request_job_cancel
from app.services.market_data_prewarm_service import (
    MarketDataPrewarmRequest,
    enqueue_market_data_prewarm,
)

router = APIRouter(prefix="/api/market-data", tags=["market-data"])
DbSession = Annotated[Session, Depends(get_db)]


class MarketDataPrewarmBody(BaseModel):
    universe_source: str = "RECENT_RUNS"
    recent_run_count: int = Field(default=5, ge=1)
    tickers: list[str] = Field(default_factory=list)
    include_benchmarks: bool = True
    freshness_date: date | None = None
    requested_by: str = "local-user"


DEFAULT_PREWARM_BODY = MarketDataPrewarmBody()


@router.post("/prewarm")
@unsafe_route(ROUTE_CLASS_PUBLIC_LOCAL, reason="queues a local read-only market-data prewarm")
def queue_market_data_prewarm(
    db: DbSession,
    payload: MarketDataPrewarmBody = DEFAULT_PREWARM_BODY,
) -> dict[str, object]:
    request = MarketDataPrewarmRequest(
        universe_source=payload.universe_source,
        recent_run_count=payload.recent_run_count,
        tickers=tuple(payload.tickers),
        include_benchmarks=payload.include_benchmarks,
        freshness_date=payload.freshness_date,
        requested_by=payload.requested_by,
    )
    try:
        job, universe = enqueue_market_data_prewarm(db, request)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise

    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "request_key": job.request_key,
        "coalesced": bool(getattr(job, "_coalesced", False)),
        "universe_source": universe.source,
        "universe_fingerprint": universe.fingerprint,
        "ticker_count": len(universe.tickers),
        "tickers": list(universe.tickers),
        "include_benchmarks": universe.include_benchmarks,
        "freshness_date": universe.freshness_date.isoformat(),
        "status_url": f"/api/market-data/prewarm/{job.id}",
    }


@router.get("/prewarm/{job_id}")
def market_data_prewarm_status(job_id: int, db: DbSession) -> dict[str, object]:
    job = db.get(BackgroundJob, job_id)
    if job is None or job.job_type != "MARKET_DATA_PREWARM":
        raise HTTPException(status_code=404, detail="Market-data prewarm job not found.")
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "request_key": job.request_key,
        "requested_cancel": job.requested_cancel,
        "payload": job.payload_json,
        "result": job.result_json,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


@router.post("/prewarm/{job_id}/cancel")
@unsafe_route(ROUTE_CLASS_PUBLIC_LOCAL, reason="requests cancellation of a market-data prewarm")
def cancel_market_data_prewarm(job_id: int, db: DbSession) -> dict[str, object]:
    job = db.get(BackgroundJob, job_id)
    if job is None or job.job_type != "MARKET_DATA_PREWARM":
        raise HTTPException(status_code=404, detail="Market-data prewarm job not found.")
    try:
        job = request_job_cancel(db, job_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "requested_cancel": job.requested_cancel,
    }
