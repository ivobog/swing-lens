from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    BackgroundJob,
    WinnerForwardOutcome,
    WinnerMarketDataObligation,
    WinnerProcessingRun,
)
from app.services.background_job_service import ACTIVE_JOB_STATUSES
from app.services.us_market_calendar import latest_completed_us_trading_day
from app.services.winner_probability.job_handlers import (
    WINNER_OUTCOME_MATURATION,
)
from app.services.winner_probability.market_data_obligation_service import (
    global_daily_bar_lag,
)
from app.services.winner_probability.outcome_orchestration_service import (
    H5NextOpenOrchestrationService,
)


class WinnerProbabilityOperationsService:
    def status(self, db: Session) -> dict[str, Any]:
        today = date.today()
        pending_count = _count(
            db,
            select(func.count(WinnerForwardOutcome.id)).where(
                WinnerForwardOutcome.status == "PENDING"
            ),
        )
        overdue_count = _count(
            db,
            select(func.count(WinnerForwardOutcome.id))
            .where(WinnerForwardOutcome.status == "PENDING")
            .where(WinnerForwardOutcome.due_session < today),
        )
        failed_count = _count(
            db,
            select(func.count(WinnerProcessingRun.id)).where(
                WinnerProcessingRun.status == "FAILED"
            ),
        )
        recent_runs = list(
            db.scalars(
                select(WinnerProcessingRun)
                .order_by(WinnerProcessingRun.started_at.desc().nullslast())
                .limit(20)
            )
        )
        active_maturation = db.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.job_type == WINNER_OUTCOME_MATURATION)
            .where(BackgroundJob.status.in_(ACTIVE_JOB_STATUSES))
            .order_by(BackgroundJob.created_at.asc(), BackgroundJob.id.asc())
            .limit(1)
        )
        queue = H5NextOpenOrchestrationService().queue_state(db)
        obligation_counts = {
            str(status): int(count)
            for status, count in db.execute(
                select(
                    WinnerMarketDataObligation.status,
                    func.count(WinnerMarketDataObligation.id),
                ).group_by(WinnerMarketDataObligation.status)
            )
        }
        daily_lag = global_daily_bar_lag(
            db,
            latest_completed_session=latest_completed_us_trading_day(),
        )
        return {
            "pending_outcomes": pending_count,
            "overdue_pending_outcomes": overdue_count,
            "failed_processing_runs": failed_count,
            "maturation_queue": {
                "due_total": queue.due_total,
                "retry_eligible_now": queue.retry_eligible_now,
                "retry_deferred": queue.retry_deferred,
                "earliest_retry_not_before": (
                    queue.earliest_retry_not_before.isoformat()
                    if queue.earliest_retry_not_before
                    else None
                ),
            },
            "active_maturation_workflow": (
                _active_job_payload(active_maturation) if active_maturation else None
            ),
            "market_data_obligations": obligation_counts,
            "daily_bar_freshness": {
                "latest_completed_session": daily_lag.latest_completed_session.isoformat(),
                "latest_local_session": (
                    daily_lag.latest_local_session.isoformat()
                    if daily_lag.latest_local_session
                    else None
                ),
                "lag_sessions": daily_lag.lag_sessions,
                "degraded": daily_lag.degraded,
            },
            "recent_processing_runs": [
                _processing_run_payload(
                    row,
                    db.get(BackgroundJob, row.background_job_id)
                    if row.background_job_id is not None
                    else None,
                )
                for row in recent_runs
            ],
        }


def _processing_run_payload(
    row: WinnerProcessingRun, job: BackgroundJob | None = None
) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "background_job_id": row.background_job_id,
        "run_id": row.run_id,
        "process_type": row.process_type,
        "status": row.status,
        "config_hash": row.config_hash,
        "source_cutoff_at": row.source_cutoff_at.isoformat() if row.source_cutoff_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "counts": row.counts_json or {},
        "checkpoint": row.checkpoint_json or {},
        "error_message": row.error_message,
    }
    if job is not None:
        payload.update(
            {
                "root_job_id": job.root_job_id,
                "parent_job_id": job.parent_job_id,
                "workflow_key": job.workflow_key,
                "continuation_depth": job.continuation_depth,
                "trigger_source": job.trigger_source,
            }
        )
    return payload


def _active_job_payload(job: BackgroundJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "status": job.status,
        "workflow_key": job.workflow_key,
        "root_job_id": job.root_job_id,
        "parent_job_id": job.parent_job_id,
        "continuation_depth": job.continuation_depth,
        "trigger_source": job.trigger_source,
        "run_after": job.run_after.isoformat() if job.run_after else None,
    }


def _count(db: Session, statement) -> int:
    return int(db.scalar(statement) or 0)
