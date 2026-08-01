from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import WinnerForwardOutcome, WinnerProcessingRun


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
        return {
            "pending_outcomes": pending_count,
            "overdue_pending_outcomes": overdue_count,
            "failed_processing_runs": failed_count,
            "recent_processing_runs": [_processing_run_payload(row) for row in recent_runs],
        }


def _processing_run_payload(row: WinnerProcessingRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "background_job_id": row.background_job_id,
        "run_id": row.run_id,
        "process_type": row.process_type,
        "status": row.status,
        "config_hash": row.config_hash,
        "source_cutoff_at": row.source_cutoff_at.isoformat()
        if row.source_cutoff_at
        else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "counts": row.counts_json or {},
        "checkpoint": row.checkpoint_json or {},
        "error_message": row.error_message,
    }


def _count(db: Session, statement) -> int:
    return int(db.scalar(statement) or 0)
