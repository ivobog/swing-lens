from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic
from uuid import uuid4

from app.db import SessionLocal
from app.models.tables import BackgroundWorker
from app.services.background_job_service import (
    fence_jobs_for_worker,
    fence_stalled_jobs,
    requeue_stalled_jobs,
)
from app.services.process_identity import process_is_alive, process_started_at
from app.services.process_memory import memory_status, process_memory_snapshot
from app.services.supervisor_registry import (
    acquire_supervisor,
    heartbeat_supervisor,
    release_supervisor,
)
from app.services.worker_registry import associate_worker_launcher, retire_worker_registration
from app.settings import get_settings

logger = logging.getLogger(__name__)
WORKER_REGISTRATION_TIMEOUT_SECONDS = 120.0


@dataclass
class LaunchedWorker:
    process: subprocess.Popen
    launched_at: float


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Supervise and automatically recover the isolated SwingLens worker."
    )
    parser.add_argument("--worker-id", default=settings.job_worker_id)
    parser.add_argument("--queues", default="interactive,broker,background")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_settings()
    stop = Event()
    instance_id = uuid4().hex
    process_id = os.getpid()
    process_start = process_started_at(process_id)

    def request_stop(_signum, _frame) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        value = getattr(signal, name, None)
        if value is not None:
            signal.signal(value, request_stop)

    logging.basicConfig(level=logging.INFO)
    child: LaunchedWorker | None = None
    owns_supervision = False
    try:
        while not stop.is_set():
            try:
                owns_supervision = _acquire_or_heartbeat_supervisor(
                    worker_id=args.worker_id,
                    instance_id=instance_id,
                    process_id=process_id,
                    process_start=process_start,
                    already_owned=owns_supervision,
                )
                if owns_supervision:
                    child = _supervise_once(
                        worker_id=args.worker_id,
                        queues=args.queues,
                        child=child,
                    )
            except Exception:
                logger.exception(
                    "worker.supervisor.cycle_failed",
                    extra={
                        "worker_id": args.worker_id,
                        "supervisor_instance_id": instance_id,
                    },
                )
            stop.wait(settings.job_watchdog_interval_seconds)
    finally:
        if owns_supervision:
            _shutdown_owned_worker(args.worker_id, child)
            try:
                with SessionLocal() as db:
                    release_supervisor(
                        db, worker_id=args.worker_id, instance_id=instance_id
                    )
                    db.commit()
            except Exception:
                logger.exception(
                    "worker.supervisor.release_failed",
                    extra={"worker_id": args.worker_id, "instance_id": instance_id},
                )


def _acquire_or_heartbeat_supervisor(
    *,
    worker_id: str,
    instance_id: str,
    process_id: int,
    process_start: datetime,
    already_owned: bool,
) -> bool:
    settings = get_settings()
    with SessionLocal() as db:
        if already_owned:
            owned = heartbeat_supervisor(
                db, worker_id=worker_id, instance_id=instance_id
            )
            db.commit()
            return owned
        supervisor = acquire_supervisor(
            db,
            worker_id=worker_id,
            instance_id=instance_id,
            process_id=process_id,
            process_started_at=process_start,
            heartbeat_timeout_seconds=settings.job_worker_heartbeat_timeout_seconds,
        )
        generation = supervisor.generation if supervisor is not None else None
        db.commit()
    if supervisor is not None:
        context = {
                "worker_id": worker_id,
                "supervisor_instance_id": instance_id,
                "registered_pid": process_id,
                "process_started_at": process_start.isoformat(),
                "generation": generation,
            }
        logger.info(
            "worker.supervisor.acquired %s",
            context,
            extra=context,
        )
    return supervisor is not None


