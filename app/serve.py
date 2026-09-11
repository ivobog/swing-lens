from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import re
import signal
import socket
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

import uvicorn

from app.observability.logging import configure_json_logging, log_event
from app.services.parent_watchdog import install_parent_watchdog
from app.services.process_roles import require_process_role
from app.services.redaction import redact_text
from app.services.startup_preflight import StartupPreflightError, run_startup_preflight
from app.settings import ProcessRole, get_settings

RUNTIME_RELOAD_EXCLUDES = (
    "logs/**",
    "output/**",
    "data/**",
    "backups/**",
    ".qa_work/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    "**/__pycache__/**",
    "*.log",
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ListenerOwner:
    process_id: int
    process_name: str | None = None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Start the isolated SwingLens web/API process.")
    parser.add_argument("--host", default=settings.app_host)
    parser.add_argument("--port", type=int, default=settings.app_port)
    parser.add_argument("--reload", action="store_true")
    # Lifecycle identity markers are intentionally present in the OS command
    # line so another checkout cannot be mistaken for this runtime.
    parser.add_argument("--runtime-instance-id")
    parser.add_argument("--repo-root")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    configure_json_logging("web")
    log_event(logger, "runtime.process_boot", stage="process_boot", reason_code="NONE")
    try:
        args = parse_args(argv)
        settings = get_settings()
        require_process_role(settings, ProcessRole.WEB)
        log_event(logger, "runtime.role_validation", stage="role_validation", result="success")
    except Exception as exc:
        log_event(
            logger,
            "runtime.role_validation_failed",
            level=logging.ERROR,
            stage="role_validation",
            result="failure",
            reason_code="CONFIGURATION_CONFLICT",
            error=redact_text(str(exc)),
        )
        raise
    watchdog = install_parent_watchdog(lambda: os.kill(os.getpid(), signal.SIGTERM))
    log_event(
        logger,
        "runtime.parent_watchdog_install",
        stage="parent_watchdog_install",
        result="success",
        supervised=watchdog is not None,
    )
    log_event(logger, "runtime.listener_probe", stage="listener_probe", port=args.port)
    conflict = diagnose_listener(args.host, args.port)
    if conflict is not None:
        name = f" ({conflict.process_name})" if conflict.process_name else ""
        log_event(
            logger,
            "runtime.listener_conflict",
            level=logging.ERROR,
            stage="listener_probe",
            result="failure",
            reason_code="FOREIGN_LISTENER",
            listener_pid=conflict.process_id,
            port=args.port,
        )
        raise SystemExit(
            f"SwingLens cannot bind {args.host}:{args.port}: an existing listener is owned "
            f"by PID {conflict.process_id}{name}. Verify whether that process is a stale "
            "SwingLens instance before stopping it."
        )
    log_event(logger, "runtime.startup_preflight_begin", stage="startup_preflight_begin")
    try:
        run_startup_preflight()
    except StartupPreflightError as exc:
        failure_stage, reason_code = _preflight_failure_identity(str(exc))
        log_event(
            logger,
            "runtime.startup_preflight_failed",
            level=logging.ERROR,
            stage=failure_stage,
            result="failure",
            reason_code=reason_code,
            error=redact_text(str(exc)),
        )
        raise SystemExit(f"SwingLens startup preflight failed: {redact_text(str(exc))}") from exc
    log_event(logger, "runtime.database_probe", stage="database_probe", result="success")
    log_event(
        logger,
        "runtime.database_provenance",
        stage="database_provenance",
        result="pending",
        reason_code="CORE_READINESS_VALIDATION",
    )
    log_event(
        logger,
        "runtime.alembic_head_check",
        stage="alembic_head_check",
        result="success",
    )
    log_event(logger, "runtime.storage_check", stage="storage_check", result="success")
    log_event(
        logger,
        "runtime.startup_preflight_complete",
        stage="startup_preflight",
        result="success",
    )
    log_event(logger, "runtime.uvicorn_bind_begin", stage="uvicorn_bind_begin", port=args.port)
    try:
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_config=None,
            reload_excludes=list(RUNTIME_RELOAD_EXCLUDES) if args.reload else None,
        )
    finally:
        log_event(logger, "runtime.process_shutdown", stage="process_shutdown")


def _preflight_failure_identity(message: str) -> tuple[str, str]:
    normalized = message.lower()
    if "database" in normalized and "migration" not in normalized:
        return "database_probe", "DATABASE_UNAVAILABLE"
    if "migration" in normalized or "alembic" in normalized:
        return "alembic_head_check", "ALEMBIC_MISMATCH"
    if "storage" in normalized or "directory" in normalized:
        return "storage_check", "STORAGE_UNAVAILABLE"
    return "startup_preflight", "STARTUP_PREFLIGHT_FAILED"


def diagnose_listener(host: str, port: int) -> ListenerOwner | None:
    if not _connects(host, port):
        return None
    return _windows_listener(port) if os.name == "nt" else _unix_listener(port)


def explain_bind_error(host: str, port: int, error: OSError) -> str:
    owner = diagnose_listener(host, port)
    if owner is not None:
        return (
            f"Bind failed for {host}:{port}; PID {owner.process_id}"
            f" ({owner.process_name or 'unknown process'}) already listens there."
        )
    if getattr(error, "winerror", None) == 10013:
        return (
            f"Windows denied access to {host}:{port} (WSAEACCES/10013), but no listener "
            "was found. Check excluded port ranges or security policy; this is not being "
            "classified as ordinary address-in-use."
        )
    return f"Bind failed for {host}:{port}: {error}"


def _connects(host: str, port: int) -> bool:
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    try:
        with socket.create_connection((connect_host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _windows_listener(port: int) -> ListenerOwner | None:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        check=False,
        capture_output=True,
        text=True,
    )
    pattern = re.compile(rf"^\s*TCP\s+\S+:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$", re.I)
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        pid = int(match.group(1))
        return ListenerOwner(pid, _windows_process_name(pid))
    return None


def _windows_process_name(process_id: int) -> str | None:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {process_id}", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
    )
    rows = list(csv.reader(io.StringIO(result.stdout)))
    return rows[0][0] if rows and len(rows[0]) >= 2 else None


def _unix_listener(port: int) -> ListenerOwner | None:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-FpPc"],
        check=False,
        capture_output=True,
        text=True,
    )
    pid = None
    name = None
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("c"):
            name = line[1:]
    return ListenerOwner(pid, name) if pid is not None else None


if __name__ == "__main__":
    main()
