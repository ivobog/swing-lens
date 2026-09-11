from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil
import pytest

from app.services.lifecycle_safety import (
    LifecycleConflict,
    PostgresExpectation,
    atomic_write_json,
    read_runtime_state,
    validate_runtime_process,
    verify_postgres_provenance,
)
from scripts.ops import lifecycle_probe


@pytest.fixture
def postgres_evidence():
    expected = PostgresExpectation(
        service="postgresql-x64-18",
        major=18,
        data_directory=r"C:\Program Files\PostgreSQL\18\data",
        service_executable=r"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe",
    )
    database = {
        "host": "127.0.0.1",
        "port": 5432,
        "database": "swinglens",
        "currentDatabase": "swinglens",
        "reachable": True,
        "serverVersion": "18.3",
        "serverVersionNum": 180003,
        "dataDirectory": r"C:\Program Files\PostgreSQL\18\data",
    }
    services = [
        {
            "name": "postgresql-x64-18",
            "state": "running",
            "pid": 10,
            "executable": r"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe",
            "dataDirectory": r"C:\Program Files\PostgreSQL\18\data",
        }
    ]
    listeners = [{"port": 5432, "pid": 20}]
    processes = {
        20: {
            "parentPid": 10,
            "executable": r"C:\Program Files\PostgreSQL\18\bin\postgres.exe",
            "createdAt": "2026-09-07T10:00:00+00:00",
        },
        10: {"parentPid": 4, "executable": expected.service_executable},
    }
    return expected, database, services, listeners, processes


def test_authoritative_postgres_accepts_complete_independent_evidence(postgres_evidence) -> None:
    report = verify_postgres_provenance(*postgres_evidence[1:], postgres_evidence[0])
    assert report["verified"] is True
    assert report["service"] == "postgresql-x64-18"
    assert report["listenerPid"] == 20


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d, s, _l, _p: d.update(serverVersionNum=170009), "major version"),
        (lambda d, s, _l, _p: d.update(dataDirectory=r"C:\docker\pgdata"), "data directory"),
        (lambda d, s, _l, _p: s[0].update(name="postgresql-x64-17"), "service"),
        (lambda d, s, _l, _p: s.append(deepcopy(s[0])), "ambiguous"),
        (lambda d, s, _l, _p: s.__setitem__(slice(None), [{"name": "pgagent"}]), "service"),
        (lambda d, s, _l, p: p[20].update(parentPid=999), "not owned"),
        (
            lambda d, s, _l, p: p[20].update(executable=r"C:\Docker\postgres.exe"),
            "executable",
        ),
        (lambda d, s, _l, _p: d.update(currentDatabase="other"), "database identity"),
        (lambda d, s, _l, _p: d.update(host="database.internal"), "host is not local"),
        (lambda d, s, _l, _p: d.update(reachable=False), "not reachable"),
    ],
)
def test_postgres_provenance_ambiguity_and_wrong_clusters_fail_closed(
    postgres_evidence, mutation, message
) -> None:
    expected, database, services, listeners, processes = deepcopy(postgres_evidence)
    mutation(database, services, listeners, processes)
    with pytest.raises(LifecycleConflict, match=message):
        verify_postgres_provenance(database, services, listeners, processes, expected)


def _runtime_identity(tmp_path: Path) -> tuple[dict, dict]:
    created = datetime(2026, 9, 7, 10, tzinfo=UTC)
    expected = {
        "pid": 123,
        "createdAt": created.isoformat(),
        "role": "web",
        "module": "app.serve",
        "repoRoot": str(tmp_path),
        "runtimeInstanceId": "runtime-a",
        "port": 8000,
    }
    actual = {
        "pid": 123,
        "createdAt": created.isoformat(),
        "cwd": str(tmp_path),
        "commandLine": ["python", "-m", "app.serve", "runtime-a"],
    }
    return expected, actual


def test_runtime_process_requires_full_identity_tuple(tmp_path) -> None:
    expected, actual = _runtime_identity(tmp_path)
    validate_runtime_process(expected, actual, listener_pid=123)


