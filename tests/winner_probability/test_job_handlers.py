from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.tables import BackgroundJob, WinnerProcessingRun
from app.services.background_job_service import JobStatus
from app.services.background_worker import CancelRequested, default_job_handlers
from app.services.winner_probability.capture_service import (
    WinnerPredictionCaptureCancelled,
    WinnerPredictionCaptureResult,
)
from app.services.winner_probability.job_handlers import (
    FEATURE_NOT_ENABLED,
    WINNER_COHORT_REFRESH,
    WINNER_MODEL_TRAINING,
    WINNER_OUTCOME_MATURATION,
    WINNER_PREDICTION_CAPTURE,
    WinnerCohortRefreshCancelled,
    WinnerCohortRefreshResult,
    WinnerJobFeatureNotEnabled,
    disabled_winner_job_handler,
    execute_cohort_refresh_job,
    execute_outcome_maturation_job,
    execute_prediction_capture_job,
)
from app.services.winner_probability.outcome_orchestration_service import H5DrainResult
from app.services.winner_probability.outcome_service import (
    OutcomeMaturationCancelled,
    OutcomeMaturationResult,
)


def test_default_job_handlers_register_completed_winner_jobs_only() -> None:
    handlers = default_job_handlers()

    assert WINNER_PREDICTION_CAPTURE in handlers
    assert WINNER_OUTCOME_MATURATION in handlers
    assert WINNER_COHORT_REFRESH in handlers
    assert WINNER_MODEL_TRAINING not in handlers


def test_disabled_winner_handler_fails_closed() -> None:
    db = JobHandlerFakeDb()
    handler = disabled_winner_job_handler(WINNER_MODEL_TRAINING)
    job = BackgroundJob(
        id=3,
        job_type=WINNER_MODEL_TRAINING,
        status=JobStatus.RUNNING,
        payload_json={"run_id": 7},
    )

    with pytest.raises(WinnerJobFeatureNotEnabled, match=FEATURE_NOT_ENABLED):
        handler(db, job)

    assert db.processing_runs[0].process_type == WINNER_MODEL_TRAINING
    assert db.processing_runs[0].status == JobStatus.FAILED
    assert db.processing_runs[0].error_message.startswith(FEATURE_NOT_ENABLED)


def test_capture_job_persists_processing_run_and_counts() -> None:
    db = JobHandlerFakeDb()
    job = _job()
    service = FakeCaptureService(WinnerPredictionCaptureResult(inserted=2, pending_outcomes=20))

    result = execute_prediction_capture_job(db, job, capture_service=service)

    processing_run = db.processing_runs[0]
    assert result["job_type"] == WINNER_PREDICTION_CAPTURE
    assert result["inserted"] == 2
    assert result["pending_outcomes"] == 20
    assert processing_run.process_type == WINNER_PREDICTION_CAPTURE
    assert processing_run.status == JobStatus.COMPLETED
    assert processing_run.config_hash
    assert processing_run.counts_json["inserted"] == 2
    assert processing_run.completed_at is not None
    assert "execution_token" not in processing_run.metadata_json
    assert processing_run.metadata_json["execution_token_hash"]
    assert processing_run.metadata_json["execution_token_suffix"] == "oken-1"


def test_capture_job_marks_partial_when_ticker_failures_are_reported() -> None:
    db = JobHandlerFakeDb()
    job = _job()
    service = FakeCaptureService(WinnerPredictionCaptureResult(inserted=1, failed=1))

    result = execute_prediction_capture_job(db, job, capture_service=service)

    assert result["status"] == JobStatus.PARTIAL
    assert job.status == JobStatus.PARTIAL
    assert db.processing_runs[0].status == JobStatus.PARTIAL


def test_capture_job_observes_cancel_before_next_batch() -> None:
    db = JobHandlerFakeDb(cancel_requested=True)
    job = _job()
    heartbeat_calls = {"count": 0}
    job._heartbeat = lambda: heartbeat_calls.__setitem__("count", heartbeat_calls["count"] + 1)
    service = CancellingCaptureService()

    with pytest.raises(CancelRequested):
        execute_prediction_capture_job(db, job, capture_service=service)

    assert heartbeat_calls["count"] == 1
    assert db.processing_runs[0].status == JobStatus.CANCELLED


