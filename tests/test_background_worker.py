import logging
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.background_worker import (
    CancelRequested,
    JobDeferred,
    PreClaimInfrastructureError,
    _execute_full_pipeline_job,
    _run_worker_control_loop,
    execute_job,
    log_worker_startup_configuration,
    run_worker_once,
)
from app.services.pipeline_prerequisites import CeriBootstrapRequiredError
from app.settings import RuntimeMode, SecDocumentIncrementalMode, Settings


@pytest.fixture(autouse=True)
def _stub_worker_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.background_worker.recover_abandoned_jobs_for_worker",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.background_worker.register_worker",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.background_worker.heartbeat_worker",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.background_worker.mark_worker_infrastructure_healthy",
        lambda *_args, **_kwargs: None,
    )


def test_execute_job_dispatches_to_registered_handler() -> None:
    job = BackgroundJob(id=1, job_type="TEST_JOB", status=JobStatus.RUNNING)
    calls = {}

    def handler(db, handled_job):
        calls["job"] = handled_job
        return {"handled": True}

    result = execute_job(
        db=object(),
        job=job,
        handlers={"TEST_JOB": handler},
    )

    assert result == {"handled": True}
    assert calls["job"] is job


def test_worker_startup_warns_when_provider_ingest_uses_sec_off(caplog) -> None:
    class Db:
        def scalar(self, _statement):
            return "0085_ceri_change_scope"

    settings = Settings(
        _env_file=None,
        ceri_provider_ingest_enabled=True,
        sec_document_incremental_mode=SecDocumentIncrementalMode.OFF,
        winner_probability_auto_maturation_enabled=True,
        winner_probability_auto_cohort_refresh_enabled=False,
        winner_cohort_refresh_v2_enabled=True,
    )

    with caplog.at_level(logging.CRITICAL):
        summary = log_worker_startup_configuration(
            Db(), settings=settings, worker_id="worker-test"
        )

    assert summary["sec_incremental_mode"] == "OFF"
    assert summary["sec_processor_signature"].startswith("sec-guidance:")
    assert summary["worker_id"] == "worker-test"
    assert summary["worker_process_id"] > 0
    assert summary["worker_started_at"].endswith("+00:00")
    assert summary["winner_probability_auto_maturation_enabled"] is True
    assert summary["winner_probability_auto_cohort_refresh_enabled"] is False
    assert summary["winner_cohort_refresh_v2_enabled"] is True
    assert "legacy repeated-download path" in caplog.text


def test_execute_job_exposes_heartbeat_to_handler_until_it_returns() -> None:
    job = BackgroundJob(id=1, job_type="TEST_JOB", status=JobStatus.RUNNING)
    calls = {"heartbeat": 0}

    def heartbeat() -> None:
        calls["heartbeat"] += 1

    def handler(db, handled_job):
        handled_job._heartbeat()
        return {"handled": True}

    result = execute_job(
        db=object(),
        job=job,
        handlers={"TEST_JOB": handler},
        heartbeat=heartbeat,
    )

    assert result == {"handled": True}
    assert calls["heartbeat"] == 1
    assert not hasattr(job, "_heartbeat")


