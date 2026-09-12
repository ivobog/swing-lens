from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import psutil
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.database_safety import DATABASE_SAFETY_CONTEXT_ENV, DatabaseSafetyContext
from app.models.tables import BackgroundJob, BackgroundSupervisor, BackgroundWorker
from app.services.alembic_heads import database_alembic_heads, repository_alembic_heads
from app.services.canonical_runtime_launcher import build_canonical_runtime_launch
from app.services.lifecycle_control import (
    GIT_SHA_ENV,
    RUNTIME_FINGERPRINT_ENV,
    TOPOLOGY_VERSION,
    append_lifecycle_event,
    configuration_provenance,
    redacted_tail,
    runtime_generation,
    shutdown_request_path,
    supervisor_state_path,
    update_lifecycle_metrics,
)
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
from app.services.redaction import redact_sensitive, redact_text
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_STATE = ROOT / "data" / "cache" / "swinglens-lifecycle.json"
LIFECYCLE_CONTROLLER_VERSION = 5


class RuntimeStateClassification(StrEnum):
    ACTIVE_VALID = "ACTIVE_VALID"
    ACTIVE_GENERATION_MISMATCH = "ACTIVE_GENERATION_MISMATCH"
    DEAD_STALE = "DEAD_STALE"
    AMBIGUOUS_CONFLICT = "AMBIGUOUS_CONFLICT"
    MISSING = "MISSING"


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
        "jobWorkerEnabledLegacy": settings.job_worker_enabled,
        "topologyVersion": TOPOLOGY_VERSION,
        "provenance": configuration_provenance(settings, env_file=ROOT / ".env"),
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


def _launch_runtime(stdout_path: Path, stderr_path: Path) -> dict[str, object]:
    settings = _settings()
    runtime_instance_id = uuid4().hex
    creationflags = (
        subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_BREAKAWAY_FROM_JOB
        if os.name == "nt"
        else 0
    )
    shutdown_request_path(ROOT, runtime_instance_id).unlink(missing_ok=True)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        stdout_path.open("ab", buffering=0) as stdout,
        stderr_path.open("ab", buffering=0) as stderr,
    ):
        launch = build_canonical_runtime_launch(
            settings=settings,
            parent_environment=os.environ,
            repo_root=ROOT,
            git_sha=_git_commit(),
            runtime_instance_id=runtime_instance_id,
        )
        command = list(launch.command)
        environment = launch.environment
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
            supervisors = [
                candidate
                for candidate in candidates
                if _process_matches_runtime(candidate, "app.worker_supervisor", runtime_instance_id)
            ]
            if supervisors:
                supervisor_identity = supervisors[-1]
            if _process_matches_runtime(
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
        "launcherPid": supervisor_identity["pid"],
        "launcherCreatedAt": supervisor_identity["createdAt"],
        # Windows creation flags apply to the exact Popen PID. The venv launcher
        # may then create a real-interpreter child, so this identity is
        # deliberately distinct from supervisorPid.
        "processGroupPid": launcher["pid"],
        "processGroupCreatedAt": launcher["createdAt"],
        "runtimeInstanceId": runtime_instance_id,
        "supervisorPid": supervisor_identity["pid"],
        "supervisorCreatedAt": supervisor_identity["createdAt"],
    }
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


def _runtime_diagnostics(state: dict[str, object]) -> dict[str, object]:
    return {
        "recordedGitSha": state.get("gitCommit"),
        "desiredGitSha": _git_commit(),
        "recordedFingerprint": state.get("runtimeConfigFingerprint"),
        "desiredFingerprint": os.environ.get(RUNTIME_FINGERPRINT_ENV),
        "staleRuntimeInstanceId": state.get("runtimeInstanceId"),
    }


