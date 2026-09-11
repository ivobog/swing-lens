from threading import enumerate as enumerate_threads

from fastapi.testclient import TestClient

import app.main as main
from app.settings import Settings


def test_web_lifespan_never_owns_a_worker_supervisor() -> None:
    settings = Settings(
        _env_file=None,
        durable_worker_process_enabled=True,
        job_worker_id="test-worker",
    )

    with TestClient(main.create_app(settings)):
        assert not any(thread.name == "swinglens-test-worker" for thread in enumerate_threads())


def test_app_lifespan_does_not_start_worker_when_disabled() -> None:
    settings = Settings(_env_file=None, job_worker_enabled=False)

    with TestClient(main.create_app(settings)):
        pass
