from __future__ import annotations

import json
import logging
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

from app import worker_supervisor
from app.models.tables import BackgroundWorker
from app.services import parent_watchdog, process_identity
from app.services.lifecycle_safety import atomic_write_json, read_runtime_state
from app.services.operational_metrics import operational_metrics
from app.services.process_memory import ProcessMemorySnapshot
from app.settings import Settings


class FakeProcess:
    def __init__(self, pid: int, return_code: int | None) -> None:
        self.pid = pid
        self.returncode = return_code

    def poll(self):
        return self.returncode


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        job_worker_heartbeat_timeout_seconds=30,
        job_worker_heartbeat_interval_seconds=1,
        worker_shutdown_grace_seconds=1,
    )


def _worker(*, pid: int = 200, instance_id: str | None = "instance-a") -> BackgroundWorker:
    now = datetime.now(UTC)
    return BackgroundWorker(
        worker_id="worker-a",
        queues_json=["interactive", "broker", "background"],
        hostname="host-a",
        process_id=pid,
        instance_id=instance_id,
        process_started_at=now - timedelta(minutes=1),
        generation=3,
        started_at=now - timedelta(minutes=1),
        heartbeat_at=now,
        resource_collector_heartbeat_at=now,
        control_loop_heartbeat_at=now,
    )


@pytest.mark.parametrize("published_before_fence", [False, True])
def test_memory_critical_fence_preserves_exact_sample_before_cleanup(
    monkeypatch, caplog, published_before_fence,
):
    worker = _worker(pid=222)
    sample = ProcessMemorySnapshot(222, 6500 * 1024**2, 7000 * 1024**2, None, None)
    observations = []
    events = []
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _: worker)
    monkeypatch.setattr(worker_supervisor, "_registered_worker_process_alive", lambda _: True)
    monkeypatch.setattr(worker_supervisor, "_registered_worker_control_loop_alive", lambda _: True)

    def observe(pid):
        observations.append(pid)
        return sample

    monkeypatch.setattr(worker_supervisor, "process_memory_snapshot", observe)

    def no_progress(*args, **kwargs):
        pytest.fail("Memory-critical fencing must take precedence over no-progress fencing.")

    monkeypatch.setattr(worker_supervisor, "_fence_no_progress", no_progress)

    def fence(*args, **kwargs):
        events.append(("fence", kwargs["memory_evidence"]))
        return worker_supervisor.WorkerJobReconciliation(
            fenced_job_ids=() if published_before_fence else (1882,),
            finalized_job_ids=(1882,) if published_before_fence else (),
        )

    monkeypatch.setattr(worker_supervisor, "_fence_worker", fence)
    monkeypatch.setattr(
        worker_supervisor, "_terminate_worker_instance", lambda *a: events.append("stop")
    )
    monkeypatch.setattr(worker_supervisor, "_retire_worker_registration", lambda *a: None)
    requeued = []
    monkeypatch.setattr(
        worker_supervisor, "_requeue",
        lambda ids, **kwargs: (events.append("requeue"), requeued.extend(ids)),
    )

    with caplog.at_level(logging.ERROR):
        worker_supervisor._supervise_once(worker_id="worker-a", queues="interactive", child=None)

    assert observations == [222]
    assert [event[0] if isinstance(event, tuple) else event for event in events] == [
        "fence", "stop", "requeue",
    ]
    evidence = events[0][1]
    assert evidence["private_bytes"] == evidence["measured_bytes"] == 7000 * 1024**2
    assert evidence["rss_bytes"] == 6500 * 1024**2
    assert evidence["critical_bytes"] == 6144 * 1024**2
    assert evidence["measurement_basis"] == "private_bytes"
    assert evidence["registered_pid"] == 222
    assert evidence["worker_instance_id"] == "instance-a"
    assert evidence["observed_at"]
    record = next(
        row for row in caplog.records if row.msg.startswith("worker.supervisor.memory_critical")
    )
    assert record.private_bytes == evidence["private_bytes"]
    assert record.observed_at == evidence["observed_at"]
    assert requeued == ([] if published_before_fence else [1882])


def test_memory_observation_identifies_rss_fallback_and_inspection_failure(monkeypatch):
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "process_memory_snapshot", lambda _: (
        ProcessMemorySnapshot(200, 7000 * 1024**2, None, None, None)
    ))
    state, evidence = worker_supervisor._safe_memory_observation(_worker())
    assert state == "CRITICAL"
    assert evidence["measurement_basis"] == "rss_bytes"
    assert evidence["measured_bytes"] == evidence["rss_bytes"]
    assert evidence["private_bytes"] is None

    def unavailable(_):
        raise OSError("process unavailable")

    monkeypatch.setattr(worker_supervisor, "process_memory_snapshot", unavailable)
    assert worker_supervisor._safe_memory_observation(_worker()) == ("UNKNOWN", {})


