from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import MarketCalculationContext, PipelineRun
from app.observability.transaction_metrics import publish_after_commit
from app.services.market_clock_service import MarketCalculationCutoff, MarketClockService


class PipelineCalculationContextError(ValueError):
    """A pipeline-owned operation cannot prove its frozen market context."""


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


def resolve_pipeline_market_context(
    db: Session,
    *,
    calculation_context_id: int | None,
    upload_run_id: int,
    pipeline_run_id: int | None = None,
    expected_cutoff_at: datetime | None = None,
    expected_latest_completed_session: date | None = None,
    expected_calendar_version: str | None = None,
) -> MarketCalculationCutoff:
    """Resolve and validate the authoritative context for pipeline-owned work.

    Durable jobs may repeat cutoff/session fields for auditability, but the
    persisted context ID remains authoritative.  A missing or inconsistent
    identity fails closed instead of deriving a replacement from wall time.
    """

    if calculation_context_id is None:
        raise PipelineCalculationContextError(
            "Pipeline-owned operation is missing calculation_context_id."
        )
    row = db.get(MarketCalculationContext, int(calculation_context_id))
    if row is None:
        raise PipelineCalculationContextError(
            f"Market calculation context {calculation_context_id} was not found."
        )
    if row.pipeline_run_id is None:
        raise PipelineCalculationContextError(
            f"Market calculation context {row.id} is not owned by a pipeline."
        )
    if pipeline_run_id is not None and row.pipeline_run_id != pipeline_run_id:
        raise PipelineCalculationContextError(
            f"Market calculation context {row.id} belongs to pipeline "
            f"{row.pipeline_run_id}, not pipeline {pipeline_run_id}."
        )
    if row.upload_run_id != upload_run_id:
        raise PipelineCalculationContextError(
            f"Market calculation context {row.id} belongs to upload run "
            f"{row.upload_run_id}, not upload run {upload_run_id}."
        )
    pipeline = db.get(PipelineRun, row.pipeline_run_id)
    if pipeline is None or pipeline.upload_run_id != upload_run_id:
        raise PipelineCalculationContextError(
            f"Pipeline {row.pipeline_run_id} does not own upload run {upload_run_id}."
        )
    authoritative_id = db.scalar(
        select(MarketCalculationContext.id).where(
            MarketCalculationContext.pipeline_run_id == row.pipeline_run_id
        )
    )
    if authoritative_id != row.id:
        raise PipelineCalculationContextError(
            f"Market calculation context {row.id} is not pipeline "
            f"{row.pipeline_run_id}'s authoritative context."
        )

    cutoff = cutoff_from_row(row)
    if expected_cutoff_at is not None and cutoff.cutoff_at != expected_cutoff_at:
        raise PipelineCalculationContextError(
            f"Payload cutoff_at does not match market calculation context {row.id}."
        )
    if (
        expected_latest_completed_session is not None
        and cutoff.latest_completed_session != expected_latest_completed_session
    ):
        raise PipelineCalculationContextError(
            f"Payload as_of_session does not match market calculation context {row.id}."
        )
    if (
        expected_calendar_version is not None
        and cutoff.calendar_version != expected_calendar_version
    ):
        raise PipelineCalculationContextError(
            f"Payload calendar_version does not match market calculation context {row.id}."
        )
    return cutoff


def assert_pipeline_calculation_context(
    db: Session,
    *,
    pipeline_run_id: int,
    upload_run_id: int,
    supplied_context: MarketCalculationCutoff | None,
) -> MarketCalculationCutoff:
    if supplied_context is None:
        raise PipelineCalculationContextError(
            "Pipeline-owned operation is missing its frozen market calculation context."
        )
    authoritative = resolve_pipeline_market_context(
        db,
        calculation_context_id=supplied_context.context_id,
        upload_run_id=upload_run_id,
        pipeline_run_id=pipeline_run_id,
        expected_cutoff_at=supplied_context.cutoff_at,
        expected_latest_completed_session=supplied_context.latest_completed_session,
        expected_calendar_version=supplied_context.calendar_version,
    )
    if (
        supplied_context.exchange_timezone != authoritative.exchange_timezone
        or supplied_context.daily_bar_ready_at != authoritative.daily_bar_ready_at
        or supplied_context.bar_readiness_version != authoritative.bar_readiness_version
    ):
        raise PipelineCalculationContextError(
            "Supplied market calculation context does not match the persisted pipeline context."
        )
    return authoritative


def standalone_market_context(
    *, reason: str, cutoff_at: datetime | None = None
) -> MarketCalculationCutoff:
    return MarketClockService().cutoff_for(cutoff_at or datetime.now(UTC), reason=reason)


def prospective_pipeline_market_context(
    *, cutoff_at: datetime | None = None, clock: MarketClockService | None = None
) -> MarketCalculationCutoff:
    """Build, but do not persist, the context a pipeline enqueued now would freeze."""

    return (clock or MarketClockService()).cutoff_for(
        cutoff_at or datetime.now(UTC),
        reason="FULL_PIPELINE_FROZEN_AT_ENQUEUE",
    )


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
