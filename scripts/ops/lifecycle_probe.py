from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import psutil
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.tables import BackgroundSupervisor, BackgroundWorker
from app.services.alembic_heads import database_alembic_heads, repository_alembic_heads
from app.services.lifecycle_quiesce import (
    blocking_jobs,
    request_worker_quiesce,
    resume_worker_claims,
)
from app.services.lifecycle_safety import (
    LifecycleConflict,
    PostgresExpectation,
    atomic_write_json,
    collect_windows_postgres_evidence,
    inspect_process,
    read_runtime_state,
    validate_runtime_process,
    verify_postgres_provenance,
)
from app.services.process_roles import build_process_environment
from app.services.redaction import redact_text
from app.settings import ProcessRole, Settings

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_STATE = ROOT / "data" / "cache" / "swinglens-lifecycle.json"


def _settings() -> Settings:
    return Settings()


def _config_report() -> dict[str, object]:
    settings = _settings()
    url = make_url(settings.database_url)
    return {
        "database": {
            "scheme": url.drivername,
            "host": url.host or "",
            "port": url.port or 5432,
            "database": url.database or "",
        },
        "postgres": {
            "service": settings.swinglens_postgres_service,
            "expectedMajor": settings.swinglens_postgres_expected_major,
            "dataDirectory": str(settings.swinglens_postgres_data_dir),
            "serviceExecutable": str(settings.swinglens_postgres_executable),
            "managementEnabled": settings.swinglens_manage_postgres,
        },
        "web": {"host": settings.app_host, "port": settings.app_port},
        "metrics": {
            "enabled": settings.observability_metrics_enabled,
            "workerPort": settings.observability_worker_metrics_port,
            "supervisorPort": settings.observability_supervisor_metrics_port,
        },
        "grafanaPasswordConfigured": bool(
            settings.grafana_admin_password and settings.grafana_admin_password.get_secret_value()
        ),
        "migrationTimeoutSeconds": settings.swinglens_migration_timeout_seconds,
        "lockTimeoutSeconds": settings.swinglens_lifecycle_lock_timeout_seconds,
        "observabilityTimeoutSeconds": settings.swinglens_observability_timeout_seconds,
        "workerId": settings.job_worker_id,
        "useDurablePipeline": settings.use_durable_pipeline,
        "runtimeMode": settings.runtime_mode.value,
        "processRole": settings.process_role.value,
        "durableWorkerProcessEnabled": settings.durable_worker_process_enabled,
        "embeddedJobWorkerEnabled": settings.embedded_job_worker_enabled,
    }


def _database_report() -> dict[str, object]:
    settings = _settings()
    url = make_url(settings.database_url)
    report: dict[str, object] = {
        "scheme": url.drivername,
        "host": url.host or "",
        "port": url.port or 5432,
        "database": url.database or "",
        "reachable": False,
        "dataDirectory": None,
        "serverVersion": None,
        "serverVersionNum": None,
        "currentDatabase": None,
        "currentHeads": [],
        "expectedHeads": list(repository_alembic_heads(ROOT)),
        "schemaAtHead": False,
        "useDurablePipeline": settings.use_durable_pipeline,
    }
    engine = None
    try:
        engine = create_engine(
            settings.database_url,
            poolclass=NullPool,
            connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
        )
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "select current_setting('data_directory'), current_setting('server_version'), "
                    "current_setting('server_version_num'), current_database()"
                )
            ).one()
            report.update(
                reachable=True,
                dataDirectory=str(row[0]),
                serverVersion=str(row[1]),
                serverVersionNum=int(row[2]),
                currentDatabase=str(row[3]),
            )
            report["currentHeads"] = list(database_alembic_heads(connection))
            report["schemaAtHead"] = report["currentHeads"] == report["expectedHeads"]
    except Exception as exc:
        report["error"] = redact_text(str(exc))
    finally:
        if engine is not None:
            engine.dispose()
    return report


def _provenance_report() -> dict[str, object]:
    settings = _settings()
    database = _database_report()
    services, listeners, processes = collect_windows_postgres_evidence(int(database["port"]))
    expected = PostgresExpectation(
        service=settings.swinglens_postgres_service,
        major=settings.swinglens_postgres_expected_major,
        data_directory=str(settings.swinglens_postgres_data_dir),
        service_executable=str(settings.swinglens_postgres_executable),
        database=str(database["database"]),
    )
    try:
        return verify_postgres_provenance(database, services, listeners, processes, expected)
    except LifecycleConflict as exc:
        return {"verified": False, "error": str(exc), "database": database}


