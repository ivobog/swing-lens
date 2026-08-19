import logging

import pytest

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.background_worker import (
    CancelRequested,
    JobDeferred,
    execute_job,
    log_worker_startup_configuration,
    run_worker_once,
)
from app.settings import SecDocumentIncrementalMode, Settings


@pytest.fixture(autouse=True)
def _stub_worker_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.background_worker.register_worker",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.background_worker.heartbeat_worker",
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
            return "0048_sec_guidance_normalization_performance"

    settings = Settings(
        _env_file=None,
        ceri_provider_ingest_enabled=True,
        sec_document_incremental_mode=SecDocumentIncrementalMode.OFF,
    )

    with caplog.at_level(logging.CRITICAL):
        summary = log_worker_startup_configuration(
            Db(), settings=settings, worker_id="worker-test"
        )

    assert summary["sec_incremental_mode"] == "OFF"
    assert summary["sec_processor_signature"].startswith("sec-guidance:")
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
    assert db.commit_count == 3
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


class FakeWorkerDb:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.closed = True
