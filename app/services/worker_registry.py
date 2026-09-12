from __future__ import annotations

import os
import socket
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundWorker
from app.observability.db_monitor import get_database_monitor
from app.observability.resource_sampler import process_sampler_status
from app.observability.transaction_metrics import publish_after_commit
from app.services.background_queue import job_queue_class, normalize_worker_queues
from app.services.process_identity import process_started_at


def register_worker(
    db: Session,
    *,
    worker_id: str,
    queues: Iterable[str],
    heartbeat_timeout_seconds: int,
    hostname: str | None = None,
    process_id: int | None = None,
    now: datetime | None = None,
    instance_id: str | None = None,
    process_start: datetime | None = None,
) -> BackgroundWorker:
    clean_worker_id = worker_id.strip()
    if not clean_worker_id:
        raise ValueError("worker_id is required")
    registered_at = now or datetime.now(UTC)
    host = hostname or socket.gethostname()
    pid = process_id or os.getpid()
    queue_names = list(normalize_worker_queues(queues))
    worker = db.get(BackgroundWorker, clean_worker_id)
    requested_instance_id = instance_id or (
        worker.instance_id if worker is not None and worker.instance_id else uuid4().hex
    )
    replace_registration = worker is None
    if worker is None:
        worker = BackgroundWorker(worker_id=clean_worker_id)
        db.add(worker)
    elif (
        worker.stopping_at is None
        and worker.heartbeat_at is not None
        and worker.heartbeat_at >= registered_at - timedelta(seconds=heartbeat_timeout_seconds)
        and (
            worker.hostname != host
            or worker.process_id != pid
            or (requested_instance_id is not None and worker.instance_id != requested_instance_id)
        )
    ):
        raise RuntimeError(f"worker_id {clean_worker_id!r} is already active")
    elif worker.stopping_at is not None or worker.heartbeat_at < (
        registered_at - timedelta(seconds=heartbeat_timeout_seconds)
    ):
        replace_registration = True
    worker.queues_json = queue_names
    worker.hostname = host
    worker.process_id = pid
    worker.instance_id = requested_instance_id
    if replace_registration:
        worker.started_at = registered_at
        if process_start is not None:
            worker.process_started_at = process_start
        else:
            try:
                worker.process_started_at = process_started_at(pid)
            except Exception:
                worker.process_started_at = registered_at
        worker.generation = int(worker.generation or 0) + 1
        worker.launcher_process_id = None
        worker.quiesce_requested_at = None
        worker.quiesced_at = None
    worker.heartbeat_at = registered_at
    worker.control_loop_heartbeat_at = registered_at
    worker.stopping_at = None
    db.flush()
    return worker


def heartbeat_worker_control_loop(
    db: Session,
    worker_id: str,
    *,
    instance_id: str | None = None,
    now: datetime | None = None,
) -> BackgroundWorker | None:
    """Record functional claim-loop progress separately from the process heartbeat."""
    if not isinstance(db, Session):
        # Lightweight unit-test stores have no mapped registration; production
        # workers always use a SQLAlchemy Session.
        return None
    worker = db.get(BackgroundWorker, worker_id)
    if worker is None:
        raise RuntimeError(f"worker {worker_id!r} is not registered")
    if instance_id is not None and worker.instance_id != instance_id:
        raise RuntimeError(f"worker registration {worker_id!r} is owned by another process")
    worker.control_loop_heartbeat_at = now or datetime.now(UTC)
    db.flush()
    return worker


def heartbeat_worker(
    db: Session,
    worker_id: str,
    *,
    hostname: str | None = None,
    process_id: int | None = None,
    now: datetime | None = None,
    instance_id: str | None = None,
    rss_bytes: int | None = None,
    private_bytes: int | None = None,
    cpu_percent: float | None = None,
    memory_status: str | None = None,
) -> BackgroundWorker:
    worker = db.get(BackgroundWorker, worker_id)
    if worker is None:
        raise RuntimeError(f"worker {worker_id!r} is not registered")
    _require_owner(
        worker,
        hostname=hostname or socket.gethostname(),
        process_id=process_id or os.getpid(),
        instance_id=instance_id,
    )
    observed_at = now or datetime.now(UTC)
    worker.heartbeat_at = observed_at
    if worker.quiesce_requested_at is not None and worker.quiesced_at is None:
        worker.quiesced_at = observed_at
        requested_at = worker.quiesce_requested_at
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=UTC)
        publish_after_commit(
            db,
            "observe",
            "swinglens_worker_quiesce_ack_latency_seconds",
            max(0.0, (observed_at - requested_at).total_seconds()),
        )
    else:
        if worker.quiesce_requested_at is None:
            worker.quiesced_at = None
    if instance_id is not None:
        worker.instance_id = instance_id
    if rss_bytes is not None:
        worker.rss_bytes = rss_bytes
    if private_bytes is not None:
        worker.private_bytes = private_bytes
    if cpu_percent is not None:
        worker.cpu_percent = cpu_percent
    if memory_status is not None:
        worker.memory_status = memory_status
    sampler = process_sampler_status("worker")
    monitor = get_database_monitor()
    if _telemetry_columns_available(db):
        monitor_status = monitor.status() if monitor is not None and monitor.enabled else {}
        worker.telemetry_status = (
            "FAILED"
            if monitor_status.get("fatal_error") or monitor_status.get("writer_alive") is False
            else ("OPTIONAL_UNAVAILABLE" if monitor is None or not monitor.enabled else "OK")
        )
        failed_categories = [
            key for key, value in sampler.items() if key != "last_observed_at" and value == "failed"
        ]
        worker.resource_collector_status = "FAILED" if failed_categories else "OK"
        worker.resource_collector_heartbeat_at = sampler.get("last_observed_at")
    db.flush()
    return worker