def _launch_web(stdout_path: Path, stderr_path: Path) -> dict[str, object]:
    settings = _settings()
    runtime_instance_id = uuid4().hex
    supervised = settings.durable_worker_process_enabled
    role = ProcessRole.SUPERVISOR if supervised else ProcessRole.WEB
    environment = build_process_environment(os.environ, role=role, settings=settings)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        stdout_path.open("ab", buffering=0) as stdout,
        stderr_path.open("ab", buffering=0) as stderr,
    ):
        command = [sys.executable, "-m"]
        if supervised:
            command.extend(
                [
                    "app.worker_supervisor",
                    "--worker-id",
                    settings.job_worker_id,
                    "--queues",
                    "interactive,broker,background",
                ]
            )
        else:
            command.append("app.serve")
        command.extend(
            [
                "--host",
                settings.app_host,
                "--port",
                str(settings.app_port),
                "--runtime-instance-id",
                runtime_instance_id,
                "--repo-root",
                str(ROOT),
            ]
        )
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    launcher = inspect_process(process.pid)
    identity = launcher
    supervisor_identity = launcher
    # The Windows venv launcher can create more than one same-command Python
    # process.  A command-line match alone is not a canonical WEB identity: the
    # only process safe to publish is the descendant that actually owns the
    # listening socket.  Imports on a cold certification database can take
    # materially longer than the creation of the intermediate launcher.
    deadline = monotonic() + 90
    while monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "CANONICAL_RUNTIME_START_FAILED: supervisor exited before the WEB listener "
                f"was established (exit_code={process.returncode})"
            )
        candidates = []
        try:
            children = psutil.Process(process.pid).children(recursive=True)
            candidates = [inspect_process(child.pid) for child in children]
        except (OSError, psutil.Error):
            pass
        matching = [
            candidate
            for candidate in candidates
            if _process_matches_runtime(candidate, "app.serve", runtime_instance_id)
        ]
        listener = _listener_process_identity(
            port=settings.app_port,
            runtime_instance_id=runtime_instance_id,
            ancestor_pid=process.pid,
        )
        nested_web = [
            candidate
            for candidate in matching
            if any(
                int(other["pid"]) != int(candidate["pid"])
                and _process_descends_from(int(candidate["pid"]), int(other["pid"]))
                for other in matching
            )
        ]
        # On Windows a venv python.exe launcher creates the real interpreter as
        # a same-command child. Recording the wrapper breaks strong listener
        # ownership validation because only the child owns the socket.
        selectable = nested_web if os.name == "nt" else matching
        if listener is not None:
            identity = listener
            if supervised:
                supervisors = [
                    candidate
                    for candidate in candidates
                    if _process_matches_runtime(
                        candidate, "app.worker_supervisor", runtime_instance_id
                    )
                ]
                if supervisors:
                    supervisor_identity = supervisors[-1]
            if not supervised or _process_matches_runtime(
                supervisor_identity, "app.worker_supervisor", runtime_instance_id
            ):
                break
        # Preserve the best command-line candidate only for the eventual error
        # report.  Never publish it unless it becomes the verified listener.
        if selectable:
            identity = max(
                selectable,
                key=lambda candidate: len(psutil.Process(int(candidate["pid"])).parents()),
            )
        sleep(0.05)
    if (
        not _process_matches_runtime(identity, "app.serve", runtime_instance_id)
        or _listener_process_identity(
            port=settings.app_port,
            runtime_instance_id=runtime_instance_id,
            ancestor_pid=process.pid,
        )
        is None
    ):
        _terminate_process_tree(process)
        raise RuntimeError(
            "WEB_LISTENER_IDENTITY_TIMEOUT: SwingLens supervisor did not establish a verified "
            "WEB listener within 90 seconds"
        )
    report = {
        "pid": identity["pid"],
        "createdAt": identity["createdAt"],
        "launcherPid": supervisor_identity["pid"] if supervised else launcher["pid"],
        "launcherCreatedAt": (
            supervisor_identity["createdAt"] if supervised else launcher["createdAt"]
        ),
        "runtimeInstanceId": runtime_instance_id,
    }
    if supervised:
        report["supervisorPid"] = supervisor_identity["pid"]
        report["supervisorCreatedAt"] = supervisor_identity["createdAt"]
    return report


