from __future__ import annotations

import copy
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.lifecycle_control import GIT_SHA_ENV, RUNTIME_FINGERPRINT_ENV
from app.services.lifecycle_safety import LifecycleConflict, read_runtime_state
from app.services.parent_watchdog import PARENT_PID_ENV, PARENT_STARTED_AT_ENV
from app.services.runtime_identity_recovery import publish_missing_state, verified_recovery_state
from scripts.ops import lifecycle_probe as lp


@pytest.fixture
def observed(tmp_path):
    now = datetime.now(UTC)
    exe = sys.executable
    runtime_id = "test-generation"

    def process(pid, parent, seconds, module):
        command = [exe, "-m", module, "--worker-id", "test-worker"]
        if module != "app.worker":
            command += [
                "--runtime-instance-id",
                runtime_id,
                "--repo-root",
                str(tmp_path),
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ]
        return {
            "pid": pid,
            "parentPid": parent,
            "createdAt": (now - timedelta(seconds=seconds)).isoformat(),
            "executable": exe,
            "commandLine": command,
            "cwd": str(tmp_path),
        }

    processes = {
        10: process(10, 0, 10, "app.worker_supervisor"),
        11: process(11, 10, 9, "app.worker_supervisor"),
        12: process(12, 11, 8, "app.serve"),
        13: process(13, 12, 7, "app.serve"),
        14: process(14, 11, 6, "app.worker"),
        15: process(15, 14, 5, "app.worker"),
    }
    environments = {}
    for pid, role in ((11, "SUPERVISOR"), (13, "WEB"), (15, "DURABLE_WORKER")):
        environments[pid] = {
            GIT_SHA_ENV: "a" * 40,
            RUNTIME_FINGERPRINT_ENV: "b" * 64,
            "SWINGLENS_RUNTIME_INSTANCE_ID": runtime_id,
            "PROCESS_ROLE": role,
            "RUNTIME_MODE": "NORMAL",
        }
        if pid != 11:
            environments[pid].update(
                {PARENT_PID_ENV: "11", PARENT_STARTED_AT_ENV: processes[11]["createdAt"]}
            )

    def registration(pid):
        return {
            "pid": pid,
            "createdAt": processes[pid]["createdAt"],
            "workerId": "test-worker",
            "instanceId": "instance-" + str(pid),
            "generation": 2,
            "stoppingAt": None,
            "heartbeatAt": now.isoformat(),
            "launcherPid": 14 if pid == 15 else None,
        }

    evidence = {
        "roles": [
            {"role": role, "pid": pid}
            for role, pid in (("supervisor", 11), ("web", 13), ("worker", 15))
        ],
        "processes": processes,
        "environments": environments,
        "processGroup": processes[10],
        "listeners": {"listeners": [{"port": 8000, "pid": 13, "address": "127.0.0.1"}]},
        "registrations": {
            "reachable": True,
            "supervisor": registration(11),
            "worker": registration(15),
        },
        "jobs": {"reachable": True, "activeCount": 0},
        "runningPipelines": 0,
        "supervisorState": {
            "runtime_instance_id": runtime_id,
            "runtime_config_fingerprint": "b" * 64,
            "topology_version": "supervisor-root-v1",
            "timestamp": now.isoformat(),
            "cycle_failed": False,
            "supervisor": {"pid": 11, "instance_id": "instance-11"},
            "web": {"launcher_pid": 12, "state": "RUNNING"},
            "worker": {"launcher_pid": 14, "state": "RUNNING"},
        },
    }
    arguments = {
        "root": tmp_path,
        "port": 8000,
        "host": "127.0.0.1",
        "worker_id": "test-worker",
        "executables": [exe],
        "now": now,
        "max_age": 60,
    }
    return evidence, arguments


