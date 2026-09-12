from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from app.services.alembic_heads import repository_alembic_heads
from app.services.lifecycle_safety import atomic_write_json, normalize_path
from app.services.redaction import redact_sensitive, redact_text

TOPOLOGY_VERSION = "supervisor-root-v1"
LIFECYCLE_OPERATION_ID_ENV = "SWINGLENS_LIFECYCLE_OPERATION_ID"
RUNTIME_INSTANCE_ID_ENV = "SWINGLENS_RUNTIME_INSTANCE_ID"
RUNTIME_FINGERPRINT_ENV = "SWINGLENS_RUNTIME_CONFIG_FINGERPRINT"
GIT_SHA_ENV = "SWINGLENS_GIT_SHA"
LIFECYCLE_OVERRIDE_KEYS_ENV = "SWINGLENS_LIFECYCLE_OVERRIDE_KEYS"
SUPERVISOR_STATE_ENV = "SWINGLENS_SUPERVISOR_STATE_PATH"

CRITICAL_SETTING_FIELDS = {
    "DATABASE_URL": "database_url",
    "USE_DURABLE_PIPELINE": "use_durable_pipeline",
    "DURABLE_WORKER_PROCESS_ENABLED": "durable_worker_process_enabled",
    "EMBEDDED_JOB_WORKER_ENABLED": "embedded_job_worker_enabled",
    "JOB_WORKER_ENABLED": "job_worker_enabled",
    "RUNTIME_MODE": "runtime_mode",
    "APP_HOST": "app_host",
    "APP_PORT": "app_port",
    "OBSERVABILITY_METRICS_HOST": "observability_metrics_host",
    "OBSERVABILITY_WORKER_METRICS_PORT": "observability_worker_metrics_port",
    "OBSERVABILITY_SUPERVISOR_METRICS_PORT": "observability_supervisor_metrics_port",
    "SWINGLENS_POSTGRES_SERVICE": "swinglens_postgres_service",
    "SWINGLENS_POSTGRES_EXPECTED_MAJOR": "swinglens_postgres_expected_major",
    "SWINGLENS_POSTGRES_DATA_DIR": "swinglens_postgres_data_dir",
    "SWINGLENS_POSTGRES_EXECUTABLE": "swinglens_postgres_executable",
    "SWINGLENS_MANAGE_POSTGRES": "swinglens_manage_postgres",
    "GRAFANA_ADMIN_PASSWORD": "grafana_admin_password",
}

JOURNAL_FIELDS = frozenset(
    {
        "timestamp",
        "operation_id",
        "action",
        "stage",
        "event",
        "component",
        "result",
        "duration_ms",
        "reason_code",
        "git_sha",
        "runtime_instance_id",
        "config_fingerprint",
        "runtime_mode",
        "process_role",
        "pid",
        "parent_pid",
        "port",
        "service",
        "alembic_head",
        "message",
        "signal_name",
        "signal_number",
        "shutdown_method",
        "recorded_git_sha",
        "recorded_fingerprint",
        "desired_git_sha",
        "desired_fingerprint",
        "topology_version",
        "retirement_operation_id",
    }
)


def sanitized_database_endpoint(value: str) -> str:
    try:
        url = make_url(value)
        host = url.host or ""
        port = url.port or 5432
        return f"{url.drivername}://{host}:{port}/{url.database or ''}"
    except Exception:
        return "<invalid>"


def configuration_provenance(
    settings: Any,
    *,
    environment: Mapping[str, str] | None = None,
    env_file: Path | None = None,
) -> dict[str, dict[str, Any]]:
    environment = environment or os.environ
    env_file = env_file or Path(".env")
    dotenv_keys = _dotenv_keys(env_file)
    overrides = {
        item.strip()
        for item in environment.get(LIFECYCLE_OVERRIDE_KEYS_ENV, "").split(",")
        if item.strip()
    }
    report: dict[str, dict[str, Any]] = {}
    for env_name, field_name in CRITICAL_SETTING_FIELDS.items():
        value = getattr(settings, field_name, None)
        if env_name == "DATABASE_URL":
            rendered: Any = sanitized_database_endpoint(str(value))
        elif env_name == "GRAFANA_ADMIN_PASSWORD":
            secret = value.get_secret_value() if value is not None else ""
            rendered = "PRESENT" if secret else "MISSING"
        elif hasattr(value, "value"):
            rendered = value.value
        elif isinstance(value, Path):
            rendered = str(value)
        else:
            rendered = value
        if env_name in overrides:
            source = (
                "certification override"
                if str(getattr(settings, "runtime_mode", "")) == "CERTIFICATION"
                else "canonical lifecycle override"
            )
        elif env_name in environment:
            source = "process environment"
        elif env_name in dotenv_keys:
            source = ".env"
        else:
            source = "application default"
        report[env_name] = {"value": rendered, "source": source}
    return report


