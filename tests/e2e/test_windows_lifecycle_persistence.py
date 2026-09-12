from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

import psutil
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import BackgroundSupervisor, BackgroundWorker
from app.services.lifecycle_control import TOPOLOGY_VERSION
from app.services.lifecycle_safety import normalize_path
from scripts.ops import lifecycle_probe

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows lifecycle persistence gate"),
]
ROOT = Path(__file__).resolve().parents[2]
RUNTIME_STATE = ROOT / "data" / "cache" / "swinglens-lifecycle.json"


def test_windows_runtime_survives_controller_exit_and_reuses_generation(
    disposable_postgres_database: str,
) -> None:
    assert not RUNTIME_STATE.exists(), "canonical runtime state must be absent before the gate"
    _migrate(disposable_postgres_database)
    worker_id = f"windows-persistence-{uuid4().hex}"
    evidence_path = ROOT / "test-results" / f"windows-lifecycle-{uuid4().hex}"
    evidence_path.mkdir(parents=True, exist_ok=False)
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
        supervisor_state_path=evidence_path / "supervisor-state.json",
        persistence_test_path=evidence_path,
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
        _wait_for_runtime_survival(first_state, ports, seconds=45)

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
        supervisor_log = (evidence_path / "supervisor-stderr.log").read_text(
            encoding="utf-8", errors="replace"
        )
        shutdown_evidence = lifecycle_tail + "\n" + supervisor_log
        for event in (
            "runtime.shutdown_requested",
            "runtime.shutdown_begin",
            "runtime.shutdown_complete",
            "runtime.process_shutdown",
        ):
            assert event in shutdown_evidence
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
    persistence_test_path: Path,
) -> dict[str, str]:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
        "PROCESS_ROLE": "CLI_OR_MAINTENANCE",
        "RUNTIME_MODE": "CERTIFICATION",
        "USE_DURABLE_PIPELINE": "true",
        "DURABLE_WORKER_PROCESS_ENABLED": "true",
        "EMBEDDED_JOB_WORKER_ENABLED": "false",
        "JOB_WORKER_ENABLED": "false",
        "APP_HOST": "127.0.0.1",
        "APP_PORT": str(web_port),
        "OBSERVABILITY_METRICS_ENABLED": "true",
        "OBSERVABILITY_WORKER_METRICS_PORT": str(worker_metrics_port),
        "OBSERVABILITY_SUPERVISOR_METRICS_PORT": str(supervisor_metrics_port),
        "SWINGLENS_SUPERVISOR_STATE_PATH": str(supervisor_state_path),
        "SWINGLENS_PERSISTENCE_TEST_PATH": str(persistence_test_path),
        "SWINGLENS_RUNTIME_CONFIG_FINGERPRINT": f"disposable-{uuid4().hex}",
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
    controller_env = {
        **env,
        "SWINGLENS_LIFECYCLE_OPERATION_ID": f"windows-persistence-{action}-{uuid4().hex}",
        "PYTHONPATH": os.pathsep.join(
            value for value in (str(ROOT), env.get("PYTHONPATH")) if value
        ),
    }
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), action],
        cwd=ROOT,
        env=controller_env,
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


def _controller_main(action: str) -> None:
    test_path = Path(os.environ["SWINGLENS_PERSISTENCE_TEST_PATH"])
    if action == "start":
        listener_pid = _listener_pid(int(os.environ["APP_PORT"]))
        report = lifecycle_probe._runtime_state_report(listener_pid)
        if report.get("valid"):
            print(json.dumps({"result": "reused", "state": report["state"]}))
            return
        if report.get("classification") not in {"MISSING", None}:
            raise RuntimeError(f"unexpected runtime state: {report}")
        launch = lifecycle_probe._launch_runtime(
            test_path / "supervisor-stdout.log",
            test_path / "supervisor-stderr.log",
        )
        state = {
            "version": 5,
            "runtimeInstanceId": launch["runtimeInstanceId"],
            "repoRoot": str(ROOT),
            "gitCommit": lifecycle_probe._git_commit(),
            "topologyVersion": TOPOLOGY_VERSION,
            "runtimeConfigFingerprint": os.environ[
                "SWINGLENS_RUNTIME_CONFIG_FINGERPRINT"
            ],
            "web": {
                "pid": launch["pid"],
                "createdAt": launch["createdAt"],
                "launcherPid": launch["launcherPid"],
                "launcherCreatedAt": launch["launcherCreatedAt"],
                "role": "web",
                "module": "app.serve",
                "repoRoot": str(ROOT),
                "runtimeInstanceId": launch["runtimeInstanceId"],
                "port": int(os.environ["APP_PORT"]),
            },
            "processGroup": {
                "pid": launch["processGroupPid"],
                "createdAt": launch["processGroupCreatedAt"],
                "role": "supervisor",
                "module": "app.worker_supervisor",
                "repoRoot": str(ROOT),
                "runtimeInstanceId": launch["runtimeInstanceId"],
            },
            "supervisor": {
                "pid": launch["supervisorPid"],
                "createdAt": launch["supervisorCreatedAt"],
                "role": "supervisor",
                "module": "app.worker_supervisor",
                "repoRoot": str(ROOT),
                "runtimeInstanceId": launch["runtimeInstanceId"],
            },
        }
        lifecycle_probe._write_state(json.dumps(state))
        _wait_for_core_ready(int(os.environ["APP_PORT"]), timeout=90)
        print(json.dumps({"result": "launched", "state": state}))
        return
    if action != "stop":
        raise ValueError(action)
    state = json.loads(RUNTIME_STATE.read_text(encoding="utf-8"))
    web_pid = int(state["web"]["pid"])
    supervisor_pid = int(state["supervisor"]["pid"])
    result = lifecycle_probe._signal_break(
        web_pid, _listener_pid(int(os.environ["APP_PORT"]))
    )
    if not result.get("signaled"):
        raise RuntimeError(f"shutdown request failed: {result}")
    deadline = time.monotonic() + 45
    ports = [
        int(os.environ[name])
        for name in (
            "APP_PORT",
            "OBSERVABILITY_WORKER_METRICS_PORT",
            "OBSERVABILITY_SUPERVISOR_METRICS_PORT",
        )
    ]
    while time.monotonic() < deadline and (
        any(_listener_pid(port) for port in ports) or psutil.pid_exists(supervisor_pid)
    ):
        time.sleep(0.25)
    if any(_listener_pid(port) for port in ports) or psutil.pid_exists(supervisor_pid):
        raise TimeoutError("runtime topology did not stop")
    RUNTIME_STATE.unlink(missing_ok=True)
    print(json.dumps({"result": "stopped", "shutdown": result}))


def _wait_for_core_ready(port: int, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310 - loopback disposable test
                f"http://127.0.0.1:{port}/ready/core", timeout=3
            ) as response:
                payload = json.load(response)
                if response.status == 200 and payload.get("status") == "ok":
                    return
        except OSError:
            pass
        time.sleep(0.25)
    raise TimeoutError("runtime did not reach core readiness")


if __name__ == "__main__":
    _controller_main(sys.argv[1])