def _validate_recorded_runtime_shape(state: dict[str, object]) -> dict[str, object]:
    web = state.get("web")
    if not isinstance(web, dict):
        raise LifecycleConflict("runtime web identity is missing")
    runtime_instance_id = str(state.get("runtimeInstanceId") or web.get("runtimeInstanceId") or "")
    if not runtime_instance_id:
        raise LifecycleConflict("runtime instance identity is missing")
    if int(state.get("version") or 0) >= 5:
        required = ("gitCommit", "runtimeConfigFingerprint", "topologyVersion", "repoRoot")
        if any(not state.get(key) for key in required):
            raise LifecycleConflict("runtime generation state is incomplete")
    identities = [("web", web)]
    for name in ("supervisor", "processGroup"):
        identity = state.get(name)
        if identity is not None:
            if not isinstance(identity, dict):
                raise LifecycleConflict(f"recorded {name} identity has an invalid shape")
            identities.append((name, identity))
    for name, identity in identities:
        required = ("pid", "createdAt", "role", "module", "repoRoot", "runtimeInstanceId")
        if any(not identity.get(key) for key in required):
            raise LifecycleConflict(f"recorded {name} identity is incomplete")
        if int(identity.get("pid") or 0) <= 0:
            raise LifecycleConflict(f"recorded {name} PID is invalid")
        if str(identity.get("runtimeInstanceId")) != runtime_instance_id:
            raise LifecycleConflict(f"recorded {name} runtime instance identity mismatch")
        expected_module = "app.serve" if name == "web" else "app.worker_supervisor"
        if str(identity.get("module")) != expected_module:
            raise LifecycleConflict(f"recorded {name} module identity mismatch")
    return web


def _recorded_auxiliary_identities(state: dict[str, object]) -> list[tuple[str, int, str]]:
    """Return PID-only identities recorded by the supervisor flight recorder."""

    path = supervisor_state_path(ROOT)
    if not path.is_file():
        return []
    value = read_runtime_state(path)
    if value is None:
        return []
    runtime_instance_id = str(state.get("runtimeInstanceId") or "")
    if str(value.get("runtime_instance_id") or "") != runtime_instance_id:
        return []
    identities: list[tuple[str, int, str]] = []
    for role, module in (("web", "app.serve"), ("worker", "app.worker")):
        record = value.get(role)
        if not isinstance(record, dict) or record.get("launcher_pid") is None:
            continue
        pid = int(record.get("launcher_pid") or 0)
        if pid <= 0:
            raise LifecycleConflict(f"recorded {role} launcher PID is invalid")
        identities.append((role, pid, module))
    return identities


def _assert_physically_quiescent(state: dict[str, object]) -> None:
    runtime_instance_id = str(state.get("runtimeInstanceId") or "")
    recorded_pids = {
        int(identity["pid"])
        for name in ("web", "supervisor", "processGroup")
        if isinstance((identity := state.get(name)), dict)
    }
    for role, pid, module in _recorded_auxiliary_identities(state):
        if pid in recorded_pids:
            continue
        try:
            actual = inspect_process(pid)
        except psutil.NoSuchProcess:
            continue
        if _process_matches_runtime(actual, module, runtime_instance_id):
            raise LifecycleConflict(f"recorded {role} launcher is still alive")
        raise LifecycleConflict(f"recorded {role} launcher PID was reused or changed identity")

    role_processes = _role_processes()
    if role_processes:
        summary = ", ".join(f"{row.get('role')}:{row.get('pid')}" for row in role_processes)
        raise LifecycleConflict(f"canonical SwingLens runtime processes remain: {summary}")

    listener_report = _listeners_report()
    if listener_report.get("error"):
        raise LifecycleConflict(
            "lifecycle listener evidence could not be inspected: " + str(listener_report["error"])
        )
    lifecycle_ports = {8000, 9101, 9102}
    web = state.get("web")
    if isinstance(web, dict) and web.get("port"):
        lifecycle_ports.add(int(web["port"]))
    listeners = [
        row
        for row in listener_report.get("listeners", [])
        if int(row.get("port") or 0) in lifecycle_ports
    ]
    if listeners:
        summary = ", ".join(
            f"{row.get('port')}:{row.get('pid') or 'unknown'}" for row in listeners
        )
        raise LifecycleConflict(f"lifecycle ports remain occupied: {summary}")