def runtime_generation(
    settings: Any,
    *,
    repo_root: Path,
    database: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    git_sha = _git_sha(repo_root)
    heads = list(repository_alembic_heads(repo_root))
    critical = configuration_provenance(settings, env_file=repo_root / ".env")
    critical_values = {key: row["value"] for key, row in critical.items()}
    database_identity = {
        "endpoint": sanitized_database_endpoint(str(settings.database_url)),
        "server_major": int(database.get("serverVersionNum") or 0) // 10000,
        "service": provenance.get("service"),
        "data_directory": normalize_path(provenance.get("dataDirectory")),
        "listener_executable": normalize_path(provenance.get("listenerExecutable")),
        "verified": bool(provenance.get("verified")),
    }
    payload = {
        "git_sha": git_sha,
        "repo_root": normalize_path(repo_root),
        "python_executable": normalize_path(os.path.realpath(os.sys.executable)),
        "runtime_mode": settings.runtime_mode.value,
        "topology_version": TOPOLOGY_VERSION,
        "database": database_identity,
        "alembic_heads": heads,
        "critical_config_fingerprint": _digest(critical_values),
        "ports": {
            "web": settings.app_port,
            "worker_metrics": settings.observability_worker_metrics_port,
            "supervisor_metrics": settings.observability_supervisor_metrics_port,
        },
        "worker_mode": {
            "durable": settings.durable_worker_process_enabled,
            "embedded": settings.embedded_job_worker_enabled,
        },
    }
    return {"fingerprint": _digest(payload), "generation": payload}


def append_lifecycle_event(root: Path, **fields: Any) -> Path:
    path = root / "logs" / "lifecycle" / "lifecycle.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: value for key, value in fields.items() if key in JOURNAL_FIELDS}
    payload.setdefault("timestamp", datetime.now(UTC).isoformat())
    payload.setdefault("operation_id", os.environ.get(LIFECYCLE_OPERATION_ID_ENV))
    payload.setdefault("git_sha", os.environ.get(GIT_SHA_ENV))
    payload.setdefault("runtime_instance_id", os.environ.get(RUNTIME_INSTANCE_ID_ENV))
    payload.setdefault("config_fingerprint", os.environ.get(RUNTIME_FINGERPRINT_ENV))
    payload.setdefault("pid", os.getpid())
    payload.setdefault("parent_pid", os.getppid())
    line = json.dumps(redact_sensitive(payload), separators=(",", ":"), default=str) + "\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line.encode("utf-8", errors="replace"))
    finally:
        os.close(descriptor)
    return path


def update_lifecycle_metrics(root: Path, event: Mapping[str, Any]) -> Path:
    path = root / "data" / "cache" / "lifecycle-metrics.json"
    state: dict[str, Any] = {}
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                state = value
        except (OSError, UnicodeError, json.JSONDecodeError):
            state = {}
    failures = state.setdefault("failures", {})
    result = str(event.get("result") or "").lower()
    state.update(
        {
            "last_operation_timestamp": datetime.now(UTC).timestamp(),
            "last_operation_success": 1 if result == "success" else 0,
            "last_operation_duration_seconds": float(event.get("duration_ms") or 0) / 1000.0,
            "last_action": str(event.get("action") or "unknown"),
            "last_stage": str(event.get("stage") or "unknown"),
            "last_reason": str(event.get("reason_code") or "NONE"),
        }
    )
    if result == "failure":
        key = "|".join(
            (
                state["last_action"],
                state["last_stage"],
                state["last_reason"],
            )
        )
        failures[key] = int(failures.get(key, 0)) + 1
    atomic_write_json(path, state)
    return path


def reason_code(error: BaseException | str) -> str:
    message = str(error).upper()
    known = (
        "RESTART_REQUIRED",
        "CRASH_LOOP",
        "DATABASE_PROVENANCE_MISMATCH",
        "DATABASE_UNAVAILABLE",
        "ALEMBIC_MISMATCH",
        "FOREIGN_LISTENER",
        "ACTIVE_LEASE_BLOCKS_STOP",
        "CORE_READINESS_TIMEOUT",
        "CONFIGURATION_CONFLICT",
        "LIFECYCLE_LOCK_TIMEOUT",
    )
    for code in known:
        if code in message:
            return code
    if "POSTGRESQL" in message and ("MATCH" in message or "PROVENANCE" in message):
        return "DATABASE_PROVENANCE_MISMATCH"
    if "ALEMBIC" in message or "MIGRATION" in message:
        return "ALEMBIC_MISMATCH"
    if "PORT" in message and ("OCCUP" in message or "LISTENER" in message):
        return "FOREIGN_LISTENER"
    return "LIFECYCLE_OPERATION_FAILED"


def supervisor_state_path(root: Path) -> Path:
    configured = os.environ.get(SUPERVISOR_STATE_ENV)
    return Path(configured) if configured else root / "data" / "cache" / "swinglens-supervisor.json"


def shutdown_request_path(root: Path, runtime_instance_id: str) -> Path:
    """Return an instance-scoped controller-to-supervisor shutdown request path."""

    digest = hashlib.sha256(runtime_instance_id.encode("utf-8")).hexdigest()
    return root / "data" / "cache" / "shutdown-requests" / f"{digest}.json"


def _dotenv_keys(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    keys = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                keys.add(stripped.split("=", 1)[0].strip())
    except (OSError, UnicodeError):
        return set()
    return keys


def _git_sha(root: Path) -> str:
    configured = os.environ.get(GIT_SHA_ENV)
    if configured:
        return configured
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return "UNKNOWN"
    return result.stdout.strip()


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def redacted_tail(path: Path, *, lines: int = 200) -> str:
    if not path.is_file():
        return "<unavailable>\n"
    try:
        values = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        return "\n".join(redact_text(line) for line in values) + "\n"
    except OSError as exc:
        return f"<unavailable:{type(exc).__name__}>\n"
