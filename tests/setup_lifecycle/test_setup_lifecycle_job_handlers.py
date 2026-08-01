from __future__ import annotations

import pytest

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.background_worker import CancelRequested, default_job_handlers
from app.services.setup_lifecycle.evaluation_service import (
    SetupLifecycleEvaluationCancelled,
    SetupLifecycleEvaluationResult,
)
from app.services.setup_lifecycle.job_handlers import (
    SETUP_LIFECYCLE_EVALUATE_RUN,
    execute_evaluate_run_job,
)


def test_setup_lifecycle_job_handler_is_registered_by_default_worker() -> None:
    handlers = default_job_handlers()

    assert SETUP_LIFECYCLE_EVALUATE_RUN in handlers


def test_execute_evaluate_run_job_returns_result_payload() -> None:
    job = _job(payload={"run_id": 7, "requester": "tester"})
    service = FakeEvaluationService(
        SetupLifecycleEvaluationResult(
            evaluation_run_id=11,
            status=JobStatus.COMPLETED,
            snapshots_captured=2,
            canonical_snapshots=2,
        )
    )

    result = execute_evaluate_run_job(db=object(), job=job, evaluation_service=service)

    assert result["job_type"] == SETUP_LIFECYCLE_EVALUATE_RUN
    assert result["run_id"] == 7
    assert result["evaluation_run_id"] == 11
    assert result["snapshots_captured"] == 2
    assert result["canonical_snapshots"] == 2
    assert service.calls == [(7, "tester")]


def test_execute_evaluate_run_job_marks_partial_when_result_has_failures() -> None:
    job = _job(payload={"run_id": 7})
    service = FakeEvaluationService(
        SetupLifecycleEvaluationResult(
            evaluation_run_id=11,
            status=JobStatus.PARTIAL,
            failed=1,
        )
    )

    execute_evaluate_run_job(db=object(), job=job, evaluation_service=service)

    assert job.status == JobStatus.PARTIAL


def test_execute_evaluate_run_job_translates_cancellation() -> None:
    job = _job(payload={"run_id": 7})

    with pytest.raises(CancelRequested):
        execute_evaluate_run_job(
            db=object(),
            job=job,
            evaluation_service=FakeCancellingEvaluationService(),
        )


def test_execute_evaluate_run_job_requires_run_id() -> None:
    job = _job(payload={})

    with pytest.raises(ValueError, match="payload is missing run_id"):
        execute_evaluate_run_job(db=object(), job=job, evaluation_service=FakeEvaluationService())


class FakeEvaluationService:
    def __init__(self, result: SetupLifecycleEvaluationResult | None = None) -> None:
        self.result = result or SetupLifecycleEvaluationResult(
            evaluation_run_id=1,
            status=JobStatus.COMPLETED,
        )
        self.calls = []

    def evaluate_run(self, _db, run_id, *, requester=None, should_cancel=None):
        self.calls.append((run_id, requester))
        return self.result


class FakeCancellingEvaluationService:
    def evaluate_run(self, *_args, **_kwargs):
        raise SetupLifecycleEvaluationCancelled("cancelled")


def _job(payload: dict) -> BackgroundJob:
    return BackgroundJob(
        id=1,
        job_type=SETUP_LIFECYCLE_EVALUATE_RUN,
        status=JobStatus.RUNNING,
        payload_json=payload,
    )
