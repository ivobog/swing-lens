from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.tables import BackgroundJob, UploadRun
from app.services.background_job_service import JobStatus
from app.services.ceri.backlog_cleanup_service import (
    CLEANUP_CONFIRMATION,
    apply_legacy_ceri_backlog_cleanup,
    inspect_legacy_ceri_backlog,
)


def test_backlog_inspection_is_dry_run_and_conservative() -> None:
    preserved = _job(1, run_id=10, request_key="preserved")
    superseded = _job(2, run_id=20, request_key="superseded")
    review = _job(3, run_id=30, request_key="review")
    running = _job(4, run_id=20, request_key="running", status=JobStatus.RUNNING)
    db = FakeDb(
        jobs=[preserved, superseded, review, running],
        upload_run_ids=[10, 20, 30],
    )

    report = inspect_legacy_ceri_backlog(
        db,
        superseded_run_ids=(20,),
        preserved_run_ids=(10,),
    )

    classifications = {row.job_id: row.classification for row in report.jobs}
    assert classifications == {
        1: "PRESERVED_COMPARISON_RUN",
        2: "EXPLICIT_SUPERSEDED_TEST_RUN",
        3: "REVIEW_REQUIRED",
        4: "PROTECTED_RUNNING",
    }
    assert report.cleanup_candidate_ids == (2,)
    assert all(job.status in {JobStatus.QUEUED, JobStatus.RUNNING} for job in db.jobs)
    assert db.flushes == 0


def test_backlog_inspection_identifies_orphans_and_queued_duplicates() -> None:
    keeper = _job(1, run_id=10, request_key="same", status=JobStatus.RUNNING)
    duplicate = _job(2, run_id=10, request_key="same")
    orphan = _job(3, run_id=999, request_key="orphan")
    db = FakeDb(jobs=[keeper, duplicate, orphan], upload_run_ids=[10])

    report = inspect_legacy_ceri_backlog(db)

    classifications = {row.job_id: row.classification for row in report.jobs}
    assert classifications[1] == "PROTECTED_RUNNING"
    assert classifications[2] == "DUPLICATE_REQUEST_KEY"
    assert classifications[3] == "ORPHANED_RELATED_RUN"
    assert report.cleanup_candidate_ids == (2, 3)


def test_cleanup_requires_explicit_confirmation_and_reason() -> None:
    db = FakeDb(jobs=[_job(1, run_id=20, request_key="old")], upload_run_ids=[20])

    with pytest.raises(ValueError, match="confirmation"):
        apply_legacy_ceri_backlog_cleanup(
            db,
            superseded_run_ids=(20,),
            reason="superseded QA run",
            confirmation="wrong",
        )
    with pytest.raises(ValueError, match="reason"):
        apply_legacy_ceri_backlog_cleanup(
            db,
            superseded_run_ids=(20,),
            reason="",
            confirmation=CLEANUP_CONFIRMATION,
        )


def test_cleanup_cancels_only_reviewed_queued_candidates_and_retains_payload() -> None:
    candidate = _job(1, run_id=20, request_key="old")
    candidate.payload_json = {"ticker": "MSFT", "source": "legacy"}
    running = _job(2, run_id=20, request_key="active", status=JobStatus.RUNNING)
    review = _job(3, run_id=30, request_key="review")
    db = FakeDb(
        jobs=[candidate, running, review],
        upload_run_ids=[20, 30],
        locked_jobs=[candidate],
    )

    result = apply_legacy_ceri_backlog_cleanup(
        db,
        superseded_run_ids=(20,),
        reason="superseded QA comparison run",
        confirmation=CLEANUP_CONFIRMATION,
    )

    assert result.cancelled_job_ids == (1,)
    assert candidate.status == JobStatus.CANCELLED
    assert candidate.requested_cancel is True
    assert candidate.payload_json == {"ticker": "MSFT", "source": "legacy"}
    cleanup = candidate.operational_metadata_json["ceri_backlog_cleanup"]
    assert cleanup["classification"] == "EXPLICIT_SUPERSEDED_TEST_RUN"
    assert cleanup["reason"] == "superseded QA comparison run"
    assert running.status == JobStatus.RUNNING
    assert review.status == JobStatus.QUEUED
    assert db.flushes == 1


def _job(
    identifier: int,
    *,
    run_id: int,
    request_key: str,
    status: str = JobStatus.QUEUED,
) -> BackgroundJob:
    return BackgroundJob(
        id=identifier,
        related_run_id=run_id,
        job_type="CERI_PROVIDER_INGEST",
        status=status,
        priority=120,
        request_key=request_key,
        payload_json={"request_key": request_key},
        created_at=datetime(2026, 8, 1, identifier, tzinfo=UTC),
        run_after=datetime(2026, 8, 1, identifier, tzinfo=UTC),
        requested_cancel=False,
        operational_metadata_json={},
    )


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows


class FakeDb:
    def __init__(self, *, jobs, upload_run_ids, locked_jobs=None) -> None:
        self.jobs = list(jobs)
        self.upload_run_ids = list(upload_run_ids)
        self.locked_jobs = list(locked_jobs if locked_jobs is not None else jobs)
        self.background_query_count = 0
        self.flushes = 0

    def scalars(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is UploadRun:
            return FakeScalarResult(self.upload_run_ids)
        if entity is BackgroundJob:
            self.background_query_count += 1
            rows = self.jobs if self.background_query_count == 1 else self.locked_jobs
            return FakeScalarResult(rows)
        raise AssertionError(f"unexpected query entity: {entity}")

    def flush(self) -> None:
        self.flushes += 1