def _runtime_state_report(listener_pid: int | None) -> dict[str, object]:
    state: dict[str, object] | None = None
    try:
        state = read_runtime_state(RUNTIME_STATE)
        if state is None:
            return {
                "classification": RuntimeStateClassification.MISSING.value,
                "valid": False,
                "missing": True,
                "stale": False,
                "runtimeActive": False,
                "conflict": False,
                "staleStateReason": None,
                "error": "runtime state is missing",
            }
        diagnostics = _runtime_diagnostics(state)
        web = _validate_recorded_runtime_shape(state)
        try:
            actual = inspect_process(int(web.get("pid") or 0))
        except psutil.NoSuchProcess:
            for name in ("supervisor", "processGroup"):
                identity = state.get(name)
                if not isinstance(identity, dict):
                    continue
                try:
                    remaining = inspect_process(int(identity.get("pid") or 0))
                except psutil.NoSuchProcess:
                    continue
                validate_runtime_process(identity, remaining)
                raise LifecycleConflict(f"recorded {name} process is still alive") from None
            _assert_physically_quiescent(state)
            return {
                "classification": RuntimeStateClassification.DEAD_STALE.value,
                "valid": False,
                "stale": True,
                "runtimeActive": False,
                "conflict": False,
                "state": state,
                "staleStateReason": (
                    "recorded runtime is absent and physical lifecycle evidence is quiescent"
                ),
                **diagnostics,
            }
        validate_runtime_process(web, actual, listener_pid=listener_pid)
        launcher = actual
        process_group = None
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
            recorded_group = state.get("processGroup")
            if isinstance(recorded_group, dict):
                process_group = inspect_process(int(recorded_group.get("pid") or 0))
                validate_runtime_process(recorded_group, process_group)
                if not _process_matches_runtime(
                    process_group,
                    "app.worker_supervisor",
                    str(state.get("runtimeInstanceId")),
                ):
                    raise LifecycleConflict("recorded process-group command identity mismatch")
                if not _process_descends_from(int(launcher["pid"]), int(process_group["pid"])):
                    raise LifecycleConflict("supervisor is not owned by its recorded process group")
            else:
                # Version 3 states recorded only the inner real interpreter.
                # Derive the outer venv launcher so an in-place upgrade can
                # still stop safely without targeting the wrong group ID.
                process_group = _runtime_group_identity(
                    int(launcher["pid"]),
                    "app.worker_supervisor",
                    str(state.get("runtimeInstanceId")),
                )
        elif web.get("launcherPid") is not None:
            launcher_expected = {
                **web,
                "pid": web.get("launcherPid"),
                "createdAt": web.get("launcherCreatedAt"),
            }
            launcher = inspect_process(int(web.get("launcherPid") or 0))
            validate_runtime_process(launcher_expected, launcher)
            process_group = launcher
        else:
            process_group = actual
        desired_fingerprint = os.environ.get(RUNTIME_FINGERPRINT_ENV)
        if (
            int(state.get("version") or 0) >= 5
            and desired_fingerprint
            and state.get("runtimeConfigFingerprint") != desired_fingerprint
        ):
            return {
                "classification": RuntimeStateClassification.ACTIVE_GENERATION_MISMATCH.value,
                "valid": False,
                "stale": False,
                "runtimeActive": True,
                "conflict": True,
                "state": state,
                "staleStateReason": None,
                "error": "RESTART_REQUIRED: runtime generation fingerprint differs",
                **diagnostics,
            }
        return {
            "classification": RuntimeStateClassification.ACTIVE_VALID.value,
            "valid": True,
            "stale": False,
            "runtimeActive": True,
            "conflict": False,
            "staleStateReason": None,
            "state": state,
            "actual": actual,
            "launcher": launcher,
            "processGroup": process_group,
            **diagnostics,
        }
    except (LifecycleConflict, OSError, psutil.Error, ValueError) as exc:
        report = {
            "classification": RuntimeStateClassification.AMBIGUOUS_CONFLICT.value,
            "valid": False,
            "stale": False,
            "runtimeActive": False,
            "conflict": True,
            "staleStateReason": None,
            "state": state,
            "error": str(exc),
        }
        if state is not None:
            report.update(_runtime_diagnostics(state))
        return report


