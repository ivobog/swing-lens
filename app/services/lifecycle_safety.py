from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
from sqlalchemy import text
from sqlalchemy.engine import Connection


class LifecycleConflict(RuntimeError):
    """Identity evidence is missing, inconsistent, or ambiguous."""


def normalize_path(value: str | Path | None) -> str:
    if value is None:
        return ""
    return os.path.normcase(os.path.abspath(os.fspath(value))).rstrip("\\/")


@dataclass(frozen=True)
class PostgresExpectation:
    service: str
    major: int
    data_directory: str
    service_executable: str
    database: str = "swinglens"


def verify_postgres_provenance(
    database: dict[str, Any],
    services: list[dict[str, Any]],
    listeners: list[dict[str, Any]],
    processes: dict[int, dict[str, Any]],
    expected: PostgresExpectation,
) -> dict[str, Any]:
    """Fail closed unless independent SQL, service, listener and process evidence agrees."""
    if database.get("host") not in {"127.0.0.1", "localhost", "::1", ""}:
        raise LifecycleConflict("authoritative PostgreSQL host is not local")
    if not database.get("reachable"):
        raise LifecycleConflict("authoritative PostgreSQL is not reachable")
    if (
        database.get("database") != expected.database
        or database.get("currentDatabase") != expected.database
    ):
        raise LifecycleConflict(
            "connected PostgreSQL database identity does not match configuration"
        )
    if int(database.get("serverVersionNum") or 0) // 10000 != expected.major:
        raise LifecycleConflict("PostgreSQL server major version does not match configuration")
    if normalize_path(database.get("dataDirectory")) != normalize_path(expected.data_directory):
        raise LifecycleConflict("PostgreSQL data directory does not match configuration")

    named = [service for service in services if service.get("name") == expected.service]
    if len(named) != 1:
        raise LifecycleConflict("authoritative PostgreSQL Windows service is missing or ambiguous")
    service = named[0]
    if normalize_path(service.get("executable")) != normalize_path(expected.service_executable):
        raise LifecycleConflict("PostgreSQL service executable does not match configuration")
    if normalize_path(service.get("dataDirectory")) != normalize_path(expected.data_directory):
        raise LifecycleConflict("PostgreSQL service data directory does not match configuration")
    if str(service.get("state", "")).lower() != "running" or int(service.get("pid") or 0) <= 0:
        raise LifecycleConflict("authoritative PostgreSQL Windows service is not running")

    port = int(database.get("port") or 5432)
    owners = [row for row in listeners if int(row.get("port") or 0) == port]
    if len(owners) != 1:
        raise LifecycleConflict("PostgreSQL listener ownership is missing or ambiguous")
    listener_pid = int(owners[0].get("pid") or 0)
    listener = processes.get(listener_pid)
    if listener is None or Path(str(listener.get("executable", ""))).name.lower() != "postgres.exe":
        raise LifecycleConflict("configured PostgreSQL port is not owned by postgres.exe")
    expected_listener = Path(expected.service_executable).with_name("postgres.exe")
    if normalize_path(listener.get("executable")) != normalize_path(expected_listener):
        raise LifecycleConflict("PostgreSQL listener executable does not match configured install")
    if not listener.get("createdAt"):
        raise LifecycleConflict("PostgreSQL listener creation time is unavailable")
    if not _descends_from(listener_pid, int(service["pid"]), processes):
        raise LifecycleConflict(
            "PostgreSQL listener is not owned by the configured Windows service"
        )
    return {
        "verified": True,
        "service": expected.service,
        "version": database.get("serverVersion"),
        "executable": service.get("executable"),
        "dataDirectory": database.get("dataDirectory"),
        "listenerPid": listener_pid,
        "listenerExecutable": listener.get("executable"),
        "listenerCreatedAt": listener.get("createdAt"),
        "database": expected.database,
        "host": database.get("host"),
        "port": port,
    }


def _descends_from(pid: int, ancestor_pid: int, processes: dict[int, dict[str, Any]]) -> bool:
    current = pid
    seen: set[int] = set()
    for _ in range(16):
        if current == ancestor_pid:
            return True
        if current <= 0 or current in seen:
            return False
        seen.add(current)
        row = processes.get(current)
        if row is None:
            return False
        current = int(row.get("parentPid") or 0)
    return False


