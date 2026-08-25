from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, BackgroundSupervisor, BackgroundWorker
from app.services.background_job_service import JobStatus, enqueue_job
from app.services.readiness_service import ReadinessService
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
    supervisor = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.worker_supervisor",
            "--worker-id",
            "windows-recovery-worker",
            "--queues",
            "interactive,broker,background",
        ],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    try:
        try:
            supervisor_state = _wait_for_supervisor(engine)
            worker = _wait_for_fresh_worker(engine)
        except AssertionError as exc:
            subprocess.run(
                ["taskkill", "/PID", str(supervisor.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
            stdout, stderr = supervisor.communicate(timeout=10)
            pytest.fail(f"{exc}\nsupervisor stdout={stdout}\nsupervisor stderr={stderr}")
        assert worker.process_id != supervisor_state.process_id
        assert worker.instance_id
        assert worker.process_started_at

        with Session(engine) as db:
            job = enqueue_job(
                db,
                "WORKER_RECOVERY_PROBE",
                {"total_checkpoints": 10, "checkpoint_delay_seconds": 0.1},
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
            stdout, stderr = supervisor.communicate(timeout=10)
            pytest.fail(f"{exc}\nsupervisor stdout={stdout}\nsupervisor stderr={stderr}")
        assert replacement.process_id
        completed = _wait_for_status(engine, job_id, JobStatus.COMPLETED, timeout=150)
        assert completed.progress_processed == 10
        assert completed.result_json["completed"] == 10
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
        readiness = ReadinessService(
            engine=engine,
            settings=Settings(
                _env_file=None,
                database_url=disposable_postgres_database,
                job_worker_enabled=True,
                use_durable_pipeline=True,
                job_worker_id="windows-recovery-worker",
                job_worker_heartbeat_timeout_seconds=5,
                job_worker_heartbeat_interval_seconds=0.2,
                ceri_provider_ingest_enabled=False,
                upload_dir=tmp_path / "uploads",
                export_dir=tmp_path / "exports",
                cache_dir=tmp_path / "cache",
            ),
        ).report()
        assert readiness.status == "ok"
        assert readiness.checks["supervisor"].ok
        assert readiness.checks["worker_registered"].ok
        assert readiness.checks["worker_heartbeat"].ok
        assert readiness.checks["worker"].ok
        assert readiness.checks["jobs"].ok
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
        engine.dispose()


def _migrate(database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
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