def _listener_process_identity(
    *, port: int, runtime_instance_id: str, ancestor_pid: int
) -> dict[str, object] | None:
    """Return the exact app.serve descendant that owns ``port``."""

    try:
        connections = psutil.net_connections(kind="tcp")
    except (OSError, psutil.Error):
        return None
    for connection in connections:
        if connection.status != psutil.CONN_LISTEN or connection.pid is None:
            continue
        local_address = connection.laddr
        local_port = getattr(local_address, "port", None)
        if local_port is None and len(local_address) >= 2:
            local_port = local_address[1]
        if int(local_port or 0) != int(port):
            continue
        try:
            identity = inspect_process(int(connection.pid))
        except (OSError, psutil.Error):
            continue
        if _process_matches_runtime(
            identity, "app.serve", runtime_instance_id
        ) and _process_descends_from(int(connection.pid), ancestor_pid):
            return identity
    return None


def _terminate_process_tree(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            process.terminate()
    except (OSError, psutil.Error):
        pass


def _write_state(payload: str) -> dict[str, object]:
    atomic_write_json(RUNTIME_STATE, json.loads(payload))
    return {"written": True}


def _runtime_state_report(listener_pid: int | None) -> dict[str, object]:
    try:
        state = read_runtime_state(RUNTIME_STATE)
        if state is None:
            return {
                "valid": False,
                "missing": True,
                "stale": False,
                "conflict": False,
                "error": "runtime state is missing",
            }
        web = state.get("web")
        if not isinstance(web, dict):
            raise LifecycleConflict("runtime web identity is missing")
        try:
            actual = inspect_process(int(web.get("pid") or 0))
        except psutil.NoSuchProcess:
            return {
                "valid": False,
                "stale": True,
                "conflict": False,
                "state": state,
            }
        validate_runtime_process(web, actual, listener_pid=listener_pid)
        launcher = actual
        supervisor = state.get("supervisor")
        if isinstance(supervisor, dict):
            launcher = inspect_process(int(supervisor.get("pid") or 0))
            validate_runtime_process(supervisor, launcher)
            if not _process_matches_runtime(
                launcher, "app.worker_supervisor", str(state.get("runtimeInstanceId"))
            ):
                raise LifecycleConflict("recorded supervisor command identity mismatch")
            if not _process_descends_from(int(actual["pid"]), int(launcher["pid"])):
                raise LifecycleConflict("web listener is not owned by its recorded supervisor")
        elif web.get("launcherPid") is not None:
            launcher_expected = {
                **web,
                "pid": web.get("launcherPid"),
                "createdAt": web.get("launcherCreatedAt"),
            }
            launcher = inspect_process(int(web.get("launcherPid") or 0))
            validate_runtime_process(launcher_expected, launcher)
        return {
            "valid": True,
            "stale": False,
            "conflict": False,
            "state": state,
            "actual": actual,
            "launcher": launcher,
        }
    except (LifecycleConflict, OSError, psutil.Error, ValueError) as exc:
        return {"valid": False, "stale": False, "conflict": True, "error": str(exc)}


def _quiesce_report(resume: bool = False) -> dict[str, object]:
    settings = _settings()
    engine = create_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )
    try:
        with Session(engine) as db:
            if resume:
                resumed = resume_worker_claims(db, settings.job_worker_id)
                db.commit()
                return {"reachable": True, "resumed": resumed}
            worker = request_worker_quiesce(db, settings.job_worker_id)
            db.commit()
            if worker is not None:
                db.refresh(worker)
            active = blocking_jobs(db)
            process_roles = {row["role"] for row in _role_processes()}
            # A failed startup may have WEB/SUPERVISOR alive but no worker able
            # to acknowledge quiesce. With zero active business jobs, absence
            # of a worker process is itself a safe, deterministic acknowledgement.
            absent_is_safe = "worker" not in process_roles and not active
            return {
                "reachable": True,
                "workerPresent": worker is not None,
                "requested": worker is not None and worker.quiesce_requested_at is not None,
                "acknowledged": absent_is_safe
                or (worker is not None and worker.quiesced_at is not None),
                "activeCount": len(active),
                "active": [
                    {"id": row.id, "job_type": row.job_type, "status": row.status} for row in active
                ],
            }
    except Exception as exc:
        return {"reachable": False, "error": redact_text(str(exc))}
    finally:
        engine.dispose()


def _migrate() -> dict[str, object]:
    provenance = _provenance_report()
    if not provenance.get("verified"):
        return {
            "migrated": False,
            "conflict": True,
            "error": "PostgreSQL provenance could not be verified. Alembic was not executed: "
            + str(provenance.get("error")),
        }
    timeout = _settings().swinglens_migration_timeout_seconds
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"migrated": False, "error": f"Alembic exceeded {timeout} seconds"}
    output = redact_text((result.stdout or "") + (result.stderr or ""))
    return {
        "migrated": result.returncode == 0,
        "exitCode": result.returncode,
        "output": output,
        "error": "" if result.returncode == 0 else output,
    }


