from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.alembic_heads import schema_is_at_head
from app.services.lifecycle_control import (
    GIT_SHA_ENV,
    RUNTIME_INSTANCE_ID_ENV,
    TOPOLOGY_VERSION,
    supervisor_state_path,
)
from app.services.lifecycle_safety import (
    LifecycleConflict,
    inspect_process,
    normalize_path,
    read_runtime_state,
    validate_runtime_process,
)
from app.services.process_roles import certification_profile_environment
from app.settings import Settings

RUNTIME_STATE_FILENAME = "swinglens-lifecycle.json"


class CertificationSessionDiscoveryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class CertificationSessionBinding:
    runtime_instance_id: str
    git_sha: str
    runtime_config_fingerprint: str
    worker_id: str
    worker_instance_id: str
    worker_generation: int
    runtime_state_path: str


def discover_active_certification_session(
    db: Session,
    *,
    repo_root: Path,
    settings: Settings,
    environment: dict[str, str] | None = None,
    readiness_loader: Callable[[str], dict[str, Any]] | None = None,
    process_inspector: Callable[[int], dict[str, Any]] = inspect_process,
    now: datetime | None = None,
) -> CertificationSessionBinding:
    """Resolve and verify the one active canonical certification runtime."""

    env = environment if environment is not None else os.environ
    root = repo_root.resolve()
    state_path = root / "data" / "cache" / RUNTIME_STATE_FILENAME
    state = _read_required_state(state_path, code="CERTIFICATION_RUNTIME_NOT_ACTIVE")
    runtime_id = str(state.get("runtimeInstanceId") or "").strip()
    runtime_fingerprint = str(state.get("runtimeConfigFingerprint") or "").strip()
    if not runtime_id:
        _fail("CERTIFICATION_RUNTIME_IDENTITY_INVALID", "runtime instance ID is missing")
    if not runtime_fingerprint:
        _fail("CERTIFICATION_RUNTIME_IDENTITY_INVALID", "runtime fingerprint is missing")
    if int(state.get("version") or 0) < 5:
        _fail("CERTIFICATION_RUNTIME_IDENTITY_INVALID", "runtime state version is unsupported")
    if state.get("topologyVersion") != TOPOLOGY_VERSION:
        _fail("CERTIFICATION_RUNTIME_TOPOLOGY_INVALID", "runtime topology is not canonical")
    if normalize_path(state.get("repoRoot")) != normalize_path(root):
        _fail("CERTIFICATION_RUNTIME_REPOSITORY_MISMATCH", "runtime belongs to another checkout")

    repository_sha = _repository_git_sha(root)
    recorded_sha = str(state.get("gitCommit") or "").strip()
    desired_sha = str(env.get(GIT_SHA_ENV) or repository_sha).strip()
    if not recorded_sha or recorded_sha != repository_sha or recorded_sha != desired_sha:
        _fail(
            "CERTIFICATION_RUNTIME_SHA_MISMATCH",
            "recorded="
            f"{recorded_sha or 'missing'} repository={repository_sha} desired={desired_sha}",
        )

    for name in ("web", "supervisor", "processGroup"):
        identity = state.get(name)
        if not isinstance(identity, dict):
            _fail("CERTIFICATION_RUNTIME_NOT_ACTIVE", f"runtime {name} identity is missing")
        if str(identity.get("runtimeInstanceId") or "") != runtime_id:
            _fail(
                "CERTIFICATION_RUNTIME_IDENTITY_INVALID",
                f"runtime {name} belongs to another session",
            )
        try:
            validate_runtime_process(identity, process_inspector(int(identity.get("pid") or 0)))
        except (LifecycleConflict, OSError, ValueError, psutil.Error) as exc:
            _fail("CERTIFICATION_RUNTIME_NOT_ACTIVE", f"runtime {name} is not valid: {exc}")

    web = state["web"]
    if int(web.get("port") or 0) != int(settings.app_port):
        _fail("CERTIFICATION_RUNTIME_PORT_MISMATCH", "runtime web port differs from settings")
    readiness = (readiness_loader or _load_readiness)(
        f"http://127.0.0.1:{int(web['port'])}/ready"
    )
    _validate_readiness(readiness)
    if not schema_is_at_head(db.connection(), root):
        _fail("CERTIFICATION_RUNTIME_SCHEMA_MISMATCH", "database schema is not at repository head")

    recorder_path = supervisor_state_path(root)
    recorder = _read_required_state(
        recorder_path,
        code="CERTIFICATION_RUNTIME_WORKER_MISMATCH",
    )
    if str(recorder.get("runtime_instance_id") or "") != runtime_id:
        _fail(
            "CERTIFICATION_RUNTIME_WORKER_MISMATCH",
            "supervisor flight recorder belongs to another runtime session",
        )
    if str(recorder.get("runtime_config_fingerprint") or "") != runtime_fingerprint:
        _fail(
            "CERTIFICATION_RUNTIME_WORKER_MISMATCH",
            "supervisor flight recorder generation fingerprint differs",
        )
    worker = recorder.get("worker")
    supervisor = recorder.get("supervisor")
    if not isinstance(worker, dict) or worker.get("state") != "RUNNING":
        _fail("CERTIFICATION_RUNTIME_WORKER_MISMATCH", "canonical worker is not running")
    if not isinstance(supervisor, dict):
        _fail("CERTIFICATION_RUNTIME_WORKER_MISMATCH", "canonical supervisor is missing")

    worker_id = str(worker.get("worker_id") or "").strip()
    worker_registration = db.execute(
        text(
            "select worker_id,instance_id,process_id,process_started_at,generation,heartbeat_at,"
            "stopping_at from background_workers where worker_id=:worker_id"
        ),
        {"worker_id": worker_id},
    ).mappings().one_or_none()
    supervisor_registration = db.execute(
        text(
            "select worker_id,instance_id,process_id,process_started_at,generation,heartbeat_at,"
            "stopping_at from background_supervisors where worker_id=:worker_id"
        ),
        {"worker_id": worker_id},
    ).mappings().one_or_none()
    _validate_registration(
        "worker",
        worker,
        worker_registration,
        settings=settings,
        repo_root=root,
        process_inspector=process_inspector,
        now=now,
    )
    _validate_registration(
        "supervisor",
        supervisor,
        supervisor_registration,
        settings=settings,
        repo_root=root,
        process_inspector=process_inspector,
        now=now,
    )

    manual_runtime_id = str(env.get(RUNTIME_INSTANCE_ID_ENV) or "").strip()
    if manual_runtime_id and manual_runtime_id != runtime_id:
        _fail(
            "CERTIFICATION_RUNTIME_OVERRIDE_MISMATCH",
            "explicit runtime instance ID does not match the canonical active runtime",
        )
    return CertificationSessionBinding(
        runtime_instance_id=runtime_id,
        git_sha=recorded_sha,
        runtime_config_fingerprint=runtime_fingerprint,
        worker_id=worker_id,
        worker_instance_id=str(worker_registration["instance_id"]),
        worker_generation=int(worker_registration["generation"]),
        runtime_state_path=str(state_path),
    )


