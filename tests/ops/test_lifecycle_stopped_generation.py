from __future__ import annotations

import json
from pathlib import Path

import psutil
import pytest

from app.services.lifecycle_safety import atomic_write_json
from scripts.ops import lifecycle_probe

CREATED = "2026-09-11T17:28:55.201577+00:00"
OLD_SHA = "095a552e21f92523a7d8e67df2109742a0cad0a9"
NEW_SHA = "0fb435ee6eb76049ddf6d76d859775fec35768f5"
OLD_FINGERPRINT = "old-generation"
NEW_FINGERPRINT = "desired-generation"
RUNTIME_ID = "44718fd5e4414bf4b3e9488a4b9e7c9b"


def _identity(pid: int, role: str, module: str, root: Path) -> dict[str, object]:
    return {
        "pid": pid,
        "createdAt": CREATED,
        "role": role,
        "module": module,
        "repoRoot": str(root),
        "runtimeInstanceId": RUNTIME_ID,
    }


def _state(root: Path, fingerprint: str = OLD_FINGERPRINT) -> dict[str, object]:
    return {
        "version": 5,
        "gitCommit": OLD_SHA,
        "runtimeConfigFingerprint": fingerprint,
        "topologyVersion": "supervisor-root-v1",
        "repoRoot": str(root),
        "runtimeInstanceId": RUNTIME_ID,
        "web": {**_identity(10444, "web", "app.serve", root), "port": 8000},
        "supervisor": _identity(15972, "supervisor", "app.worker_supervisor", root),
        "processGroup": _identity(6480, "supervisor", "app.worker_supervisor", root),
    }


def _actual(expected: dict[str, object], root: Path) -> dict[str, object]:
    return {
        "pid": expected["pid"],
        "createdAt": expected["createdAt"],
        "cwd": str(root),
        "commandLine": [
            "python",
            "-m",
            str(expected["module"]),
            "--runtime-instance-id",
            RUNTIME_ID,
        ],
    }


@pytest.fixture
def incident(monkeypatch, tmp_path):
    state_path = tmp_path / "swinglens-lifecycle.json"
    state = _state(tmp_path)
    atomic_write_json(state_path, state)
    monkeypatch.setattr(lifecycle_probe, "ROOT", tmp_path)
    monkeypatch.setattr(lifecycle_probe, "RUNTIME_STATE", state_path)
    monkeypatch.setattr(lifecycle_probe, "_git_commit", lambda: NEW_SHA)
    monkeypatch.setattr(lifecycle_probe, "_role_processes", lambda: [])
    monkeypatch.setattr(lifecycle_probe, "_listeners_report", lambda: {"listeners": []})
    monkeypatch.setenv("SWINGLENS_RUNTIME_CONFIG_FINGERPRINT", NEW_FINGERPRINT)
    return state_path, state


def _all_recorded_dead(monkeypatch) -> None:
    def missing(pid: int):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(lifecycle_probe, "inspect_process", missing)


def test_dead_recorded_web_with_different_fingerprint_is_dead_stale(incident, monkeypatch):
    _all_recorded_dead(monkeypatch)
    report = lifecycle_probe._runtime_state_report(listener_pid=None)
    assert report["classification"] == "DEAD_STALE"
    assert report["stale"] is True
    assert report["conflict"] is False
    assert report["recordedGitSha"] == OLD_SHA
    assert report["desiredGitSha"] == NEW_SHA
    assert report["recordedFingerprint"] == OLD_FINGERPRINT
    assert report["desiredFingerprint"] == NEW_FINGERPRINT


def test_active_verified_runtime_with_different_fingerprint_requires_restart(
    incident, monkeypatch
):
    _path, state = incident
    actual = {
        int(state[name]["pid"]): _actual(state[name], Path(state["repoRoot"]))
        for name in ("web", "supervisor", "processGroup")
    }
    monkeypatch.setattr(lifecycle_probe, "inspect_process", lambda pid: actual[pid])
    monkeypatch.setattr(lifecycle_probe, "_process_descends_from", lambda _child, _parent: True)
    report = lifecycle_probe._runtime_state_report(listener_pid=10444)
    assert report["classification"] == "ACTIVE_GENERATION_MISMATCH"
    assert report["runtimeActive"] is True
    assert "RESTART_REQUIRED" in report["error"]


def test_dead_runtime_with_same_fingerprint_is_stale_stopped(incident, monkeypatch):
    path, state = incident
    state["runtimeConfigFingerprint"] = NEW_FINGERPRINT
    atomic_write_json(path, state)
    _all_recorded_dead(monkeypatch)
    report = lifecycle_probe._runtime_state_report(listener_pid=None)
    assert report["classification"] == "DEAD_STALE"
    assert report["runtimeActive"] is False


def test_recorded_pid_reused_by_unrelated_process_is_conflict(incident, monkeypatch):
    _path, state = incident
    reused = _actual(state["web"], Path(state["repoRoot"]))
    reused["createdAt"] = "2026-09-11T18:00:00+00:00"
    monkeypatch.setattr(lifecycle_probe, "inspect_process", lambda _pid: reused)
    report = lifecycle_probe._runtime_state_report(listener_pid=None)
    assert report["classification"] == "AMBIGUOUS_CONFLICT"
    assert "PID reuse" in report["error"]