def _retire_stale_runtime_state() -> dict[str, object]:
    report = _runtime_state_report(listener_pid=None)
    if report.get("classification") == RuntimeStateClassification.MISSING.value:
        return {
            "retired": False,
            "alreadyMissing": True,
            "conflict": False,
            "classification": "MISSING",
        }
    if report.get("classification") in {
        RuntimeStateClassification.ACTIVE_VALID.value,
        RuntimeStateClassification.ACTIVE_GENERATION_MISMATCH.value,
    }:
        return {
            "retired": False,
            "conflict": False,
            "classification": report.get("classification"),
        }
    if report.get("classification") != RuntimeStateClassification.DEAD_STALE.value:
        return {
            "retired": False,
            "conflict": True,
            "classification": report.get("classification"),
            "error": report.get("error") or "runtime state is not safely stale",
        }
    state = report["state"]
    archive_dir = ROOT / "data" / "cache" / "lifecycle-archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    operation_id = os.environ.get("SWINGLENS_LIFECYCLE_OPERATION_ID") or uuid4().hex
    safe_operation = "".join(ch for ch in operation_id if ch.isalnum() or ch in "-_")[:64]
    archive = archive_dir / f"stale-runtime-{safe_operation}.json"
    if archive.exists():
        raise LifecycleConflict("stale runtime archive already exists for this operation")
    os.replace(RUNTIME_STATE, archive)
    append_lifecycle_event(
        ROOT,
        operation_id=operation_id,
        action=os.environ.get("SWINGLENS_LIFECYCLE_ACTION") or "lifecycle",
        stage="runtime_state",
        event="stale_runtime_state_retired",
        result="success",
        reason_code="DEAD_GENERATION_RETIRED",
        runtime_instance_id=state.get("runtimeInstanceId"),
        recorded_git_sha=state.get("gitCommit"),
        recorded_fingerprint=state.get("runtimeConfigFingerprint"),
        desired_git_sha=report.get("desiredGitSha"),
        desired_fingerprint=report.get("desiredFingerprint"),
        topology_version=state.get("topologyVersion"),
        retirement_operation_id=operation_id,
        message=f"archived stale runtime state as {archive.name}",
    )
    return {
        "retired": True,
        "conflict": False,
        "classification": RuntimeStateClassification.DEAD_STALE.value,
        "reasonCode": "DEAD_GENERATION_RETIRED",
        "archive": str(archive),
        **_runtime_diagnostics(state),
    }


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
            now = datetime.now(UTC)
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
                "active": [_blocking_job_dict(row, now, settings) for row in active],
            }
    except Exception as exc:
        return {"reachable": False, "error": redact_text(str(exc))}
    finally:
        engine.dispose()