def test_full_pipeline_control_callbacks_use_detached_job_on_independent_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    db = Session(engine)
    pipeline = SimpleNamespace(id=17)
    job = BackgroundJob(
        id=23,
        job_type="FULL_PIPELINE",
        status=JobStatus.RUNNING,
        execution_token="token-23",
        worker_id="worker-1",
        payload_json={"pipeline_run_id": 17},
    )
    db.get = lambda *_args, **_kwargs: pipeline  # type: ignore[method-assign]
    heartbeats: list[tuple[Session, BackgroundJob]] = []
    progress_updates: list[tuple[Session, dict]] = []

    monkeypatch.setattr(
        "app.services.scope_refresh_adoption.validate_same_authority",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.market_calculation_context_service.validate_pipeline_job_market_context",
        lambda *_args, **_kwargs: SimpleNamespace(context_id=31),
    )
    monkeypatch.setattr(
        "app.services.background_worker.get_settings",
        lambda: SimpleNamespace(job_stale_after_seconds=900),
    )
    monkeypatch.setattr(
        "app.services.background_worker.heartbeat_job",
        lambda control_db, control_job, **_kwargs: heartbeats.append(
            (control_db, control_job)
        ),
    )
    monkeypatch.setattr(
        "app.services.background_worker.record_job_progress",
        lambda control_db, **progress: progress_updates.append((control_db, progress)),
    )
    monkeypatch.setattr(
        "app.services.background_job_service.is_cancel_requested",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "app.services.domain_write_fence.current_fenced_domain_session",
        lambda *_args, **_kwargs: None,
    )

    def execute_pipeline(
        *_args, should_cancel, progress_callback, lease_guard, **_kwargs
    ):
        assert should_cancel() is False
        progress_callback(db, stage="SCORING_FUNDAMENTALS", current_item=None)
        lease_guard()
        pipeline.current_step = "SCORING_TECHNICALS"
        progress_callback(
            db,
            stage="SCORING_TECHNICALS",
            processed=10,
            total=25,
        )
        assert should_cancel() is False
        return SimpleNamespace(status="COMPLETED")

    monkeypatch.setattr(
        "app.services.pipeline_executor.execute_full_pipeline", execute_pipeline
    )

    try:
        assert _execute_full_pipeline_job(db, job) == {"status": "COMPLETED"}
    finally:
        db.close()
        engine.dispose()

    assert len(heartbeats) == 3
    assert sum(control_db is db for control_db, _control_job in heartbeats) == 1
    assert sum(control_db is not db for control_db, _control_job in heartbeats) == 2
    assert all(control_job is not job for _control_db, control_job in heartbeats)
    assert {control_job.id for _control_db, control_job in heartbeats} == {job.id}
    assert progress_updates[0][0] is db
    assert progress_updates[1][0] is not db
    assert progress_updates[1][1]["processed"] == 10


def test_cancellation_can_stop_handler_before_next_bounded_batch() -> None:
    job = BackgroundJob(id=1, job_type="TEST_JOB", status=JobStatus.RUNNING)
    batches: list[int] = []

    def handler(db, handled_job):
        for batch_number in range(3):
            handled_job._heartbeat()
            if batch_number == 1:
                raise CancelRequested
            batches.append(batch_number)

    with pytest.raises(CancelRequested):
        execute_job(
            db=object(),
            job=job,
            handlers={"TEST_JOB": handler},
            heartbeat=lambda: None,
        )

    assert batches == [0]


def test_execute_job_rejects_unsupported_job_type() -> None:
    job = BackgroundJob(id=1, job_type="UNKNOWN", status=JobStatus.RUNNING)

    with pytest.raises(ValueError, match="Unsupported job type: UNKNOWN"):
        execute_job(db=object(), job=job, handlers={})


def test_worker_rolls_back_failed_transaction_before_marking_job_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = BackgroundJob(
        id=1,
        job_type="TEST_JOB",
        status=JobStatus.RUNNING,
        execution_token="token",
    )
    db = FakeWorkerDb()
    calls: list[str] = []

    monkeypatch.setattr(
        "app.services.background_worker.recover_stale_jobs",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.background_worker.claim_next_job",
        lambda *_args, **_kwargs: job,
    )
    monkeypatch.setattr(
        "app.services.background_worker.heartbeat_job",
        lambda *_args, **_kwargs: calls.append("heartbeat"),
    )

    def mark_failed(_db, _job, _exc, *, execution_token):
        assert _db.rollback_count == 1
        assert execution_token == "token"
        calls.append("marked_failed")

    monkeypatch.setattr(
        "app.services.background_worker.mark_job_failed_or_retry",
        mark_failed,
    )

    ran = run_worker_once(
        worker_id="worker-a",
        stale_after_seconds=60,
        session_factory=lambda: db,
        handlers={"TEST_JOB": lambda *_args: (_ for _ in ()).throw(RuntimeError("boom"))},
    )

    assert ran is True
    assert calls == ["heartbeat", "marked_failed"]
    # Registration/recovery, durable claim, heartbeat, and failure publication.
    assert db.commit_count == 4
    assert db.closed is True