def collect_windows_postgres_evidence(
    port: int,
) -> tuple[list[dict], list[dict], dict[int, dict]]:
    services: list[dict] = []
    if os.name == "nt":
        for service in psutil.win_service_iter():
            try:
                row = service.as_dict()
                binary_path = str(row.get("binpath") or "")
                executable_match = re.match(r'^\s*(?:"([^"]+)"|(\S+))', binary_path)
                data_match = re.search(r'(?:^|\s)-D\s+(?:"([^"]+)"|(\S+))', binary_path, re.I)
                executable = (
                    (executable_match.group(1) or executable_match.group(2))
                    if executable_match
                    else ""
                )
                data_directory = (
                    (data_match.group(1) or data_match.group(2)) if data_match else None
                )
                services.append(
                    {
                        "name": row.get("name"),
                        "state": row.get("status"),
                        "pid": row.get("pid") or 0,
                        "executable": executable,
                        "dataDirectory": data_directory,
                    }
                )
            except (OSError, psutil.Error):
                continue
    listeners_by_pid: dict[int, dict] = {}
    processes: dict[int, dict] = {}
    for socket_row in psutil.net_connections(kind="tcp"):
        if socket_row.status != psutil.CONN_LISTEN or not socket_row.laddr:
            continue
        if int(socket_row.laddr.port) != port or socket_row.pid is None:
            continue
        listeners_by_pid[socket_row.pid] = {"port": port, "pid": socket_row.pid}
        try:
            current = psutil.Process(socket_row.pid)
            for process in (current, *current.parents()):
                if process.pid not in processes:
                    processes[process.pid] = inspect_process(process.pid)
        except (OSError, psutil.Error):
            continue
    return services, list(listeners_by_pid.values()), processes


def verify_authoritative_connection(connection: Connection, settings: Any) -> dict[str, Any]:
    row = connection.execute(
        text(
            "select current_setting('data_directory'), current_setting('server_version'), "
            "current_setting('server_version_num'), current_database(), "
            "inet_server_addr()::text, inet_server_port()"
        )
    ).one()
    url = connection.engine.url
    database = {
        "host": url.host or row[4] or "",
        "port": url.port or row[5] or 5432,
        "database": url.database or "",
        "currentDatabase": str(row[3]),
        "reachable": True,
        "serverVersion": str(row[1]),
        "serverVersionNum": int(row[2]),
        "dataDirectory": str(row[0]),
    }
    evidence = collect_windows_postgres_evidence(int(database["port"]))
    expectation = PostgresExpectation(
        service=settings.swinglens_postgres_service,
        major=settings.swinglens_postgres_expected_major,
        data_directory=str(settings.swinglens_postgres_data_dir),
        service_executable=str(settings.swinglens_postgres_executable),
        database=str(database["database"]),
    )
    return verify_postgres_provenance(database, *evidence, expectation)


def inspect_process(process_id: int) -> dict[str, Any]:
    process = psutil.Process(process_id)
    report = {
        "pid": process.pid,
        "parentPid": process.ppid(),
        "createdAt": datetime.fromtimestamp(process.create_time(), UTC).isoformat(),
        "executable": process.exe(),
    }
    try:
        report["commandLine"] = process.cmdline()
    except (OSError, psutil.Error):
        report["commandLine"] = None
    try:
        report["cwd"] = process.cwd()
    except (OSError, psutil.Error):
        report["cwd"] = None
    return report


def validate_runtime_process(
    expected: dict[str, Any], actual: dict[str, Any], *, listener_pid: int | None = None
) -> None:
    required = ("pid", "createdAt", "role", "module", "repoRoot", "runtimeInstanceId")
    if any(not expected.get(key) for key in required):
        raise LifecycleConflict("runtime state is incomplete")
    if int(expected["pid"]) != int(actual.get("pid") or 0):
        raise LifecycleConflict("runtime PID changed")
    expected_time = datetime.fromisoformat(str(expected["createdAt"]).replace("Z", "+00:00"))
    actual_time = datetime.fromisoformat(str(actual.get("createdAt", "")).replace("Z", "+00:00"))
    if abs((actual_time - expected_time).total_seconds()) > 0.01:
        raise LifecycleConflict("runtime process creation time changed (possible PID reuse)")
    command = [str(part) for part in actual.get("commandLine") or []]
    joined = " ".join(command)
    if f"-m {expected['module']}" not in joined:
        raise LifecycleConflict("runtime module does not match")
    if str(expected["runtimeInstanceId"]) not in command:
        raise LifecycleConflict("runtime instance identity does not match")
    if normalize_path(expected["repoRoot"]) != normalize_path(actual.get("cwd")):
        raise LifecycleConflict("runtime belongs to another repository checkout")
    if listener_pid is not None and int(expected["pid"]) != int(listener_pid):
        raise LifecycleConflict("runtime state PID does not own the expected listener")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_runtime_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleConflict("runtime state is corrupt or truncated") from exc
    if not isinstance(value, dict):
        raise LifecycleConflict("runtime state has an invalid shape")
    return value