@pytest.mark.parametrize("return_code", [0, 17])
def test_exited_worker_launcher_is_replaced_automatically(monkeypatch, return_code) -> None:
    operational_metrics.reset()
    replacement = FakeProcess(300, None)
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: None)
    monkeypatch.setattr(worker_supervisor, "_start_worker", lambda *_args: replacement)

    result = worker_supervisor._supervise_once(
        worker_id="worker-a",
        queues="interactive,broker,background",
        child=worker_supervisor.LaunchedWorker(FakeProcess(100, return_code), 0),
    )

    assert result is not None
    assert result.process is replacement
    assert operational_metrics.total("swinglens_worker_restarts_total", worker_id="worker-a") == 1


def test_exited_worker_launcher_records_exit_before_registration(monkeypatch, caplog) -> None:
    replacement = FakeProcess(300, None)
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: None)
    monkeypatch.setattr(worker_supervisor, "_start_worker", lambda *_args: replacement)

    with caplog.at_level(logging.ERROR):
        worker_supervisor._supervise_once(
            worker_id="worker-a",
            queues="interactive,broker,background",
            child=worker_supervisor.LaunchedWorker(FakeProcess(100, 23), 0),
        )

    assert "worker.supervisor.child_exited_before_registration" in caplog.text
    assert "DURABLE_WORKER_EXITED_BEFORE_REGISTRATION" in caplog.text
    assert "'exit_code': 23" in caplog.text


def test_worker_launcher_preserves_windows_virtual_environment(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "Scripts" / "python.exe"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path))
    assert Path(worker_supervisor._worker_python_executable()) == executable


def test_launcher_pid_mismatch_uses_registered_worker_identity(monkeypatch) -> None:
    worker = _worker(pid=222)
    launcher = FakeProcess(111, None)
    associated = []
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: worker)
    monkeypatch.setattr(worker_supervisor, "_registered_worker_process_alive", lambda _row: True)
    monkeypatch.setattr(worker_supervisor, "_safe_memory_observation", lambda _row: ("OK", {}))
    monkeypatch.setattr(worker_supervisor, "_fence_no_progress", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        worker_supervisor, "_associate_launcher", lambda row, pid: associated.append((row, pid))
    )

    result = worker_supervisor._supervise_once(
        worker_id="worker-a",
        queues="interactive,broker,background",
        child=worker_supervisor.LaunchedWorker(launcher, 0),
    )

    assert result is not None
    assert result.process.pid == 111
    assert associated == [(worker, 111)]


def test_frozen_worker_is_fenced_terminated_and_recoverable(monkeypatch) -> None:
    worker = _worker()
    events = []
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: worker)
    monkeypatch.setattr(worker_supervisor, "_registered_worker_process_alive", lambda _row: True)
    monkeypatch.setattr(worker_supervisor, "_safe_memory_observation", lambda _row: ("OK", {}))
    monkeypatch.setattr(worker_supervisor, "_fence_no_progress", lambda *_args, **_kwargs: [71])
    monkeypatch.setattr(
        worker_supervisor,
        "_terminate_worker_instance",
        lambda *_args: events.append("terminated"),
    )
    monkeypatch.setattr(
        worker_supervisor, "_retire_worker_registration", lambda _row: events.append("retired")
    )
    monkeypatch.setattr(
        worker_supervisor,
        "_requeue",
        lambda ids, **_kwargs: events.append(("requeued", ids)),
    )

    result = worker_supervisor._supervise_once(
        worker_id="worker-a", queues="interactive,broker,background", child=None
    )

    assert result is None
    assert events == ["terminated", "retired", ("requeued", [71])]


def test_stale_control_loop_terminates_generation_before_fencing_locked_jobs(monkeypatch) -> None:
    worker = _worker()
    worker.control_loop_heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
    events = []
    os_alive = {"value": True}
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: worker)
    monkeypatch.setattr(worker_supervisor, "_registered_worker_process_alive", lambda _row: True)
    monkeypatch.setattr(
        worker_supervisor, "_registered_worker_job_execution_alive", lambda _row: False
    )
    monkeypatch.setattr(
        worker_supervisor, "_worker_os_process_alive", lambda _row: os_alive["value"]
    )

    def terminate(*_args):
        events.append("terminated")
        os_alive["value"] = False

    monkeypatch.setattr(worker_supervisor, "_terminate_worker_instance", terminate)
    monkeypatch.setattr(
        worker_supervisor,
        "_fence_worker",
        lambda *_args, **_kwargs: (
            events.append("fenced")
            or worker_supervisor.WorkerJobReconciliation(fenced_job_ids=(91,))
        ),
    )
    monkeypatch.setattr(
        worker_supervisor, "_retire_worker_registration", lambda _row: events.append("retired")
    )
    monkeypatch.setattr(
        worker_supervisor,
        "_requeue",
        lambda ids, **_kwargs: events.append(("requeued", ids)),
    )

    result = worker_supervisor._supervise_once(
        worker_id="worker-a", queues="interactive,broker,background", child=None
    )

    assert result is None
    assert events == ["terminated", "fenced", "retired", ("requeued", [91])]