def test_worker_defers_barrier_without_consuming_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    job = BackgroundJob(
        id=1,
        job_type="TEST_JOB",
        status=JobStatus.RUNNING,
        execution_token="token",
        retry_count=2,
    )
    db = FakeWorkerDb()
    calls = []
    monkeypatch.setattr(
        "app.services.background_worker.recover_stale_jobs",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.background_worker.claim_next_job",
        lambda *_args, **_kwargs: job,
    )
    monkeypatch.setattr(
        "app.services.background_worker.heartbeat_job",
        lambda *_args, **_kwargs: None,
    )

    def mark_deferred(_db, _job, *, delay, reason, execution_token):
        calls.append((delay.total_seconds(), reason, execution_token, _job.retry_count))

    monkeypatch.setattr(
        "app.services.background_worker.mark_job_deferred",
        mark_deferred,
    )

    ran = run_worker_once(
        worker_id="worker-a",
        stale_after_seconds=60,
        session_factory=lambda: db,
        handlers={
            "TEST_JOB": lambda *_args: (_ for _ in ()).throw(
                JobDeferred("upstream pending", delay_seconds=7)
            )
        },
    )

    assert ran is True
    assert calls == [(7.0, "upstream pending", "token", 2)]
    assert db.rollback_count == 0


def test_worker_blocks_deterministic_prerequisite_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = BackgroundJob(
        id=1,
        job_type="TEST_JOB",
        status=JobStatus.RUNNING,
        execution_token="token",
        retry_count=2,
        max_retries=3,
    )
    db = FakeWorkerDb()
    calls = []
    monkeypatch.setattr(
        "app.services.background_worker.recover_stale_jobs",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.background_worker.claim_next_job",
        lambda *_args, **_kwargs: job,
    )
    monkeypatch.setattr(
        "app.services.background_worker.heartbeat_job",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.background_worker.mark_job_failed_or_retry",
        lambda *_args, **_kwargs: calls.append("retry"),
    )

    def mark_blocked(_db, _job, _exc, **kwargs):
        calls.append((kwargs["reason_code"], _job.retry_count))

    monkeypatch.setattr("app.services.background_worker.mark_job_blocked", mark_blocked)

    ran = run_worker_once(
        worker_id="worker-a",
        stale_after_seconds=60,
        session_factory=lambda: db,
        handlers={
            "TEST_JOB": lambda *_args: (_ for _ in ()).throw(
                CeriBootstrapRequiredError("bootstrap required")
            )
        },
    )

    assert ran is True
    assert calls == [("SEC_BOOTSTRAP_REQUIRED", 2)]
    assert db.rollback_count == 1


class FakeWorkerDb:
    def __init__(self, *, commit_failure: tuple[int, Exception] | None = None) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self.invalidate_count = 0
        self.closed = False
        self.commit_failure = commit_failure

    def commit(self) -> None:
        self.commit_count += 1
        if self.commit_failure is not None and self.commit_count == self.commit_failure[0]:
            raise self.commit_failure[1]

    def rollback(self) -> None:
        self.rollback_count += 1

    def invalidate(self) -> None:
        self.invalidate_count += 1

    def close(self) -> None:
        self.closed = True