def _supervise_once(
    *, worker_id: str, queues: str, child: LaunchedWorker | None
) -> LaunchedWorker | None:
    settings = get_settings()
    worker = _registered_worker(worker_id)
    worker_alive = _registered_worker_process_alive(worker)

    if worker_alive and worker is not None:
        if child is not None:
            _associate_launcher(worker, child.process.pid)
            if child.process.poll() is not None and child.process.pid != worker.process_id:
                logger.info(
                    "worker.supervisor.launcher_exited_worker_alive",
                    extra=_worker_log_context(worker, launcher_pid=child.process.pid),
                )
                child = None
        state = _safe_memory_status(worker)
        stalled = _fence_no_progress(worker_id, worker.instance_id, worker.heartbeat_at)
        if state == "CRITICAL":
            stalled.extend(
                _fence_worker(
                    worker_id,
                    worker.instance_id,
                    f"Worker exceeded {settings.worker_memory_critical_mb} MB memory budget.",
                )
            )
        if not stalled:
            return child
        context = {
                **_worker_log_context(worker, launcher_pid=_launcher_pid(child)),
                "job_ids": sorted(set(stalled)),
                "reason": "stalled_or_memory_critical",
            }
        logger.error(
            "worker.supervisor.recycling %s",
            context,
            extra=context,
        )
        _terminate_worker_instance(worker, child, settings.worker_shutdown_grace_seconds)
        _retire_worker_registration(worker)
        _requeue(sorted(set(stalled)))
        return None

    registration_active = worker is not None and worker.stopping_at is None
    if child is not None and child.process.poll() is None:
        startup_age = monotonic() - child.launched_at
        if not registration_active and startup_age < WORKER_REGISTRATION_TIMEOUT_SECONDS:
            return child

    if registration_active and worker is not None:
        reason = (
            f"Registered worker instance {worker.instance_id or '<missing>'} "
            f"pid={worker.process_id} is no longer alive or has a stale heartbeat."
        )
        fenced = _fence_worker(worker_id, worker.instance_id, reason)
        _retire_worker_registration(worker)
        _requeue(fenced)
        context = {
                **_worker_log_context(worker, launcher_pid=_launcher_pid(child)),
                "reason": reason,
                "job_ids": fenced,
            }
        logger.warning(
            "worker.supervisor.worker_lost %s",
            context,
            extra=context,
        )
    if child is not None and child.process.poll() is None:
        _terminate_launcher(child.process, settings.worker_shutdown_grace_seconds)

    replacement = _start_worker(worker_id, queues)
    context = {
            "worker_id": worker_id,
            "worker_instance_id": None,
            "registered_pid": None,
            "launcher_pid": replacement.pid,
            "state": "STARTING",
            "reason": "no_usable_registered_worker",
        }
    logger.info(
        "worker.supervisor.started %s",
        context,
        extra=context,
    )
    return LaunchedWorker(replacement, monotonic())


def _start_worker(worker_id: str, queues: str) -> subprocess.Popen:
    environment = dict(os.environ)
    environment["JOB_WORKER_ENABLED"] = "false"
    kwargs: dict[str, object] = {"env": environment}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        [sys.executable, "-m", "app.worker", "--worker-id", worker_id, "--queues", queues],
        **kwargs,
    )


def _registered_worker(worker_id: str) -> BackgroundWorker | None:
    with SessionLocal() as db:
        worker = db.get(BackgroundWorker, worker_id)
        if worker is not None:
            db.expunge(worker)
        return worker


def _registered_worker_process_alive(worker: BackgroundWorker | None) -> bool:
    if worker is None or worker.stopping_at is not None:
        return False
    settings = get_settings()
    heartbeat = worker.heartbeat_at
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    if heartbeat < datetime.now(UTC) - timedelta(
        seconds=settings.job_worker_heartbeat_timeout_seconds
    ):
        return False
    try:
        return process_is_alive(worker.process_id, worker.process_started_at)
    except Exception:
        logger.exception(
            "worker.supervisor.liveness_inspection_failed",
            extra=_worker_log_context(worker),
        )
        return False


def _associate_launcher(worker: BackgroundWorker, launcher_pid: int) -> None:
    if worker.instance_id is None or worker.launcher_process_id == launcher_pid:
        return
    try:
        with SessionLocal() as db:
            associate_worker_launcher(
                db,
                worker_id=worker.worker_id,
                instance_id=worker.instance_id,
                launcher_process_id=launcher_pid,
            )
            db.commit()
        worker.launcher_process_id = launcher_pid
    except Exception:
        logger.exception(
            "worker.supervisor.launcher_association_failed",
            extra=_worker_log_context(worker, launcher_pid=launcher_pid),
        )


def _fence_no_progress(
    worker_id: str,
    worker_instance_id: str | None,
    worker_heartbeat_at: datetime | None = None,
) -> list[int]:
    settings = get_settings()
    with SessionLocal() as db:
        fenced = fence_stalled_jobs(
            db,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
            worker_heartbeat_at=worker_heartbeat_at,
            default_timeout_seconds=settings.job_progress_timeout_seconds,
            market_data_timeout_seconds=settings.job_market_data_progress_timeout_seconds,
            long_stage_timeout_seconds=settings.job_long_stage_progress_timeout_seconds,
        )
        db.commit()
        return fenced


