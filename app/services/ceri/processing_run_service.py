from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriProcessingRun
from app.services.ceri.config import load_ceri_config
from app.services.ceri.deployment_identity import current_deployment_identity


class CeriProcessingRunService:
    def create_or_get(
        self,
        db: Session,
        *,
        job_type: str,
        request_key: str,
        scope: dict[str, Any] | None = None,
        config_version: str | None = None,
        config_hash: str | None = None,
        actor: str | None = None,
        cutoff_at: datetime | None = None,
    ) -> tuple[CeriProcessingRun, bool]:
        existing = _maybe_scalar(
            db,
            select(CeriProcessingRun).where(
                CeriProcessingRun.deterministic_request_key == request_key
            ),
        )
        if existing is not None:
            return existing, False
        run = CeriProcessingRun(
            job_type=job_type,
            status="RUNNING",
            deterministic_request_key=request_key,
            scope_json=scope or {},
            config_version=config_version,
            config_hash=config_hash,
            deployment_identity_json=current_deployment_identity(
                config_hash=config_hash,
                calculation_version=load_ceri_config().engine.calculation_version,
            ),
            actor=actor,
            cutoff_at=cutoff_at,
            started_at=_utcnow(),
        )
        db.add(run)
        db.flush()
        return run, True

    def finish(
        self,
        db: Session,
        run: CeriProcessingRun,
        *,
        status: str,
        counts: dict[str, int] | None = None,
        checkpoint: dict[str, Any] | None = None,
        errors: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> CeriProcessingRun:
        counts = counts or {}
        run.status = status
        run.read_count = counts.get("read", run.read_count or 0)
        run.normalized_count = counts.get("normalized", run.normalized_count or 0)
        run.feature_count = counts.get("features", run.feature_count or 0)
        run.score_snapshot_count = counts.get("score_snapshots", run.score_snapshot_count or 0)
        run.change_event_count = counts.get("change_events", run.change_event_count or 0)
        run.alert_event_count = counts.get("alerts", run.alert_event_count or 0)
        run.warning_count = counts.get("warnings", len(warnings or []))
        run.failed_count = counts.get("failed", run.failed_count or 0)
        run.counts_json = counts
        run.checkpoint_json = checkpoint
        run.errors_json = errors
        run.completed_at = _utcnow()
        if run.started_at is not None:
            run.duration_ms = _duration_ms(run.started_at, run.completed_at)
        db.flush()
        return run


def _maybe_scalar(db: Session, statement):
    scalar = getattr(db, "scalar", None)
    if callable(scalar):
        return scalar(statement)
    return None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _duration_ms(started_at: datetime, completed_at: datetime) -> int:
    if started_at.tzinfo is None and completed_at.tzinfo is not None:
        started_at = started_at.replace(tzinfo=completed_at.tzinfo)
    if completed_at.tzinfo is None and started_at.tzinfo is not None:
        completed_at = completed_at.replace(tzinfo=started_at.tzinfo)
    return int((completed_at - started_at).total_seconds() * 1000)