def test_valid_missing_state_restores_normal_ownership(observed, tmp_path, monkeypatch):
    evidence, arguments = observed
    state = verified_recovery_state(evidence, **arguments)
    path = tmp_path / "runtime.json"
    publish_missing_state(path, state)
    monkeypatch.setattr(lp, "RUNTIME_STATE", path)
    monkeypatch.setattr(lp, "ROOT", tmp_path)
    monkeypatch.setattr(lp, "inspect_process", lambda pid: evidence["processes"][pid])
    monkeypatch.setattr(
        lp, "_process_descends_from", lambda child, parent: (child, parent) in {(13, 11), (11, 10)}
    )
    monkeypatch.delenv(RUNTIME_FINGERPRINT_ENV, raising=False)
    assert lp._runtime_state_report(listener_pid=13)["valid"] is True
    assert read_runtime_state(path)["gitCommit"] == "a" * 40
    assert "sources" in state["recovery"]


@pytest.mark.parametrize(
    "case",
    [
        "foreign_listener",
        "foreign_module",
        "wrong_executable",
        "wrong_repo",
        "wrong_repo_argument",
        "reused_supervisor_pid",
        "reused_worker_pid",
        "supervisor_mismatch",
        "worker_mismatch",
        "wrong_worker_scope",
        "worker_launcher_mismatch",
        "wrong_generation",
        "wrong_fingerprint",
        "wrong_git_sha",
        "wrong_parent",
        "wrong_parent_time",
        "stale_heartbeat",
        "stale_flight_recorder",
        "missing_worker",
        "missing_environment",
        "missing_git",
        "missing_registrations",
        "listener_error",
        "duplicate_supervisor",
        "active_jobs",
        "running_pipeline",
        "child_predates_supervisor",
    ],
)
def test_contradictory_or_partial_evidence_is_refused(observed, case):
    evidence, arguments = observed
    if case == "foreign_listener":
        evidence["listeners"]["listeners"][0]["pid"] = 999
    elif case == "foreign_module":
        evidence["processes"][13]["commandLine"][2] = "http.server"
    elif case == "wrong_executable":
        evidence["processes"][13]["executable"] = "/foreign/python.exe"
    elif case == "wrong_repo":
        evidence["processes"][13]["cwd"] = "/other/repository"
    elif case == "wrong_repo_argument":
        command = evidence["processes"][13]["commandLine"]
        command[command.index("--repo-root") + 1] = "/other/repository"
    elif case in {"reused_supervisor_pid", "reused_worker_pid"}:
        evidence["registrations"]["supervisor" if case == "reused_supervisor_pid" else "worker"][
            "createdAt"
        ] = arguments["now"].isoformat()
    elif case == "supervisor_mismatch":
        evidence["supervisorState"]["supervisor"]["instance_id"] = "other"
    elif case == "worker_mismatch":
        evidence["registrations"]["worker"]["pid"] = 99
    elif case == "wrong_worker_scope":
        evidence["registrations"]["worker"]["workerId"] = "other"
    elif case == "worker_launcher_mismatch":
        evidence["registrations"]["worker"]["launcherPid"] = 99
    elif case == "wrong_generation":
        evidence["environments"][15]["SWINGLENS_RUNTIME_INSTANCE_ID"] = "other"
    elif case == "wrong_fingerprint":
        evidence["environments"][13][RUNTIME_FINGERPRINT_ENV] = "c" * 64
    elif case == "wrong_git_sha":
        evidence["environments"][15][GIT_SHA_ENV] = "c" * 40
    elif case == "wrong_parent":
        evidence["processes"][12]["parentPid"] = 10
    elif case == "wrong_parent_time":
        evidence["environments"][15][PARENT_STARTED_AT_ENV] = arguments["now"].isoformat()
    elif case == "stale_heartbeat":
        evidence["registrations"]["worker"]["heartbeatAt"] = (
            arguments["now"] - timedelta(minutes=5)
        ).isoformat()
    elif case == "stale_flight_recorder":
        evidence["supervisorState"]["timestamp"] = (
            arguments["now"] - timedelta(minutes=5)
        ).isoformat()
    elif case == "missing_worker":
        evidence["roles"] = evidence["roles"][:-1]
    elif case == "missing_environment":
        evidence["environments"][15] = {}
    elif case == "missing_git":
        evidence["environments"][11].pop(GIT_SHA_ENV)
    elif case == "missing_registrations":
        evidence["registrations"]["reachable"] = False
    elif case == "listener_error":
        evidence["listeners"]["error"] = "AccessDenied"
    elif case == "duplicate_supervisor":
        evidence["roles"].append({"role": "supervisor", "pid": 11})
    elif case == "active_jobs":
        evidence["jobs"]["activeCount"] = 1
    elif case == "running_pipeline":
        evidence["runningPipelines"] = 1
    elif case == "child_predates_supervisor":
        evidence["processes"][13]["createdAt"] = evidence["processes"][10]["createdAt"]
    with pytest.raises(LifecycleConflict):
        verified_recovery_state(evidence, **arguments)


