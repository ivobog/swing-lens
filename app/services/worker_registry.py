from __future__ import annotations

import os
import socket
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundWorker
from app.services.background_queue import job_queue_class, normalize_worker_queues


def register_worker(
    db: Session,
    *,
    worker_id: str,
    queues: Iterable[str],
    heartbeat_timeout_seconds: int,
    hostname: str | None = None,
    process_id: int | None = None,
    now: datetime | None = None,
) -> BackgroundWorker:
    clean_worker_id = worker_id.strip()
    if not clean_worker_id:
        raise ValueError("worker_id is required")
    registered_at = now or datetime.now(UTC)
    host = hostname or socket.gethostname()
    pid = process_id or os.getpid()
    queue_names = list(normalize_worker_queues(queues))
    worker = db.get(BackgroundWorker, clean_worker_id)
    replace_registration = worker is None
    if worker is None:
        worker = BackgroundWorker(worker_id=clean_worker_id)
        db.add(worker)
    elif (
        worker.stopping_at is None
        and worker.heartbeat_at is not None
        and worker.heartbeat_at >= registered_at - timedelta(seconds=heartbeat_timeout_seconds)
        and (worker.hostname != host or worker.process_id != pid)
    ):
        raise RuntimeError(f"worker_id {clean_worker_id!r} is already active")
    elif worker.stopping_at is not None or worker.heartbeat_at < (
        registered_at - timedelta(seconds=heartbeat_timeout_seconds)
    ):
        replace_registration = True
    worker.queues_json = queue_names
    worker.hostname = host
    worker.process_id = pid
    if replace_registration:
        worker.started_at = registered_at
    worker.heartbeat_at = registered_at
    worker.stopping_at = None
    db.flush()
    return worker


def heartbeat_worker(
    db: Session,
    worker_id: str,
    *,
    hostname: str | None = None,
    process_id: int | None = None,
    now: datetime | None = None,
) -> BackgroundWorker:
    worker = db.get(BackgroundWorker, worker_id)
    if worker is None:
        raise RuntimeError(f"worker {worker_id!r} is not registered")
    _require_owner(
        worker,
        hostname=hostname or socket.gethostname(),
        process_id=process_id or os.getpid(),
    )
    worker.heartbeat_at = now or datetime.now(UTC)
    worker.stopping_at = None
    db.flush()
    return worker


def mark_worker_stopping(
    db: Session,
    worker_id: str,
    *,
    hostname: str | None = None,
    process_id: int | None = None,
    now: datetime | None = None,
) -> BackgroundWorker | None:
    worker = db.get(BackgroundWorker, worker_id)
    if worker is None:
        return None
    if worker.hostname != (hostname or socket.gethostname()) or worker.process_id != (
        process_id or os.getpid()
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
) -> None:
    if worker.hostname != hostname or worker.process_id != process_id:
        raise RuntimeError(f"worker registration {worker.worker_id!r} is owned by another process")


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