def _docker_command(arguments: list[str], timeout: int) -> dict[str, object]:
    try:
        result = subprocess.run(
            ["docker", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "Docker CLI is unavailable"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Docker command exceeded {timeout} seconds"}
    if result.returncode != 0:
        return {
            "ok": False,
            "error": f"Docker command exited with code {result.returncode}",
        }
    return {"ok": True}


def _observability_report(action: str) -> dict[str, object]:
    settings = _settings()
    timeout = settings.swinglens_observability_timeout_seconds
    info = _docker_command(["info", "--format", "{{.ServerVersion}}"], min(timeout, 15))
    if not info["ok"] or action == "info":
        return info
    compose = ["compose", "-f", str(ROOT / "docker-compose.observability.yml")]
    if action == "start":
        return _docker_command([*compose, "up", "-d"], timeout)
    components = {
        service: _docker_command([*compose, "stop", service], timeout)
        for service in ("grafana", "prometheus")
    }
    failed = [service for service, result in components.items() if not result["ok"]]
    return {
        "ok": not failed,
        "components": components,
        "error": "" if not failed else "Docker stop failed for " + ", ".join(failed),
    }


def _signal_break(process_id: int, listener_pid: int | None) -> dict[str, object]:
    report = _runtime_state_report(listener_pid)
    if not report.get("valid"):
        return {"signaled": False, "conflict": True, "error": report.get("error")}
    expected_pid = int(report["state"]["web"]["pid"])
    if process_id != expected_pid:
        return {"signaled": False, "conflict": True, "error": "requested PID is not state PID"}
    second = _runtime_state_report(listener_pid)
    if not second.get("valid"):
        return {"signaled": False, "conflict": True, "error": second.get("error")}
    try:
        supervisor = report["state"].get("supervisor")
        signal_pid = int(supervisor["pid"]) if isinstance(supervisor, dict) else process_id
        os.kill(signal_pid, signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        return {"signaled": True, "signalPid": signal_pid}
    except Exception as exc:
        return {"signaled": False, "error": redact_text(str(exc))}


def _role_processes() -> list[dict[str, object]]:
    patterns = {
        "web": "app.serve",
        "supervisor": "app.worker_supervisor",
        "worker": "app.worker",
    }
    found = []
    for process in psutil.process_iter(("pid", "cmdline", "create_time", "cwd")):
        try:
            command = [str(part) for part in process.info.get("cmdline") or []]
            joined = " ".join(command)
            role = next(
                (name for name, module in patterns.items() if f"-m {module}" in joined),
                None,
            )
            if role and os.path.normcase(process.info.get("cwd") or "") == os.path.normcase(
                str(ROOT)
            ):
                found.append(
                    {
                        "role": role,
                        "pid": process.pid,
                        "createdAt": datetime.fromtimestamp(
                            process.info["create_time"], UTC
                        ).isoformat(),
                    }
                )
        except (OSError, psutil.Error, TypeError):
            continue
    return [
        row
        for row in found
        if not any(
            other["role"] == row["role"]
            and other["pid"] != row["pid"]
            and _process_descends_from(int(other["pid"]), int(row["pid"]))
            for other in found
        )
    ]


def _process_matches_runtime(
    actual: dict[str, object], module: str, runtime_instance_id: str | None = None
) -> bool:
    command = [str(part) for part in actual.get("commandLine") or []]
    return (
        f"-m {module}" in " ".join(command)
        and (runtime_instance_id is None or runtime_instance_id in command)
        and os.path.normcase(str(actual.get("cwd") or "")) == os.path.normcase(str(ROOT))
    )


def _process_descends_from(process_id: int, ancestor_id: int) -> bool:
    if process_id == ancestor_id:
        return True
    try:
        return any(parent.pid == ancestor_id for parent in psutil.Process(process_id).parents())
    except (OSError, psutil.Error):
        return False


def _registrations_report() -> dict[str, object]:
    settings = _settings()
    engine = create_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )
    try:
        with Session(engine) as db:
            worker = db.get(BackgroundWorker, settings.job_worker_id)
            supervisor = db.get(BackgroundSupervisor, settings.job_worker_id)
            return {
                "reachable": True,
                "worker": _registration_dict(worker, "worker"),
                "supervisor": _registration_dict(supervisor, "supervisor"),
            }
    except Exception as exc:
        return {"reachable": False, "error": redact_text(str(exc))}
    finally:
        engine.dispose()


def _registration_dict(row, role: str) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "role": role,
        "pid": row.process_id,
        "createdAt": row.process_started_at.isoformat() if row.process_started_at else None,
        "instanceId": row.instance_id,
        "generation": row.generation,
        "stoppingAt": row.stopping_at.isoformat() if row.stopping_at else None,
        "launcherPid": getattr(row, "launcher_process_id", None),
    }


def _signal_registered(role: str) -> dict[str, object]:
    module = f"app.worker_{role}" if role == "supervisor" else "app.worker"
    first = _registrations_report()
    expected = first.get(role)
    if not first.get("reachable") or not isinstance(expected, dict):
        if not any(row["role"] == role for row in _role_processes()):
            return {"signaled": True, "alreadyExited": True, "pid": 0}
        return {"signaled": False, "conflict": True, "error": f"{role} registration unavailable"}
    try:
        try:
            actual = inspect_process(int(expected["pid"]))
        except psutil.NoSuchProcess:
            if not any(row["role"] == role for row in _role_processes()):
                return {
                    "signaled": True,
                    "alreadyExited": True,
                    "pid": expected["pid"],
                }
            raise LifecycleConflict(
                f"{role} registered process disappeared but a peer remains"
            ) from None
        created = datetime.fromisoformat(str(expected["createdAt"]).replace("Z", "+00:00"))
        observed = datetime.fromisoformat(str(actual["createdAt"]).replace("Z", "+00:00"))
        command = " ".join(actual.get("commandLine") or [])
        if (
            abs((created - observed).total_seconds()) > 2
            or f"-m {module}" not in command
            or os.path.normcase(actual.get("cwd") or "") != os.path.normcase(str(ROOT))
        ):
            raise LifecycleConflict(f"{role} OS process identity mismatch")
        signal_pid = int(expected.get("launcherPid") or expected["pid"])
        launcher = inspect_process(signal_pid)
        if not _process_matches_runtime(launcher, module) or not _process_descends_from(
            int(expected["pid"]), signal_pid
        ):
            raise LifecycleConflict(f"{role} launcher identity mismatch")
        second = _registrations_report()
        current = second.get(role)
        if not isinstance(current, dict) or any(
            current.get(key) != expected.get(key) for key in ("pid", "instanceId", "generation")
        ):
            raise LifecycleConflict(f"{role} registration changed before signal")
        validate_runtime = inspect_process(int(expected["pid"]))
        if not _process_matches_runtime(validate_runtime, module):
            raise LifecycleConflict(f"{role} OS process changed before signal")
        os.kill(
            signal_pid if os.name == "nt" else int(expected["pid"]),
            signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM,
        )
        return {"signaled": True, "pid": expected["pid"]}
    except (LifecycleConflict, OSError, psutil.Error, ValueError) as exc:
        return {"signaled": False, "conflict": True, "error": str(exc)}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in (
        "config",
        "database",
        "provenance",
        "quiesce",
        "resume",
        "migrate",
        "processes",
        "registrations",
        "observability-info",
        "observability-start",
        "observability-stop",
    ):
        subparsers.add_parser(name)
    launch = subparsers.add_parser("launch-web")
    launch.add_argument("--stdout", type=Path, required=True)
    launch.add_argument("--stderr", type=Path, required=True)
    write_state = subparsers.add_parser("write-state")
    write_state.add_argument("--json", required=True)
    state = subparsers.add_parser("runtime-state")
    state.add_argument("--listener-pid", type=int)
    stop = subparsers.add_parser("signal-break")
    stop.add_argument("--pid", type=int, required=True)
    stop.add_argument("--listener-pid", type=int)
    registered = subparsers.add_parser("signal-registered")
    registered.add_argument("--role", choices=("supervisor", "worker"), required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    commands = {
        "config": _config_report,
        "database": _database_report,
        "provenance": _provenance_report,
        "quiesce": _quiesce_report,
        "resume": lambda: _quiesce_report(True),
        "migrate": _migrate,
        "processes": lambda: {"processes": _role_processes()},
        "registrations": _registrations_report,
        "observability-info": lambda: _observability_report("info"),
        "observability-start": lambda: _observability_report("start"),
        "observability-stop": lambda: _observability_report("stop"),
    }
    if args.command in commands:
        report = commands[args.command]()
    elif args.command == "launch-web":
        report = _launch_web(args.stdout, args.stderr)
    elif args.command == "write-state":
        report = _write_state(args.json)
    elif args.command == "runtime-state":
        report = _runtime_state_report(args.listener_pid)
    elif args.command == "signal-break":
        report = _signal_break(args.pid, args.listener_pid)
    else:
        report = _signal_registered(args.role)
    print(json.dumps(report, default=str, separators=(",", ":")))


if __name__ == "__main__":
    main()
