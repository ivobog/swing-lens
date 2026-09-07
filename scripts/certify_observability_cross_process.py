"""Disposable Windows/Docker certification for process-local Prometheus targets."""

from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

import psycopg
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import BackgroundJob, BackgroundWorker
from app.observability.correlation import root_action_scope
from app.services.background_job_service import JobStatus, enqueue_job
from app.services.ceri.sec.processor_lifecycle import (
    certify_processor,
    promote_processor,
    register_deployed_processor,
)
from app.services.redaction import redact_text
from app.settings import get_settings

DATABASE_PREFIX = "swinglens_obs_cert_"
PROMETHEUS_URL = ""


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait(description: str, callback, timeout: float = 60.0):
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = callback()
            if value:
                return value
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    detail = f": {type(last_error).__name__}" if last_error else ""
    raise RuntimeError(f"timed out waiting for {description}{detail}")


def _prometheus_query(expression: str) -> list[dict]:
    query = urllib.parse.urlencode({"query": expression})
    with urllib.request.urlopen(f"{PROMETHEUS_URL}/api/v1/query?{query}", timeout=3) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload.get('status')}")
    return list(payload["data"]["result"])


def _target_up(job: str) -> bool:
    rows = _prometheus_query(f'up{{job="{job}"}}')
    return bool(rows and float(rows[0]["value"][1]) == 1.0)


def _worker_metric() -> list[dict]:
    return [
        row
        for row in _prometheus_query(
            'swinglens_job_progress_total{job="swinglens-worker",stage="WORKER_RECOVERY_PROBE"}'
        )
        if float(row["value"][1]) >= 1.0
    ]


def _worker_metric_timestamp() -> float | None:
    rows = _prometheus_query(
        "timestamp(swinglens_job_progress_total{job=\"swinglens-worker\","
        "stage=\"WORKER_RECOVERY_PROBE\"})"
    )
    timestamps = [float(row["value"][1]) for row in rows]
    return max(timestamps) if timestamps else None


def _supervisor_metric() -> bool:
    rows = _prometheus_query(
        'swinglens_supervisor_up{job="swinglens-supervisor"}'
    )
    return bool(rows and float(rows[0]["value"][1]) == 1.0)


def _enqueue_probe(engine, request_key: str) -> int:
    with Session(engine) as session:
        with root_action_scope("ADMINISTRATIVE", "cross-process-certification"):
            job = enqueue_job(
                session,
                "WORKER_RECOVERY_PROBE",
                {"total_checkpoints": 1, "checkpoint_delay_seconds": 0},
                request_key=request_key,
            )
        session.commit()
        return int(job.id)


def _completed(engine, job_id: int) -> bool:
    with Session(engine) as session:
        return (
            session.scalar(select(BackgroundJob.status).where(BackgroundJob.id == job_id))
            == JobStatus.COMPLETED
        )


