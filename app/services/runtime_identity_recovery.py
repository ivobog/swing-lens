"""Missing-state recovery from independently collected, agreeing runtime evidence."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from app.services.lifecycle_control import GIT_SHA_ENV, RUNTIME_FINGERPRINT_ENV, TOPOLOGY_VERSION
from app.services.lifecycle_safety import (
    LifecycleConflict,
    normalize_path,
    validate_runtime_process,
)
from app.services.parent_watchdog import PARENT_PID_ENV, PARENT_STARTED_AT_ENV


def _require(condition, reason):
    if not condition:
        raise LifecycleConflict("RUNTIME_IDENTITY_RECOVERY_REFUSED: " + reason)


def _argument(process, name):
    command = process.get("commandLine") or []
    _require(command.count(name) == 1, "missing or ambiguous command argument " + name)
    index = command.index(name) + 1
    _require(index < len(command), "missing command argument value " + name)
    return command[index]


def _fresh(value, now, max_age):
    _require(bool(value), "heartbeat timestamp unavailable")
    age = (now - datetime.fromisoformat(str(value).replace("Z", "+00:00"))).total_seconds()
    _require(-5 <= age <= max_age, "runtime heartbeat is stale or in the future")


def _descends(processes, child, ancestor):
    seen = set()
    while child not in seen and child in processes:
        if child == ancestor:
            return True
        seen.add(child)
        child = processes[child].get("parentPid")
    return False


def verified_recovery_state(evidence, *, root, port, host, worker_id, executables, now, max_age):
    """Pure proof. No PID, generation, executable or historical SHA is inferred from HEAD."""
    _require(evidence["jobs"].get("reachable") is True, "durable state unavailable")
    _require(evidence["jobs"].get("activeCount") == 0, "active durable jobs")
    _require(evidence.get("runningPipelines") == 0, "running pipelines")
    roles = evidence["roles"]
    processes = {int(key): value for key, value in evidence["processes"].items()}
    selected = {}
    for role in ("web", "supervisor", "worker"):
        matches = [row for row in roles if row["role"] == role]
        _require(len(matches) == 1, "missing or ambiguous " + role)
        selected[role] = processes[matches[0]["pid"]]
    web, supervisor, worker = (selected[role] for role in ("web", "supervisor", "worker"))
    listeners = evidence["listeners"].get("listeners", [])
    _require(not evidence["listeners"].get("error"), "listener inspection unavailable")
    sockets = [row for row in listeners if row.get("port") == port]
    _require(
        bool(sockets) and {row.get("pid") for row in sockets} == {web["pid"]},
        "FOREIGN_LISTENER: listener ownership disagrees",
    )
    _require(any(row.get("address") == host for row in sockets), "configured bind address differs")
    registrations = evidence["registrations"]
    _require(registrations.get("reachable") is True, "registrations unavailable")
    flight = evidence["supervisorState"]
    _require(
        isinstance(flight, dict) and not flight.get("cycle_failed"), "supervisor state unavailable"
    )
    _require(flight.get("topology_version") == TOPOLOGY_VERSION, "supervisor topology differs")
    _fresh(flight.get("timestamp"), now, max_age)
    runtime_id = _argument(supervisor, "--runtime-instance-id")
    environments = evidence["environments"]
    env = environments[supervisor["pid"]]
    git_sha, fingerprint = env.get(GIT_SHA_ENV), env.get(RUNTIME_FINGERPRINT_ENV)
    _require(
        bool(re.fullmatch(r"[0-9a-f]{40,64}", git_sha or "")), "historical Git identity unavailable"
    )
    _require(
        bool(re.fullmatch(r"[0-9a-f]{64}", fingerprint or "")), "generation fingerprint unavailable"
    )
    _require(
        flight.get("runtime_instance_id") == runtime_id
        and flight.get("runtime_config_fingerprint") == fingerprint,
        "supervisor generation disagrees",
    )
    expected_executables = {normalize_path(path) for path in executables}
    identities = {}
    for role, module, process_role in (
        ("web", "app.serve", "WEB"),
        ("supervisor", "app.worker_supervisor", "SUPERVISOR"),
        ("worker", "app.worker", "DURABLE_WORKER"),
    ):
        process = selected[role]
        command = process.get("commandLine") or []
        _require(
            command.count("-m") == 1 and _argument(process, "-m") == module,
            "FOREIGN_LISTENER: process module differs",
        )
        _require(
            normalize_path(process.get("executable")) in expected_executables
            and command
            and normalize_path(command[0]) == normalize_path(process["executable"]),
            "Python executable differs",
        )
        _require(normalize_path(process.get("cwd")) == normalize_path(root), "repository differs")
        process_env = environments[process["pid"]]
        _require(
            all(
                process_env.get(key) == value
                for key, value in {
                    GIT_SHA_ENV: git_sha,
                    RUNTIME_FINGERPRINT_ENV: fingerprint,
                    "SWINGLENS_RUNTIME_INSTANCE_ID": runtime_id,
                    "PROCESS_ROLE": process_role,
                    "RUNTIME_MODE": env.get("RUNTIME_MODE"),
                }.items()
            ),
            "process environment generation differs",
        )
        _require(env.get("RUNTIME_MODE") in {"NORMAL", "CERTIFICATION"}, "runtime mode unavailable")
        if role != "worker":
            _require(
                _argument(process, "--repo-root")
                and normalize_path(_argument(process, "--repo-root")) == normalize_path(root),
                "command repository differs",
            )
            _require(
                _argument(process, "--runtime-instance-id") == runtime_id,
                "command generation differs",
            )
            _require(
                _argument(process, "--port") == str(port) and _argument(process, "--host") == host,
                "command bind address differs",
            )
        if role != "web":
            _require(_argument(process, "--worker-id") == worker_id, "worker scope differs")
            registration = registrations.get(role)
            _require(
                isinstance(registration, dict)
                and registration.get("workerId") == worker_id
                and registration.get("pid") == process["pid"]
                and registration.get("stoppingAt") is None
                and bool(registration.get("instanceId"))
                and int(registration.get("generation") or 0) > 0,
                role + " registration differs",
            )
            _fresh(registration.get("heartbeatAt"), now, max_age)
            registered_time = datetime.fromisoformat(registration["createdAt"])
            observed_time = datetime.fromisoformat(process["createdAt"])
            _require(
                abs((registered_time - observed_time).total_seconds()) <= 0.01,
                role + " creation time differs (possible PID reuse)",
            )
        identities[role] = {
            "pid": process["pid"],
            "createdAt": process["createdAt"],
            "role": role,
            "module": module,
            "repoRoot": str(root),
            "runtimeInstanceId": runtime_id,
            "executable": process["executable"],
        }
    _require(
        flight.get("supervisor")
        == {
            "pid": supervisor["pid"],
            "instance_id": registrations["supervisor"]["instanceId"],
        },
        "supervisor registry/flight recorder disagree",
    )
    for role in ("web", "worker"):
        process = selected[role]
        _require(
            _descends(processes, process["pid"], supervisor["pid"]), "supervisor ancestry differs"
        )
        _require(
            environments[process["pid"]].get(PARENT_PID_ENV) == str(supervisor["pid"]),
            "parent watchdog PID differs",
        )
        parent_time = environments[process["pid"]].get(PARENT_STARTED_AT_ENV)
        _require(bool(parent_time), "parent watchdog creation time unavailable")
        _require(
            abs(
                (
                    datetime.fromisoformat(parent_time)
                    - datetime.fromisoformat(supervisor["createdAt"])
                ).total_seconds()
            )
            <= 0.01,
            "parent watchdog creation time differs",
        )
        launcher_pid = (flight.get(role) or {}).get("launcher_pid")
        _require(
            launcher_pid in processes
            and _descends(processes, process["pid"], launcher_pid)
            and _descends(processes, launcher_pid, supervisor["pid"]),
            "child launcher ancestry differs",
        )
        launcher = processes[launcher_pid]
        _require(
            normalize_path(launcher.get("executable")) in expected_executables
            and normalize_path(launcher.get("cwd")) == normalize_path(root)
            and _argument(launcher, "-m") == identities[role]["module"],
            "child launcher differs",
        )
        _require(
            datetime.fromisoformat(supervisor["createdAt"])
            <= datetime.fromisoformat(launcher["createdAt"])
            <= datetime.fromisoformat(process["createdAt"]),
            "child launcher creation time differs",
        )
        _require((flight.get(role) or {}).get("state") == "RUNNING", "child is not running")
        if role == "worker":
            _require(
                registrations[role].get("launcherPid") == launcher_pid,
                "worker launcher registration differs",
            )
        _require(
            datetime.fromisoformat(process["createdAt"])
            >= datetime.fromisoformat(supervisor["createdAt"]),
            "child predates supervisor",
        )
    group = evidence["processGroup"]
    _require(
        group["pid"] in processes
        and processes[group["pid"]] == group
        and _descends(processes, supervisor["pid"], group["pid"]),
        "supervisor process group differs",
    )
    group_identity = {
        **identities["supervisor"],
        "pid": group["pid"],
        "createdAt": group["createdAt"],
        "executable": group["executable"],
    }
    validate_runtime_process(group_identity, group)
    _require(
        normalize_path(group.get("executable")) in expected_executables,
        "process group executable differs",
    )
    _require(
        _argument(group, "-m") == "app.worker_supervisor"
        and datetime.fromisoformat(group["createdAt"])
        <= datetime.fromisoformat(supervisor["createdAt"]),
        "process group creation/module differs",
    )
    identities["web"].update(
        port=port, launcherPid=supervisor["pid"], launcherCreatedAt=supervisor["createdAt"]
    )
    return {
        "version": 5,
        "runtimeInstanceId": runtime_id,
        "repoRoot": str(root),
        "gitCommit": git_sha,
        "runtimeConfigFingerprint": fingerprint,
        "topologyVersion": flight["topology_version"],
        "recordedAtUtc": now.isoformat(),
        **identities,
        "processGroup": group_identity,
        "recovery": {
            "method": "verified-missing-state",
            "runtimeMode": env["RUNTIME_MODE"],
            "registrationIdentity": {
                role: {key: registrations[role][key] for key in ("instanceId", "generation")}
                for role in ("supervisor", "worker")
            },
            "sources": {
                "pid/createdAt/executable": "OS listener/process snapshot",
                "repoRoot/role/module/runtimeInstanceId": "verified command/cwd/environment",
                "gitCommit/runtimeConfigFingerprint": "live environments and supervisor recorder",
                "worker/supervisor": "fresh DB registration, OS creation time and ancestry",
                "processGroup": "outermost verified supervisor launcher",
                "topologyVersion": "verified supervisor recorder",
                "recordedAtUtc": "recovery observation clock",
            },
        },
    }


def publish_missing_state(path: Path, state: dict) -> None:
    """Atomic create-only publication; never replace even an invalid/existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".runtime-recovery-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise LifecycleConflict(
                "RUNTIME_STATE_ALREADY_EXISTS: recovery never overwrites state"
            ) from error
    finally:
        temporary.unlink(missing_ok=True)