def _blocking_job_dict(row: BackgroundJob, now: datetime, settings: Settings) -> dict[str, object]:
    lease = row.lease_expires_at
    if lease is not None and lease.tzinfo is None:
        lease = lease.replace(tzinfo=UTC)
    heartbeat = row.heartbeat_at or row.last_progress_at or row.started_at
    if heartbeat is not None and heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    fresh_lease = bool(lease is not None and lease > now)
    fresh_heartbeat = bool(
        heartbeat is not None
        and (now - heartbeat).total_seconds() <= settings.job_worker_heartbeat_timeout_seconds
    )
    if fresh_lease:
        reason = "fresh lease"
    elif fresh_heartbeat:
        reason = "fresh recovery/worker heartbeat"
    else:
        reason = "stale active state; conservative stop requires operator diagnosis"
    return {
        "id": row.id,
        "job_type": row.job_type,
        "status": row.status,
        "worker_id": row.worker_id,
        "worker_instance_id": row.worker_instance_id,
        "lease_owner": row.lease_owner,
        "lease_expires_at": lease.isoformat() if lease else None,
        "heartbeat_at": heartbeat.isoformat() if heartbeat else None,
        "heartbeat_age_seconds": max(0.0, (now - heartbeat).total_seconds()) if heartbeat else None,
        "fresh_lease": fresh_lease,
        "fresh_heartbeat": fresh_heartbeat,
        "blocking_reason": reason,
    }


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
        environment = dict(os.environ)
        environment[DATABASE_SAFETY_CONTEXT_ENV] = DatabaseSafetyContext.AUTHORITATIVE_LOCAL.value
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
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
    return {
        "ok": True,
        "output": redact_text((result.stdout or "").strip())[:20000],
    }


def _observability_report(action: str) -> dict[str, object]:
    settings = _settings()
    timeout = settings.swinglens_observability_timeout_seconds
    info = _docker_command(["info", "--format", "{{.ServerVersion}}"], min(timeout, 15))
    if not info["ok"] or action == "info":
        return info
    compose = ["compose", "-f", str(ROOT / "docker-compose.observability.yml")]
    if action == "status":
        return {
            "ok": True,
            "engine": info,
            "compose": _docker_command([*compose, "ps", "--format", "json"], timeout),
        }
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
        first_group = report.get("processGroup") or report.get("launcher") or report["actual"]
        second_group = second.get("processGroup") or second.get("launcher") or second["actual"]
        if int(first_group["pid"]) != int(second_group["pid"]):
            return {
                "signaled": False,
                "conflict": True,
                "error": "runtime process-group identity changed during validation",
            }
        signal_pid = int(second_group["pid"])
        if os.name == "nt":
            supervisor = second["state"].get("supervisor") or second_group
            request_path = shutdown_request_path(
                ROOT, str(second["state"]["runtimeInstanceId"])
            )
            atomic_write_json(
                request_path,
                {
                    "version": 1,
                    "runtimeInstanceId": second["state"]["runtimeInstanceId"],
                    "supervisorPid": int(supervisor["pid"]),
                    "supervisorCreatedAt": supervisor["createdAt"],
                    "requestedAt": datetime.now(UTC).isoformat(),
                    "operationId": os.environ.get("SWINGLENS_LIFECYCLE_OPERATION_ID"),
                },
            )
            return {
                "signaled": True,
                "signalPid": signal_pid,
                "method": "INSTANCE_SCOPED_SHUTDOWN_REQUEST",
            }
        os.kill(signal_pid, signal.SIGTERM)
        return {"signaled": True, "signalPid": signal_pid, "method": "SIGTERM"}
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


def _runtime_group_identity(
    process_id: int, module: str, runtime_instance_id: str
) -> dict[str, object]:
    """Find the outermost same-runtime launcher that owns a process group."""

    candidates = [inspect_process(process_id)]
    try:
        candidates.extend(
            inspect_process(parent.pid) for parent in psutil.Process(process_id).parents()
        )
    except (OSError, psutil.Error):
        pass
    matching = [
        candidate
        for candidate in candidates
        if _process_matches_runtime(candidate, module, runtime_instance_id)
    ]
    if not matching:
        raise LifecycleConflict("runtime process-group identity could not be derived")
    return matching[-1]


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


def _git_commit() -> str:
    configured = os.environ.get(GIT_SHA_ENV)
    if configured:
        return configured
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"


def _runtime_generation_report() -> dict[str, object]:
    settings = _settings()
    database = _database_report()
    provenance = _provenance_report()
    return runtime_generation(
        settings,
        repo_root=ROOT,
        database=database,
        provenance=provenance,
    )


