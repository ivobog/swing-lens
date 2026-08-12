from __future__ import annotations

import os
import platform
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriChangeEvent,
    CeriScoreSnapshot,
)
from app.models.tables import (
    BackgroundJob,
    IBFetchItem,
    IBFetchRun,
    PipelineRun,
    PipelineStep,
    TechnicalFeatureArtifact,
    UploadRun,
)
from app.settings import Settings, get_settings

ACTIVE_CERI_PREFIX = "CERI_"


def capture_background_performance_baseline(
    db: Session,
    *,
    target_sizes: Iterable[int] = (175, 402),
    now: datetime | None = None,
    settings: Settings | None = None,
    physical_cpu_cores: int | None = None,
    memory_gib: float | None = None,
    investigate_fetch_run_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Build a read-only performance report from durable runtime history."""
    observed_at = now or datetime.now(UTC)
    runtime_settings = settings or get_settings()
    return {
        "schema_version": "swinglens-background-performance-baseline-v1",
        "captured_at": observed_at.isoformat(),
        "source": "read_only_existing_postgresql_history",
        "hardware": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "physical_cpu_cores": physical_cpu_cores,
            "logical_cpu_count": os.cpu_count(),
            "memory_gib": memory_gib,
        },
        "runtime_flags": _runtime_flags(runtime_settings),
        "queue": _queue_report(db, observed_at),
        "technical_artifacts": _technical_artifact_report(db),
        "workloads": [
            _workload_report(db, ticker_count=int(size), now=observed_at) for size in target_sizes
        ],
        "investigated_ib_fetch_runs": [
            _ib_fetch_run_report(db, db.get(IBFetchRun, int(fetch_run_id)))
            for fetch_run_id in investigate_fetch_run_ids
        ],
        "measurement_limits": [
            "IB pacing, network, and cache-write sub-timers were not persisted before Phase 1.",
            (
                "Process memory pressure was not historically sampled; current memory can be "
                "supplied by the caller."
            ),
            (
                "CERI workflow duration is derived from durable job timestamps, not an "
                "external profiler."
            ),
        ],
    }


def _runtime_flags(settings: Settings) -> dict[str, Any]:
    names = (
        "job_worker_enabled",
        "ceri_enabled",
        "ceri_provider_ingest_enabled",
        "ceri_legacy_pipeline_scheduling_enabled",
        "ceri_batched_workflow_enabled",
        "ceri_run_capture_enabled",
        "ceri_alerts_enabled",
        "technical_process_pool_enabled",
        "technical_worker_processes",
        "technical_max_in_flight",
        "technical_artifact_cache_mode",
        "technical_artifact_cache_enabled",
        "technical_artifact_cache_write_enabled",
        "technical_artifact_cache_shadow_read_enabled",
        "fetch_technical_overlap_enabled",
        "market_data_prewarm_enabled",
        "market_data_prewarm_config_version",
        "market_data_prewarm_cancel_bound_seconds",
        "market_data_prewarm_resume_delay_seconds",
    )
    return {name: getattr(settings, name) for name in names}


def _queue_report(db: Session, now: datetime) -> dict[str, Any]:
    ready = list(
        db.scalars(
            select(BackgroundJob).where(
                BackgroundJob.status == "QUEUED",
                BackgroundJob.run_after <= now,
                BackgroundJob.requested_cancel.is_(False),
            )
        )
    )
    running = list(db.scalars(select(BackgroundJob).where(BackgroundJob.status == "RUNNING")))
    oldest = min((job.created_at for job in ready), default=None)
    return {
        "ready_depth": len(ready),
        "running_depth": len(running),
        "oldest_ready_job_age_seconds": _elapsed_seconds(oldest, now),
        "ready_by_job_type": dict(sorted(Counter(job.job_type for job in ready).items())),
        "ready_by_priority": {
            str(key): value for key, value in sorted(Counter(job.priority for job in ready).items())
        },
        "stale_running_count": sum(
            bool(job.lease_expires_at and job.lease_expires_at <= now) for job in running
        ),
        "running_jobs": [
            {
                "id": job.id,
                "job_type": job.job_type,
                "related_run_id": job.related_run_id,
                "worker_id": job.worker_id,
                "started_at": _iso(job.started_at),
                "heartbeat_at": _iso(job.heartbeat_at),
                "lease_expires_at": _iso(job.lease_expires_at),
            }
            for job in running
        ],
    }


def _technical_artifact_report(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(
            TechnicalFeatureArtifact.status,
            TechnicalFeatureArtifact.artifact_kind,
            func.count(TechnicalFeatureArtifact.id),
        ).group_by(
            TechnicalFeatureArtifact.status,
            TechnicalFeatureArtifact.artifact_kind,
        )
    ).all()
    shadow_rows = db.execute(
        select(
            TechnicalFeatureArtifact.shadow_validation_status,
            func.count(TechnicalFeatureArtifact.id),
            func.sum(TechnicalFeatureArtifact.shadow_validation_count),
            func.sum(TechnicalFeatureArtifact.shadow_mismatch_count),
        ).group_by(TechnicalFeatureArtifact.shadow_validation_status)
    ).all()
    return {
        "counts": [
            {"status": status, "artifact_kind": kind, "count": int(count)}
            for status, kind, count in rows
        ],
        "shadow_validation": [
            {
                "status": status,
                "artifact_count": int(count),
                "validation_count": int(validation_count or 0),
                "mismatch_count": int(mismatch_count or 0),
            }
            for status, count, validation_count, mismatch_count in shadow_rows
        ],
        "historical_cache_hit_count": None,
        "historical_cache_miss_count": None,
    }


def _workload_report(db: Session, *, ticker_count: int, now: datetime) -> dict[str, Any]:
    row = db.execute(
        select(PipelineRun, UploadRun)
        .join(UploadRun, UploadRun.id == PipelineRun.upload_run_id)
        .where(UploadRun.row_count == ticker_count)
        .order_by(PipelineRun.created_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return {"ticker_count": ticker_count, "available": False}
    pipeline, upload_run = row
    steps = list(
        db.scalars(
            select(PipelineStep)
            .where(PipelineStep.pipeline_run_id == pipeline.id)
            .order_by(PipelineStep.step_order)
        )
    )
    result = pipeline.result_json or {}
    return {
        "ticker_count": ticker_count,
        "available": True,
        "upload_run_id": upload_run.id,
        "pipeline_run_id": pipeline.id,
        "pipeline_status": pipeline.status,
        "pipeline_created_at": _iso(pipeline.created_at),
        "pipeline_started_at": _iso(pipeline.started_at),
        "pipeline_completed_at": _iso(pipeline.completed_at),
        "pipeline_queue_delay_ms": _elapsed_ms(pipeline.created_at, pipeline.started_at),
        "pipeline_execution_ms": _elapsed_ms(pipeline.started_at, pipeline.completed_at),
        "pipeline_total_wall_ms": _elapsed_ms(pipeline.created_at, pipeline.completed_at),
        "steps": [
            {
                "name": step.step_name,
                "status": step.status,
                "duration_ms": _elapsed_ms(step.started_at, step.completed_at),
                "started_at": _iso(step.started_at),
                "completed_at": _iso(step.completed_at),
            }
            for step in steps
        ],
        "persisted_performance": result.get("performance"),
        "ib": _ib_report(db, upload_run.id),
        "ceri": _ceri_report(db, upload_run.id, now),
    }


def _ib_report(db: Session, upload_run_id: int) -> dict[str, Any]:
    fetch_run = db.scalar(
        select(IBFetchRun)
        .where(IBFetchRun.run_id == upload_run_id)
        .order_by(IBFetchRun.started_at.desc())
        .limit(1)
    )
    if fetch_run is None:
        return {"available": False}
    return _ib_fetch_run_report(db, fetch_run)


def _ib_fetch_run_report(
    db: Session,
    fetch_run: IBFetchRun | None,
) -> dict[str, Any]:
    if fetch_run is None:
        return {"available": False}
    items = list(
        db.scalars(
            select(IBFetchItem)
            .where(IBFetchItem.fetch_run_id == fetch_run.id)
            .order_by(IBFetchItem.id)
        )
    )
    requested = {ticker.upper() for ticker in (fetch_run.requested_tickers or [])}
    executed = [item for item in items if (item.attempt_count or 0) > 0]
    return {
        "available": True,
        "fetch_run_id": fetch_run.id,
        "status": fetch_run.status,
        "duration_ms": _elapsed_ms(fetch_run.started_at, fetch_run.completed_at),
        "planned_request_count": fetch_run.planned_request_count,
        "decision_counts": fetch_run.decision_counts_json,
        "executed_request_count": fetch_run.executed_request_count,
        "skipped_count": fetch_run.skipped_count,
        "force_refresh": fetch_run.force_refresh,
        "force_full_backfill": fetch_run.force_full_backfill,
        "dependency_tickers": sorted(
            {item.ticker.upper() for item in items if item.ticker.upper() not in requested}
        ),
        "actions": dict(sorted(Counter(item.action or "UNKNOWN" for item in items).items())),
        "data_types": dict(sorted(Counter(item.what_to_show for item in executed).items())),
        "historical_ranges": dict(
            sorted(Counter(item.duration or "NONE" for item in executed).items())
        ),
        "reasons": dict(sorted(Counter(item.reason or "NONE" for item in items).items())),
        "pacing_wait_ms": None,
        "network_ms": None,
        "cache_write_ms": None,
        "executed_requests": [
            {
                "ticker": item.ticker,
                "ticker_role": "requested" if item.ticker.upper() in requested else "dependency",
                "data_type": item.what_to_show,
                "historical_range": item.duration,
                "bar_size": item.bar_size,
                "action": item.action,
                "reason": item.reason,
                "stored_bar_count_before": item.current_bar_count,
                "attempt_count": item.attempt_count,
                "status": item.status,
                "decision_metadata": item.decision_metadata_json,
            }
            for item in executed
        ],
    }


def _ceri_report(db: Session, upload_run_id: int, now: datetime) -> dict[str, Any]:
    jobs = list(
        db.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.related_run_id == upload_run_id,
                BackgroundJob.job_type.startswith(ACTIVE_CERI_PREFIX),
            )
            .order_by(BackgroundJob.created_at, BackgroundJob.id)
        )
    )
    if not jobs:
        return {"available": False, "job_volume": 0}
    created_at = min(job.created_at for job in jobs)
    completed_values = [job.completed_at for job in jobs if job.completed_at is not None]
    terminal_statuses = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "STALE"}
    all_terminal = all(job.status in terminal_statuses for job in jobs)
    request_key_counts = Counter(job.request_key for job in jobs if job.request_key)
    queue_delays = [
        value for job in jobs if (value := _elapsed_ms(job.created_at, job.started_at)) is not None
    ]
    snapshot_ids = select(CeriScoreSnapshot.id).where(CeriScoreSnapshot.run_id == upload_run_id)
    change_ids = select(CeriChangeEvent.id).where(CeriChangeEvent.to_snapshot_id.in_(snapshot_ids))
    return {
        "available": True,
        "job_volume": len(jobs),
        "status_counts": dict(sorted(Counter(job.status for job in jobs).items())),
        "job_type_counts": dict(sorted(Counter(job.job_type for job in jobs).items())),
        "first_job_created_at": _iso(created_at),
        "last_job_completed_at": _iso(max(completed_values) if completed_values else None),
        "observed_span_ms": _elapsed_ms(created_at, max(completed_values) if all_terminal else now),
        "terminal_duration_ms": (
            _elapsed_ms(created_at, max(completed_values))
            if all_terminal and completed_values
            else None
        ),
        "queue_delay_ms": _distribution(queue_delays),
        "duplicate_request_keys": {
            key: count for key, count in sorted(request_key_counts.items()) if count > 1
        },
        "effects": {
            "snapshots": int(
                db.scalar(
                    select(func.count(CeriScoreSnapshot.id)).where(
                        CeriScoreSnapshot.run_id == upload_run_id
                    )
                )
                or 0
            ),
            "changes": int(db.scalar(select(func.count()).select_from(change_ids.subquery())) or 0),
            "alerts_from_changes": int(
                db.scalar(
                    select(func.count(CeriAlertEvent.id)).where(
                        CeriAlertEvent.source_change_event_id.in_(change_ids)
                    )
                )
                or 0
            ),
        },
    }


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "median": round(median, 3),
        "max": round(ordered[-1], 3),
    }


def _elapsed_seconds(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    milliseconds = _elapsed_ms(started_at, completed_at)
    return round(milliseconds / 1000, 3) if milliseconds is not None else None


def _elapsed_ms(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    if started_at is None or completed_at is None:
        return None
    if started_at.tzinfo is None and completed_at.tzinfo is not None:
        started_at = started_at.replace(tzinfo=completed_at.tzinfo)
    if completed_at.tzinfo is None and started_at.tzinfo is not None:
        completed_at = completed_at.replace(tzinfo=started_at.tzinfo)
    return round(max(0.0, (completed_at - started_at).total_seconds() * 1000), 3)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