class DriverFailure(Exception):
    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def _prepare_preclaim_failure(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    monkeypatch.setattr(
        "app.services.background_worker.recover_stale_jobs",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.background_worker.claim_next_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(exc),
    )


def test_preclaim_postgres_out_of_memory_is_contained_without_job_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeWorkerDb()
    job = BackgroundJob(id=7, job_type="TEST_JOB", status=JobStatus.QUEUED)
    failure = OperationalError(
        "select next job",
        {},
        DriverFailure("out of memory", sqlstate="53200"),
    )
    _prepare_preclaim_failure(monkeypatch, failure)
    mutation_calls: list[str] = []
    monkeypatch.setattr(
        "app.services.background_worker.mark_job_failed_or_retry",
        lambda *_args, **_kwargs: mutation_calls.append("job_failed"),
    )

    with pytest.raises(PreClaimInfrastructureError) as caught:
        run_worker_once(
            worker_id="worker-a",
            worker_instance_id="instance-a",
            stale_after_seconds=60,
            session_factory=lambda: db,
            handlers={},
            certification_mode=True,
            certification_session_id="cert-a",
        )

    assert caught.value.reason_code == "POSTGRES_OUT_OF_MEMORY"
    assert db.rollback_count == 1
    assert db.invalidate_count == 1
    assert db.closed is True
    assert mutation_calls == []
    assert job.status == JobStatus.QUEUED


def test_preclaim_connection_reset_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeWorkerDb()
    failure = OperationalError(
        "select next job",
        {},
        DriverFailure("server closed the connection unexpectedly", sqlstate="08006"),
    )
    _prepare_preclaim_failure(monkeypatch, failure)

    with pytest.raises(PreClaimInfrastructureError) as caught:
        run_worker_once(
            worker_id="worker-a",
            stale_after_seconds=60,
            session_factory=lambda: db,
            handlers={},
        )

    assert caught.value.reason_code == "DATABASE_CONNECTION_FAILURE"
    assert db.invalidate_count == 1


def test_claim_commit_infrastructure_failure_is_contained_before_durable_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OperationalError(
        "commit claim",
        {},
        DriverFailure("out of memory", sqlstate="53200"),
    )
    db = FakeWorkerDb(commit_failure=(2, failure))
    job = BackgroundJob(
        id=8,
        job_type="TEST_JOB",
        status=JobStatus.RUNNING,
        execution_token="uncommitted-token",
    )
    mutation_calls: list[str] = []
    monkeypatch.setattr(
        "app.services.background_worker.recover_stale_jobs",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.background_worker.claim_next_job",
        lambda *_args, **_kwargs: job,
    )
    monkeypatch.setattr(
        "app.services.background_worker.mark_job_failed_or_retry",
        lambda *_args, **_kwargs: mutation_calls.append("job_failed"),
    )

    with pytest.raises(PreClaimInfrastructureError) as caught:
        run_worker_once(
            worker_id="worker-a",
            stale_after_seconds=60,
            session_factory=lambda: db,
            handlers={},
        )

    assert caught.value.reason_code == "POSTGRES_OUT_OF_MEMORY"
    assert db.rollback_count == 1
    assert db.invalidate_count == 1
    assert mutation_calls == []


def test_preclaim_programming_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeWorkerDb()
    failure = ProgrammingError(
        "broken SQL",
        {},
        DriverFailure("syntax error", sqlstate="42601"),
    )
    _prepare_preclaim_failure(monkeypatch, failure)

    with pytest.raises(ProgrammingError):
        run_worker_once(
            worker_id="worker-a",
            stale_after_seconds=60,
            session_factory=lambda: db,
            handlers={},
        )

    assert db.rollback_count == 1
    assert db.invalidate_count == 0


def test_preclaim_semantic_operational_error_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeWorkerDb()
    failure = OperationalError(
        "select next job",
        {},
        DriverFailure("serialization failure", sqlstate="40001"),
    )
    _prepare_preclaim_failure(monkeypatch, failure)

    with pytest.raises(OperationalError):
        run_worker_once(
            worker_id="worker-a",
            stale_after_seconds=60,
            session_factory=lambda: db,
            handlers={},
        )

    assert db.rollback_count == 1
    assert db.invalidate_count == 0


def test_worker_control_loop_retries_with_bounded_backoff_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    waits: list[float] = []
    published: list[str] = []

    class RecordingEvent(Event):
        def wait(self, timeout=None):
            waits.append(float(timeout or 0))
            return False

    def poll_once(**_kwargs) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PreClaimInfrastructureError("POSTGRES_OUT_OF_MEMORY", RuntimeError("oom"))
        return False

    monkeypatch.setattr(
        "app.services.background_worker._publish_worker_infrastructure_state",
        lambda **kwargs: published.append(kwargs["degraded_reason"]) or True,
    )
    monkeypatch.setattr(
        "app.services.background_worker.operational_metrics.increment",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.background_worker.operational_metrics.set_gauge",
        lambda *_args, **_kwargs: None,
    )
    settings = SimpleNamespace(
        runtime_mode=RuntimeMode.CERTIFICATION,
        worker_infrastructure_backoff_initial_seconds=1.0,
        worker_infrastructure_backoff_max_seconds=4.0,
        job_stale_after_seconds=60,
        job_worker_heartbeat_timeout_seconds=30,
        queue_fairness_enabled=False,
        job_max_consecutive_interactive_claims=4,
        job_age_promotion_seconds=300,
        winner_probability_auto_maturation_enabled=False,
        ceri_enabled=False,
        ceri_provider_ingest_enabled=False,
        job_poll_interval_seconds=0.1,
    )

    _run_worker_control_loop(
        settings=settings,
        worker_id="worker-a",
        worker_instance_id="instance-a",
        queue_names=("interactive",),
        handlers={},
        claim_state=SimpleNamespace(),
        session_factory=lambda: FakeWorkerDb(),
        runtime_stop_event=RecordingEvent(),
        certification_session_id="cert-a",
        stop_after_one=True,
        poll_once=poll_once,
    )

    assert calls == 2
    assert waits == [1.0]
    assert published == ["POSTGRES_OUT_OF_MEMORY"]


def test_worker_control_loop_caps_repeated_infrastructure_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    class StopAfterThirdWait(Event):
        def wait(self, timeout=None):
            waits.append(float(timeout or 0))
            return len(waits) == 4

    monkeypatch.setattr(
        "app.services.background_worker._publish_worker_infrastructure_state",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.services.background_worker.operational_metrics.increment",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.background_worker.operational_metrics.set_gauge",
        lambda *_args, **_kwargs: None,
    )
    settings = SimpleNamespace(
        runtime_mode=RuntimeMode.CERTIFICATION,
        worker_infrastructure_backoff_initial_seconds=1.0,
        worker_infrastructure_backoff_max_seconds=4.0,
        job_stale_after_seconds=60,
        job_worker_heartbeat_timeout_seconds=30,
        queue_fairness_enabled=False,
        job_max_consecutive_interactive_claims=4,
        job_age_promotion_seconds=300,
        winner_probability_auto_maturation_enabled=False,
        ceri_enabled=False,
        ceri_provider_ingest_enabled=False,
        job_poll_interval_seconds=0.1,
    )

    _run_worker_control_loop(
        settings=settings,
        worker_id="worker-a",
        worker_instance_id="instance-a",
        queue_names=("interactive",),
        handlers={},
        claim_state=SimpleNamespace(),
        session_factory=lambda: FakeWorkerDb(),
        runtime_stop_event=StopAfterThirdWait(),
        certification_session_id="cert-a",
        stop_after_one=False,
        poll_once=lambda **_kwargs: (_ for _ in ()).throw(
            PreClaimInfrastructureError("POSTGRES_OUT_OF_MEMORY", RuntimeError("oom"))
        ),
    )

    assert waits == [1.0, 2.0, 4.0, 4.0]


def test_successful_preclaim_interaction_clears_degraded_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeWorkerDb()
    healthy_calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "app.services.background_worker.recover_stale_jobs",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.background_worker.claim_next_job",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.background_worker.mark_worker_infrastructure_healthy",
        lambda _db, worker_id, **kwargs: healthy_calls.append(
            (worker_id, kwargs["instance_id"])
        ),
    )

    assert (
        run_worker_once(
            worker_id="worker-a",
            worker_instance_id="instance-a",
            stale_after_seconds=60,
            session_factory=lambda: db,
            handlers={},
        )
        is False
    )
    assert healthy_calls == [("worker-a", "instance-a")]