def _journal_report(payload: str) -> dict[str, object]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("journal payload must be an object")
    path = append_lifecycle_event(ROOT, **value)
    if value.get("event") == "operation_complete":
        update_lifecycle_metrics(ROOT, value)
    return {"written": True, "path": str(path)}


def _listeners_report() -> dict[str, object]:
    ports = {
        8000,
        9101,
        9102,
        9090,
        3000,
        int(make_url(_settings().database_url).port or 5432),
    }
    rows: list[dict[str, object]] = []
    try:
        connections = psutil.net_connections(kind="tcp")
    except (OSError, psutil.Error) as exc:
        return {"error": type(exc).__name__, "listeners": []}
    for connection in connections:
        if connection.status != psutil.CONN_LISTEN or not connection.laddr:
            continue
        port = int(connection.laddr.port)
        if port not in ports:
            continue
        identity: dict[str, object] = {}
        if connection.pid:
            try:
                identity = inspect_process(connection.pid)
            except (OSError, psutil.Error):
                identity = {"pid": connection.pid}
        rows.append(
            {
                "address": str(connection.laddr.ip),
                "port": port,
                **identity,
            }
        )
    return {"listeners": sorted(rows, key=lambda row: (int(row["port"]), int(row.get("pid") or 0)))}


def _jobs_report() -> dict[str, object]:
    settings = _settings()
    engine = create_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )
    try:
        with Session(engine) as db:
            now = datetime.now(UTC)
            rows = db.query(BackgroundJob).filter(
                BackgroundJob.status.in_(("RUNNING", "RECOVERING"))
            ).order_by(BackgroundJob.id).limit(100).all()
            active = []
            for row in rows:
                lease = row.lease_expires_at
                if lease is not None and lease.tzinfo is None:
                    lease = lease.replace(tzinfo=UTC)
                heartbeat = row.heartbeat_at or row.last_progress_at or row.started_at
                if heartbeat is not None and heartbeat.tzinfo is None:
                    heartbeat = heartbeat.replace(tzinfo=UTC)
                lease_fresh = bool(lease is not None and lease > now)
                owner_fresh = bool(
                    heartbeat is not None
                    and (now - heartbeat).total_seconds()
                    <= settings.job_worker_heartbeat_timeout_seconds
                )
                classification = (
                    f"{row.status}_FRESH_OWNERSHIP"
                    if lease_fresh or owner_fresh
                    else f"STALE_{row.status}"
                )
                active.append(
                    {
                        "id": row.id,
                        "job_type": row.job_type,
                        "status": row.status,
                        "worker_id": row.worker_id,
                        "worker_instance_id": row.worker_instance_id,
                        "lease_owner": row.lease_owner,
                        "lease_expires_at": lease.isoformat() if lease else None,
                        "heartbeat_at": heartbeat.isoformat() if heartbeat else None,
                        "heartbeat_age_seconds": (
                            max(0.0, (now - heartbeat).total_seconds()) if heartbeat else None
                        ),
                        "classification": classification,
                        "blocks_stop": lease_fresh or owner_fresh,
                    }
                )
            return {"reachable": True, "active": active, "activeCount": len(active)}
    except Exception as exc:
        return {"reachable": False, "error": redact_text(str(exc)), "active": []}
    finally:
        engine.dispose()


def _http_json(url: str) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - loopback only
            body = response.read().decode("utf-8", errors="replace")
            try:
                payload: object = json.loads(body)
            except json.JSONDecodeError:
                payload = body[:2000]
            return {"reachable": True, "statusCode": response.status, "payload": payload}
    except (OSError, urllib.error.URLError) as exc:
        return {"reachable": False, "error": redact_text(str(exc))}


