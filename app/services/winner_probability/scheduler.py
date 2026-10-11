from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.observability.correlation import root_action_scope
from app.services.background_job_service import JobStatus
from app.services.winner_probability.job_handlers import (
    WINNER_OUTCOME_MATURATION,
    enqueue_outcome_maturation_workflow,
)
from app.services.winner_probability.outcome_orchestration_service import (
    H5NextOpenOrchestrationService,
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
    operation_at = now or datetime.now(UTC)
    if operation_at.tzinfo is None or operation_at.utcoffset() is None:
        raise ValueError("MUTATION_AWARE_OPERATION_CUTOFF_REQUIRED")
    completed_session = latest_completed_session(operation_at)
    request_key = f"winner:h5-next-open:session:{completed_session.isoformat()}"
    session_info = getattr(db, "info", None)
    if session_info is None:
        session_info = {}
        db.info = session_info
    session_cache = session_info.setdefault("winner_primary_h5_schedule_cache", {})
    cached = session_cache.get(request_key)
    if cached is not None:
        return cached
    active = db.scalar(
        select(BackgroundJob)
        .where(BackgroundJob.job_type == WINNER_OUTCOME_MATURATION)
        .where(
            BackgroundJob.status.in_(
                (
                    JobStatus.QUEUED,
                    JobStatus.RUNNING,
                    JobStatus.RECOVERING,
                    JobStatus.STALLED,
                )
            )
        )
        .order_by(BackgroundJob.id.desc())
        .limit(1)
    )
    if active is not None:
        session_cache[request_key] = active
        return active

    lineage = list(
        db.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.job_type == WINNER_OUTCOME_MATURATION)
            .where(
                (BackgroundJob.request_key == request_key)
                | BackgroundJob.request_key.like(f"{request_key}:%")
            )
            .order_by(BackgroundJob.id.desc())
        )
    )
    # An idle eligibility poll is not an enqueue attempt. In particular, a
    # completed job for this trading session terminates before a root context or
    # durable enqueue-attempt record can be created.
    if lineage:
        latest = lineage[0]
        waiting = next(
            (
                item
                for item in lineage
                if item.status == JobStatus.PARTIAL
                and (item.result_json or {}).get("continuation_reason")
                == "WAITING_FOR_ACQUISITION"
            ),
            None,
        )
        if waiting is None:
            session_cache[request_key] = latest
            return latest
        ready_ids = H5NextOpenOrchestrationService().maturation_ready_outcome_ids(
            db,
            now=operation_at,
            due_session=completed_session,
        )
        if not ready_ids:
            session_cache[request_key] = latest
            return latest
        readiness_fingerprint = sha256(
            ",".join(str(value) for value in ready_ids).encode("ascii")
        ).hexdigest()[:24]
        readiness_key = f"{request_key}:ready:{readiness_fingerprint}"
        same_readiness = next(
            (item for item in lineage if item.request_key == readiness_key),
            None,
        )
        if same_readiness is not None:
            session_cache[request_key] = same_readiness
            return same_readiness
        request_key = readiness_key
    with root_action_scope("SCHEDULER", f"winner-primary-h5:{completed_session.isoformat()}"):
        scheduled = enqueue_outcome_maturation_workflow(
            db,
            payload={
                "entry_model": "NEXT_OPEN",
                "horizon_sessions": 5,
                "due_session": completed_session.isoformat(),
                "operation_cutoff_at": operation_at.isoformat(),
                "semantic_mode": "OUTCOME_MATURATION",
                "limit": batch_size,
                "max_batches": max_batches,
            },
            request_key=request_key,
            trigger_source="SCHEDULER",
            priority=35,
        )
    session_cache[request_key] = scheduled
    return scheduled
