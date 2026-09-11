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
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

from app.db import SessionLocal
from app.models.tables import BackgroundWorker
from app.observability.logging import configure_json_logging, log_event
from app.observability.metrics import operational_metrics, start_metrics_http_server
from app.observability.resource_sampler import ResourceSampler
from app.services.background_job_service import (
    fence_jobs_for_worker,
    fence_stalled_jobs,
    requeue_stalled_jobs,
)
from app.services.lifecycle_control import (
    RUNTIME_FINGERPRINT_ENV,
    TOPOLOGY_VERSION,
    supervisor_state_path,
)
from app.services.lifecycle_safety import atomic_write_json
from app.services.parent_watchdog import PARENT_PID_ENV, PARENT_STARTED_AT_ENV
from app.services.process_identity import process_is_alive, process_started_at
from app.services.process_memory import memory_status, process_memory_snapshot
from app.services.process_roles import build_process_environment, require_process_role
from app.services.supervisor_registry import (
    acquire_supervisor,
    heartbeat_supervisor,
    release_supervisor,
)
from app.services.supervisor_restart import RestartBudget, RestartDecision
from app.services.worker_registry import associate_worker_launcher, retire_worker_registration
from app.settings import ProcessRole, get_settings

logger = logging.getLogger(__name__)
WORKER_REGISTRATION_TIMEOUT_SECONDS = 120.0


@dataclass
class LaunchedWorker:
    process: subprocess.Popen
    launched_at: float


