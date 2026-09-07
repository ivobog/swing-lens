from __future__ import annotations

import logging
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

import psutil
from sqlalchemy import Engine, case, func, select, text
from sqlalchemy.orm import Session, undefer

from app.models.tables import BackgroundJob, BackgroundSupervisor, BackgroundWorker
from app.observability.db_monitor import get_database_monitor
from app.observability.logging import log_event
from app.observability.metrics import operational_metrics
from app.services.background_job_service import JobStatus, prune_enqueue_attempt_evidence
from app.services.background_queue import job_queue_class

logger = logging.getLogger(__name__)
_health_lock = Lock()
_process_health: dict[str, dict[str, Any]] = {}


def process_sampler_status(process_role: str) -> dict[str, Any]:
    with _health_lock:
        return dict(_process_health.get(process_role, {}))


class ResourceSampler:
    def __init__(
        self,
        *,
        process_role: str,
        interval_seconds: float,
        worker_id: str | None = None,
    ) -> None:
        self.process_role = process_role
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.worker_id = worker_id
        self._stop = Event()
        self._thread: Thread | None = None
        self._process: Any | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.sample_once()
        self._thread = Thread(
            target=self._run, name=f"resource-sampler-{self.process_role}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 1)

    def sample_once(self) -> dict[str, float]:
        result: dict[str, float] = {}

        def memory_sample() -> None:
            process = self._get_process()
            memory = process.memory_info()
            result["rss_bytes"] = float(memory.rss)
            operational_metrics.set_gauge(
                "swinglens_process_rss_bytes", memory.rss, process_role=self.process_role
            )
            if self.worker_id:
                operational_metrics.set_gauge(
                    "swinglens_worker_rss_bytes", memory.rss, worker_id=self.worker_id
                )
                private = getattr(memory, "private", None)
                if private is not None:
                    operational_metrics.set_gauge(
                        "swinglens_worker_private_bytes", private, worker_id=self.worker_id
                    )

        def cpu_sample() -> None:
            cpu = max(0.0, self._get_process().cpu_percent(None))
            result["cpu_percent"] = cpu
            operational_metrics.set_gauge(
                "swinglens_process_cpu_percent", cpu, process_role=self.process_role
            )
            if self.worker_id:
                operational_metrics.set_gauge(
                    "swinglens_worker_cpu_percent", cpu, worker_id=self.worker_id
                )

        self._sample_category("process_memory", memory_sample)
        self._sample_category("cpu", cpu_sample)
        if self.worker_id:
            self._sample_category(
                "worker_registry",
                lambda: operational_metrics.set_gauge(
                    "swinglens_worker_up", 1, worker_id=self.worker_id
                ),
            )
        return result

    def _get_process(self) -> Any:
        if self._process is None:
            self._process = psutil.Process()
        return self._process

    def _sample_category(self, category: str, callback) -> None:
        _fault_contained_sample(self.process_role, category, callback)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.sample_once()


class SystemMetricsCollector:
    """Periodically projects bounded authoritative state into Prometheus gauges."""

    def __init__(self, engine: Engine, settings: Any) -> None:
        self.engine = engine
        self.settings = settings
        self.interval_seconds = float(settings.observability_collection_interval_seconds)
        self._db_size_interval = float(settings.observability_db_size_interval_seconds)
        self._last_db_size = 0.0
        self._stop = Event()
        self._thread: Thread | None = None
        self._job_state_series: set[tuple[str, str]] = set()
        self._progress_series: set[tuple[str, str]] = set()
        self._worker_series: set[str] = set()

    def start(self) -> None:
        if not self.settings.observability_metrics_enabled:
            return
        self._thread = Thread(target=self._run, name="system-metrics-collector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 1)

    def collect_once(self, now: datetime | None = None) -> None:
        observed_at = now or datetime.now(UTC)
        self._db_category("queue_snapshot", lambda session: self._queue(session, observed_at))
        self._db_category("worker_registry", lambda session: self._workers(session, observed_at))
        self._db_category(
            "supervisor_registry", lambda session: self._supervisor(session, observed_at)
        )
        if time.monotonic() - self._last_db_size >= self._db_size_interval:
            if self._db_category("db_size", self._database_size):
                self._last_db_size = time.monotonic()
            self._db_category("enqueue_attempt_retention", self._retention)
        _fault_contained_sample("web", "db_pool", self._pool)
        _fault_contained_sample("web", "disk", self._disk_space)
        _fault_contained_sample("web", "log_storage", self._log_storage)
        _fault_contained_sample("web", "sql_recorder", self._db_monitor)

    def _db_category(self, category: str, callback) -> bool:
        succeeded = False

        def collect() -> None:
            nonlocal succeeded
            with Session(self.engine) as session:
                callback(session)
            succeeded = True

        _fault_contained_sample("web", category, collect)
        return succeeded

    def _queue(self, session: Session, now: datetime) -> None:
        queue_state = case(
            (
                (BackgroundJob.status == JobStatus.QUEUED) & (BackgroundJob.run_after <= now),
                "RUNNABLE",
            ),
            (BackgroundJob.status == JobStatus.QUEUED, "SCHEDULED"),
            else_=BackgroundJob.status,
        ).label("queue_state")
        rows = session.execute(
            select(
                BackgroundJob.job_type,
                queue_state,
                func.count(BackgroundJob.id),
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
        ).all()
        counts: dict[tuple[str, str], int] = {}
        oldest: dict[str, float] = {}
        type_counts: dict[tuple[str, str], int] = {}
        for job_type, status, count, created_at in rows:
            queue_class = job_queue_class(str(job_type))
            counts[(queue_class, str(status))] = counts.get((queue_class, str(status)), 0) + int(
                count
            )
            type_counts[(str(job_type), str(status))] = int(count)
            if status == "RUNNABLE" and created_at is not None:
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                oldest[queue_class] = max(
                    oldest.get(queue_class, 0.0), max(0.0, (now - created_at).total_seconds())
                )
        for queue_class in ("interactive", "broker", "background"):
            for status in (
                "RUNNABLE",
                "SCHEDULED",
                JobStatus.RUNNING,
                JobStatus.BLOCKED,
                JobStatus.STALLED,
                JobStatus.RECOVERING,
            ):
                operational_metrics.set_gauge(
                    "swinglens_queue_depth",
                    counts.get((queue_class, status), 0),
                    queue_class=queue_class,
                    status=status,
                )
            operational_metrics.set_gauge(
                "swinglens_queue_oldest_age_seconds",
                oldest.get(queue_class, 0.0),
                queue_class=queue_class,
            )
        for (job_type, status), count in type_counts.items():
            metric = {
                JobStatus.RUNNING: "swinglens_jobs_running",
                JobStatus.STALLED: "swinglens_jobs_stalled",
                JobStatus.RECOVERING: "swinglens_jobs_recovering",
            }.get(status)
            if metric:
                operational_metrics.set_gauge(metric, count, job_type=job_type)
        active_state_series = {
            (job_type, status)
            for job_type, status in type_counts
            if status in {JobStatus.RUNNING, JobStatus.STALLED, JobStatus.RECOVERING}
        }
        for job_type, status in self._job_state_series - active_state_series:
            metric = {
                JobStatus.RUNNING: "swinglens_jobs_running",
                JobStatus.STALLED: "swinglens_jobs_stalled",
                JobStatus.RECOVERING: "swinglens_jobs_recovering",
            }[status]
            operational_metrics.set_gauge(metric, 0, job_type=job_type)
        self._job_state_series = active_state_series
        running = session.scalars(
            select(BackgroundJob).where(BackgroundJob.status == JobStatus.RUNNING).limit(100)
        ).all()
        active_progress_series: set[tuple[str, str]] = set()
        for job in running:
            if job.last_progress_at is None:
                continue
            last_progress = job.last_progress_at
            if last_progress.tzinfo is None:
                last_progress = last_progress.replace(tzinfo=UTC)
            stage = job.progress_stage or "unknown"
            operational_metrics.set_gauge(
                "swinglens_job_progress_age_seconds",
                max(0.0, (now - last_progress).total_seconds()),
                job_type=job.job_type,
                stage=stage,
            )
            active_progress_series.add((job.job_type, stage))
        for job_type, stage in self._progress_series - active_progress_series:
            operational_metrics.set_gauge(
                "swinglens_job_progress_age_seconds", 0, job_type=job_type, stage=stage
            )
        self._progress_series = active_progress_series

    def _workers(self, session: Session, now: datetime) -> None:
        workers = session.scalars(
            select(BackgroundWorker)
            .options(undefer(BackgroundWorker.cpu_percent))
            .where(BackgroundWorker.stopping_at.is_(None))
            .limit(20)
        ).all()
        active_worker_series = {worker.worker_id for worker in workers}
        for worker in workers:
            heartbeat = worker.heartbeat_at
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=UTC)
            age = max(0.0, (now - heartbeat).total_seconds())
            operational_metrics.set_gauge(
                "swinglens_worker_up",
                float(age <= self.settings.job_worker_heartbeat_timeout_seconds),
                worker_id=worker.worker_id,
            )
            operational_metrics.set_gauge(
                "swinglens_worker_heartbeat_age_seconds", age, worker_id=worker.worker_id
            )
            if worker.rss_bytes is not None:
                operational_metrics.set_gauge(
                    "swinglens_worker_rss_bytes", worker.rss_bytes, worker_id=worker.worker_id
                )
            if worker.private_bytes is not None:
                operational_metrics.set_gauge(
                    "swinglens_worker_private_bytes",
                    worker.private_bytes,
                    worker_id=worker.worker_id,
                )
            if worker.cpu_percent is not None:
                operational_metrics.set_gauge(
                    "swinglens_worker_cpu_percent",
                    worker.cpu_percent,
                    worker_id=worker.worker_id,
                )
            status = str(worker.memory_status or "NORMAL").upper()
            for candidate in ("NORMAL", "WARNING", "CRITICAL"):
                operational_metrics.set_gauge(
                    "swinglens_worker_memory_status",
                    float(status == candidate),
                    worker_id=worker.worker_id,
                    status=candidate,
                )
        for worker_id in self._worker_series - active_worker_series:
            operational_metrics.set_gauge("swinglens_worker_up", 0, worker_id=worker_id)
        self._worker_series = active_worker_series

    def _supervisor(self, session: Session, now: datetime) -> None:
        row = session.scalar(
            select(BackgroundSupervisor)
            .where(BackgroundSupervisor.stopping_at.is_(None))
            .order_by(BackgroundSupervisor.heartbeat_at.desc())
            .limit(1)
        )
        if row is None:
            operational_metrics.set_gauge("swinglens_supervisor_up", 0)
            return
        heartbeat = (
            row.heartbeat_at if row.heartbeat_at.tzinfo else row.heartbeat_at.replace(tzinfo=UTC)
        )
        age = max(0.0, (now - heartbeat).total_seconds())
        operational_metrics.set_gauge("swinglens_supervisor_heartbeat_age_seconds", age)
        operational_metrics.set_gauge(
            "swinglens_supervisor_up",
            float(age <= self.settings.job_worker_heartbeat_timeout_seconds),
        )

    def _pool(self) -> None:
        pool = self.engine.pool
        for metric, method in (
            ("swinglens_db_pool_size", "size"),
            ("swinglens_db_pool_checked_out", "checkedout"),
            ("swinglens_db_pool_overflow", "overflow"),
        ):
            value = getattr(pool, method, None)
            if callable(value):
                operational_metrics.set_gauge(metric, max(0, value()))
        configured = getattr(pool, "configured_max_overflow", None)
        max_overflow = (
            int(configured())
            if callable(configured)
            else int(self.settings.database_pool_max_overflow)
        )
        size = getattr(pool, "size", lambda: 0)()
        operational_metrics.set_gauge(
            "swinglens_db_pool_capacity", max(0, int(size) + max_overflow)
        )

    def _database_size(self, session: Session) -> None:
        if self.engine.dialect.name != "postgresql":
            return
        size = session.scalar(text("select pg_database_size(current_database())"))
        if size is not None:
            operational_metrics.set_gauge("swinglens_db_size_bytes", size)

    def _retention(self, session: Session) -> None:
        prune_enqueue_attempt_evidence(
            session,
            retention_days=self.settings.observability_enqueue_attempt_retention_days,
        )
        session.commit()

    def _disk_space(self) -> None:
        paths = {
            "uploads": Path(self.settings.upload_dir),
            "exports": Path(self.settings.export_dir),
            "cache": Path(self.settings.cache_dir),
            "telemetry": Path(self.settings.db_monitor_log_dir),
        }
        for path_class, path in paths.items():
            path.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(path.resolve())
            operational_metrics.set_gauge(
                "swinglens_disk_free_bytes", usage.free, path_class=path_class
            )
            operational_metrics.set_gauge(
                "swinglens_disk_total_bytes", usage.total, path_class=path_class
            )

    def _log_storage(self) -> None:
        log_bytes = sum(
            item.stat().st_size
            for item in Path(self.settings.db_monitor_log_dir).glob("**/*")
            if item.is_file()
        )
        operational_metrics.set_gauge(
            "swinglens_log_storage_bytes", log_bytes, log_class="db_monitor"
        )

    def _db_monitor(self) -> None:
        monitor = get_database_monitor()
        if monitor is None:
            return
        status = monitor.status()
        for priority in ("P0", "P1", "P2"):
            operational_metrics.set_gauge(
                "swinglens_db_monitor_queue_depth",
                status.get(f"{priority.lower()}_queue_depth", 0),
                priority=priority,
            )

    def _run(self) -> None:
        # The first bounded collection happens immediately, but off the application
        # startup path so observability cannot delay web readiness.
        self.collect_once()
        while not self._stop.wait(self.interval_seconds):
            self.collect_once()

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def _fault_contained_sample(process_role: str, category: str, callback) -> None:
    try:
        callback()
    except Exception as exc:
        with _health_lock:
            state = _process_health.setdefault(process_role, {})
            state[category] = "failed"
            state["last_observed_at"] = datetime.now(UTC)
        try:
            operational_metrics.set_gauge(
                "swinglens_resource_sampler_up", 0, process_role=process_role, category=category
            )
            operational_metrics.increment(
                "swinglens_resource_sampler_errors_total",
                process_role=process_role,
                category=category,
            )
        except Exception:
            pass
        log_event(
            logger,
            "observability.resource_sampler_category_failed",
            level=logging.WARNING,
            process_role=process_role,
            category=category,
            error_type=type(exc).__name__,
            error_summary=str(exc),
        )
        return
    with _health_lock:
        state = _process_health.setdefault(process_role, {})
        state[category] = "ok"
        state["last_observed_at"] = datetime.now(UTC)
    try:
        operational_metrics.set_gauge(
            "swinglens_resource_sampler_up", 1, process_role=process_role, category=category
        )
    except Exception:
        pass