def _prometheus_targets_report() -> dict[str, object]:
    response = _http_json("http://127.0.0.1:9090/api/v1/targets")
    expected = ("swinglens-web", "swinglens-worker", "swinglens-supervisor")
    rows = []
    payload = response.get("payload")
    if isinstance(payload, dict):
        active = payload.get("data", {}).get("activeTargets", [])
        for job in expected:
            matches = [row for row in active if row.get("labels", {}).get("job") == job]
            if matches:
                target = matches[0]
                rows.append(
                    {
                        "job": job,
                        "discoveredTarget": target.get("discoveredLabels", {}).get("__address__"),
                        "health": target.get("health"),
                        "lastScrape": target.get("lastScrape"),
                        "lastScrapeError": redact_text(str(target.get("lastError") or "")),
                    }
                )
            else:
                rows.append({"job": job, "health": "missing", "lastScrapeError": "not discovered"})
    else:
        rows = [{"job": job, "health": "unavailable"} for job in expected]
    return {
        "reachable": bool(response.get("reachable")),
        "allUp": len(rows) == len(expected) and all(row.get("health") == "up" for row in rows),
        "targets": rows,
        "error": response.get("error"),
    }


def _process_tree_report() -> dict[str, object]:
    roots = _role_processes()
    rows: dict[int, dict[str, object]] = {}
    for root in roots:
        try:
            process = psutil.Process(int(root["pid"]))
            for item in (process, *process.children(recursive=True)):
                rows[item.pid] = inspect_process(item.pid)
        except (OSError, psutil.Error):
            continue
    return {"roots": roots, "processes": [rows[key] for key in sorted(rows)]}


def _read_json_file(path: Path) -> object:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"error": type(exc).__name__}


