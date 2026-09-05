from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.tables import BackgroundJob, WinnerProcessingRun
from app.services.background_job_service import JobStatus
from app.services.background_worker import CancelRequested, JobDeferred, default_job_handlers
from app.services.operational_metrics import operational_metrics
from app.services.winner_probability.capture_service import (
    WinnerPredictionCaptureCancelled,
    WinnerPredictionCaptureResult,
)
from app.services.winner_probability.job_handlers import (
    FEATURE_NOT_ENABLED,
    MAX_MATURATION_CONTINUATION_DEPTH,
    WINNER_COHORT_REFRESH,
    WINNER_MATURATION_WORKFLOW_KEY,
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


def test_zero_progress_retry_deferred_slice_never_enqueues_immediate_child(monkeypatch) -> None:
    operational_metrics.reset()
    fixed_now = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)
    db = JobHandlerFakeDb()
    job = _job(job_type=WINNER_OUTCOME_MATURATION)
    service = FakeOrchestrationService(
        _drain_result(
            due_total=4227,
            retry_eligible_now=0,
            retry_deferred=4227,
            processed_h5=0,
            pending_h5_after_cycle=4227,
            unvisited_h5_after_cycle=4227,
            unvisited_total=4227,
            eligible_remaining=0,
            earliest_retry_not_before="2026-09-05T11:15:00+00:00",
        )
    )
    queued = []
    monkeypatch.setattr(
        "app.services.winner_probability.job_handlers.enqueue_job",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    with pytest.raises(JobDeferred, match="RETRY_DEFERRED") as deferred:
        execute_outcome_maturation_job(db, job, orchestration_service=service, now=fixed_now)

    assert deferred.value.delay_seconds == 900
    assert queued == []
    assert db.processing_runs[0].terminal_reason_code == "RETRY_DEFERRED"
    assert db.processing_runs[0].counts_json["continuation_decision"] == "DEFER_SAME_JOB"
    assert operational_metrics.total("winner_maturation_zero_progress_total") == 1
    assert operational_metrics.total("winner_maturation_due_total") == 4227
    assert operational_metrics.total("winner_maturation_retry_eligible") == 0
    assert operational_metrics.total("winner_maturation_retry_deferred") == 4227


def test_partial_with_progress_enqueues_exactly_one_child_with_stable_lineage(monkeypatch) -> None:
    db = JobHandlerFakeDb()
    job = _job(job_type=WINNER_OUTCOME_MATURATION)
    job.root_job_id = job.id
    job.workflow_key = WINNER_MATURATION_WORKFLOW_KEY
    job.continuation_depth = 2
    service = FakeOrchestrationService(
        _drain_result(
            due_total=1200,
            retry_eligible_now=1200,
            processed_h5=500,
            matured_h5=500,
            pending_h5_after_cycle=700,
            unvisited_h5_after_cycle=700,
            unvisited_total=700,
            eligible_remaining=700,
        )
    )
    queued = []
    monkeypatch.setattr(
        "app.services.winner_probability.job_handlers.enqueue_job",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    result = execute_outcome_maturation_job(db, job, orchestration_service=service)

    maturation_children = [item for item in queued if item[0][1] == WINNER_OUTCOME_MATURATION]
    assert len(maturation_children) == 1
    _, kwargs = maturation_children[0]
    assert kwargs["workflow_key"] == WINNER_MATURATION_WORKFLOW_KEY
    assert kwargs["root_job_id"] == job.id
    assert kwargs["parent_job_id"] == job.id
    assert kwargs["continuation_depth"] == 3
    assert kwargs["trigger_source"] == "CONTINUATION"
    assert kwargs["single_flight_workflow"] is True
    assert result["continuation_decision"] == "ENQUEUE_CONTINUATION"


def test_progress_with_only_retry_deferred_rows_does_not_enqueue_child(monkeypatch) -> None:
    db = JobHandlerFakeDb()
    job = _job(job_type=WINNER_OUTCOME_MATURATION)
    service = FakeOrchestrationService(
        _drain_result(
            due_total=3,
            retry_eligible_now=1,
            retry_deferred=2,
            processed_h5=1,
            matured_h5=0,
            pending_h5_after_cycle=3,
            unvisited_h5_after_cycle=2,
            unvisited_total=2,
            eligible_remaining=0,
            earliest_retry_not_before="2026-09-05T11:15:00+00:00",
        )
    )
    queued = []
    monkeypatch.setattr(
        "app.services.winner_probability.job_handlers.enqueue_job",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    with pytest.raises(JobDeferred, match="RETRY_DEFERRED"):
        execute_outcome_maturation_job(db, job, orchestration_service=service)

    assert queued == []


def test_processed_but_zero_matured_is_real_progress_and_can_continue(monkeypatch) -> None:
    db = JobHandlerFakeDb()
    job = _job(job_type=WINNER_OUTCOME_MATURATION)
    service = FakeOrchestrationService(
        _drain_result(
            due_total=600,
            retry_eligible_now=600,
            processed_h5=500,
            matured_h5=0,
            pending_h5_after_cycle=600,
            unvisited_h5_after_cycle=100,
            unvisited_total=100,
            eligible_remaining=100,
        )
    )
    queued = []
    monkeypatch.setattr(
        "app.services.winner_probability.job_handlers.enqueue_job",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    result = execute_outcome_maturation_job(db, job, orchestration_service=service)

    assert len([item for item in queued if item[0][1] == WINNER_OUTCOME_MATURATION]) == 1
    assert result["continuation_decision"] == "ENQUEUE_CONTINUATION"


def test_continuation_depth_circuit_breaker_stops_child(monkeypatch) -> None:
    db = JobHandlerFakeDb()
    job = _job(job_type=WINNER_OUTCOME_MATURATION)
    job.root_job_id = job.id
    job.continuation_depth = MAX_MATURATION_CONTINUATION_DEPTH
    service = FakeOrchestrationService(
        _drain_result(
            due_total=2,
            retry_eligible_now=2,
            processed_h5=1,
            matured_h5=1,
            pending_h5_after_cycle=1,
            unvisited_h5_after_cycle=1,
            unvisited_total=1,
            eligible_remaining=1,
        )
    )
    queued = []
    monkeypatch.setattr(
        "app.services.winner_probability.job_handlers.enqueue_job",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    result = execute_outcome_maturation_job(db, job, orchestration_service=service)

    assert queued == []
    assert result["continuation_decision"] == "STOP_CONTINUATION_LIMIT"
    assert db.processing_runs[0].terminal_reason_code == "CONTINUATION_LIMIT_REACHED"


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
        kwargs["lease_guard"]()
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


def _drain_result(**overrides) -> H5DrainResult:
    values = {
        "due_h5_next_open": 0,
        "oldest_due_h5_session": None,
        "oldest_due_h5_age": None,
        "processed_h5": 0,
        "matured_h5": 0,
        "pending_h5_after_cycle": 0,
        "excluded_h5": 0,
        "failed_h5": 0,
        "target_stop_matured": 0,
        "unvisited_h5_after_cycle": 0,
        "last_successful_full_drain_at": None,
    }
    values.update(overrides)
    return H5DrainResult(**values)
