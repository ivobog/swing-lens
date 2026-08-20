from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from urllib.request import urlopen

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, BackgroundWorker
from app.services.background_job_service import claim_next_job, enqueue_job
from app.services.worker_registry import (
    heartbeat_worker,
    live_workers,
    mark_worker_stopping,
    register_worker,
)


def test_postgresql_worker_registry_and_queue_allowlist(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)

    with Session(engine) as db:
        enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 1}, priority=10)
        enqueue_job(db, "IB_HISTOGRAM_FETCH", {"ticker": "MSFT"}, priority=20)
        enqueue_job(db, "CERI_FEATURE_BATCH", {"tickers": ["MSFT"]}, priority=5)
        register_worker(
            db,
            worker_id="worker-a",
            queues=("broker",),
            heartbeat_timeout_seconds=30,
            hostname="test-host",
            process_id=100,
            now=now,
        )
        db.commit()

    with Session(engine) as db:
        claimed = claim_next_job(
            db,
            worker_id="worker-a",
            queues=("broker",),
        )
        assert claimed is not None
        assert claimed.job_type == "IB_HISTOGRAM_FETCH"
        db.rollback()

    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="already active"):
            register_worker(
                db,
                worker_id="worker-a",
                queues=("broker",),
                heartbeat_timeout_seconds=30,
                hostname="other-host",
                process_id=200,
                now=now + timedelta(seconds=1),
            )
        db.rollback()

    with Session(engine) as db:
        assert (
            mark_worker_stopping(
                db,
                "worker-a",
                hostname="other-host",
                process_id=200,
                now=now + timedelta(seconds=1),
            )
            is None
        )
        db.commit()
        heartbeat_worker(
            db,
            "worker-a",
            hostname="test-host",
            process_id=100,
            now=now + timedelta(seconds=2),
        )
        db.commit()
        workers = live_workers(
            db,
            heartbeat_timeout_seconds=30,
            now=now + timedelta(seconds=3),
        )
        assert [worker.worker_id for worker in workers] == ["worker-a"]
        mark_worker_stopping(
            db,
            "worker-a",
            hostname="test-host",
            process_id=100,
            now=now + timedelta(seconds=4),
        )
        db.commit()
        assert live_workers(
            db,
            heartbeat_timeout_seconds=30,
            now=now + timedelta(seconds=5),
        ) == []

    inspector = inspect(engine)
    assert "background_workers" in inspector.get_table_names()
    assert "idx_background_workers_heartbeat" in {
        index["name"] for index in inspector.get_indexes("background_workers")
    }
    with Session(engine) as db:
        assert db.scalar(select(BackgroundWorker.worker_id)) == "worker-a"
        assert db.scalar(select(BackgroundJob.job_type).where(BackgroundJob.status == "QUEUED"))
    engine.dispose()


