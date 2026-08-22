from __future__ import annotations

import argparse
import csv
import io
import os
import re
import socket
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

import uvicorn

from app.settings import get_settings

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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    conflict = diagnose_listener(args.host, args.port)
    if conflict is not None:
        name = f" ({conflict.process_name})" if conflict.process_name else ""
        raise SystemExit(
            f"SwingLens cannot bind {args.host}:{args.port}: an existing listener is owned "
            f"by PID {conflict.process_id}{name}. Verify whether that process is a stale "
            "SwingLens instance before stopping it."
        )
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_excludes=list(RUNTIME_RELOAD_EXCLUDES) if args.reload else None,
    )


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
