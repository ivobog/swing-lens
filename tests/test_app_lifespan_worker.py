from threading import enumerate as enumerate_threads

from fastapi.testclient import TestClient

import app.main as main
from app.settings import Settings


def test_app_lifespan_maintains_out_of_process_supervisor_when_enabled(monkeypatch) -> None:
    events: list[str] = []

    class Manager:
        def __init__(self, settings):
            assert settings.job_worker_id == "test-worker"

        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    monkeypatch.setattr(main, "SupervisorProcessManager", Manager)
    settings = Settings(
        _env_file=None,
        job_worker_enabled=True,
        job_worker_id="test-worker",
    )

    with TestClient(main.create_app(settings)):
        assert not any(thread.name == "swinglens-test-worker" for thread in enumerate_threads())

    assert events == ["start", "stop"]


def test_app_lifespan_does_not_start_worker_when_disabled() -> None:
    settings = Settings(_env_file=None, job_worker_enabled=False)

    with TestClient(main.create_app(settings)):
        pass