def _diagnose(operation_id: str) -> dict[str, object]:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_operation = "".join(ch for ch in operation_id if ch.isalnum() or ch in "-_")[:64]
    bundle = ROOT / "artifacts" / "diagnostics" / f"lifecycle-{timestamp}-{safe_operation}"
    bundle.mkdir(parents=True, exist_ok=False)
    reports: dict[str, object] = {
        "configuration.json": _config_report(),
        "database.json": _database_report(),
        "database-provenance.json": _provenance_report(),
        "runtime-generation.json": _runtime_generation_report(),
        "process-tree.json": _process_tree_report(),
        "listeners.json": _listeners_report(),
        "registrations.json": _registrations_report(),
        "active-jobs.json": _jobs_report(),
        "runtime-state.json": _read_json_file(RUNTIME_STATE),
        "supervisor-state.json": _read_json_file(supervisor_state_path(ROOT)),
        "health.json": _http_json("http://127.0.0.1:8000/health"),
        "ready-core.json": _http_json("http://127.0.0.1:8000/ready/core"),
        "ready-application.json": _http_json("http://127.0.0.1:8000/ready"),
        "prometheus-readiness.json": _http_json("http://127.0.0.1:9090/-/ready"),
        "prometheus-targets.json": _prometheus_targets_report(),
        "grafana-health.json": _http_json("http://127.0.0.1:3000/api/health"),
        "docker.json": _observability_report("status"),
    }
    runtime_state = reports["runtime-state.json"]
    runtime_instance_id = (
        runtime_state.get("runtimeInstanceId") if isinstance(runtime_state, dict) else None
    )
    reports["shutdown-request.json"] = (
        _read_json_file(shutdown_request_path(ROOT, str(runtime_instance_id)))
        if runtime_instance_id
        else None
    )
    reports["status.json"] = {
        "lifecycleControllerVersion": LIFECYCLE_CONTROLLER_VERSION,
        "operationId": operation_id,
        "database": reports["database.json"],
        "runtime": reports["runtime-state.json"],
        "supervisor": reports["supervisor-state.json"],
        "processTree": reports["process-tree.json"],
        "activeJobs": reports["active-jobs.json"],
        "core": reports["ready-core.json"],
        "application": reports["ready-application.json"],
        "observability": {
            "prometheus": reports["prometheus-targets.json"],
            "grafana": reports["grafana-health.json"],
        },
    }
    git = subprocess.run(
        ["git", "status", "--short", "--branch"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    reports["git.json"] = {
        "head": _git_commit(),
        "status": redact_text(git.stdout),
        "exitCode": git.returncode,
    }
    for name, payload in reports.items():
        atomic_write_json(bundle / name, redact_sensitive(payload))
    log_paths = {
        "lifecycle.log": ROOT / "logs" / "lifecycle" / "lifecycle.jsonl",
        "supervisor-stdout.log": ROOT / "logs" / "lifecycle-supervisor.out.log",
        "supervisor-stderr.log": ROOT / "logs" / "lifecycle-supervisor.err.log",
        "web.log": ROOT / "logs" / "lifecycle-web.log",
        "worker.log": ROOT / "logs" / "lifecycle-worker.log",
    }
    for name, path in log_paths.items():
        (bundle / name).write_text(redacted_tail(path), encoding="utf-8")
    core = reports["ready-core.json"]
    likely = "CORE_RUNTIME_STOPPED_OR_UNREACHABLE"
    if isinstance(core, dict) and core.get("reachable"):
        payload = core.get("payload")
        likely = (
            "CORE_READY"
            if isinstance(payload, dict) and payload.get("status") == "ok"
            else "CORE_READINESS_FAILED"
        )
    summary = (
        "# SwingLens lifecycle diagnostic summary\n\n"
        f"- Operation ID: `{operation_id}`\n"
        f"- Captured: `{datetime.now(UTC).isoformat()}`\n"
        f"- Most likely boundary: `{likely}`\n"
        "- This bundle was collected read-only with respect to business state; it did not migrate, "
        "start, stop, restart, enqueue, or run a pipeline.\n"
    )
    (bundle / "SUMMARY.md").write_text(summary, encoding="utf-8")
    checksums = {}
    for path in sorted(bundle.iterdir()):
        if path.name == "manifest.json":
            continue
        checksums[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    atomic_write_json(
        bundle / "manifest.json",
        {
            "version": 1,
            "operation_id": operation_id,
            "created_at": datetime.now(UTC).isoformat(),
            "files": checksums,
        },
    )
    return {"created": True, "bundle": str(bundle), "reasonCode": likely}


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
        "fingerprint",
        "jobs",
        "listeners",
        "prometheus-targets",
        "observability-info",
        "observability-start",
        "observability-stop",
    ):
        subparsers.add_parser(name)
    journal = subparsers.add_parser("journal")
    journal.add_argument("--json", required=True)
    diagnose = subparsers.add_parser("diagnose")
    diagnose.add_argument("--operation-id", required=True)
    launch = subparsers.add_parser("launch-runtime")
    launch.add_argument("--stdout", type=Path, required=True)
    launch.add_argument("--stderr", type=Path, required=True)
    write_state = subparsers.add_parser("write-state")
    write_state.add_argument("--json", required=True)
    state = subparsers.add_parser("runtime-state")
    state.add_argument("--listener-pid", type=int)
    subparsers.add_parser("retire-stale-state")
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
        "fingerprint": _runtime_generation_report,
        "jobs": _jobs_report,
        "listeners": _listeners_report,
        "prometheus-targets": _prometheus_targets_report,
        "observability-info": lambda: _observability_report("info"),
        "observability-start": lambda: _observability_report("start"),
        "observability-stop": lambda: _observability_report("stop"),
    }
    if args.command in commands:
        report = commands[args.command]()
    elif args.command == "launch-runtime":
        report = _launch_runtime(args.stdout, args.stderr)
    elif args.command == "write-state":
        report = _write_state(args.json)
    elif args.command == "journal":
        report = _journal_report(args.json)
    elif args.command == "diagnose":
        report = _diagnose(args.operation_id)
    elif args.command == "runtime-state":
        report = _runtime_state_report(args.listener_pid)
    elif args.command == "retire-stale-state":
        report = _retire_stale_runtime_state()
    elif args.command == "signal-break":
        report = _signal_break(args.pid, args.listener_pid)
    else:
        report = _signal_registered(args.role)
    print(json.dumps(report, default=str, separators=(",", ":")))


if __name__ == "__main__":
    main()