def _worker_pid(engine, worker_id: str) -> int | None:
    with Session(engine) as session:
        row = session.get(BackgroundWorker, worker_id)
        if row is None or row.stopping_at is not None:
            return None
        return int(row.process_id) if row.process_id else None


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
        process.wait(timeout=20)
    except Exception:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    global PROMETHEUS_URL
    if os.name != "nt":
        raise RuntimeError("This certification is specifically for Windows + Docker Desktop.")
    root = Path(__file__).resolve().parents[1]
    configured_url = make_url(get_settings().database_url)
    admin_url = configured_url.set(database="postgres")
    psycopg_admin_url = admin_url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    database_name = f"{DATABASE_PREFIX}{uuid4().hex[:12]}"
    database_url = configured_url.set(database=database_name).render_as_string(hide_password=False)
    admin = psycopg.connect(psycopg_admin_url, autocommit=True)
    engine = None
    web = None
    supervisor = None
    web_log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    supervisor_log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    last_job_id: int | None = None
    web_port = _available_loopback_port()
    worker_metrics_port = _available_loopback_port()
    supervisor_metrics_port = _available_loopback_port()
    prometheus_port = _available_loopback_port()
    PROMETHEUS_URL = f"http://127.0.0.1:{prometheus_port}"
    prometheus_container = f"swinglens-obs-cert-{secrets.token_hex(6)}"
    prometheus_directory = tempfile.TemporaryDirectory(prefix="swinglens-obs-prom-")
    prometheus_config = Path(prometheus_directory.name) / "prometheus.yml"
    prometheus_config.write_text(
        "\n".join(
            (
                "global:",
                "  scrape_interval: 2s",
                "scrape_configs:",
                "  - job_name: swinglens-web",
                f'    static_configs: [{{targets: ["host.docker.internal:{web_port}"]}}]',
                "  - job_name: swinglens-worker",
                "    static_configs: "
                f'[{{targets: ["host.docker.internal:{worker_metrics_port}"]}}]',
                "  - job_name: swinglens-supervisor",
                "    static_configs: "
                f'[{{targets: ["host.docker.internal:{supervisor_metrics_port}"]}}]',
                "",
            )
        ),
        encoding="utf-8",
    )
    worker_id = f"obs-cert-{uuid4().hex[:8]}"
    proof: dict[str, object] = {"database": "disposable"}
    try:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        config = Config()
        config.set_main_option("script_location", str(root / "alembic"))
        identity = run_guarded_alembic_upgrade(config, database_url)
        print(f"migrated disposable database: {identity.database_name}")
        engine = create_engine(database_url)
        with engine.connect() as connection:
            actual = connection.exec_driver_sql("select current_database()").scalar()
            if actual != database_name or not str(actual).startswith(DATABASE_PREFIX):
                raise RuntimeError("refusing certification against a non-disposable database")
        with Session(engine) as session:
            deployed = register_deployed_processor(session, git_sha="observability-certification")
            certify_processor(
                session,
                processor_signature=deployed.processor_signature,
                evidence={"scope": "disposable-observability-certification"},
                actor="observability-certification",
            )
            promote_processor(
                session,
                processor_signature=deployed.processor_signature,
                actor="observability-certification",
            )
            session.commit()

        process_environment = dict(os.environ)
        process_environment.update(
            {
                "DATABASE_URL": database_url,
                "OBSERVABILITY_METRICS_ENABLED": "true",
                "OBSERVABILITY_METRICS_HOST": "127.0.0.1",
                "OBSERVABILITY_WORKER_METRICS_PORT": str(worker_metrics_port),
                "OBSERVABILITY_SUPERVISOR_METRICS_PORT": str(supervisor_metrics_port),
                "OBSERVABILITY_COLLECTION_INTERVAL_SECONDS": "1",
                "JOB_POLL_INTERVAL_SECONDS": "0.25",
                "JOB_WATCHDOG_INTERVAL_SECONDS": "1",
                "JOB_WORKER_HEARTBEAT_INTERVAL_SECONDS": "1",
                "JOB_WORKER_HEARTBEAT_TIMEOUT_SECONDS": "10",
                "JOB_WORKER_ID": worker_id,
                "DB_MONITOR_ENABLED": "true",
            }
        )
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        web_environment = dict(process_environment)
        web_environment["JOB_WORKER_ENABLED"] = "false"
        web = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(web_port),
            ],
            cwd=root,
            env=web_environment,
            creationflags=creationflags,
            stdout=web_log,
            stderr=subprocess.STDOUT,
        )
        supervisor = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.worker_supervisor",
                "--worker-id",
                worker_id,
            ],
            cwd=root,
            env=process_environment,
            creationflags=creationflags,
            stdout=supervisor_log,
            stderr=subprocess.STDOUT,
        )
        _wait(
            "web metrics",
            lambda: (
                urllib.request.urlopen(  # noqa: S310 - loopback certification
                    f"http://127.0.0.1:{web_port}/metrics", timeout=2
                ).status
                == 200
            ),
        )
        first_pid = _wait("real worker registration", lambda: _worker_pid(engine, worker_id))

        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                prometheus_container,
                "--add-host",
                "host.docker.internal:host-gateway",
                "--publish",
                f"127.0.0.1:{prometheus_port}:9090",
                "--volume",
                f"{prometheus_config}:/etc/prometheus/prometheus.yml:ro",
                "prom/prometheus:v3.5.0",
                "--config.file=/etc/prometheus/prometheus.yml",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        _wait("Prometheus web target", lambda: _target_up("swinglens-web"), timeout=90)
        _wait("Prometheus worker target", lambda: _target_up("swinglens-worker"), timeout=90)
        _wait(
            "Prometheus supervisor target",
            lambda: _target_up("swinglens-supervisor"),
            timeout=90,
        )

        first_job = _enqueue_probe(engine, f"cross-process-first-{uuid4().hex}")
        last_job_id = first_job
        _wait("first worker-only probe", lambda: _completed(engine, first_job))
        first_metric = _wait("first worker-only Prometheus metric", _worker_metric, timeout=90)
        first_metric_timestamp = _wait(
            "first worker-only Prometheus metric timestamp",
            _worker_metric_timestamp,
            timeout=90,
        )

        restart_started_at = time.time()
        restart_result = subprocess.run(
            ["taskkill", "/PID", str(first_pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        if restart_result.returncode != 0:
            raise RuntimeError("failed to stop the durable worker for supervisor restart")
        second_pid = _wait(
            "supervisor worker restart",
            lambda: (
                pid
                if (pid := _worker_pid(engine, worker_id)) is not None and pid != first_pid
                else None
            ),
            timeout=90,
        )
        _wait(
            "restarted Prometheus worker target",
            lambda: _target_up("swinglens-worker"),
            timeout=90,
        )
        second_job = _enqueue_probe(engine, f"cross-process-second-{uuid4().hex}")
        last_job_id = second_job
        _wait("second worker-only probe", lambda: _completed(engine, second_job))
        second_metric_timestamp = _wait(
            "fresh post-restart worker Prometheus scrape",
            lambda: (
                observed
                if (observed := _worker_metric_timestamp()) is not None
                and observed > restart_started_at
                else None
            ),
            timeout=90,
        )
        second_metric = _worker_metric()
        supervisor_metric = _wait("supervisor liveness metric", _supervisor_metric, timeout=90)
        proof.update(
            {
                "web_target_up": _target_up("swinglens-web"),
                "worker_target_up": _target_up("swinglens-worker"),
                "supervisor_target_up": _target_up("swinglens-supervisor"),
                "first_worker_pid": first_pid,
                "second_worker_pid": second_pid,
                "first_job_status": "COMPLETED",
                "second_job_status": "COMPLETED",
                "worker_only_metric_before_restart": bool(first_metric),
                "worker_only_metric_after_restart": bool(second_metric),
                "worker_metric_first_sample_at": first_metric_timestamp,
                "worker_metric_post_restart_sample_at": second_metric_timestamp,
                "worker_metric_target": "swinglens-worker",
                "supervisor_liveness_metric": bool(supervisor_metric),
            }
        )
        print(json.dumps(proof, sort_keys=True))
        return 0
    except Exception:
        diagnostics: dict[str, object] = {}
        if engine is not None:
            with Session(engine) as session:
                if last_job_id is not None:
                    row = session.get(BackgroundJob, last_job_id)
                    diagnostics["last_job"] = {
                        "id": last_job_id,
                        "status": row.status if row else None,
                        "error": redact_text(str(row.error_message or "")) if row else None,
                    }
                worker = session.get(BackgroundWorker, worker_id)
                diagnostics["worker"] = {
                    "process_id": worker.process_id if worker else None,
                    "stopping": bool(worker and worker.stopping_at),
                    "telemetry_status": worker.telemetry_status if worker else None,
                    "collector_status": worker.resource_collector_status if worker else None,
                }
        for name, handle in (("web_log", web_log), ("supervisor_log", supervisor_log)):
            handle.flush()
            handle.seek(0)
            diagnostics[name] = redact_text(handle.read()[-6000:])
        print(json.dumps(diagnostics, sort_keys=True))
        raise
    finally:
        _stop_process(supervisor)
        _stop_process(web)
        subprocess.run(
            [
                "docker",
                "rm",
                "--force",
                prometheus_container,
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        prometheus_directory.cleanup()
        if engine is not None:
            engine.dispose()
        if database_name.startswith(DATABASE_PREFIX):
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )
        admin.close()
        web_log.close()
        supervisor_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
