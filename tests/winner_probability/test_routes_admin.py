from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import get_db
from app.main import create_app
from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.winner_probability.job_handlers import WINNER_PREDICTION_CAPTURE
from app.settings import Settings


def test_capture_admin_endpoint_queues_prediction_capture_job() -> None:
    db = AdminRouteFakeDb(run_exists=True)
    app = create_app(
        Settings(
            _env_file=None,
            job_worker_enabled=False,
            winner_probability_admin_enabled=True,
        )
    )
    app.dependency_overrides[get_db] = lambda: db

    response = TestClient(app).post("/api/winner-probability/runs/7/capture")

    assert response.status_code == 200
    assert response.json() == {
        "job_id": 1,
        "job_type": WINNER_PREDICTION_CAPTURE,
        "status": JobStatus.QUEUED,
        "run_id": 7,
    }
    assert db.jobs[0].job_type == WINNER_PREDICTION_CAPTURE
    assert db.jobs[0].payload_json == {"run_id": 7}
    assert db.jobs[0].related_run_id == 7
    assert db.commits == 1


def test_capture_admin_endpoint_is_hidden_when_disabled() -> None:
    db = AdminRouteFakeDb(run_exists=True)
    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    app.dependency_overrides[get_db] = lambda: db

    response = TestClient(app).post("/api/winner-probability/runs/7/capture")

    assert response.status_code == 404
    assert db.jobs == []


def test_capture_admin_endpoint_rejects_unknown_run() -> None:
    db = AdminRouteFakeDb(run_exists=False)
    app = create_app(
        Settings(
            _env_file=None,
            job_worker_enabled=False,
            winner_probability_admin_enabled=True,
        )
    )
    app.dependency_overrides[get_db] = lambda: db

    response = TestClient(app).post("/api/winner-probability/runs/404/capture")

    assert response.status_code == 404
    assert db.jobs == []


class AdminRouteFakeDb:
    def __init__(self, *, run_exists: bool) -> None:
        self.run_exists = run_exists
        self.jobs: list[BackgroundJob] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self._next_id = 1

    def scalar(self, _statement):
        return 7 if self.run_exists else None

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        if isinstance(row, BackgroundJob):
            self.jobs.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1