def test_outcome_maturation_job_persists_processing_run_and_counts() -> None:
    db = JobHandlerFakeDb()
    job = _job(job_type=WINNER_OUTCOME_MATURATION, payload={"limit": 25})
    service = FakeOutcomeService(OutcomeMaturationResult(processed=2, matured=2))

    result = execute_outcome_maturation_job(db, job, outcome_service=service)

    assert result["job_type"] == WINNER_OUTCOME_MATURATION
    assert result["processed"] == 2
    assert result["matured"] == 2
    assert service.limit == 25
    assert db.processing_runs[0].process_type == WINNER_OUTCOME_MATURATION
    assert db.processing_runs[0].status == JobStatus.COMPLETED


def test_outcome_maturation_job_observes_cancel_before_next_batch() -> None:
    db = JobHandlerFakeDb(cancel_requested=True)
    job = _job(job_type=WINNER_OUTCOME_MATURATION)
    heartbeat_calls = {"count": 0}
    job._heartbeat = lambda: heartbeat_calls.__setitem__("count", heartbeat_calls["count"] + 1)
    service = CancellingOutcomeService()

    with pytest.raises(CancelRequested):
        execute_outcome_maturation_job(db, job, outcome_service=service)

    assert heartbeat_calls["count"] == 1
    assert db.processing_runs[0].status == JobStatus.CANCELLED


