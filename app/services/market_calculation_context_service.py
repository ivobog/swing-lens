from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import MarketCalculationContext, PipelineRun
from app.observability.transaction_metrics import publish_after_commit
from app.services.market_clock_service import MarketCalculationCutoff, MarketClockService


def create_pipeline_market_context(
    db: Session,
    pipeline: PipelineRun,
    *,
    cutoff_at: datetime | None = None,
    clock: MarketClockService | None = None,
) -> MarketCalculationCutoff:
    existing = pipeline.market_calculation_context
    if existing is not None:
        return cutoff_from_row(existing)
    cutoff = (clock or MarketClockService()).cutoff_for(
        cutoff_at or datetime.now(UTC), reason="FULL_PIPELINE_FROZEN_AT_ENQUEUE"
    )
    row = MarketCalculationContext(
        pipeline_run_id=pipeline.id,
        upload_run_id=pipeline.upload_run_id,
        cutoff_at=cutoff.cutoff_at,
        exchange_timezone=cutoff.exchange_timezone,
        latest_completed_session=cutoff.latest_completed_session,
        daily_bar_ready_at=cutoff.daily_bar_ready_at,
        calendar_version=cutoff.calendar_version,
        bar_readiness_version=cutoff.bar_readiness_version,
        cutoff_reason=cutoff.cutoff_reason,
    )
    db.add(row)
    db.flush()
    if isinstance(db, Session):
        publish_after_commit(
            db,
            "increment",
            "swinglens_market_calculation_cutoffs_total",
            scope="pipeline",
        )
    return cutoff.with_context_id(row.id)


def market_context_for_pipeline(db: Session, pipeline: PipelineRun) -> MarketCalculationCutoff:
    if not isinstance(db, Session):
        return standalone_market_context(reason="PIPELINE_TEST_SESSION_COMPATIBILITY")
    row = db.scalar(
        select(MarketCalculationContext).where(
            MarketCalculationContext.pipeline_run_id == pipeline.id
        )
    )
    if row is None:
        # Legacy queued pipeline rows are frozen once, at their first execution.
        return create_pipeline_market_context(db, pipeline)
    return cutoff_from_row(row)


def market_context_for_upload_run(
    db: Session, upload_run_id: int
) -> MarketCalculationCutoff | None:
    # Some pure calculation callers use the intentionally narrow session
    # protocol (execute/add/flush) and cannot perform ORM scalar lookups.  Such
    # callers receive an explicit standalone cutoff from their owning service.
    if not isinstance(db, Session):
        return None
    row = db.scalar(
        select(MarketCalculationContext)
        .where(MarketCalculationContext.upload_run_id == upload_run_id)
        .order_by(MarketCalculationContext.id.desc())
        .limit(1)
    )
    return cutoff_from_row(row) if row is not None else None


def standalone_market_context(
    *, reason: str, cutoff_at: datetime | None = None
) -> MarketCalculationCutoff:
    return MarketClockService().cutoff_for(cutoff_at or datetime.now(UTC), reason=reason)


def cutoff_from_row(row: MarketCalculationContext) -> MarketCalculationCutoff:
    return MarketCalculationCutoff(
        cutoff_at=row.cutoff_at,
        exchange_timezone=row.exchange_timezone,
        latest_completed_session=row.latest_completed_session,
        daily_bar_ready_at=row.daily_bar_ready_at,
        calendar_version=row.calendar_version,
        bar_readiness_version=row.bar_readiness_version,
        cutoff_reason=row.cutoff_reason,
        context_id=row.id,
    )