@pytest.mark.parametrize("contents", [b'{"valid":"original"}', b"{", b""])
def test_existing_file_never_overwritten(tmp_path, contents):
    path = tmp_path / "runtime.json"
    path.write_bytes(contents)
    with pytest.raises(LifecycleConflict, match="ALREADY_EXISTS"):
        publish_missing_state(path, {"replacement": True})
    assert path.read_bytes() == contents
    assert list(tmp_path.iterdir()) == [path]


def _configure_recovery(observed, tmp_path, monkeypatch):
    evidence, _ = observed
    monkeypatch.setattr(lp, "ROOT", tmp_path)
    monkeypatch.setattr(lp, "RUNTIME_STATE", tmp_path / "runtime.json")
    monkeypatch.setattr(
        lp,
        "_settings",
        lambda: SimpleNamespace(
            app_port=8000,
            app_host="127.0.0.1",
            job_worker_id="test-worker",
            job_worker_heartbeat_timeout_seconds=60,
        ),
    )
    monkeypatch.setattr(lp, "_runtime_recovery_evidence", lambda: copy.deepcopy(evidence))
    return evidence


def test_recovery_command_observes_twice_then_publishes(observed, tmp_path, monkeypatch):
    _configure_recovery(observed, tmp_path, monkeypatch)
    result = lp._recover_runtime_state()
    assert result["recovered"] is True
    assert read_runtime_state(lp.RUNTIME_STATE) == result["state"]
    assert lp._recover_runtime_state()["recovered"] is False


def test_registration_changes_between_observations_refuse_publication(
    observed, tmp_path, monkeypatch
):
    evidence = _configure_recovery(observed, tmp_path, monkeypatch)
    changed = copy.deepcopy(evidence)
    changed["registrations"]["worker"]["generation"] += 1
    snapshots = iter([evidence, changed])
    monkeypatch.setattr(lp, "_runtime_recovery_evidence", lambda: next(snapshots))
    result = lp._recover_runtime_state()
    assert result["recovered"] is False
    assert "changed" in result["error"]
    assert not lp.RUNTIME_STATE.exists()


def test_publication_race_cannot_overwrite_another_lifecycle_owner(observed, tmp_path, monkeypatch):
    evidence = _configure_recovery(observed, tmp_path, monkeypatch)
    calls = []

    def collect():
        calls.append(True)
        if len(calls) == 2:
            lp.RUNTIME_STATE.write_text("original owner", encoding="utf-8")
        return copy.deepcopy(evidence)

    monkeypatch.setattr(lp, "_runtime_recovery_evidence", collect)
    assert lp._recover_runtime_state()["recovered"] is False
    assert lp.RUNTIME_STATE.read_text() == "original owner"


def test_shutdown_allows_old_code_only_after_strong_ownership_checks(
    observed, tmp_path, monkeypatch
):
    test_valid_missing_state_restores_normal_ownership(observed, tmp_path, monkeypatch)
    monkeypatch.setenv(RUNTIME_FINGERPRINT_ENV, "c" * 64)
    assert lp._runtime_state_report(13)["classification"] == "ACTIVE_GENERATION_MISMATCH"
    assert lp._runtime_state_report(13, for_shutdown=True)["valid"] is True
    observed[0]["processes"][13]["createdAt"] = datetime.now(UTC).isoformat()
    assert lp._runtime_state_report(13, for_shutdown=True)["valid"] is False
