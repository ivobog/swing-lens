from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import psutil
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models.tables import BackgroundSupervisor, BackgroundWorker
from app.services.lifecycle_safety import collect_windows_postgres_evidence, normalize_path

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows lifecycle persistence gate"),
]
ROOT = Path(__file__).resolve().parents[2]
RUNTIME_STATE = ROOT / "data" / "cache" / "swinglens-lifecycle.json"


def test_windows_runtime_survives_controller_exit_and_reuses_generation(
    disposable_postgres_database: str,
    tmp_path: Path,
) -> None:
    assert not RUNTIME_STATE.exists(), "canonical runtime state must be absent before the gate"
    _migrate(disposable_postgres_database)
    worker_id = f"windows-persistence-{uuid4().hex}"
    ports: list[int] = []
    while len(ports) < 3:
        candidate = _free_port()
        if candidate not in ports:
            ports.append(candidate)
    env = _lifecycle_environment(
        disposable_postgres_database,
        worker_id=worker_id,
        web_port=ports[0],
        worker_metrics_port=ports[1],
        supervisor_metrics_port=ports[2],
        supervisor_state_path=tmp_path / "supervisor-state.json",
    )
    log_path = ROOT / "logs" / "lifecycle" / "lifecycle.jsonl"
    log_offset = log_path.stat().st_size if log_path.exists() else 0
    first_state: dict[str, object] | None = None
    try:
        first = _controller("start", env)
        assert first.returncode == 0, _controller_failure(first)
        first_state = json.loads(RUNTIME_STATE.read_text(encoding="utf-8"))
        first_registration = _registration_snapshot(
            disposable_postgres_database, worker_id
        )
        _wait_for_runtime_survival(first_state, ports, seconds=15)

        second = _controller("start", env)
        assert second.returncode == 0, _controller_failure(second)
        second_state = json.loads(RUNTIME_STATE.read_text(encoding="utf-8"))
        second_registration = _registration_snapshot(
            disposable_postgres_database, worker_id
        )

        assert second_state["runtimeInstanceId"] == first_state["runtimeInstanceId"]
        assert second_state["supervisor"] == first_state["supervisor"]
        assert second_state["web"] == first_state["web"]
        assert second_registration == first_registration
        assert _runtime_role_counts(str(first_state["runtimeInstanceId"]), worker_id) == {
            "supervisor": 1,
            "web": 1,
            "worker": 1,
        }
        lifecycle_tail = _read_tail(log_path, log_offset)
        assert "stale_runtime_state_retired" not in lifecycle_tail

        stopped = _controller("stop", env)
        assert stopped.returncode == 0, _controller_failure(stopped)
        assert not RUNTIME_STATE.exists()
        assert all(_listener_pid(port) is None for port in ports)
        assert _runtime_role_counts(str(first_state["runtimeInstanceId"]), worker_id) == {
            "supervisor": 0,
            "web": 0,
            "worker": 0,
        }
        lifecycle_tail = _read_tail(log_path, log_offset)
        for event in (
            "runtime.shutdown_requested",
            "runtime.shutdown_begin",
            "runtime.shutdown_complete",
            "runtime.process_shutdown",
        ):
            assert event in lifecycle_tail
    finally:
        if first_state is None and RUNTIME_STATE.exists():
            first_state = json.loads(RUNTIME_STATE.read_text(encoding="utf-8"))
        if first_state is not None:
            _cleanup_runtime(str(first_state["runtimeInstanceId"]))
        RUNTIME_STATE.unlink(missing_ok=True)


