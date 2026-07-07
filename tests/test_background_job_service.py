from datetime import UTC, datetime, timedelta

import pytest

from app.models.tables import BackgroundJob
from app.services.background_job_service import (
    JobStatus,
    claim_next_job,
    default_retry_delay,
    enqueue_job,
    is_cancel_requested,
    mark_job_cancelled,
    mark_job_completed,
    mark_job_failed_or_retry,
    mark_job_partial,
    recover_stale_jobs,
    request_job_cancel,
)


def test_enqueue_job_persists_payload_and_defaults() -> None:
    db = FakeDb()

    job = enqueue_job(
        db,
        job_type="FULL_PIPELINE",
        payload={"pipeline_run_id": 42},
        related_run_id=7,
        priority=10,
        max_retries=5,
    )

    assert job in db.added
    assert job.job_type == "FULL_PIPELINE"
    assert job.related_run_id == 7
    assert job.status == JobStatus.QUEUED
    assert job.priority == 10
    assert job.payload_json == {"pipeline_run_id": 42}
    assert job.max_retries == 5
    assert db.flushes == 1


def test_claim_next_job_marks_job_running() -> None:
    job = BackgroundJob(id=11, job_type="FULL_PIPELINE", status=JobStatus.QUEUED)
    db = FakeDb(existing=job, scalar_result=11)

    claimed = claim_next_job(db, worker_id="worker-a")

    assert claimed is job
    assert job.status == JobStatus.RUNNING
    assert job.worker_id == "worker-a"
    assert job.locked_at is not None
    assert job.started_at is not None
    assert db.flushes == 1


def test_claim_next_job_returns_none_when_queue_is_empty() -> None:
    db = FakeDb(scalar_result=None)

    assert claim_next_job(db, worker_id="worker-a") is None
    assert db.flushes == 0


def test_mark_job_completed_clears_lock_and_stores_result() -> None:
    job = _running_job()
    db = FakeDb(existing=job)

    mark_job_completed(db, job, {"ok": True})

    assert job.status == JobStatus.COMPLETED
    assert job.result_json == {"ok": True}
    assert job.worker_id is None
    assert job.locked_at is None
    assert job.completed_at is not None
    assert db.flushes == 1


def test_mark_job_partial_finishes_with_partial_status() -> None:
    job = _running_job()
    db = FakeDb(existing=job)

    mark_job_partial(db, job, {"failed_tickers": 2})

    assert job.status == JobStatus.PARTIAL
    assert job.result_json == {"failed_tickers": 2}
    assert job.completed_at is not None


def test_mark_job_cancelled_finishes_and_records_cancel_request() -> None:
    job = _running_job()
    db = FakeDb(existing=job)

    mark_job_cancelled(db, job)

    assert job.status == JobStatus.CANCELLED
    assert job.requested_cancel is True
    assert job.worker_id is None
    assert job.locked_at is None
    assert job.completed_at is not None


def test_failed_job_requeues_with_backoff_until_retries_are_exhausted() -> None:
    job = _running_job(max_retries=2)
    db = FakeDb(existing=job)

    mark_job_failed_or_retry(db, job, "temporary failure")

    assert job.status == JobStatus.QUEUED
    assert job.retry_count == 1
    assert job.error_message == "temporary failure"
    assert job.worker_id is None
    assert job.locked_at is None
    assert job.run_after is not None
    assert job.completed_at is None

    job.status = JobStatus.RUNNING
    mark_job_failed_or_retry(db, job, "still broken")

    assert job.status == JobStatus.QUEUED
    assert job.retry_count == 2

    job.status = JobStatus.RUNNING
    mark_job_failed_or_retry(db, job, "final failure")

    assert job.status == JobStatus.FAILED
    assert job.retry_count == 3
    assert job.completed_at is not None


def test_failed_job_error_message_is_sanitized_and_truncated() -> None:
    job = _running_job(max_retries=0)
    db = FakeDb(existing=job)

    mark_job_failed_or_retry(db, job, "x\n" * 600)

    assert "\n" not in job.error_message
    assert len(job.error_message) == 500


def test_request_job_cancel_cancels_queued_job_immediately() -> None:
    job = BackgroundJob(id=9, job_type="FULL_PIPELINE", status=JobStatus.QUEUED)
    db = FakeDb(existing=job)

    returned = request_job_cancel(db, 9)

    assert returned is job
    assert job.requested_cancel is True
    assert job.status == JobStatus.CANCELLED
    assert job.completed_at is not None
    assert db.flushes == 1


def test_request_job_cancel_for_running_job_is_cooperative() -> None:
    job = _running_job()
    db = FakeDb(existing=job)

    request_job_cancel(db, job.id)

    assert job.requested_cancel is True
    assert job.status == JobStatus.RUNNING


def test_request_job_cancel_raises_for_missing_job() -> None:
    db = FakeDb(existing=None)

    with pytest.raises(ValueError, match="Background job 404 was not found"):
        request_job_cancel(db, 404)


def test_is_cancel_requested_reads_persistent_flag() -> None:
    db = FakeDb(scalar_result=True)

    assert is_cancel_requested(db, 11) is True


def test_recover_stale_jobs_requeues_jobs_with_retries_remaining() -> None:
    stale = _running_job(retry_count=1, max_retries=3)
    db = FakeDb(stale_jobs=[stale])

    count = recover_stale_jobs(db, stale_after_seconds=900)

    assert count == 1
    assert stale.status == JobStatus.QUEUED
    assert stale.worker_id is None
    assert stale.locked_at is None
    assert stale.error_message == "Recovered after stale worker lock."
    assert db.flushes == 1


def test_recover_stale_jobs_marks_exhausted_jobs_stale() -> None:
    stale = _running_job(retry_count=3, max_retries=3)
    db = FakeDb(stale_jobs=[stale])

    recover_stale_jobs(db, stale_after_seconds=900)

    assert stale.status == JobStatus.STALE
    assert stale.completed_at is not None


def test_default_retry_delay_uses_capped_schedule() -> None:
    assert default_retry_delay(1) == timedelta(seconds=60)
    assert default_retry_delay(2) == timedelta(seconds=180)
    assert default_retry_delay(3) == timedelta(seconds=600)
    assert default_retry_delay(99) == timedelta(seconds=600)


def _running_job(
    retry_count: int = 0,
    max_retries: int = 3,
) -> BackgroundJob:
    return BackgroundJob(
        id=11,
        job_type="FULL_PIPELINE",
        status=JobStatus.RUNNING,
        retry_count=retry_count,
        max_retries=max_retries,
        worker_id="worker-a",
        locked_at=datetime.now(UTC) - timedelta(hours=1),
        started_at=datetime.now(UTC) - timedelta(hours=1),
    )


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


class FakeDb:
    def __init__(
        self,
        existing: BackgroundJob | None = None,
        scalar_result=None,
        stale_jobs: list[BackgroundJob] | None = None,
    ) -> None:
        self.added = []
        self.existing = existing
        self.scalar_result = scalar_result
        self.stale_jobs = stale_jobs or []
        self.flushes = 0

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def get(self, model, row_id):
        return self.existing

    def scalar(self, statement):
        return self.scalar_result

    def scalars(self, statement):
        return FakeScalarResult(self.stale_jobs)