def test_material_h5_maturation_enqueues_cohort_refresh(monkeypatch) -> None:
    db = JobHandlerFakeDb()
    job = _job(job_type=WINNER_OUTCOME_MATURATION)
    service = FakeOrchestrationService(
        H5DrainResult(
            due_h5_next_open=3,
            oldest_due_h5_session=None,
            oldest_due_h5_age=None,
            processed_h5=3,
            matured_h5=3,
            pending_h5_after_cycle=0,
            excluded_h5=0,
            failed_h5=0,
            target_stop_matured=3,
            unvisited_h5_after_cycle=0,
            last_successful_full_drain_at="2026-08-14T22:05:00+02:00",
        )
    )
    queued = []
    monkeypatch.setattr(
        "app.services.winner_probability.job_handlers.enqueue_job",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    result = execute_outcome_maturation_job(db, job, orchestration_service=service)

    assert result["matured_h5"] == 3
    assert service.now is None
    assert len(queued) == 1
    assert queued[0][0][1] == WINNER_COHORT_REFRESH
    assert "training_cutoff_at" not in queued[0][0][2]


def test_outcome_maturation_worker_path_receives_injected_time() -> None:
    fixed_now = datetime(2027, 1, 15, 22, 0, tzinfo=UTC)
    db = JobHandlerFakeDb()
    job = _job(job_type=WINNER_OUTCOME_MATURATION)
    service = FakeOrchestrationService(
        H5DrainResult(
            due_h5_next_open=1,
            oldest_due_h5_session=None,
            oldest_due_h5_age=None,
            processed_h5=1,
            matured_h5=1,
            pending_h5_after_cycle=0,
            excluded_h5=0,
            failed_h5=0,
            target_stop_matured=0,
            unvisited_h5_after_cycle=0,
            last_successful_full_drain_at=fixed_now.isoformat(),
        )
    )

    result = execute_outcome_maturation_job(
        db,
        job,
        orchestration_service=service,
        now=fixed_now,
    )

    assert result["status"] == JobStatus.COMPLETED
    assert service.now == fixed_now


def test_cohort_refresh_job_persists_processing_run_and_counts() -> None:
    db = JobHandlerFakeDb()
    job = _job(
        job_type=WINNER_COHORT_REFRESH,
        payload={"outcome_definition_id": "T2_5_S2_0_H5_NEXT_OPEN"},
    )
    service = FakeCohortRefreshService(WinnerCohortRefreshResult(processed=3, duplicate=2))

    result = execute_cohort_refresh_job(db, job, cohort_refresh_service=service)

    assert result["job_type"] == WINNER_COHORT_REFRESH
    assert result["processed"] == 3
    assert result["duplicate"] == 2
    assert service.outcome_definition_id == "T2_5_S2_0_H5_NEXT_OPEN"
    assert db.processing_runs[0].process_type == WINNER_COHORT_REFRESH
    assert db.processing_runs[0].status == JobStatus.COMPLETED
    assert db.processing_runs[0].checkpoint_json["last_completed_phase"] == "cohort_refresh"


def test_cohort_refresh_job_observes_cancel_before_next_prediction() -> None:
    db = JobHandlerFakeDb(cancel_requested=True)
    job = _job(job_type=WINNER_COHORT_REFRESH, payload={})
    heartbeat_calls = {"count": 0}
    job._heartbeat = lambda: heartbeat_calls.__setitem__("count", heartbeat_calls["count"] + 1)
    service = CancellingCohortRefreshService()

    with pytest.raises(CancelRequested):
        execute_cohort_refresh_job(db, job, cohort_refresh_service=service)

    assert heartbeat_calls["count"] == 1
    assert db.processing_runs[0].status == JobStatus.CANCELLED


class FakeCaptureService:
    def __init__(self, result: WinnerPredictionCaptureResult) -> None:
        self.result = result

    def capture_run(self, _db, **kwargs) -> WinnerPredictionCaptureResult:
        assert kwargs["run_id"] == 7
        assert callable(kwargs["should_cancel"])
        assert kwargs["should_cancel"] is not None
        return self.result


class CancellingCaptureService:
    def capture_run(self, _db, **kwargs) -> WinnerPredictionCaptureResult:
        assert kwargs["should_cancel"]()
        raise WinnerPredictionCaptureCancelled("cancelled")


class FakeOutcomeService:
    def __init__(self, result: OutcomeMaturationResult) -> None:
        self.result = result
        self.limit = None

    def process_due_outcomes(self, _db, **kwargs) -> OutcomeMaturationResult:
        self.limit = kwargs["limit"]
        assert callable(kwargs["should_cancel"])
        return self.result


class CancellingOutcomeService:
    def process_due_outcomes(self, _db, **kwargs) -> OutcomeMaturationResult:
        assert kwargs["should_cancel"]()
        raise OutcomeMaturationCancelled("cancelled")


class FakeOrchestrationService:
    def __init__(self, result: H5DrainResult) -> None:
        self.result = result
        self.now = None

    def drain_due(self, _db, **kwargs) -> H5DrainResult:
        assert callable(kwargs["should_cancel"])
        self.now = kwargs["now"]
        return self.result


class FakeCohortRefreshService:
    def __init__(self, result: WinnerCohortRefreshResult) -> None:
        self.result = result
        self.outcome_definition_id = None

    def refresh_cohorts(self, _db, **kwargs) -> WinnerCohortRefreshResult:
        self.outcome_definition_id = kwargs["outcome_definition_id"]
        assert callable(kwargs["should_cancel"])
        return self.result


class CancellingCohortRefreshService:
    def refresh_cohorts(self, _db, **kwargs) -> WinnerCohortRefreshResult:
        assert kwargs["should_cancel"]()
        raise WinnerCohortRefreshCancelled("cancelled")


class JobHandlerFakeDb:
    def __init__(self, *, cancel_requested: bool = False) -> None:
        self.cancel_requested = cancel_requested
        self.processing_runs: list[WinnerProcessingRun] = []
        self.flushes = 0
        self._next_id = 1

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        if isinstance(row, WinnerProcessingRun):
            self.processing_runs.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def scalar(self, _statement):
        return self.cancel_requested


def _job(
    *,
    job_type: str = WINNER_PREDICTION_CAPTURE,
    payload: dict | None = None,
) -> BackgroundJob:
    return BackgroundJob(
        id=11,
        job_type=job_type,
        status=JobStatus.RUNNING,
        payload_json=payload or {"run_id": 7},
        execution_token="token-1",
        lease_owner="worker-a",
    )
