from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.tables import BackgroundJob, BackgroundJobEnqueueAttempt
from app.observability.correlation import (
    CorrelationMiddleware,
    current_causality,
    root_action_scope,
    worker_job_scope,
)
from app.services.background_job_service import enqueue_job


class FakeDb:
    def __init__(self) -> None:
        self.jobs: list[BackgroundJob] = []
        self.attempts: list[BackgroundJobEnqueueAttempt] = []

    def add(self, row) -> None:
        if isinstance(row, BackgroundJob):
            row.id = len(self.jobs) + 1
            self.jobs.append(row)
        elif isinstance(row, BackgroundJobEnqueueAttempt):
            row.id = len(self.attempts) + 1
            self.attempts.append(row)

    def flush(self) -> None:
        return None


def test_http_root_is_propagated_and_cleaned() -> None:
    app = FastAPI()
    app.add_middleware(CorrelationMiddleware)

    @app.get("/root")
    def root() -> dict:
        context = current_causality()
        assert context is not None
        return {
            "request_id": context.request_id,
            "root_correlation_id": context.root_correlation_id,
        }

    response = TestClient(app).get(
        "/root",
        headers={"X-Request-ID": "request-fixture", "X-Root-Correlation-ID": "root-fixture"},
    )

    assert response.json()["root_correlation_id"] == "root-fixture"
    assert response.headers["x-root-correlation-id"] == "root-fixture"
    assert current_causality() is None


def test_root_parent_children_and_coalesced_attempt_are_durable(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.background_job_service._database_has_causality_columns", lambda _db: True
    )
    db = FakeDb()
    with root_action_scope("HTTP", "POST /pipeline", request_id="request-1"):
        parent = enqueue_job(db, "FULL_PIPELINE", {}, request_key="parent")
        with worker_job_scope(parent):
            child = enqueue_job(db, "CERI_FEATURE_BATCH", {}, request_key="child")
            authoritative = enqueue_job(db, "CERI_FEATURE_BATCH", {}, request_key="child")

    assert parent.root_correlation_id == child.root_correlation_id
    assert child.parent_job_id == parent.id
    assert child.triggered_by_job_id == parent.id
    assert authoritative is child
    assert [row.result for row in db.attempts] == ["CREATED", "CREATED", "COALESCED"]
    assert db.attempts[-1].authoritative_job_id == child.id
    assert db.attempts[-1].parent_job_id == parent.id