def test_valid_process_with_stale_listener_pid_fails_closed(tmp_path) -> None:
    expected, actual = _runtime_identity(tmp_path)
    with pytest.raises(LifecycleConflict, match="does not own"):
        validate_runtime_process(expected, actual, listener_pid=124)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda e, a: a.update(pid=124), "PID changed"),
        (
            lambda e, a: a.update(
                createdAt=(
                    datetime.fromisoformat(a["createdAt"]) + timedelta(seconds=1)
                ).isoformat()
            ),
            "PID reuse",
        ),
        (lambda e, a: a.update(cwd=str(Path(a["cwd"]) / "other")), "another repository"),
        (lambda e, a: a.update(commandLine=["python", "-m", "app.worker"]), "module"),
        (lambda e, a: a.update(commandLine=["python", "-m", "app.serve", "other"]), "instance"),
    ],
)
def test_runtime_process_never_accepts_stale_reused_or_foreign_pid(
    tmp_path, mutation, message
) -> None:
    expected, actual = _runtime_identity(tmp_path)
    mutation(expected, actual)
    with pytest.raises(LifecycleConflict, match=message):
        validate_runtime_process(expected, actual, listener_pid=123)


def test_atomic_runtime_state_replaces_complete_json(tmp_path) -> None:
    path = tmp_path / "runtime.json"
    atomic_write_json(path, {"version": 1, "value": "old"})
    atomic_write_json(path, {"version": 2, "value": "new"})
    assert read_runtime_state(path) == {"value": "new", "version": 2}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("payload", ["{", "[]", "not-json"])
def test_corrupt_or_truncated_runtime_state_fails_closed(tmp_path, payload) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(LifecycleConflict):
        read_runtime_state(path)


def test_missing_runtime_state_is_distinct_from_corruption(tmp_path) -> None:
    assert read_runtime_state(tmp_path / "missing.json") is None


def test_runtime_report_validates_windows_listener_and_launcher_chain(
    tmp_path, monkeypatch
) -> None:
    expected, listener = _runtime_identity(tmp_path)
    expected.update(launcherPid=122, launcherCreatedAt=expected["createdAt"])
    launcher = {**listener, "pid": 122}
    state_path = tmp_path / "runtime.json"
    atomic_write_json(state_path, {"version": 3, "web": expected})
    monkeypatch.setattr(lifecycle_probe, "RUNTIME_STATE", state_path)
    monkeypatch.setattr(
        lifecycle_probe,
        "inspect_process",
        lambda pid: {123: listener, 122: launcher}[pid],
    )
    monkeypatch.setattr(lifecycle_probe, "_process_descends_from", lambda child, parent: True)

    report = lifecycle_probe._runtime_state_report(listener_pid=123)

    assert report["valid"] is True
    assert report["launcher"]["pid"] == 122


def test_complete_runtime_state_with_gone_pid_is_stale_not_corrupt(
    tmp_path, monkeypatch
) -> None:
    expected, _actual = _runtime_identity(tmp_path)
    state_path = tmp_path / "runtime.json"
    atomic_write_json(state_path, {"version": 3, "web": expected})
    monkeypatch.setattr(lifecycle_probe, "RUNTIME_STATE", state_path)

    def missing(_pid):
        raise psutil.NoSuchProcess(123)

    monkeypatch.setattr(lifecycle_probe, "inspect_process", missing)

    assert lifecycle_probe._runtime_state_report(listener_pid=None)["stale"] is True


def test_signal_break_targets_verified_process_group_not_inner_supervisor(monkeypatch) -> None:
    report = {
        "valid": True,
        "state": {"web": {"pid": 303}},
        "actual": {"pid": 303},
        "launcher": {"pid": 202},
        "processGroup": {"pid": 101},
    }
    signals = []
    monkeypatch.setattr(lifecycle_probe, "_runtime_state_report", lambda _listener: report)
    monkeypatch.setattr(lifecycle_probe.os, "kill", lambda pid, event: signals.append((pid, event)))

    result = lifecycle_probe._signal_break(303, 303)

    assert result == {"signaled": True, "signalPid": 101}
    assert signals[0][0] == 101


def test_listener_identity_uses_socket_owner_not_intermediate_windows_launcher(
    monkeypatch,
) -> None:
    listener = {
        "pid": 303,
        "commandLine": [
            "python.exe",
            "-m",
            "app.serve",
            "--runtime-instance-id",
            "runtime-a",
        ],
        "cwd": str(lifecycle_probe.ROOT),
    }
    connection = type(
        "Connection",
        (),
        {
            "status": psutil.CONN_LISTEN,
            "pid": 303,
            "laddr": type("Address", (), {"port": 8000})(),
        },
    )()
    monkeypatch.setattr(lifecycle_probe.psutil, "net_connections", lambda **_kwargs: [connection])
    monkeypatch.setattr(lifecycle_probe, "inspect_process", lambda _pid: listener)
    monkeypatch.setattr(
        lifecycle_probe,
        "_process_descends_from",
        lambda child, ancestor: (child, ancestor) == (303, 101),
    )

    assert lifecycle_probe._listener_process_identity(
        port=8000,
        runtime_instance_id="runtime-a",
        ancestor_pid=101,
    ) == listener
