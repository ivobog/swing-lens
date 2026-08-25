from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundSupervisor


def acquire_supervisor(
    db: Session,
    *,
    worker_id: str,
    instance_id: str,
    process_id: int,
    process_started_at: datetime,
    heartbeat_timeout_seconds: int,
    hostname: str | None = None,
    now: datetime | None = None,
) -> BackgroundSupervisor | None:
    observed_at = now or datetime.now(UTC)
    host = hostname or socket.gethostname()
    supervisor = db.get(BackgroundSupervisor, worker_id)
    if supervisor is None:
        supervisor = BackgroundSupervisor(
            worker_id=worker_id,
            instance_id=instance_id,
            hostname=host,
            process_id=process_id,
            process_started_at=process_started_at,
            generation=1,
            started_at=observed_at,
            heartbeat_at=observed_at,
        )
        db.add(supervisor)
    elif (
        supervisor.instance_id != instance_id
        and supervisor.stopping_at is None
        and supervisor.heartbeat_at
        >= observed_at - timedelta(seconds=heartbeat_timeout_seconds)
    ):
        return None
    elif supervisor.instance_id != instance_id:
        supervisor.instance_id = instance_id
        supervisor.hostname = host
        supervisor.process_id = process_id
        supervisor.process_started_at = process_started_at
        supervisor.generation = int(supervisor.generation or 0) + 1
        supervisor.started_at = observed_at
    supervisor.heartbeat_at = observed_at
    supervisor.stopping_at = None
    db.flush()
    return supervisor


def heartbeat_supervisor(
    db: Session,
    *,
    worker_id: str,
    instance_id: str,
    now: datetime | None = None,
) -> bool:
    supervisor = db.get(BackgroundSupervisor, worker_id)
    if supervisor is None or supervisor.instance_id != instance_id:
        return False
    supervisor.heartbeat_at = now or datetime.now(UTC)
    supervisor.stopping_at = None
    db.flush()
    return True


def release_supervisor(
    db: Session,
    *,
    worker_id: str,
    instance_id: str,
    now: datetime | None = None,
) -> bool:
    supervisor = db.get(BackgroundSupervisor, worker_id)
    if supervisor is None or supervisor.instance_id != instance_id:
        return False
    stopped_at = now or datetime.now(UTC)
    supervisor.heartbeat_at = stopped_at
    supervisor.stopping_at = stopped_at
    db.flush()
    return True


def live_supervisors(
    db: Session,
    *,
    heartbeat_timeout_seconds: int,
    now: datetime | None = None,
) -> list[BackgroundSupervisor]:
    observed_at = now or datetime.now(UTC)
    cutoff = observed_at - timedelta(seconds=heartbeat_timeout_seconds)
    return list(
        db.scalars(
            select(BackgroundSupervisor)
            .where(BackgroundSupervisor.stopping_at.is_(None))
            .where(BackgroundSupervisor.heartbeat_at >= cutoff)
            .order_by(BackgroundSupervisor.worker_id)
        ).all()
    )