def test_external_worker_process_registers_and_stops_gracefully(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    env = {
        **os.environ,
        "DATABASE_URL": disposable_postgres_database,
        "JOB_WORKER_ENABLED": "false",
        "JOB_POLL_INTERVAL_SECONDS": "0.2",
        "JOB_WORKER_HEARTBEAT_INTERVAL_SECONDS": "0.2",
        "JOB_WORKER_HEARTBEAT_TIMEOUT_SECONDS": "5",
        "CERI_PROVIDER_INGEST_ENABLED": "false",
    }
    creationflags = (
        subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.worker",
            "--worker-id",
            "process-worker",
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
    web_process = None
    try:
        try:
            worker = _wait_for_worker(engine, "process-worker")
        except AssertionError:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=5)
                pytest.fail(
                    "external worker exited before registration:\n"
                    f"stdout={stdout}\nstderr={stderr}"
                )
            raise
        assert worker.queues_json == ["interactive", "broker", "background"]
        assert worker.stopping_at is None
        first_heartbeat = worker.heartbeat_at

        port = _free_port()
        web_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=os.getcwd(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_for_url(f"http://127.0.0.1:{port}/health", process=web_process)
        web_process.terminate()
        web_process.wait(timeout=15)
        assert process.poll() is None
        _wait_for_new_heartbeat(engine, "process-worker", first_heartbeat)

        if sys.platform == "win32":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGTERM)
        process.wait(timeout=15)
        with Session(engine) as db:
            stopped = db.get(BackgroundWorker, "process-worker")
            assert stopped is not None
            assert stopped.stopping_at is not None
    finally:
        if web_process is not None and web_process.poll() is None:
            web_process.kill()
            web_process.wait(timeout=10)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        engine.dispose()


def test_active_external_job_survives_uvicorn_restart(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        probe = enqueue_job(db, "WORKER_RUNTIME_PROBE", {}, priority=1)
        db.commit()
        probe_id = probe.id

    env = {
        **os.environ,
        "DATABASE_URL": disposable_postgres_database,
        "JOB_WORKER_ENABLED": "false",
        "JOB_POLL_INTERVAL_SECONDS": "0.1",
        "JOB_WORKER_HEARTBEAT_INTERVAL_SECONDS": "0.2",
        "JOB_WORKER_HEARTBEAT_TIMEOUT_SECONDS": "5",
        "CERI_PROVIDER_INGEST_ENABLED": "false",
    }
    worker_process = subprocess.Popen(
        [sys.executable, "tests/external_worker_probe_runner.py"],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    web_process = None
    try:
        _wait_for_job_status(engine, probe_id, "RUNNING")
        worker = _wait_for_worker(engine, "active-probe-worker")
        _wait_for_new_heartbeat(
            engine,
            "active-probe-worker",
            worker.heartbeat_at,
        )
        port = _free_port()
        web_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=os.getcwd(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_for_url(f"http://127.0.0.1:{port}/health", process=web_process)
        web_process.terminate()
        web_process.wait(timeout=15)
        with Session(engine) as db:
            probe = db.get(BackgroundJob, probe_id)
            assert probe is not None
            probe.payload_json = {"release": True}
            db.commit()
        worker_process.wait(timeout=15)
        completed = _wait_for_job_status(engine, probe_id, "COMPLETED")
        assert completed.result_json == {"probe": "completed"}
        with Session(engine) as db:
            worker = db.get(BackgroundWorker, "active-probe-worker")
            assert worker is not None
            assert worker.stopping_at is not None
    finally:
        if web_process is not None and web_process.poll() is None:
            web_process.kill()
            web_process.wait(timeout=10)
        if worker_process.poll() is None:
            worker_process.kill()
            worker_process.wait(timeout=10)
        engine.dispose()


def _wait_for_worker(engine, worker_id: str) -> BackgroundWorker:
    deadline = time.monotonic() + (60 if sys.platform == "win32" else 15)
    while time.monotonic() < deadline:
        with Session(engine) as db:
            worker = db.get(BackgroundWorker, worker_id)
            if worker is not None:
                return worker
        time.sleep(0.1)
    raise AssertionError(f"worker {worker_id!r} did not register")


def _wait_for_job_status(engine, job_id: int, status: str) -> BackgroundJob:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with Session(engine) as db:
            job = db.get(BackgroundJob, job_id)
            if job is not None and job.status == status:
                db.expunge(job)
                return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not reach {status}")


def _wait_for_new_heartbeat(engine, worker_id: str, previous: datetime) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with Session(engine) as db:
            worker = db.get(BackgroundWorker, worker_id)
            if worker is not None and worker.heartbeat_at > previous:
                return
        time.sleep(0.1)
    raise AssertionError(f"worker {worker_id!r} heartbeat did not advance")


def _wait_for_url(url: str, *, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + (60 if sys.platform == "win32" else 30)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"web process exited {process.returncode}: {stdout}\n{stderr}"
            )
        try:
            with urlopen(url, timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"web process did not become ready at {url}")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _upgrade(database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )
