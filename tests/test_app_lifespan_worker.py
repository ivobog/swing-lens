from threading import Event

from fastapi.testclient import TestClient

import app.main as main
from app.settings import Settings


def test_app_lifespan_starts_worker_when_enabled(monkeypatch) -> None:
    started = Event()
    stopped = Event()
    calls = {}

    def fake_run_worker(*, settings, stop_event, **_kwargs):
        calls["settings"] = settings
        calls["stop_event"] = stop_event
        started.set()
        stop_event.wait(timeout=2)
        stopped.set()

    monkeypatch.setattr(main, "run_worker", fake_run_worker)
    settings = Settings(
        _env_file=None,
        job_worker_enabled=True,
        job_worker_id="test-worker",
    )

    with TestClient(main.create_app(settings)):
        assert started.wait(timeout=1)

    assert stopped.wait(timeout=1)
    assert calls["settings"] is settings
    assert calls["stop_event"].is_set()


def test_app_lifespan_does_not_start_worker_when_disabled(monkeypatch) -> None:
    def fake_run_worker(**_kwargs):
        raise AssertionError("worker should not start")

    monkeypatch.setattr(main, "run_worker", fake_run_worker)
    settings = Settings(_env_file=None, job_worker_enabled=False)

    with TestClient(main.create_app(settings)):
        pass
