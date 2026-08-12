from datetime import UTC, datetime, timedelta

import pytest

from app.models.tables import BackgroundJob
from app.services.background_job_service import (
    JobLeaseLost,
    JobStatus,
    claim_next_job,
    default_retry_delay,
    enqueue_job,
    heartbeat_job,
    is_cancel_requested,
    mark_job_cancelled,
    mark_job_completed,
    mark_job_deferred,
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
    assert job.request_key is None
    assert job.status == JobStatus.QUEUED
    assert job.priority == 10
    assert job.payload_json == {"pipeline_run_id": 42}
    assert job.max_retries == 5
    assert db.flushes == 1


def test_enqueue_job_coalesces_matching_active_request_key() -> None:
    existing = BackgroundJob(
        id=3,
        job_type="FULL_PIPELINE",
        request_key="full-pipeline:run:7",
        status=JobStatus.QUEUED,
        payload_json={"pipeline_run_id": 1},
    )
    db = FakeDb(jobs=[existing])

    job = enqueue_job(
        db,
        job_type="FULL_PIPELINE",
        payload={"pipeline_run_id": 2},
        request_key="full-pipeline:run:7",
    )

    assert job is existing
    assert job.__dict__.get("_coalesced") is True
    assert db.added == []
    assert db.flushes == 0


def test_claim_next_job_marks_job_running() -> None:
    created_at = datetime.now(UTC) - timedelta(seconds=3)
    job = BackgroundJob(
        id=11,
        job_type="FULL_PIPELINE",
        status=JobStatus.QUEUED,
        created_at=created_at,
        run_after=created_at,
    )
    db = FakeDb(existing=job, scalar_result=11)

    claimed = claim_next_job(db, worker_id="worker-a")

    assert claimed is job
    assert job.status == JobStatus.RUNNING
    assert job.worker_id == "worker-a"
    assert job.lease_owner == "worker-a"
    assert job.execution_token is not None
    assert job.locked_at is not None
    assert job.heartbeat_at == job.locked_at
    assert job.lease_expires_at is not None
    assert job.started_at is not None
    lease_event = job.operational_metadata_json["lease_events"][-1]
    assert lease_event["event_type"] == "CLAIMED"
    assert "execution_token" not in lease_event
    assert lease_event["execution_token_hash"]
    assert lease_event["execution_token_suffix"] == job.execution_token[-6:]
    attempt = job.operational_metadata_json["current_attempt"]
    assert attempt["attempt_number"] == 1
    assert attempt["queue_delay_ms"] >= 3_000
    assert attempt["original_queue_delay_ms"] >= 3_000
    assert db.flushes == 1


def test_job_attempt_metadata_distinguishes_queue_and_execution_time() -> None:
    created_at = datetime.now(UTC) - timedelta(seconds=4)
    job = BackgroundJob(
        id=11,
        job_type="FULL_PIPELINE",
        status=JobStatus.QUEUED,
        created_at=created_at,
        run_after=created_at,
    )
    db = FakeDb(existing=job, scalar_result=job.id)

    claim_next_job(db, worker_id="worker-a")
    token = job.execution_token
    mark_job_completed(db, job, {"ok": True}, execution_token=token)

    metadata = job.operational_metadata_json
    assert "current_attempt" not in metadata
    assert metadata["attempt_count"] == 1
    assert metadata["last_attempt"]["queue_delay_ms"] >= 4_000
    assert metadata["last_attempt"]["execution_duration_ms"] >= 0
    assert metadata["last_attempt"]["status"] == JobStatus.COMPLETED


def test_claim_next_job_returns_none_when_queue_is_empty() -> None:
    db = FakeDb(scalar_result=None)

    assert claim_next_job(db, worker_id="worker-a") is None
    assert db.flushes == 0


def test_mark_job_completed_clears_lock_and_stores_result() -> None:
    job = _running_job()
    db = FakeDb(existing=job)
    token = job.execution_token

    mark_job_completed(db, job, {"ok": True}, execution_token=token)

    assert job.status == JobStatus.COMPLETED
    assert job.result_json == {"ok": True}
    assert job.worker_id is None
    assert job.lease_owner is None
    assert job.execution_token is None
    assert job.locked_at is None
    assert job.heartbeat_at is None
    assert job.lease_expires_at is None
    assert job.completed_at is not None
    assert db.flushes == 1


def test_mark_job_completed_redacts_sensitive_result_fields() -> None:
    job = _running_job()
    db = FakeDb(existing=job)

    mark_job_completed(
        db,
        job,
        {"authorization": "Bearer secret-token", "path": r"C:\Users\Ivica\Downloads\a.csv"},
        execution_token=job.execution_token,
    )

    assert job.result_json["authorization"] == "<restricted:authorization>"
    assert job.result_json["path"] == "<restricted:path>"
    assert "secret-token" not in str(job.result_json)


def test_mark_job_partial_finishes_with_partial_status() -> None:
    job = _running_job()
    db = FakeDb(existing=job)

    mark_job_partial(db, job, {"failed_tickers": 2}, execution_token=job.execution_token)

    assert job.status == JobStatus.PARTIAL
    assert job.result_json == {"failed_tickers": 2}
    assert job.completed_at is not None


def test_mark_job_cancelled_finishes_and_records_cancel_request() -> None:
    job = _running_job()
    db = FakeDb(existing=job)

    mark_job_cancelled(db, job, execution_token=job.execution_token)

    assert job.status == JobStatus.CANCELLED
    assert job.requested_cancel is True
    assert job.worker_id is None
    assert job.locked_at is None
    assert job.completed_at is not None


def test_failed_job_requeues_with_backoff_until_retries_are_exhausted() -> None:
    job = _running_job(max_retries=2)
    db = FakeDb(existing=job)

    mark_job_failed_or_retry(db, job, "temporary failure", execution_token=job.execution_token)

    assert job.status == JobStatus.QUEUED
    assert job.retry_count == 1
    assert job.error_message == "temporary failure"
    assert job.worker_id is None
    assert job.lease_owner is None
    assert job.execution_token is None
    assert job.locked_at is None
    assert job.heartbeat_at is None
    assert job.lease_expires_at is None
    assert job.run_after is not None
    assert job.completed_at is None

    job.status = JobStatus.RUNNING
    job.execution_token = "token-2"
    mark_job_failed_or_retry(db, job, "still broken", execution_token=job.execution_token)

    assert job.status == JobStatus.QUEUED
    assert job.retry_count == 2

    job.status = JobStatus.RUNNING
    job.execution_token = "token-3"
    mark_job_failed_or_retry(db, job, "final failure", execution_token=job.execution_token)

    assert job.status == JobStatus.FAILED
    assert job.retry_count == 3
    assert job.completed_at is not None


def test_failed_job_error_message_is_sanitized_and_truncated() -> None:
    job = _running_job(max_retries=0)
    db = FakeDb(existing=job)

    mark_job_failed_or_retry(db, job, "x\n" * 600, execution_token=job.execution_token)

    assert "\n" not in job.error_message
    assert len(job.error_message) == 500


def test_failed_job_error_message_redacts_sensitive_details() -> None:
    job = _running_job(max_retries=0)
    db = FakeDb(existing=job)

    mark_job_failed_or_retry(
        db,
        job,
        r"Bearer secret-token from C:\Users\Ivica\Downloads\vendor.csv",
        execution_token=job.execution_token,
    )

    assert "secret-token" not in job.error_message
    assert r"C:\Users\Ivica" not in job.error_message
    assert "<restricted:path>" in job.error_message


def test_deferred_job_preserves_retry_budget_and_releases_lease() -> None:
    job = _running_job(retry_count=2, max_retries=3)
    job.operational_metadata_json = {
        "current_attempt": {
            "attempt_number": 1,
            "started_at": (datetime.now(UTC) - timedelta(seconds=2)).isoformat(),
        }
    }
    db = FakeDb(existing=job)

    mark_job_deferred(
        db,
        job,
        delay=timedelta(seconds=7),
        reason="upstream batches pending",
        execution_token=job.execution_token,
    )

    assert job.status == JobStatus.QUEUED
    assert job.retry_count == 2
    assert job.execution_token is None
    assert job.lease_owner is None
    assert job.run_after > datetime.now(UTC)
    assert job.operational_metadata_json["last_attempt"]["status"] == "DEFERRED"


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
    assert stale.lease_owner is None
    assert stale.execution_token is None
    assert stale.locked_at is None
    assert stale.heartbeat_at is None
    assert stale.lease_expires_at is None
    assert stale.error_message == "Recovered after stale worker lock."
    lease_event = stale.operational_metadata_json["lease_events"][-1]
    assert lease_event["event_type"] == "RECOVERED"
    assert "execution_token" not in lease_event
    assert lease_event["execution_token_hash"]
    assert db.flushes == 1


def test_recover_stale_jobs_marks_exhausted_jobs_stale() -> None:
    stale = _running_job(retry_count=3, max_retries=3)
    db = FakeDb(stale_jobs=[stale])

    recover_stale_jobs(db, stale_after_seconds=900)

    assert stale.status == JobStatus.STALE
    assert stale.completed_at is not None


def test_live_heartbeat_prevents_stale_recovery() -> None:
    live = _running_job(lease_expires_at=datetime.now(UTC) + timedelta(minutes=5))
    db = FakeDb(stale_jobs=[live])

    assert recover_stale_jobs(db, stale_after_seconds=900) == 0

    assert live.status == JobStatus.RUNNING
    assert live.execution_token == "token-1"


def test_heartbeat_renews_lease_with_current_token() -> None:
    job = _running_job(lease_expires_at=datetime.now(UTC) + timedelta(seconds=5))
    db = FakeDb(existing=job)
    old_heartbeat = job.heartbeat_at
    token = job.execution_token

    heartbeat_job(db, job, lease_seconds=900, execution_token=token)

    assert job.execution_token == token
    assert job.heartbeat_at is not None
    assert job.heartbeat_at >= old_heartbeat
    assert job.lease_expires_at is not None
    assert job.lease_expires_at > job.heartbeat_at


def test_expired_lease_can_be_recovered_exactly_once() -> None:
    stale = _running_job(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    db = FakeDb(stale_jobs=[stale])

    assert recover_stale_jobs(db, stale_after_seconds=900) == 1
    assert recover_stale_jobs(db, stale_after_seconds=900) == 0


def test_recovered_job_receives_new_execution_token_when_claimed() -> None:
    stale = _running_job(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    db = FakeDb(existing=stale, stale_jobs=[stale], scalar_result=stale.id)
    old_token = stale.execution_token

    recover_stale_jobs(db, stale_after_seconds=900)
    claimed = claim_next_job(db, worker_id="worker-b")

    assert claimed is stale
    assert stale.status == JobStatus.RUNNING
    assert stale.execution_token is not None
    assert stale.execution_token != old_token
    assert stale.lease_owner == "worker-b"


def test_old_worker_cannot_commit_after_lease_is_replaced() -> None:
    job = _running_job()
    old_token = job.execution_token
    job.execution_token = "token-2"
    db = FakeDb(existing=job)

    with pytest.raises(JobLeaseLost):
        mark_job_completed(db, job, {"ok": True}, execution_token=old_token)

    assert job.status == JobStatus.RUNNING
    lease_event = job.operational_metadata_json["lease_events"][-1]
    assert "execution_token" not in lease_event
    assert lease_event["execution_token_suffix"] == old_token[-6:]


def test_duplicate_workers_cannot_both_mark_job_complete() -> None:
    job = _running_job()
    token = job.execution_token
    db = FakeDb(existing=job)

    mark_job_completed(db, job, {"ok": True}, execution_token=token)

    with pytest.raises(JobLeaseLost):
        mark_job_completed(db, job, {"ok": "again"}, execution_token=token)

    assert job.status == JobStatus.COMPLETED
    assert job.result_json == {"ok": True}


def test_default_retry_delay_uses_capped_schedule() -> None:
    assert default_retry_delay(1) == timedelta(seconds=60)
    assert default_retry_delay(2) == timedelta(seconds=180)
    assert default_retry_delay(3) == timedelta(seconds=600)
    assert default_retry_delay(99) == timedelta(seconds=600)


def _running_job(
    retry_count: int = 0,
    max_retries: int = 3,
    lease_expires_at: datetime | None = None,
) -> BackgroundJob:
    locked_at = datetime.now(UTC) - timedelta(hours=1)
    return BackgroundJob(
        id=11,
        job_type="FULL_PIPELINE",
        status=JobStatus.RUNNING,
        retry_count=retry_count,
        max_retries=max_retries,
        worker_id="worker-a",
        lease_owner="worker-a",
        execution_token="token-1",
        locked_at=locked_at,
        heartbeat_at=locked_at,
        lease_expires_at=lease_expires_at or locked_at + timedelta(minutes=15),
        started_at=locked_at,
        operational_metadata_json={},
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
        jobs: list[BackgroundJob] | None = None,
    ) -> None:
        self.added = []
        self.existing = existing
        self.scalar_result = scalar_result
        self.stale_jobs = stale_jobs or []
        self.jobs = jobs or []
        self.flushes = 0

    def add(self, row) -> None:
        self.added.append(row)
        if isinstance(row, BackgroundJob):
            self.jobs.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def get(self, model, row_id):
        return self.existing

    def scalar(self, statement):
        return self.scalar_result

    def scalars(self, statement):
        return FakeScalarResult(self.stale_jobs)