def _migrate(database_url: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr


def _lifecycle_environment(
    database_url: str,
    *,
    worker_id: str,
    web_port: int,
    worker_metrics_port: int,
    supervisor_metrics_port: int,
    supervisor_state_path: Path,
) -> dict[str, str]:
    url = make_url(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            data_directory, version_number = connection.execute(
                text(
                    "select current_setting('data_directory'), "
                    "current_setting('server_version_num')"
                )
            ).one()
    finally:
        engine.dispose()
    services, listeners, processes = collect_windows_postgres_evidence(url.port or 5432)
    listener_pids = {int(row["pid"]) for row in listeners}
    matching = [
        service
        for service in services
        if str(service.get("state", "")).lower() == "running"
        and normalize_path(service.get("dataDirectory")) == normalize_path(data_directory)
        and any(
            _descends_from(listener_pid, int(service["pid"]), processes)
            for listener_pid in listener_pids
        )
    ]
    assert len(matching) == 1, "disposable PostgreSQL Windows service identity is ambiguous"
    service = matching[0]
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
        "SWINGLENS_MANAGE_POSTGRES": "false",
        "SWINGLENS_POSTGRES_SERVICE": str(service["name"]),
        "SWINGLENS_POSTGRES_EXPECTED_MAJOR": str(int(version_number) // 10000),
        "SWINGLENS_POSTGRES_DATA_DIR": str(data_directory),
        "SWINGLENS_POSTGRES_EXECUTABLE": str(service["executable"]),
        "APP_HOST": "127.0.0.1",
        "APP_PORT": str(web_port),
        "OBSERVABILITY_METRICS_ENABLED": "true",
        "OBSERVABILITY_WORKER_METRICS_PORT": str(worker_metrics_port),
        "OBSERVABILITY_SUPERVISOR_METRICS_PORT": str(supervisor_metrics_port),
        "SWINGLENS_SUPERVISOR_STATE_PATH": str(supervisor_state_path),
        "JOB_WORKER_ID": worker_id,
        "JOB_POLL_INTERVAL_SECONDS": "0.1",
        "JOB_WORKER_HEARTBEAT_INTERVAL_SECONDS": "0.2",
        "JOB_WORKER_HEARTBEAT_TIMEOUT_SECONDS": "5",
        "JOB_WATCHDOG_INTERVAL_SECONDS": "1",
        "CERI_PROVIDER_INGEST_ENABLED": "false",
        "WINNER_PROBABILITY_AUTO_MATURATION_ENABLED": "false",
        "WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED": "false",
        "MARKET_DATA_PREWARM_ENABLED": "false",
        "WORKER_MEMORY_TRACEMALLOC_ENABLED": "false",
    }
    env.pop("GRAFANA_ADMIN_PASSWORD", None)
    return env


def _controller(action: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(ROOT / "swinglens.ps1"),
            action,
            "-RuntimeMode",
            "CERTIFICATION",
            "-Json",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def _controller_failure(result: subprocess.CompletedProcess[str]) -> str:
    return f"stdout={result.stdout[-4000:]}\nstderr={result.stderr[-4000:]}"


def _registration_snapshot(database_url: str, worker_id: str) -> dict[str, object]:
    engine = create_engine(database_url)
    try:
        with Session(engine) as db:
            supervisor = db.get(BackgroundSupervisor, worker_id)
            worker = db.get(BackgroundWorker, worker_id)
            assert supervisor is not None and supervisor.stopping_at is None
            assert worker is not None and worker.stopping_at is None
            return {
                "supervisor": (
                    supervisor.instance_id,
                    supervisor.process_id,
                    supervisor.process_started_at.isoformat(),
                    supervisor.generation,
                ),
                "worker": (
                    worker.instance_id,
                    worker.process_id,
                    worker.process_started_at.isoformat(),
                    worker.generation,
                ),
            }
    finally:
        engine.dispose()


def _wait_for_runtime_survival(
    state: dict[str, object], ports: list[int], *, seconds: float
) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.5)
    for role in ("supervisor", "web"):
        assert psutil.pid_exists(int(state[role]["pid"]))
    assert all(_listener_pid(port) is not None for port in ports)


def _runtime_role_counts(runtime_instance_id: str, worker_id: str) -> dict[str, int]:
    modules = {
        "supervisor": "app.worker_supervisor",
        "web": "app.serve",
        "worker": "app.worker",
    }
    matching: dict[str, set[int]] = {role: set() for role in modules}
    for process in psutil.process_iter(("cmdline", "cwd")):
        try:
            command = " ".join(process.info.get("cmdline") or [])
            if normalize_path(process.info.get("cwd")) != normalize_path(ROOT):
                continue
            for role, module in modules.items():
                identity = worker_id if role == "worker" else runtime_instance_id
                if f"-m {module}" in command and identity in command:
                    matching[role].add(process.pid)
        except (OSError, psutil.Error):
            continue
    return {
        role: sum(
            not any(
                other_pid != pid and _process_descends_from(other_pid, pid)
                for other_pid in pids
            )
            for pid in pids
        )
        for role, pids in matching.items()
    }


def _process_descends_from(pid: int, ancestor_pid: int) -> bool:
    try:
        return ancestor_pid in {parent.pid for parent in psutil.Process(pid).parents()}
    except (OSError, psutil.Error):
        return False


def _listener_pid(port: int) -> int | None:
    matches = {
        row.pid
        for row in psutil.net_connections(kind="tcp")
        if row.status == psutil.CONN_LISTEN
        and row.laddr
        and row.laddr.port == port
        and row.pid
    }
    assert len(matches) <= 1
    return int(next(iter(matches))) if matches else None


def _free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _read_tail(path: Path, offset: int) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(offset)
        return stream.read().decode("utf-8", errors="replace")


def _descends_from(pid: int, ancestor_pid: int, processes: dict[int, dict]) -> bool:
    current = pid
    for _ in range(16):
        if current == ancestor_pid:
            return True
        row = processes.get(current)
        if row is None:
            return False
        current = int(row.get("parentPid") or 0)
    return False


def _cleanup_runtime(runtime_instance_id: str) -> None:
    for process in psutil.process_iter(("pid", "cmdline", "cwd")):
        try:
            command = " ".join(process.info.get("cmdline") or [])
            if (
                runtime_instance_id in command
                and normalize_path(process.info.get("cwd")) == normalize_path(ROOT)
            ):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
        except (OSError, psutil.Error):
            continue