def bind_certification_session_environment(
    binding: CertificationSessionBinding,
    *,
    environment: dict[str, str] | None = None,
) -> None:
    env = environment if environment is not None else os.environ
    env.update(certification_profile_environment(env))
    env[RUNTIME_INSTANCE_ID_ENV] = binding.runtime_instance_id
    env[GIT_SHA_ENV] = binding.git_sha


def _read_required_state(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = read_runtime_state(path)
    except LifecycleConflict as exc:
        _fail(code, str(exc))
    if value is None:
        _fail(code, f"required canonical state is missing: {path.name}")
    return value


def _repository_git_sha(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _load_readiness(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - local URL
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        _fail("CERTIFICATION_RUNTIME_NOT_READY", f"readiness endpoint failed: {exc}")
    if not isinstance(value, dict):
        _fail("CERTIFICATION_RUNTIME_NOT_READY", "readiness response has an invalid shape")
    return value


def _validate_readiness(readiness: dict[str, Any]) -> None:
    checks = readiness.get("checks") or {}
    states = readiness.get("check_states") or {}
    if readiness.get("status") != "ok":
        _fail("CERTIFICATION_RUNTIME_NOT_READY", "application readiness is not ok")
    required_states = ("database", "migrations", "runtime", "topology", "worker", "jobs")
    failed = [name for name in required_states if states.get(name) != "ok"]
    if failed:
        _fail(
            "CERTIFICATION_RUNTIME_NOT_READY",
            "required readiness checks failed: " + ",".join(failed),
        )
    if checks.get("runtime") != "CERTIFICATION_ISOLATION_ACTIVE":
        _fail(
            "CERTIFICATION_RUNTIME_MODE_MISMATCH",
            "active runtime is not in certification isolation mode",
        )
    if checks.get("topology") != "READY_EXACTLY_ONE_WORKER":
        _fail(
            "CERTIFICATION_RUNTIME_WORKER_MISMATCH",
            "runtime does not have exactly one ready worker",
        )


def _validate_registration(
    role: str,
    recorded: dict[str, Any],
    registration: Any,
    *,
    settings: Settings,
    repo_root: Path,
    process_inspector: Callable[[int], dict[str, Any]],
    now: datetime | None,
) -> None:
    if registration is None:
        _fail("CERTIFICATION_RUNTIME_WORKER_MISMATCH", f"{role} registration is missing")
    expected_instance = str(recorded.get("registered_instance_id") or "")
    expected_generation = int(recorded.get("registered_generation") or 0)
    expected_pid = int(recorded.get("registered_pid") or recorded.get("pid") or 0)
    if (
        str(registration["instance_id"] or "") != expected_instance
        or int(registration["generation"] or 0) != expected_generation
        or int(registration["process_id"] or 0) != expected_pid
        or registration["stopping_at"] is not None
    ):
        _fail(
            "CERTIFICATION_RUNTIME_WORKER_MISMATCH",
            f"{role} registration differs from the canonical generation",
        )
    observed_at = now or datetime.now(UTC)
    heartbeat = registration["heartbeat_at"]
    if heartbeat is None or (observed_at - heartbeat).total_seconds() > float(
        settings.job_worker_heartbeat_timeout_seconds
    ):
        _fail("CERTIFICATION_RUNTIME_WORKER_MISMATCH", f"{role} heartbeat is stale")
    try:
        actual = process_inspector(expected_pid)
    except (OSError, ValueError, psutil.Error) as exc:
        _fail("CERTIFICATION_RUNTIME_WORKER_MISMATCH", f"{role} process is absent: {exc}")
    registered_start = registration["process_started_at"]
    try:
        actual_start = datetime.fromisoformat(
            str(actual.get("createdAt") or "").replace("Z", "+00:00")
        )
    except ValueError:
        _fail(
            "CERTIFICATION_RUNTIME_WORKER_MISMATCH",
            f"{role} process start identity is invalid",
        )
    if registered_start is None:
        _fail(
            "CERTIFICATION_RUNTIME_WORKER_MISMATCH",
            f"{role} registration has no process start identity",
        )
    if registered_start.tzinfo is None:
        registered_start = registered_start.replace(tzinfo=UTC)
    if actual_start.tzinfo is None:
        actual_start = actual_start.replace(tzinfo=UTC)
    if abs((actual_start - registered_start).total_seconds()) > 0.01:
        _fail(
            "CERTIFICATION_RUNTIME_WORKER_MISMATCH",
            f"{role} process start identity differs from its registration",
        )
    command = " ".join(str(part) for part in actual.get("commandLine") or ())
    expected_module = "app.worker" if role == "worker" else "app.worker_supervisor"
    process_matches = f"-m {expected_module}" in command and normalize_path(
        actual.get("cwd")
    ) == normalize_path(repo_root)
    if not process_matches:
        _fail(
            "CERTIFICATION_RUNTIME_WORKER_MISMATCH",
            f"{role} process identity does not match the canonical runtime",
        )


def _fail(code: str, message: str) -> None:
    raise CertificationSessionDiscoveryError(code, message)
