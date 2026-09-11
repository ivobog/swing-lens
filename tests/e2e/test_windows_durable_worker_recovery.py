from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

import psutil
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, BackgroundSupervisor, BackgroundWorker
from app.services.background_job_service import JobStatus, enqueue_job
from app.services.canonical_runtime_launcher import build_canonical_runtime_launch
from app.services.worker_registry import has_live_worker_for_job
from app.settings import Settings

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows process recovery gate"),
]
WORKER_REPLACEMENT_TIMEOUT_SECONDS = 120


@pytest.mark.parametrize("cycle", [0, 1])
def test_windows_worker_kill_self_heals_repeatedly(
    disposable_postgres_database: str,
    tmp_path,
    cycle: int,
) -> None:
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    env = {
        **os.environ,
        "DATABASE_URL": disposable_postgres_database,
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
        "SWINGLENS_LIFECYCLE_OPERATION_ID": f"windows-recovery-{cycle}-{uuid4().hex}",
        "SWINGLENS_RUNTIME_CONFIG_FINGERPRINT": f"disposable-{uuid4().hex}",
        "SWINGLENS_SUPERVISOR_STATE_PATH": str(tmp_path / "supervisor-state.json"),
        "JOB_WORKER_ENABLED": "false",
        "JOB_WORKER_ID": "windows-recovery-worker",
        "JOB_POLL_INTERVAL_SECONDS": "0.1",
        "JOB_WORKER_HEARTBEAT_INTERVAL_SECONDS": "0.2",
        "JOB_WORKER_HEARTBEAT_TIMEOUT_SECONDS": "5",
        "JOB_WATCHDOG_INTERVAL_SECONDS": "1",
        "CERI_PROVIDER_INGEST_ENABLED": "false",
        "WINNER_PROBABILITY_AUTO_MATURATION_ENABLED": "false",
        "WORKER_MEMORY_TRACEMALLOC_ENABLED": "false",
    }
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    supervisor_log_path = tmp_path / f"worker-supervisor-{cycle}.log"
    supervisor_log = supervisor_log_path.open("w", encoding="utf-8")
    launch_settings = Settings(
        _env_file=None,
        database_url=disposable_postgres_database,
        process_role="SUPERVISOR",
        job_worker_id="windows-recovery-worker",
        job_poll_interval_seconds=0.1,
        job_worker_heartbeat_interval_seconds=0.2,
        job_worker_heartbeat_timeout_seconds=5,
        job_watchdog_interval_seconds=1,
        ceri_provider_ingest_enabled=False,
        winner_probability_auto_maturation_enabled=False,
        worker_memory_tracemalloc_enabled=False,
    )
    launch = build_canonical_runtime_launch(
        settings=launch_settings,
        parent_environment=env,
        repo_root=Path.cwd(),
        git_sha="windows-disposable-recovery",
        runtime_instance_id=f"windows-recovery-runtime-{cycle}-{uuid4().hex}",
    )
    supervisor = subprocess.Popen(
        launch.command,
        cwd=os.getcwd(),
        env=launch.environment,
        stdout=supervisor_log,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creationflags,
    )
    try:
        try:
            supervisor_state = _wait_for_supervisor(engine)
            worker = _wait_for_fresh_worker(engine, timeout=60)
        except AssertionError as exc:
            subprocess.run(
                ["taskkill", "/PID", str(supervisor.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
            supervisor.wait(timeout=10)
            supervisor_log.flush()
            pytest.fail(
                f"{exc}\nsupervisor log="
                f"{supervisor_log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        assert worker.process_id != supervisor_state.process_id
        assert worker.instance_id
        assert worker.process_started_at

        with Session(engine) as db:
            job = enqueue_job(
                db,
                "WORKER_RECOVERY_PROBE",
                {"total_checkpoints": 50, "checkpoint_delay_seconds": 0.1},
                request_key=f"windows-recovery-cycle-{cycle}",
            )
            db.commit()
            job_id = job.id
        running = _wait_for_progress(engine, job_id, minimum=3)
        old_instance = running.worker_instance_id
        old_worker = _worker_snapshot(engine)
        assert old_worker is not None
        assert old_instance == old_worker.instance_id
        subprocess.run(
            ["taskkill", "/PID", str(old_worker.process_id), "/T", "/F"],
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            replacement = _wait_for_replacement(engine, old_instance)
        except AssertionError as exc:
            subprocess.run(
                ["taskkill", "/PID", str(supervisor.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
            supervisor.wait(timeout=10)
            supervisor_log.flush()
            pytest.fail(
                f"{exc}\nsupervisor log="
                f"{supervisor_log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        assert replacement.process_id
        completed = _wait_for_status(engine, job_id, JobStatus.COMPLETED, timeout=150)
        assert completed.progress_processed == 50
        assert completed.result_json["completed"] == 50
        assert completed.recovery_count >= 1

        with Session(engine) as db:
            final_job = enqueue_job(
                db,
                "WORKER_RECOVERY_PROBE",
                {"total_checkpoints": 3, "checkpoint_delay_seconds": 0.05},
                request_key="windows-recovery-final",
            )
            db.commit()
            final_job_id = final_job.id
        assert _wait_for_status(
            engine, final_job_id, JobStatus.COMPLETED, timeout=20
        ).result_json["completed"] == 3
        with Session(engine) as db:
            assert has_live_worker_for_job(
                db,
                job_type="FULL_PIPELINE",
                heartbeat_timeout_seconds=5,
            )
        core = _wait_for_core_ready(timeout=60)
        assert core["status"] == "ok"
        assert core["check_states"]["supervisor"] == "ok"
        assert core["check_states"]["web"] == "ok"
        assert core["check_states"]["worker"] == "ok"
        assert core["check_states"]["topology"] == "ok"
        old_web_pid = _listener_pid(8000)
        assert supervisor_state.process_id in {
            parent.pid for parent in psutil.Process(old_web_pid).parents()
        }
        subprocess.run(
            ["taskkill", "/PID", str(old_web_pid), "/T", "/F"],
            check=True,
            capture_output=True,
            text=True,
        )
        new_web_pid = _wait_for_replacement_web(old_web_pid, timeout=60)
        assert new_web_pid != old_web_pid
        assert _wait_for_core_ready(timeout=60)["status"] == "ok"
        supervisor_state_payload = json.loads(
            (tmp_path / "supervisor-state.json").read_text(encoding="utf-8")
        )
        assert supervisor_state_payload["runtime_instance_id"] == launch.runtime_instance_id
        assert supervisor_state_payload["runtime_config_fingerprint"] == env[
            "SWINGLENS_RUNTIME_CONFIG_FINGERPRINT"
        ]
        assert supervisor_state_payload["restart"]["web"]["restart_count"] >= 1
        assert supervisor_state_payload["restart"]["web"]["state"] != "CRASH_LOOP"
        assert supervisor_state_payload["restart"]["worker"]["restart_count"] >= 1
        combined_logs = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in (Path.cwd() / "logs").glob("lifecycle-*.log")
        )
        assert env["SWINGLENS_LIFECYCLE_OPERATION_ID"] in combined_logs
        assert disposable_postgres_database.split("@", 1)[0] not in combined_logs
    finally:
        if supervisor.poll() is None:
            supervisor.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                supervisor.wait(timeout=20)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/PID", str(supervisor.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
        supervisor_log.close()
        engine.dispose()


def _migrate(database_url: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=os.getcwd(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _wait_for_supervisor(engine, timeout: float = 20) -> BackgroundSupervisor:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with Session(engine) as db:
            row = db.get(BackgroundSupervisor, "windows-recovery-worker")
            if row is not None and row.stopping_at is None:
                db.expunge(row)
                return row
        time.sleep(0.1)
    raise AssertionError("supervisor did not register")


def _worker_snapshot(engine) -> BackgroundWorker | None:
    with Session(engine) as db:
        row = db.get(BackgroundWorker, "windows-recovery-worker")
        if row is not None:
            db.expunge(row)
        return row


def _wait_for_fresh_worker(engine, timeout: float = 20) -> BackgroundWorker:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = _worker_snapshot(engine)
        if (
            row is not None
            and row.stopping_at is None
            and row.instance_id
            and row.process_started_at
        ):
            return row
        time.sleep(0.1)
    raise AssertionError("worker did not register with a process instance")


def _wait_for_core_ready(timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310 - loopback disposable test
                "http://127.0.0.1:8000/ready/core", timeout=3
            ) as response:
                payload = json.load(response)
                if response.status == 200 and payload.get("status") == "ok":
                    return payload
        except OSError:
            pass
        time.sleep(0.25)
    raise AssertionError("canonical web did not reach core readiness")


def _listener_pid(port: int) -> int:
    matches = [
        row.pid
        for row in psutil.net_connections(kind="tcp")
        if row.status == psutil.CONN_LISTEN and row.laddr and row.laddr.port == port and row.pid
    ]
    assert len(set(matches)) == 1
    return int(matches[0])


def _wait_for_replacement_web(previous_pid: int, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            current = _listener_pid(8000)
            if current != previous_pid:
                return current
        except AssertionError:
            pass
        time.sleep(0.25)
    raise AssertionError("supervisor did not replace the controlled web failure")


def _wait_for_replacement(engine, old_instance: str | None) -> BackgroundWorker:
    deadline = time.monotonic() + WORKER_REPLACEMENT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        row = _worker_snapshot(engine)
        if (
            row is not None
            and row.stopping_at is None
            and row.instance_id
            and row.instance_id != old_instance
        ):
            return row
        time.sleep(0.1)
    raise AssertionError("supervisor did not replace killed worker")


def _wait_for_progress(engine, job_id: int, minimum: int) -> BackgroundJob:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with Session(engine) as db:
            row = db.get(BackgroundJob, job_id)
            if row is not None and row.progress_processed >= minimum:
                db.expunge(row)
                return row
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not reach progress {minimum}")


def _wait_for_status(
    engine, job_id: int, status: str, *, timeout: float
) -> BackgroundJob:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with Session(engine) as db:
            row = db.get(BackgroundJob, job_id)
            if row is not None and row.status == status:
                db.expunge(row)
                return row
        time.sleep(0.1)
    with Session(engine) as db:
        row = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id))
        raise AssertionError(
            f"job {job_id} did not reach {status}; observed={row.status if row else None}"
        )