def _fence_worker(worker_id: str, instance_id: str | None, reason: str) -> list[int]:
    with SessionLocal() as db:
        fenced = fence_jobs_for_worker(
            db,
            worker_id=worker_id,
            worker_instance_id=instance_id,
            reason=reason,
        )
        db.commit()
        return fenced


def _retire_worker_registration(worker: BackgroundWorker) -> None:
    try:
        with SessionLocal() as db:
            retire_worker_registration(
                db,
                worker_id=worker.worker_id,
                expected_instance_id=worker.instance_id,
                expected_generation=int(worker.generation or 0),
                expected_process_id=worker.process_id,
            )
            db.commit()
    except Exception:
        logger.exception(
            "worker.supervisor.registration_retirement_failed",
            extra=_worker_log_context(worker),
        )


def _requeue(job_ids: list[int]) -> None:
    if not job_ids:
        return
    with SessionLocal() as db:
        requeue_stalled_jobs(db, job_ids=job_ids)
        db.commit()


def _safe_memory_status(worker: BackgroundWorker) -> str:
    settings = get_settings()
    try:
        snapshot = process_memory_snapshot(worker.process_id)
        return memory_status(
            snapshot,
            warning_mb=settings.worker_memory_warning_mb,
            critical_mb=settings.worker_memory_critical_mb,
        )
    except Exception:
        logger.exception(
            "worker.supervisor.memory_inspection_failed",
            extra=_worker_log_context(worker),
        )
        return "UNKNOWN"


def _terminate_worker_instance(
    worker: BackgroundWorker,
    child: LaunchedWorker | None,
    grace_seconds: float,
) -> None:
    try:
        if process_is_alive(worker.process_id, worker.process_started_at):
            _terminate_pid(int(worker.process_id), grace_seconds)
    except Exception:
        logger.exception(
            "worker.supervisor.worker_termination_failed",
            extra=_worker_log_context(worker, launcher_pid=_launcher_pid(child)),
        )
    if (
        child is not None
        and child.process.poll() is None
        and child.process.pid != worker.process_id
    ):
        _terminate_launcher(child.process, grace_seconds)


def _terminate_pid(pid: int, grace_seconds: float) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"], check=False, capture_output=True, text=True
        )
    else:
        os.kill(pid, signal.SIGTERM)
    deadline = monotonic() + grace_seconds
    while monotonic() < deadline and process_is_alive(pid):
        Event().wait(0.1)
    if process_is_alive(pid):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            os.kill(pid, signal.SIGKILL)


def _terminate_launcher(child: subprocess.Popen, grace_seconds: float) -> None:
    if child.poll() is not None:
        return
    try:
        if os.name == "nt":
            child.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            child.terminate()
        child.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            child.kill()
    except Exception:
        logger.exception(
            "worker.supervisor.launcher_termination_failed",
            extra={"launcher_pid": child.pid},
        )


def _shutdown_owned_worker(worker_id: str, child: LaunchedWorker | None) -> None:
    settings = get_settings()
    try:
        worker = _registered_worker(worker_id)
        if worker is not None and _registered_worker_process_alive(worker):
            _terminate_worker_instance(worker, child, settings.worker_shutdown_grace_seconds)
            fenced = _fence_worker(
                worker_id,
                worker.instance_id,
                f"Worker instance {worker.instance_id} stopped with its supervisor.",
            )
            _retire_worker_registration(worker)
            _requeue(fenced)
        elif child is not None:
            _terminate_launcher(child.process, settings.worker_shutdown_grace_seconds)
    except Exception:
        logger.exception("worker.supervisor.shutdown_failed", extra={"worker_id": worker_id})


def _launcher_pid(child: LaunchedWorker | None) -> int | None:
    return child.process.pid if child is not None else None


def _worker_log_context(
    worker: BackgroundWorker, *, launcher_pid: int | None = None
) -> dict[str, object]:
    return {
        "worker_id": worker.worker_id,
        "worker_instance_id": worker.instance_id,
        "registered_pid": worker.process_id,
        "launcher_pid": launcher_pid or worker.launcher_process_id,
        "process_started_at": (
            worker.process_started_at.isoformat() if worker.process_started_at else None
        ),
        "heartbeat_at": worker.heartbeat_at.isoformat() if worker.heartbeat_at else None,
        "generation": worker.generation,
        "state": "STOPPING" if worker.stopping_at else "REGISTERED",
    }


if __name__ == "__main__":
    main()