def test_stale_claim_loop_does_not_kill_observably_active_job(monkeypatch, caplog) -> None:
    worker = _worker()
    worker.control_loop_heartbeat_at = datetime.now(UTC) - timedelta(seconds=31)
    events = []
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: worker)
    monkeypatch.setattr(worker_supervisor, "_registered_worker_process_alive", lambda _row: True)
    monkeypatch.setattr(
        worker_supervisor, "_registered_worker_job_execution_alive", lambda _row: True
    )
    monkeypatch.setattr(worker_supervisor, "_safe_memory_observation", lambda _row: ("OK", {}))
    monkeypatch.setattr(worker_supervisor, "_fence_no_progress", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        worker_supervisor,
        "_terminate_worker_instance",
        lambda *_args: events.append("terminated"),
    )

    with caplog.at_level(logging.WARNING):
        result = worker_supervisor._supervise_once(
            worker_id="worker-a", queues="interactive,broker,background", child=None
        )

    assert result is None
    assert events == []
    assert "worker.supervisor.control_loop_stale_active_job" in caplog.text


@pytest.mark.parametrize(("running_job_id", "expected"), [(259, True), (None, False)])
def test_job_execution_liveness_is_observed_from_the_running_claim(
    monkeypatch,
    running_job_id,
    expected,
) -> None:
    class ObservingSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, _statement):
            return running_job_id

    monkeypatch.setattr(worker_supervisor, "SessionLocal", ObservingSession)
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)

    assert worker_supervisor._registered_worker_job_execution_alive(_worker()) is expected


def test_stale_registration_is_retired_and_supervisor_continues(monkeypatch) -> None:
    worker = _worker(pid=99999)
    replacement = FakeProcess(300, None)
    events = []
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: worker)
    monkeypatch.setattr(worker_supervisor, "_registered_worker_process_alive", lambda _row: False)
    monkeypatch.setattr(
        worker_supervisor,
        "_fence_worker",
        lambda *_args, **_kwargs: worker_supervisor.WorkerJobReconciliation(fenced_job_ids=(88,)),
    )
    monkeypatch.setattr(
        worker_supervisor, "_retire_worker_registration", lambda _row: events.append("retired")
    )
    monkeypatch.setattr(
        worker_supervisor,
        "_requeue",
        lambda ids, **_kwargs: events.append(("requeued", ids)),
    )
    monkeypatch.setattr(worker_supervisor, "_start_worker", lambda *_args: replacement)

    result = worker_supervisor._supervise_once(
        worker_id="worker-a", queues="interactive,broker,background", child=None
    )

    assert result is not None and result.process is replacement
    assert events == ["retired", ("requeued", [88])]


def test_stopped_registration_does_not_kill_replacement_while_it_starts(monkeypatch) -> None:
    worker = _worker()
    worker.stopping_at = datetime.now(UTC)
    launcher = FakeProcess(444, None)
    started = []
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: worker)
    monkeypatch.setattr(worker_supervisor, "_registered_worker_process_alive", lambda _row: False)
    monkeypatch.setattr(
        worker_supervisor, "_start_worker", lambda *_args: started.append("started")
    )

    child = worker_supervisor.LaunchedWorker(launcher, worker_supervisor.monotonic())
    result = worker_supervisor._supervise_once(
        worker_id="worker-a", queues="interactive,broker,background", child=child
    )

    assert result is child
    assert started == []


def test_liveness_inspection_exception_is_contained(monkeypatch, caplog) -> None:
    worker = _worker()
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(
        worker_supervisor,
        "process_is_alive",
        lambda *_args: (_ for _ in ()).throw(OSError("win32 inspection failed")),
    )

    with caplog.at_level(logging.ERROR):
        assert worker_supervisor._registered_worker_process_alive(worker) is False

    assert "worker.supervisor.liveness_inspection_failed" in caplog.text


def test_pid_reuse_does_not_validate_old_process_instance(monkeypatch) -> None:
    old_start = datetime.now(UTC) - timedelta(hours=2)
    new_start = datetime.now(UTC)
    monkeypatch.setattr(process_identity, "process_started_at", lambda _pid: new_start)

    assert process_identity.process_is_alive(1234, old_start) is False


