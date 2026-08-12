from __future__ import annotations

from threading import Event

import pytest

import app.worker as worker_cli


def test_worker_cli_parses_id_and_queue_allowlist() -> None:
    args = worker_cli.parse_args(
        ["--worker-id", "swinglens-main", "--queues", "broker,interactive"]
    )

    assert args.worker_id == "swinglens-main"
    assert args.queues == ("interactive", "broker")


def test_worker_cli_rejects_unknown_queue() -> None:
    with pytest.raises(SystemExit):
        worker_cli.parse_args(["--queues", "interactive,cpu"])


def test_worker_cli_installs_shutdown_event_and_runs_external_worker(monkeypatch) -> None:
    registered_handlers = []
    calls = {}

    monkeypatch.setattr(
        worker_cli.signal,
        "signal",
        lambda name, handler: registered_handlers.append((name, handler)),
    )

    def fake_run_worker(*, worker_id, queues, stop_event):
        calls.update(worker_id=worker_id, queues=queues, stop_event=stop_event)

    monkeypatch.setattr(worker_cli, "run_worker", fake_run_worker)

    worker_cli.main(["--worker-id", "worker-a", "--queues", "background"])

    assert calls["worker_id"] == "worker-a"
    assert calls["queues"] == ("background",)
    assert isinstance(calls["stop_event"], Event)
    assert registered_handlers
