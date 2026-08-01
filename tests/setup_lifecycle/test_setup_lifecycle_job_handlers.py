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
    SETUP_ALERT_REBUILD,
    SETUP_LIFECYCLE_DAILY_MAINTENANCE,
    SETUP_LIFECYCLE_EVALUATE_RUN,
    SETUP_LIFECYCLE_REPAIR_TICKER,
    SETUP_LIFECYCLE_REPLAY,
    execute_alert_rebuild_job,
    execute_daily_maintenance_job,
    execute_evaluate_run_job,
    execute_repair_ticker_job,
    execute_replay_job,
)


def test_setup_lifecycle_job_handler_is_registered_by_default_worker() -> None:
    handlers = default_job_handlers()

    assert SETUP_LIFECYCLE_EVALUATE_RUN in handlers
    assert SETUP_LIFECYCLE_REPLAY in handlers
    assert SETUP_LIFECYCLE_REPAIR_TICKER in handlers
    assert SETUP_LIFECYCLE_DAILY_MAINTENANCE in handlers
    assert SETUP_ALERT_REBUILD in handlers


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


def test_phase_11_job_handlers_delegate_to_services() -> None:
    replay = execute_replay_job(
        db=object(),
        job=_job(job_type=SETUP_LIFECYCLE_REPLAY, payload={"ticker": "MSFT"}),
        replay_service=FakeReplayService(),
    )
    repair = execute_repair_ticker_job(
        db=object(),
        job=_job(job_type=SETUP_LIFECYCLE_REPAIR_TICKER, payload={"ticker": "MSFT"}),
        maintenance_service=FakeMaintenanceService(),
    )
    daily = execute_daily_maintenance_job(
        db=object(),
        job=_job(
            job_type=SETUP_LIFECYCLE_DAILY_MAINTENANCE,
            payload={"as_of_date": "2026-08-01"},
        ),
        maintenance_service=FakeMaintenanceService(),
    )
    rebuild = execute_alert_rebuild_job(
        db=object(),
        job=_job(job_type=SETUP_ALERT_REBUILD, payload={"ticker": "MSFT"}),
        maintenance_service=FakeMaintenanceService(),
    )

    assert replay["job_type"] == SETUP_LIFECYCLE_REPLAY
    assert repair["repaired"] == 1
    assert daily["aged"] == 2
    assert rebuild["alerts_created"] == 3


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


class FakeReplayService:
    def replay(self, _db, request):
        return {
            "persisted": request.persist,
            "snapshot_count": 0,
            "proposed": [],
            "comparison": {},
        }


class FakeMaintenanceService:
    def repair_ticker(self, *_args, **_kwargs):
        return _result(repaired=1)

    def daily_maintenance(self, *_args, **_kwargs):
        return _result(aged=2)

    def rebuild_alerts(self, *_args, **_kwargs):
        return _result(alerts_created=3)


def _result(**values):
    payload = {
        "status": "COMPLETED",
        "aged": 0,
        "expired": 0,
        "repaired": 0,
        "alerts_created": 0,
        "alerts_suppressed": 0,
        "skipped": 0,
        "warnings": [],
    }
    payload.update(values)
    return type(
        "Result",
        (),
        {
            "status": payload["status"],
            "as_dict": lambda self: payload,
        },
    )()


def _job(
    payload: dict,
    *,
    job_type: str = SETUP_LIFECYCLE_EVALUATE_RUN,
) -> BackgroundJob:
    return BackgroundJob(
        id=1,
        job_type=job_type,
        status=JobStatus.RUNNING,
        payload_json=payload,
    )