def _telemetry_columns_available(db: Session) -> bool:
    try:
        names = {
            column["name"] for column in sa_inspect(db.get_bind()).get_columns("background_workers")
        }
        return "telemetry_status" in names
    except Exception:
        return False


def mark_worker_stopping(
    db: Session,
    worker_id: str,
    *,
    hostname: str | None = None,
    process_id: int | None = None,
    now: datetime | None = None,
    instance_id: str | None = None,
) -> BackgroundWorker | None:
    worker = db.get(BackgroundWorker, worker_id)
    if worker is None:
        return None
    if (
        worker.hostname != (hostname or socket.gethostname())
        or worker.process_id != (process_id or os.getpid())
        or (instance_id is not None and worker.instance_id != instance_id)
    ):
        return None
    stopped_at = now or datetime.now(UTC)
    worker.heartbeat_at = stopped_at
    worker.stopping_at = stopped_at
    db.flush()
    return worker


def _require_owner(
    worker: BackgroundWorker,
    *,
    hostname: str,
    process_id: int,
    instance_id: str | None = None,
) -> None:
    if (
        worker.hostname != hostname
        or worker.process_id != process_id
        or (instance_id is not None and worker.instance_id != instance_id)
    ):
        raise RuntimeError(f"worker registration {worker.worker_id!r} is owned by another process")


def associate_worker_launcher(
    db: Session,
    *,
    worker_id: str,
    instance_id: str,
    launcher_process_id: int,
) -> BackgroundWorker | None:
    worker = db.get(BackgroundWorker, worker_id)
    if worker is None or worker.instance_id != instance_id or worker.stopping_at is not None:
        return None
    worker.launcher_process_id = launcher_process_id
    db.flush()
    return worker


def retire_worker_registration(
    db: Session,
    *,
    worker_id: str,
    expected_instance_id: str | None,
    expected_generation: int,
    expected_process_id: int | None,
    now: datetime | None = None,
) -> BackgroundWorker | None:
    """Retire only the exact registration snapshot observed by the supervisor."""
    worker = db.get(BackgroundWorker, worker_id)
    if worker is None:
        return None
    if (
        worker.instance_id != expected_instance_id
        or int(worker.generation or 0) != int(expected_generation)
        or worker.process_id != expected_process_id
    ):
        return None
    stopped_at = now or datetime.now(UTC)
    worker.heartbeat_at = stopped_at
    worker.stopping_at = stopped_at
    db.flush()
    return worker


def live_workers(
    db: Session,
    *,
    heartbeat_timeout_seconds: int,
    now: datetime | None = None,
) -> list[BackgroundWorker]:
    observed_at = now or datetime.now(UTC)
    threshold = observed_at - timedelta(seconds=heartbeat_timeout_seconds)
    return list(
        db.scalars(
            select(BackgroundWorker)
            .where(BackgroundWorker.stopping_at.is_(None))
            .where(BackgroundWorker.heartbeat_at >= threshold)
            .where(BackgroundWorker.instance_id.is_not(None))
            .where(BackgroundWorker.process_started_at.is_not(None))
            .order_by(BackgroundWorker.worker_id)
        ).all()
    )


def has_live_worker_for_job(
    db: Session,
    *,
    job_type: str,
    heartbeat_timeout_seconds: int,
    now: datetime | None = None,
) -> bool:
    required_queue = job_queue_class(job_type)
    return any(
        required_queue in set(worker.queues_json or [])
        for worker in live_workers(
            db,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            now=now,
        )
    )
