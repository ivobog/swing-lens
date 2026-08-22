from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import subprocess
import sys
from collections.abc import Sequence
from threading import Event

from app.db import SessionLocal
from app.models.tables import BackgroundWorker
from app.services.background_job_service import (
    fence_jobs_for_worker,
    fence_stalled_jobs,
    requeue_stalled_jobs,
)
from app.services.process_memory import memory_status, process_memory_snapshot
from app.services.worker_registry import mark_worker_stopping
from app.settings import get_settings

logger = logging.getLogger(__name__)


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

    def request_stop(_signum, _frame) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        value = getattr(signal, name, None)
        if value is not None:
            signal.signal(value, request_stop)

    logging.basicConfig(level=logging.INFO)
    child: subprocess.Popen | None = None
    try:
        while not stop.is_set():
            if child is None:
                if _registered_worker_process_alive(args.worker_id):
                    stop.wait(1.0)
                    continue
                _retire_dead_worker_registration(args.worker_id)
                child = _start_worker(args.worker_id, args.queues)
                logger.info("worker.supervisor.started", extra={"process_id": child.pid})

            if child.poll() is not None:
                _recover_worker_jobs(
                    args.worker_id,
                    f"Worker process {child.pid} exited with code {child.returncode}.",
                    process_id=child.pid,
                )
                child = None
                stop.wait(1.0)
                continue

            snapshot = process_memory_snapshot(child.pid)
            state = memory_status(
                snapshot,
                warning_mb=settings.worker_memory_warning_mb,
                critical_mb=settings.worker_memory_critical_mb,
            )
            stalled = _fence_no_progress(args.worker_id)
            if state == "CRITICAL":
                stalled.extend(
                    _fence_worker(
                        args.worker_id,
                        f"Worker exceeded {settings.worker_memory_critical_mb} MB memory budget.",
                    )
                )
            if stalled:
                logger.error(
                    "worker.supervisor.recycling",
                    extra={"process_id": child.pid, "job_ids": sorted(set(stalled))},
                )
                _terminate_worker_tree(child, settings.worker_shutdown_grace_seconds)
                _retire_worker_registration(args.worker_id, process_id=child.pid)
                _requeue(sorted(set(stalled)))
                child = None
                continue
            stop.wait(settings.job_watchdog_interval_seconds)
    finally:
        if child is not None and child.poll() is None:
            _terminate_worker_tree(child, settings.worker_shutdown_grace_seconds)
        if child is not None:
            _recover_worker_jobs(
                args.worker_id,
                f"Worker process {child.pid} stopped with its supervisor.",
                process_id=child.pid,
            )


def _start_worker(worker_id: str, queues: str) -> subprocess.Popen:
    environment = dict(os.environ)
    environment["JOB_WORKER_ENABLED"] = "false"
    kwargs: dict[str, object] = {"env": environment}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.worker",
            "--worker-id",
            worker_id,
            "--queues",
            queues,
        ],
        **kwargs,
    )


def _fence_no_progress(worker_id: str) -> list[int]:
    settings = get_settings()
    with SessionLocal() as db:
        fenced = fence_stalled_jobs(
            db,
            worker_id=worker_id,
            default_timeout_seconds=settings.job_progress_timeout_seconds,
            market_data_timeout_seconds=settings.job_market_data_progress_timeout_seconds,
            long_stage_timeout_seconds=settings.job_long_stage_progress_timeout_seconds,
        )
        db.commit()
        return fenced


def _fence_worker(worker_id: str, reason: str) -> list[int]:
    with SessionLocal() as db:
        fenced = fence_jobs_for_worker(db, worker_id=worker_id, reason=reason)
        db.commit()
        return fenced


def _recover_worker_jobs(worker_id: str, reason: str, *, process_id: int) -> None:
    with SessionLocal() as db:
        fenced = fence_jobs_for_worker(db, worker_id=worker_id, reason=reason)
        mark_worker_stopping(
            db,
            worker_id,
            hostname=socket.gethostname(),
            process_id=process_id,
        )
        db.commit()
    _requeue(fenced)


def _retire_worker_registration(worker_id: str, *, process_id: int) -> None:
    with SessionLocal() as db:
        mark_worker_stopping(
            db,
            worker_id,
            hostname=socket.gethostname(),
            process_id=process_id,
        )
        db.commit()


def _registered_worker_process_alive(worker_id: str) -> bool:
    with SessionLocal() as db:
        worker = db.get(BackgroundWorker, worker_id)
        process_id = (
            worker.process_id
            if worker is not None and worker.stopping_at is None
            else None
        )
    if process_id is None:
        return False
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


def _retire_dead_worker_registration(worker_id: str) -> None:
    with SessionLocal() as db:
        worker = db.get(BackgroundWorker, worker_id)
        if worker is None or worker.stopping_at is not None or worker.process_id is None:
            return
        process_id = worker.process_id
        try:
            os.kill(process_id, 0)
        except OSError:
            mark_worker_stopping(
                db,
                worker_id,
                hostname=worker.hostname,
                process_id=process_id,
            )
            db.commit()


def _requeue(job_ids: list[int]) -> None:
    if not job_ids:
        return
    with SessionLocal() as db:
        requeue_stalled_jobs(db, job_ids=job_ids)
        db.commit()


def _terminate_worker_tree(child: subprocess.Popen, grace_seconds: float) -> None:
    if child.poll() is not None:
        return
    if os.name == "nt":
        child.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        child.terminate()
    try:
        child.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.critical("worker.supervisor.kill_failed", extra={"process_id": child.pid})


if __name__ == "__main__":
    main()