@dataclass
class LaunchedWeb:
    process: subprocess.Popen
    launched_at: float


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Supervise and automatically recover the isolated SwingLens worker."
    )
    parser.add_argument("--worker-id", default=settings.job_worker_id)
    parser.add_argument("--queues", default="interactive,broker,background")
    parser.add_argument("--host", default=getattr(settings, "app_host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=getattr(settings, "app_port", 8000))
    parser.add_argument("--runtime-instance-id", default=f"manual-{os.getpid()}")
    parser.add_argument("--repo-root", default=str(Path.cwd()))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    configure_json_logging("supervisor")
    log_event(logger, "runtime.process_boot", stage="process_boot")
    args = parse_args(argv)
    settings = get_settings()
    try:
        require_process_role(settings, ProcessRole.SUPERVISOR)
    except Exception as exc:
        log_event(
            logger,
            "runtime.role_validation_failed",
            level=logging.ERROR,
            stage="role_validation",
            result="failure",
            reason_code="CONFIGURATION_CONFLICT",
            error=str(exc),
        )
        raise
    log_event(logger, "runtime.role_validation", stage="role_validation", result="success")
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

    operational_metrics.configure(enabled=settings.observability_metrics_enabled)
    metrics_server = None
    sampler = None
    if settings.observability_metrics_enabled:
        metrics_server = start_metrics_http_server(
            settings.observability_metrics_host,
            settings.observability_supervisor_metrics_port,
        )
        sampler = ResourceSampler(
            process_role="supervisor",
            interval_seconds=settings.observability_collection_interval_seconds,
        )
        sampler.start()
        operational_metrics.set_gauge("swinglens_supervisor_up", 1)
        operational_metrics.set_gauge(
            "swinglens_runtime_generation_info",
            1,
            mode=settings.runtime_mode.value,
            topology=TOPOLOGY_VERSION,
        )
    child: LaunchedWorker | None = None
    web: LaunchedWeb | None = None
    web_restarts = _restart_budget("web", settings)
    worker_restarts = _restart_budget("worker", settings)
    owns_supervision = False
    ownership_deadline = monotonic() + float(
        getattr(settings, "job_worker_heartbeat_timeout_seconds", 30)
        + getattr(settings, "job_watchdog_interval_seconds", 5)
    )
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
                    if web is not None and web.process.poll() is not None:
                        decision = web_restarts.record_failure(
                            now=monotonic(),
                            exit_code=web.process.returncode,
                            startup_stage="uvicorn_ready",
                            reason_code="WEB_CHILD_EXITED",
                        )
                        _record_child_failure(decision)
                        web = None
                        if not decision.crash_loop:
                            stop.wait(decision.backoff_seconds)
                    if not web_restarts.crash_loop:
                        try:
                            web = _supervise_web_once(args=args, child=web)
                        except Exception:
                            decision = web_restarts.record_failure(
                                now=monotonic(),
                                exit_code=None,
                                startup_stage="process_spawn",
                                reason_code="WEB_START_FAILED",
                            )
                            _record_child_failure(decision)
                            if not decision.crash_loop:
                                stop.wait(decision.backoff_seconds)

                    registered_worker = _registered_worker(args.worker_id)
                    registered_worker_alive = _registered_worker_process_alive(registered_worker)
                    registration_active = (
                        registered_worker is not None and registered_worker.stopping_at is None
                    )
                    if registration_active and not registered_worker_alive:
                        decision = worker_restarts.record_failure(
                            now=monotonic(),
                            exit_code=(
                                child.process.returncode
                                if child is not None and child.process.poll() is not None
                                else None
                            ),
                            startup_stage="worker_heartbeat",
                            reason_code="DURABLE_WORKER_PROCESS_LOST",
                        )
                        _record_child_failure(decision)
                        if child is not None and child.process.poll() is not None:
                            child = None
                        if not decision.crash_loop:
                            stop.wait(decision.backoff_seconds)
                    elif (
                        child is not None
                        and child.process.poll() is not None
                        and not registration_active
                    ):
                        decision = worker_restarts.record_failure(
                            now=monotonic(),
                            exit_code=child.process.returncode,
                            startup_stage="worker_registration",
                            reason_code="DURABLE_WORKER_EXITED_BEFORE_REGISTRATION",
                        )
                        _record_child_failure(decision)
                        child = None
                        if not decision.crash_loop:
                            stop.wait(decision.backoff_seconds)
                    elif (
                        child is not None
                        and child.process.poll() is None
                        and not registered_worker_alive
                        and monotonic() - child.launched_at >= WORKER_REGISTRATION_TIMEOUT_SECONDS
                    ):
                        decision = worker_restarts.record_failure(
                            now=monotonic(),
                            exit_code=None,
                            startup_stage="worker_registration",
                            reason_code="DURABLE_WORKER_REGISTRATION_TIMEOUT",
                        )
                        _record_child_failure(decision)
                        _terminate_launcher(child.process, settings.worker_shutdown_grace_seconds)
                        child = None
                        if not decision.crash_loop:
                            stop.wait(decision.backoff_seconds)
                    if not worker_restarts.crash_loop:
                        try:
                            child = _supervise_once(
                                worker_id=args.worker_id,
                                queues=args.queues,
                                child=child,
                            )
                        except Exception:
                            decision = worker_restarts.record_failure(
                                now=monotonic(),
                                exit_code=None,
                                startup_stage="process_spawn",
                                reason_code="DURABLE_WORKER_START_FAILED",
                            )
                            _record_child_failure(decision)
                            if not decision.crash_loop:
                                stop.wait(decision.backoff_seconds)
                    _write_supervisor_state(
                        args=args,
                        instance_id=instance_id,
                        web=web,
                        worker=child,
                        web_restarts=web_restarts,
                        worker_restarts=worker_restarts,
                    )
                elif monotonic() >= ownership_deadline:
                    logger.error(
                        "runtime.supervisor.ownership_timeout",
                        extra={"worker_id": args.worker_id, "process_id": process_id},
                    )
                    return
            except Exception:
                logger.exception(
                    "worker.supervisor.cycle_failed",
                    extra={
                        "worker_id": args.worker_id,
                        "supervisor_instance_id": instance_id,
                    },
                )
                _write_supervisor_state(
                    args=args,
                    instance_id=instance_id,
                    web=web,
                    worker=child,
                    web_restarts=web_restarts,
                    worker_restarts=worker_restarts,
                    cycle_failed=True,
                )
            stop.wait(settings.job_watchdog_interval_seconds)
    finally:
        log_event(logger, "runtime.process_shutdown", stage="process_shutdown")
        operational_metrics.set_gauge("swinglens_supervisor_up", 0)
        if owns_supervision:
            _shutdown_web(web, settings.worker_shutdown_grace_seconds)
            _shutdown_owned_worker(args.worker_id, child)
            try:
                with SessionLocal() as db:
                    release_supervisor(db, worker_id=args.worker_id, instance_id=instance_id)
                    db.commit()
            except Exception:
                logger.exception(
                    "worker.supervisor.release_failed",
                    extra={"worker_id": args.worker_id, "instance_id": instance_id},
                )
        if sampler is not None:
            sampler.stop()
        if metrics_server is not None:
            metrics_server.shutdown()


def _restart_budget(role: str, settings) -> RestartBudget:
    return RestartBudget(
        role=role,
        budget=getattr(settings, "supervisor_restart_budget", 5),
        window_seconds=getattr(settings, "supervisor_restart_window_seconds", 60.0),
        initial_backoff_seconds=getattr(
            settings, "supervisor_restart_backoff_initial_seconds", 0.5
        ),
        max_backoff_seconds=getattr(settings, "supervisor_restart_backoff_max_seconds", 10.0),
    )


def _record_child_failure(decision: RestartDecision) -> None:
    operational_metrics.increment(
        "swinglens_supervisor_child_restarts_total",
        role=decision.role,
        reason=decision.last_reason_code,
    )
    operational_metrics.increment(
        "swinglens_supervisor_child_start_failures_total",
        role=decision.role,
        stage=decision.last_startup_stage,
    )
    operational_metrics.set_gauge(
        "swinglens_supervisor_child_crash_loop",
        1 if decision.crash_loop else 0,
        role=decision.role,
    )
    log_event(
        logger,
        "runtime.supervisor.child_failure",
        level=logging.ERROR if decision.crash_loop else logging.WARNING,
        stage=decision.last_startup_stage,
        result="failure",
        reason_code="CRASH_LOOP" if decision.crash_loop else decision.last_reason_code,
        **decision.as_dict(),
    )


def _write_supervisor_state(
    *,
    args: argparse.Namespace,
    instance_id: str,
    web: LaunchedWeb | None,
    worker: LaunchedWorker | None,
    web_restarts: RestartBudget,
    worker_restarts: RestartBudget,
    cycle_failed: bool = False,
) -> None:
    root = Path(args.repo_root).resolve()
    payload = {
        "version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "runtime_instance_id": args.runtime_instance_id,
        "runtime_config_fingerprint": os.environ.get(RUNTIME_FINGERPRINT_ENV),
        "topology_version": TOPOLOGY_VERSION,
        "supervisor": {"pid": os.getpid(), "instance_id": instance_id},
        "web": {
            "launcher_pid": web.process.pid if web is not None else None,
            "state": "CRASH_LOOP" if web_restarts.crash_loop else "RUNNING",
        },
        "worker": {
            "launcher_pid": worker.process.pid if worker is not None else None,
            "state": "CRASH_LOOP" if worker_restarts.crash_loop else "RUNNING",
        },
        "restart": {
            "web": web_restarts.snapshot(),
            "worker": worker_restarts.snapshot(),
        },
        "cycle_failed": cycle_failed,
    }
    try:
        atomic_write_json(supervisor_state_path(root), payload)
    except OSError:
        logger.exception(
            "runtime.supervisor.state_write_failed",
            extra={"reason_code": "SUPERVISOR_STATE_WRITE_FAILED"},
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
            owned = heartbeat_supervisor(db, worker_id=worker_id, instance_id=instance_id)
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
    if child is not None and child.process.poll() is not None and not registration_active:
        context = {
            "worker_id": worker_id,
            "launcher_pid": child.process.pid,
            "exit_code": child.process.returncode,
            "startup_age_seconds": round(monotonic() - child.launched_at, 3),
            "failure_code": "DURABLE_WORKER_EXITED_BEFORE_REGISTRATION",
        }
        logger.error(
            "worker.supervisor.child_exited_before_registration %s",
            context,
            extra=context,
        )
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
    operational_metrics.increment("swinglens_worker_restarts_total", worker_id=worker_id)
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
    settings = get_settings()
    environment = build_process_environment(
        os.environ,
        role=ProcessRole.DURABLE_WORKER,
        settings=settings,
    )
    _set_parent_identity(environment)
    kwargs: dict[str, object] = {"env": environment}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return _start_logged_child(
        [
            _worker_python_executable(),
            "-m",
            "app.worker",
            "--worker-id",
            worker_id,
            "--queues",
            queues,
        ],
        kwargs=kwargs,
        role="worker",
    )


def _supervise_web_once(*, args: argparse.Namespace, child: LaunchedWeb | None) -> LaunchedWeb:
    if child is not None and child.process.poll() is None:
        return child
    process = _start_web(args)
    logger.info(
        "runtime.supervisor.web_started",
        extra={"web_pid": process.pid, "runtime_instance_id": args.runtime_instance_id},
    )
    return LaunchedWeb(process=process, launched_at=monotonic())


def _start_web(args: argparse.Namespace) -> subprocess.Popen:
    settings = get_settings()
    environment = build_process_environment(
        os.environ,
        role=ProcessRole.WEB,
        settings=settings,
    )
    _set_parent_identity(environment)
    kwargs: dict[str, object] = {"env": environment}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return _start_logged_child(
        [
            sys.executable,
            "-m",
            "app.serve",
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--runtime-instance-id",
            args.runtime_instance_id,
            "--repo-root",
            args.repo_root,
        ],
        kwargs=kwargs,
        role="web",
    )


def _start_logged_child(
    command: list[str], *, kwargs: dict[str, object], role: str
) -> subprocess.Popen:
    log_path = Path.cwd() / "logs" / f"lifecycle-{role}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as output:
        return subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, **kwargs)


def _shutdown_web(child: LaunchedWeb | None, grace_seconds: float) -> None:
    if child is not None:
        _terminate_launcher(child.process, grace_seconds)


def _set_parent_identity(environment: dict[str, str]) -> None:
    process_id = os.getpid()
    environment[PARENT_PID_ENV] = str(process_id)
    environment[PARENT_STARTED_AT_ENV] = process_started_at(process_id).isoformat()


def _worker_python_executable() -> str:
    """Preserve the active venv across the Windows launcher handoff."""
    candidates = []
    virtual_environment = os.environ.get("VIRTUAL_ENV")
    if virtual_environment:
        candidates.append(Path(virtual_environment) / "Scripts" / "python.exe")
    candidates.append(Path(sys.prefix) / "Scripts" / "python.exe")
    return str(next((candidate for candidate in candidates if candidate.is_file()), sys.executable))


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