def test_shutdown_request_requires_runtime_and_supervisor_identity(tmp_path) -> None:
    request_path = tmp_path / "request.json"
    started_at = datetime.now(UTC)
    payload = {
        "runtimeInstanceId": "runtime-a",
        "supervisorPid": 123,
        "supervisorCreatedAt": started_at.isoformat(),
    }
    request_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    assert (
        worker_supervisor._read_shutdown_request(
            request_path,
            runtime_instance_id="runtime-a",
            supervisor_pid=123,
            supervisor_started_at=started_at,
        )
        == payload
    )
    assert (
        worker_supervisor._read_shutdown_request(
            request_path,
            runtime_instance_id="runtime-b",
            supervisor_pid=123,
            supervisor_started_at=started_at,
        )
        is None
    )


def test_children_wait_for_shutdown_acceptance_by_their_exact_parent(tmp_path, monkeypatch):
    path = tmp_path / "request.json"
    started_at = datetime.now(UTC).isoformat()
    monkeypatch.setenv(parent_watchdog.PARENT_PID_ENV, "123")
    monkeypatch.setenv(parent_watchdog.PARENT_STARTED_AT_ENV, started_at)
    monkeypatch.setenv(parent_watchdog.PARENT_RUNTIME_INSTANCE_ENV, "runtime-a")
    monkeypatch.setenv(parent_watchdog.PARENT_SHUTDOWN_REQUEST_ENV, str(path))
    monkeypatch.setattr(parent_watchdog, "process_is_alive", lambda *_args: True)
    payload = {
        "runtimeInstanceId": "runtime-a",
        "supervisorPid": 123,
        "supervisorCreatedAt": started_at,
    }
    atomic_write_json(path, payload)
    stopped = Event()
    watcher = parent_watchdog.install_parent_watchdog(stopped.set)
    assert watcher is not None
    assert not stopped.wait(0.6)
    atomic_write_json(path, {**payload, "accepted": True, "supervisorPid": 456})
    assert not stopped.wait(0.6)
    atomic_write_json(path, {**payload, "accepted": True})
    assert stopped.wait(2)
    watcher.join(2)


@pytest.mark.parametrize("wedged", [False, True])
def test_controlled_drain_uses_one_deadline_and_leaves_failed_request(
    tmp_path, monkeypatch, wedged
):
    path = tmp_path / "request.json"
    timeline = iter([10, 11, 16, 23])
    monkeypatch.setattr(worker_supervisor, "monotonic", lambda: next(timeline))
    waited = []

    class OwnedProcess:
        def wait(self, timeout):
            assert read_runtime_state(path) == {"version": 1, "accepted": True}
            waited.append(timeout)
            if wedged and len(waited) == 2:
                raise subprocess.TimeoutExpired("owned sector worker", timeout)

    web = worker_supervisor.LaunchedWeb(OwnedProcess(), 0)
    sector = worker_supervisor.LaunchedWorker(OwnedProcess(), 0)
    worker = worker_supervisor.LaunchedWorker(OwnedProcess(), 0)
    if wedged:
        with pytest.raises(subprocess.TimeoutExpired):
            worker_supervisor._drain_controlled_children(path, {"version": 1}, web, sector, worker)
        assert waited == [44, 39]
    else:
        worker_supervisor._drain_controlled_children(path, {"version": 1}, web, sector, worker)
        assert waited == [44, 39, 32]


def test_unhandled_supervisor_failure_is_durably_logged(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        worker_supervisor,
        "main",
        lambda _argv=None: (_ for _ in ()).throw(RuntimeError("fatal test")),
    )
    monkeypatch.setattr(
        worker_supervisor,
        "log_event",
        lambda _logger, event, **fields: events.append((event, fields)),
    )

    with pytest.raises(RuntimeError, match="fatal test"):
        worker_supervisor._main_with_fatal_observability()

    assert events == [
        (
            "runtime.fatal_exception",
            {
                "level": logging.CRITICAL,
                "stage": "process_fatal",
                "result": "failure",
                "reason_code": "UNHANDLED_SUPERVISOR_EXCEPTION",
                "error": "RuntimeError: fatal test",
            },
        )
    ]


def test_shutdown_request_observability_uses_non_reserved_log_fields(caplog, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.observability.logging.append_lifecycle_event", lambda *_args, **_kwargs: None
    )
    with caplog.at_level(logging.INFO):
        worker_supervisor.log_event(
            worker_supervisor.logger,
            "runtime.shutdown_requested",
            stage="shutdown_request",
            result="success",
            reason_code="LIFECYCLE_CONTROLLER_REQUEST",
            shutdown_method="instance_scoped_file",
            request_operation_id="operation-a",
        )

    record = next(
        item for item in caplog.records if item.getMessage() == "runtime.shutdown_requested"
    )
    assert record.request_operation_id == "operation-a"
