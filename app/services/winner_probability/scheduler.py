from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.observability.correlation import root_action_scope
from app.services.background_job_service import JobStatus
from app.services.winner_probability.job_handlers import (
    WINNER_OUTCOME_MATURATION,
    enqueue_outcome_maturation_workflow,
)
from app.services.winner_probability.trading_session_service import latest_completed_session


def schedule_primary_h5_maturation(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int = 500,
    max_batches: int = 10,
) -> BackgroundJob:
    """Idempotently schedule one durable primary-H5 drain per completed US session."""
    completed_session = latest_completed_session(now or datetime.now(UTC))
    request_key = f"winner:h5-next-open:session:{completed_session.isoformat()}"
    session_info = getattr(db, "info", None)
    if session_info is None:
        session_info = {}
        db.info = session_info
    session_cache = session_info.setdefault("winner_primary_h5_schedule_cache", {})
    cached = session_cache.get(request_key)
    if cached is not None:
        return cached
    existing = db.scalar(
        select(BackgroundJob)
        .where(BackgroundJob.job_type == WINNER_OUTCOME_MATURATION)
        .where(
            or_(
                BackgroundJob.request_key == request_key,
                BackgroundJob.status.in_(
                    (
                        JobStatus.QUEUED,
                        JobStatus.RUNNING,
                        JobStatus.BLOCKED,
                        JobStatus.RECOVERING,
                        JobStatus.STALLED,
                    )
                ),
            )
        )
        .order_by(BackgroundJob.id.desc())
        .limit(1)
    )
    # An idle eligibility poll is not an enqueue attempt. In particular, a
    # completed job for this trading session terminates before a root context or
    # durable enqueue-attempt record can be created.
    if existing is not None:
        session_cache[request_key] = existing
        return existing
    with root_action_scope("SCHEDULER", f"winner-primary-h5:{completed_session.isoformat()}"):
        scheduled = enqueue_outcome_maturation_workflow(
            db,
            payload={
                "entry_model": "NEXT_OPEN",
                "horizon_sessions": 5,
                "due_session": completed_session.isoformat(),
                "limit": batch_size,
                "max_batches": max_batches,
            },
            request_key=request_key,
            trigger_source="SCHEDULER",
            priority=35,
        )
    session_cache[request_key] = scheduled
    return scheduled