def test_recorded_web_dead_but_supervisor_alive_is_conflict(incident, monkeypatch):
    _path, state = incident

    def inspect(pid: int):
        if pid == int(state["web"]["pid"]):
            raise psutil.NoSuchProcess(pid)
        return _actual(state["supervisor"], Path(state["repoRoot"]))

    monkeypatch.setattr(lifecycle_probe, "inspect_process", inspect)
    report = lifecycle_probe._runtime_state_report(listener_pid=None)
    assert report["classification"] == "AMBIGUOUS_CONFLICT"
    assert "supervisor process is still alive" in report["error"]


@pytest.mark.parametrize("port", [8000, 9101, 9102])
def test_dead_processes_with_lifecycle_port_occupied_are_conflict(
    incident, monkeypatch, port
):
    _all_recorded_dead(monkeypatch)
    monkeypatch.setattr(
        lifecycle_probe,
        "_listeners_report",
        lambda: {"listeners": [{"port": port, "pid": 777}]},
    )
    report = lifecycle_probe._runtime_state_report(listener_pid=None)
    assert report["classification"] == "AMBIGUOUS_CONFLICT"
    assert str(port) in report["error"]


def test_dead_recorded_processes_with_unknown_canonical_worker_are_conflict(
    incident, monkeypatch
):
    _all_recorded_dead(monkeypatch)
    monkeypatch.setattr(
        lifecycle_probe,
        "_role_processes",
        lambda: [{"role": "worker", "pid": 888}],
    )
    report = lifecycle_probe._runtime_state_report(listener_pid=None)
    assert report["classification"] == "AMBIGUOUS_CONFLICT"
    assert "worker:888" in report["error"]


def test_recorded_worker_launcher_still_alive_prevents_dead_stale(incident, monkeypatch):
    _path, _state_value = incident
    supervisor_state = lifecycle_probe.supervisor_state_path(lifecycle_probe.ROOT)
    atomic_write_json(
        supervisor_state,
        {
            "runtime_instance_id": RUNTIME_ID,
            "worker": {"launcher_pid": 4412, "state": "RUNNING"},
        },
    )

    def inspect(pid: int):
        if pid == 4412:
            return {
                "pid": pid,
                "createdAt": CREATED,
                "cwd": str(lifecycle_probe.ROOT),
                "commandLine": ["python", "-m", "app.worker", RUNTIME_ID],
            }
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(lifecycle_probe, "inspect_process", inspect)
    report = lifecycle_probe._runtime_state_report(listener_pid=None)
    assert report["classification"] == "AMBIGUOUS_CONFLICT"
    assert "worker launcher is still alive" in report["error"]


def test_dead_stale_status_probe_is_read_only(incident, monkeypatch):
    path, original = incident
    before = path.read_bytes()
    _all_recorded_dead(monkeypatch)
    report = lifecycle_probe._runtime_state_report(listener_pid=None)
    assert report["classification"] == "DEAD_STALE"
    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_dead_stale_retirement_archives_and_journals_safe_generation_evidence(
    incident, monkeypatch
):
    path, _state_value = incident
    _all_recorded_dead(monkeypatch)
    monkeypatch.setenv("SWINGLENS_LIFECYCLE_OPERATION_ID", "retire-test")
    monkeypatch.setenv("SWINGLENS_LIFECYCLE_ACTION", "start")
    result = lifecycle_probe._retire_stale_runtime_state()
    assert result["retired"] is True
    assert not path.exists()
    archive = Path(result["archive"])
    assert archive.is_file()
    event = json.loads(
        (path.parent / "logs/lifecycle/lifecycle.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    assert event["event"] == "stale_runtime_state_retired"
    assert event["reason_code"] == "DEAD_GENERATION_RETIRED"
    assert event["recorded_git_sha"] == OLD_SHA
    assert event["desired_git_sha"] == NEW_SHA
    assert event["retirement_operation_id"] == "retire-test"


def test_active_runtime_state_is_never_retired(incident, monkeypatch):
    path, state = incident
    actual = {
        int(state[name]["pid"]): _actual(state[name], Path(state["repoRoot"]))
        for name in ("web", "supervisor", "processGroup")
    }
    monkeypatch.setattr(lifecycle_probe, "inspect_process", lambda pid: actual[pid])
    monkeypatch.setattr(lifecycle_probe, "_process_descends_from", lambda _child, _parent: True)
    result = lifecycle_probe._retire_stale_runtime_state()
    assert result == {
        "retired": False,
        "conflict": False,
        "classification": "ACTIVE_GENERATION_MISMATCH",
    }
    assert path.is_file()


def test_tree_identical_merge_sha_dead_converges_but_active_remains_protected(
    incident, monkeypatch
):
    _path, state = incident
    _all_recorded_dead(monkeypatch)
    dead = lifecycle_probe._runtime_state_report(listener_pid=None)
    assert dead["classification"] == "DEAD_STALE"

    actual = {
        int(state[name]["pid"]): _actual(state[name], Path(state["repoRoot"]))
        for name in ("web", "supervisor", "processGroup")
    }
    monkeypatch.setattr(lifecycle_probe, "inspect_process", lambda pid: actual[pid])
    monkeypatch.setattr(lifecycle_probe, "_process_descends_from", lambda _child, _parent: True)
    active = lifecycle_probe._runtime_state_report(listener_pid=10444)
    assert active["classification"] == "ACTIVE_GENERATION_MISMATCH"
    assert active["recordedGitSha"] == OLD_SHA
    assert active["desiredGitSha"] == NEW_SHA
