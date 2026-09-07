from __future__ import annotations

import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import Engine, case, func, select
from sqlalchemy.orm import Session, load_only, selectinload, undefer

from app.models.ceri_tables import CeriProviderRequestTelemetry
from app.models.tables import (
    BackgroundJob,
    BackgroundJobEnqueueAttempt,
    BackgroundJobFanoutRoot,
    BackgroundSupervisor,
    BackgroundWorker,
    PipelineRun,
)
from app.observability.db_monitor import get_database_monitor
from app.observability.metrics import operational_metrics
from app.services.background_job_service import JobStatus
from app.services.background_queue import job_queue_class
from app.services.readiness_service import ReadinessService
from app.settings import Settings


class OperationsService:
    def __init__(self, *, engine: Engine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings
        self.limit = settings.observability_operations_limit
        self._readiness_cache: tuple[float, Any] | None = None
        self._readiness_lock = Lock()

    def snapshot(self, db: Session, *, now: datetime | None = None) -> dict[str, Any]:
        observed_at = now or datetime.now(UTC)
        readiness = self._readiness(observed_at, db)
        workers = self._workers(db, observed_at)
        supervisor = self._supervisor(db, observed_at)
        queue = self._queue(db, observed_at)
        pipelines = self._pipelines(db, observed_at)
        providers = self._providers(db, observed_at)
        database = self._database()
        storage = self._storage()
        roots = self._recent_roots(db, observed_at)
        anomalies = self._anomalies(readiness, workers, queue, providers, database, storage, roots)
        ib_samples = [
            sample
            for sample in operational_metrics.samples()
            if sample.name == "swinglens_ib_connected"
        ]
        ib_connected = ib_samples[-1].value if ib_samples else None
        return {
            "observed_at": observed_at.isoformat(),
            "health": {
                "web": {"ok": True, "message": "ok"},
                "ib": {
                    "ok": ib_connected in (None, 1.0),
                    "message": (
                        "optional_unavailable"
                        if ib_connected is None
                        else ("connected" if ib_connected else "last check failed")
                    ),
                },
                **{
                    name: {"ok": check.ok, "message": check.message}
                    for name, check in readiness.checks.items()
                },
            },
            "workers": workers,
            "supervisor": supervisor,
            "queue": queue,
            "pipelines": pipelines,
            "database": database,
            "providers": providers,
            "storage": storage,
            "anomalies": anomalies[: self.limit],
            "recent_roots": roots,
            "limits": {"rows": self.limit},
        }

    def causality_tree(self, db: Session, root_correlation_id: str) -> dict[str, Any]:
        get = getattr(db, "get", None)
        summary = get(BackgroundJobFanoutRoot, root_correlation_id) if callable(get) else None
        jobs = db.scalars(
            select(BackgroundJob)
            .options(
                load_only(
                    BackgroundJob.id,
                    BackgroundJob.job_type,
                    BackgroundJob.status,
                    BackgroundJob.parent_job_id,
                    BackgroundJob.progress_stage,
                    BackgroundJob.progress_processed,
                    BackgroundJob.progress_total,
                    BackgroundJob.created_at,
                    BackgroundJob.started_at,
                    BackgroundJob.completed_at,
                    BackgroundJob.last_progress_at,
                )
            )
            .where(BackgroundJob.root_correlation_id == root_correlation_id)
            .order_by(BackgroundJob.created_at, BackgroundJob.id)
            .limit(self.limit)
        ).all()
        attempts = db.scalars(
            select(BackgroundJobEnqueueAttempt)
            .where(BackgroundJobEnqueueAttempt.root_correlation_id == root_correlation_id)
            .order_by(BackgroundJobEnqueueAttempt.occurred_at, BackgroundJobEnqueueAttempt.id)
            .limit(self.limit)
        ).all()
        return {
            "root_correlation_id": root_correlation_id,
            "root": {
                "trigger_kind": attempts[0].trigger_kind if attempts else None,
                "trigger_name": attempts[0].trigger_name if attempts else None,
                "request_id": attempts[0].triggered_by_request_id if attempts else None,
            },
            "jobs": [self._job_row(job) for job in jobs],
            "enqueue_attempts": [
                {
                    "id": row.id,
                    "occurred_at": _iso(row.occurred_at),
                    "job_type": row.job_type,
                    "parent_job_id": row.parent_job_id,
                    "result": row.result,
                    "authoritative_job_id": row.authoritative_job_id,
                    "trigger_kind": row.trigger_kind,
                    "trigger_name": row.trigger_name,
                }
                for row in attempts
            ],
            "edges": [
                {"from": job.parent_job_id, "to": job.id, "kind": "child"}
                for job in jobs
                if job.parent_job_id is not None
            ]
            + [
                {"from": f"attempt:{row.id}", "to": row.authoritative_job_id, "kind": "coalesced"}
                for row in attempts
                if row.result == "COALESCED"
            ],
            "truncated": len(jobs) >= self.limit or len(attempts) >= self.limit,
            "fanout": {
                "workflow_family": summary.workflow_family if summary else None,
                "attempted": int(summary.attempted_enqueues) if summary else len(attempts),
                "created": int(summary.created_jobs)
                if summary
                else sum(row.result == "CREATED" for row in attempts),
                "coalesced": int(summary.coalesced_attempts)
                if summary
                else sum(row.result == "COALESCED" for row in attempts),
                "rejected": int(summary.rejected_attempts) if summary else 0,
                "total_descendant_count": int(summary.total_descendant_count)
                if summary
                else sum(row.result == "CREATED" for row in attempts),
                "maximum_depth": int(summary.maximum_depth) if summary else 0,
                "job_family_distribution": dict(summary.job_family_distribution_json or {})
                if summary
                else {},
            },
        }

    def _readiness(self, observed_at: datetime, db: Session | None = None):
        ttl = float(self.settings.observability_operations_readiness_cache_seconds)
        now_monotonic = time.monotonic()
        with self._readiness_lock:
            if self._readiness_cache is not None:
                cached_at, report = self._readiness_cache
                if now_monotonic - cached_at <= ttl:
                    return report
            readiness = ReadinessService(
                engine=self.engine, settings=self.settings, now=observed_at
            )
            report = (
                readiness.report(database_session=db) if db is not None else readiness.report()
            )
            self._readiness_cache = (now_monotonic, report)
            return report

    def _workers(self, db: Session, now: datetime) -> list[dict[str, Any]]:
        rows = db.scalars(
            select(BackgroundWorker)
            .options(undefer(BackgroundWorker.cpu_percent))
            .where(BackgroundWorker.stopping_at.is_(None))
            .order_by(BackgroundWorker.heartbeat_at.desc())
            .limit(20)
        ).all()
        current_jobs = {
            job.worker_id: job
            for job in db.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.status == JobStatus.RUNNING)
                .order_by(BackgroundJob.started_at.desc())
                .limit(20)
            ).all()
            if job.worker_id
        }
        return [
            {
                "worker_id": row.worker_id,
                "instance_id": row.instance_id,
                "process_id": row.process_id,
                "generation": row.generation,
                "heartbeat_age_seconds": _age(now, row.heartbeat_at),
                "rss_bytes": row.rss_bytes,
                "private_bytes": row.private_bytes,
                "cpu_percent": row.cpu_percent,
                "memory_status": row.memory_status,
                "current_job": self._job_row(current_jobs[row.worker_id], now=now)
                if row.worker_id in current_jobs
                else None,
            }
            for row in rows
        ]

    def _supervisor(self, db: Session, now: datetime) -> dict[str, Any] | None:
        row = db.scalar(
            select(BackgroundSupervisor)
            .where(BackgroundSupervisor.stopping_at.is_(None))
            .order_by(BackgroundSupervisor.heartbeat_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        return {
            "worker_id": row.worker_id,
            "instance_id": row.instance_id,
            "hostname": row.hostname,
            "process_id": row.process_id,
            "generation": row.generation,
            "heartbeat_age_seconds": _age(now, row.heartbeat_at),
        }

    def _queue(self, db: Session, now: datetime) -> list[dict[str, Any]]:
        queue_state = case(
            (
                (BackgroundJob.status == JobStatus.QUEUED) & (BackgroundJob.run_after <= now),
                "RUNNABLE",
            ),
            (BackgroundJob.status == JobStatus.QUEUED, "SCHEDULED"),
            else_=BackgroundJob.status,
        ).label("queue_state")
        rows = db.execute(
            select(
                BackgroundJob.job_type,
                queue_state,
                func.count(),
                func.min(case((queue_state == "RUNNABLE", BackgroundJob.created_at), else_=None)),
            )
            .where(
                BackgroundJob.status.in_(
                    (
                        JobStatus.QUEUED,
                        JobStatus.RUNNING,
                        JobStatus.BLOCKED,
                        JobStatus.STALLED,
                        JobStatus.RECOVERING,
                    )
                )
            )
            .group_by(BackgroundJob.job_type, queue_state)
            .limit(self.limit)
        ).all()
        grouped: dict[str, dict[str, Any]] = {}
        for job_type, status, count, oldest in rows:
            queue_class = job_queue_class(job_type)
            item = grouped.setdefault(queue_class, _empty_queue(queue_class))
            key = str(status).lower()
            item[key] = item.get(key, 0) + int(count)
            if status == "RUNNABLE" and oldest is not None:
                item["oldest_age_seconds"] = max(item["oldest_age_seconds"], _age(now, oldest))
        failures = db.execute(
            select(BackgroundJob.job_type, func.count())
            .where(BackgroundJob.status == JobStatus.FAILED)
            .where(BackgroundJob.completed_at >= now - timedelta(hours=24))
            .group_by(BackgroundJob.job_type)
            .limit(self.limit)
        ).all()
        for job_type, count in failures:
            queue_class = job_queue_class(job_type)
            item = grouped.setdefault(queue_class, _empty_queue(queue_class))
            item["failed_recent"] += int(count)
        return [
            grouped.get(name, _empty_queue(name))
            for name in ("interactive", "broker", "background")
        ]

    def _pipelines(self, db: Session, now: datetime) -> dict[str, Any]:
        active = db.scalars(
            select(PipelineRun)
            .options(selectinload(PipelineRun.steps))
            .where(PipelineRun.status.in_(("QUEUED", "RUNNING")))
            .order_by(PipelineRun.created_at.desc())
            .limit(5)
        ).all()
        failures = db.scalars(
            select(PipelineRun)
            .options(selectinload(PipelineRun.steps))
            .where(PipelineRun.status.in_(("FAILED", "BLOCKED")))
            .order_by(PipelineRun.created_at.desc())
            .limit(10)
        ).all()
        return {
            "active": [
                {
                    "id": row.id,
                    "status": row.status,
                    "stage": row.current_step,
                    "elapsed_seconds": _age(now, row.started_at or row.created_at),
                    "stage_duration_seconds": _active_stage_age(row, now),
                    "progress": _pipeline_progress(row),
                }
                for row in active
            ],
            "recent_failures": [
                {
                    "id": row.id,
                    "status": row.status,
                    "stage": row.current_step,
                    "at": _iso(row.completed_at or row.created_at),
                }
                for row in failures
            ],
        }

    def _providers(self, db: Session, now: datetime) -> list[dict[str, Any]]:
        cutoff = now - timedelta(hours=24)
        recent = (
            select(
                CeriProviderRequestTelemetry.provider.label("provider"),
                CeriProviderRequestTelemetry.error_code.label("error_code"),
                CeriProviderRequestTelemetry.retry_count.label("retry_count"),
                CeriProviderRequestTelemetry.latency_ms.label("latency_ms"),
                CeriProviderRequestTelemetry.observed_at.label("observed_at"),
            )
            .where(CeriProviderRequestTelemetry.observed_at >= cutoff)
            .order_by(CeriProviderRequestTelemetry.observed_at.desc())
            .limit(self.settings.observability_operations_provider_sample_limit)
            .cte("recent_provider_telemetry")
        )
        rows = db.execute(
            select(
                recent.c.provider,
                func.count(),
                func.sum(case((recent.c.error_code.is_not(None), 1), else_=0)),
                func.sum(recent.c.retry_count),
                func.percentile_cont(0.95).within_group(recent.c.latency_ms),
                func.max(recent.c.observed_at),
            )
            .group_by(recent.c.provider)
            .order_by(recent.c.provider)
            .limit(50)
        ).all()
        return [
            {
                "provider": provider,
                "requests": int(requests),
                "failures": int(failures or 0),
                "retries": int(retries or 0),
                "latency_ms_p95": round(float(latency or 0), 1),
                "last_observed_at": _iso(last_observed),
            }
            for provider, requests, failures, retries, latency, last_observed in rows
        ]

    def _database(self) -> dict[str, Any]:
        pool = self.engine.pool
        values = {}
        for name, method in (
            ("size", "size"),
            ("checked_out", "checkedout"),
            ("overflow", "overflow"),
        ):
            callback = getattr(pool, method, None)
            values[name] = max(0, int(callback())) if callable(callback) else None
        monitor = get_database_monitor()
        return {
            "pool": values,
            "telemetry": monitor.status() if monitor is not None else {"monitor_enabled": False},
        }

    def _storage(self) -> list[dict[str, Any]]:
        rows = []
        for path_class, path in (
            ("uploads", self.settings.upload_dir),
            ("exports", self.settings.export_dir),
            ("cache", self.settings.cache_dir),
            ("telemetry", self.settings.db_monitor_log_dir),
        ):
            target = Path(path).resolve()
            target.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(target)
            rows.append(
                {
                    "path_class": path_class,
                    "free_bytes": usage.free,
                    "total_bytes": usage.total,
                    "free_percent": round(usage.free / usage.total * 100, 1) if usage.total else 0,
                }
            )
        return rows

    def _anomalies(
        self, readiness, workers, queue, providers, database, storage, roots
    ) -> list[dict[str, str]]:
        result = []
        for name, check in readiness.checks.items():
            if not check.ok:
                result.append({"severity": "critical", "type": name, "message": check.message})
        for item in queue:
            if item["stalled"]:
                result.append(
                    {
                        "severity": "critical",
                        "type": "stall",
                        "message": f"{item['queue_class']}: {item['stalled']} stalled",
                    }
                )
            if (
                item["oldest_age_seconds"]
                >= self.settings.observability_queue_oldest_warning_seconds
            ):
                result.append(
                    {
                        "severity": "warning",
                        "type": "backlog",
                        "message": (
                            f"{item['queue_class']} oldest {item['oldest_age_seconds']:.0f}s"
                        ),
                    }
                )
        for worker in workers:
            if str(worker["memory_status"]).upper() == "CRITICAL":
                result.append(
                    {
                        "severity": "critical",
                        "type": "memory",
                        "message": f"{worker['worker_id']} memory critical",
                    }
                )
        for provider in providers:
            if provider["requests"] >= 5 and provider["failures"] / provider["requests"] >= 0.2:
                result.append(
                    {
                        "severity": "warning",
                        "type": "provider",
                        "message": f"{provider['provider']} failure rate elevated",
                    }
                )
        telemetry = database["telemetry"]
        if telemetry.get("p0_dropped", 0) or telemetry.get("fatal_error"):
            result.append(
                {"severity": "critical", "type": "telemetry", "message": "critical telemetry loss"}
            )
        for item in storage:
            if item["free_percent"] <= self.settings.observability_disk_warning_percent:
                result.append(
                    {
                        "severity": "critical"
                        if item["free_percent"] <= self.settings.observability_disk_critical_percent
                        else "warning",
                        "type": "disk",
                        "message": f"{item['path_class']} free {item['free_percent']}%",
                    }
                )
        for root in roots:
            attempts = int(root.get("attempted_enqueues", root.get("attempts", 0)))
            descendants = int(root.get("total_descendant_count", attempts))
            depth = int(root.get("maximum_depth", 0))
            family = str(root.get("workflow_family") or "OTHER")
            configured = (
                self.settings.observability_fanout_thresholds.get(family, {})
                if root.get("workflow_family")
                else {}
            )
            warning = int(
                configured.get("descendants_warning", self.settings.observability_fanout_warning)
            )
            critical = int(
                configured.get("descendants_critical", self.settings.observability_fanout_critical)
            )
            depth_warning = int(configured.get("depth_warning", 5))
            depth_critical = int(configured.get("depth_critical", 10))
            if descendants >= warning or depth >= depth_warning:
                result.append(
                    {
                        "severity": (
                            "critical"
                            if descendants >= critical or depth >= depth_critical
                            else "warning"
                        ),
                        "type": "fanout",
                        "message": (
                            f"{family} root {root['root_correlation_id']} attempted {attempts}, "
                            f"created {descendants}, depth {depth}"
                        ),
                    }
                )
        return result

    def _recent_roots(self, db: Session, observed_at: datetime) -> list[dict[str, Any]]:
        cutoff = observed_at - timedelta(hours=self.settings.observability_operations_window_hours)
        rows = db.execute(
            select(
                BackgroundJobFanoutRoot.root_correlation_id,
                BackgroundJobFanoutRoot.workflow_family,
                BackgroundJobFanoutRoot.first_occurred_at,
                BackgroundJobFanoutRoot.last_occurred_at,
                BackgroundJobFanoutRoot.attempted_enqueues,
                BackgroundJobFanoutRoot.created_jobs,
                BackgroundJobFanoutRoot.coalesced_attempts,
                BackgroundJobFanoutRoot.rejected_attempts,
                BackgroundJobFanoutRoot.total_descendant_count,
                BackgroundJobFanoutRoot.maximum_depth,
                BackgroundJobFanoutRoot.job_family_distribution_json,
            )
            .where(BackgroundJobFanoutRoot.last_occurred_at >= cutoff)
            .order_by(BackgroundJobFanoutRoot.last_occurred_at.desc())
            .limit(20)
        ).all()
        return [
            {
                "root_correlation_id": root,
                "workflow_family": family,
                "started_at": _iso(started),
                "last_occurred_at": _iso(last_occurred),
                "attempts": int(attempts),
                "attempted_enqueues": int(attempts),
                "created_jobs": int(created),
                "coalesced": int(coalesced or 0),
                "coalesced_attempts": int(coalesced or 0),
                "rejected_attempts": int(rejected or 0),
                "total_descendant_count": int(descendants or 0),
                "maximum_depth": int(maximum_depth or 0),
                "job_family_distribution": dict(distribution or {}),
            }
            for (
                root,
                family,
                started,
                last_occurred,
                attempts,
                created,
                coalesced,
                rejected,
                descendants,
                maximum_depth,
                distribution,
            ) in rows
        ]

    @staticmethod
    def _job_row(job: BackgroundJob, *, now: datetime | None = None) -> dict[str, Any]:
        duration_end = job.completed_at or now
        return {
            "id": job.id,
            "job_type": job.job_type,
            "status": job.status,
            "parent_job_id": job.parent_job_id,
            "stage": job.progress_stage,
            "processed": job.progress_processed,
            "total": job.progress_total,
            "created_at": _iso(job.created_at),
            "started_at": _iso(job.started_at),
            "completed_at": _iso(job.completed_at),
            "duration_seconds": (
                _duration(job.started_at, duration_end) if job.started_at is not None else None
            ),
            "progress_age_seconds": (
                _age(now, job.last_progress_at)
                if now is not None and job.last_progress_at is not None
                else None
            ),
        }


def _empty_queue(queue_class: str) -> dict[str, Any]:
    return {
        "queue_class": queue_class,
        "runnable": 0,
        "scheduled": 0,
        "blocked": 0,
        "running": 0,
        "stalled": 0,
        "recovering": 0,
        "failed_recent": 0,
        "oldest_age_seconds": 0.0,
    }


def _pipeline_progress(row: PipelineRun) -> dict[str, int]:
    total = len(row.steps)
    completed = sum(step.status in {"COMPLETED", "SKIPPED"} for step in row.steps)
    return {"completed": completed, "total": total}


def _active_stage_age(row: PipelineRun, now: datetime) -> float | None:
    step = next((item for item in row.steps if item.step_name == row.current_step), None)
    return _age(now, step.started_at) if step is not None and step.started_at is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _age(now: datetime, value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max(0.0, (now - value).total_seconds())


def _duration(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return max(0.0, (end - start).total_seconds())
