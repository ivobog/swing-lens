from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app import worker_supervisor
from app.models.tables import BackgroundWorker
from app.services import process_identity
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
    )


@pytest.mark.parametrize("return_code", [0, 17])
def test_exited_worker_launcher_is_replaced_automatically(monkeypatch, return_code) -> None:
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


def test_launcher_pid_mismatch_uses_registered_worker_identity(monkeypatch) -> None:
    worker = _worker(pid=222)
    launcher = FakeProcess(111, None)
    associated = []
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: worker)
    monkeypatch.setattr(worker_supervisor, "_registered_worker_process_alive", lambda _row: True)
    monkeypatch.setattr(worker_supervisor, "_safe_memory_status", lambda _row: "OK")
    monkeypatch.setattr(worker_supervisor, "_fence_no_progress", lambda *_args: [])
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
    monkeypatch.setattr(worker_supervisor, "_safe_memory_status", lambda _row: "OK")
    monkeypatch.setattr(worker_supervisor, "_fence_no_progress", lambda *_args: [71])
    monkeypatch.setattr(
        worker_supervisor,
        "_terminate_worker_instance",
        lambda *_args: events.append("terminated"),
    )
    monkeypatch.setattr(
        worker_supervisor, "_retire_worker_registration", lambda _row: events.append("retired")
    )
    monkeypatch.setattr(worker_supervisor, "_requeue", lambda ids: events.append(("requeued", ids)))

    result = worker_supervisor._supervise_once(
        worker_id="worker-a", queues="interactive,broker,background", child=None
    )

    assert result is None
    assert events == ["terminated", "retired", ("requeued", [71])]


def test_stale_registration_is_retired_and_supervisor_continues(monkeypatch) -> None:
    worker = _worker(pid=99999)
    replacement = FakeProcess(300, None)
    events = []
    monkeypatch.setattr(worker_supervisor, "get_settings", _settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: worker)
    monkeypatch.setattr(worker_supervisor, "_registered_worker_process_alive", lambda _row: False)
    monkeypatch.setattr(worker_supervisor, "_fence_worker", lambda *_args: [88])
    monkeypatch.setattr(
        worker_supervisor, "_retire_worker_registration", lambda _row: events.append("retired")
    )
    monkeypatch.setattr(worker_supervisor, "_requeue", lambda ids: events.append(("requeued", ids)))
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
