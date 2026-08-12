from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, UploadRun
from app.services.background_job_service import JobStatus

LEGACY_CERI_JOB_TYPES = frozenset(
    {
        "CERI_PROVIDER_INGEST",
        "CERI_NORMALIZE",
        "CERI_REBUILD_FEATURES",
        "CERI_CAPTURE_RUN",
        "CERI_CHANGE_DETECTION",
        "CERI_ALERT_REBUILD",
    }
)
CLEANUP_CONFIRMATION = "APPLY_CERI_BACKLOG_CLEANUP"


@dataclass(frozen=True)
class CeriBacklogJobInspection:
    job_id: int
    related_run_id: int | None
    job_type: str
    status: str
    priority: int
    created_at: datetime | None
    run_after: datetime | None
    request_key: str | None
    workflow_key: str | None
    classification: str
    cleanup_candidate: bool


@dataclass(frozen=True)
class CeriBacklogGroup:
    related_run_id: int | None
    job_type: str
    status: str
    priority: int
    count: int
    oldest_created_at: datetime | None
    newest_created_at: datetime | None


@dataclass(frozen=True)
class CeriBacklogReport:
    generated_at: datetime
    dry_run: bool
    superseded_run_ids: tuple[int, ...]
    preserved_run_ids: tuple[int, ...]
    groups: tuple[CeriBacklogGroup, ...]
    classifications: dict[str, int]
    jobs: tuple[CeriBacklogJobInspection, ...]

    @property
    def cleanup_candidate_ids(self) -> tuple[int, ...]:
        return tuple(row.job_id for row in self.jobs if row.cleanup_candidate)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cleanup_candidate_ids"] = list(self.cleanup_candidate_ids)
        payload["cleanup_candidate_count"] = len(self.cleanup_candidate_ids)
        return payload


@dataclass(frozen=True)
class CeriBacklogCleanupResult:
    report: CeriBacklogReport
    cancelled_job_ids: tuple[int, ...]
    skipped_job_ids: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "report": self.report.as_dict(),
            "cancelled_job_ids": list(self.cancelled_job_ids),
            "cancelled_count": len(self.cancelled_job_ids),
            "skipped_job_ids": list(self.skipped_job_ids),
            "skipped_count": len(self.skipped_job_ids),
        }


def inspect_legacy_ceri_backlog(
    db: Session,
    *,
    superseded_run_ids: tuple[int, ...] = (),
    preserved_run_ids: tuple[int, ...] = (),
) -> CeriBacklogReport:
    """Classify active legacy CERI work without changing database state.

    A run is never inferred to be superseded merely because it is old. Operators
    must identify superseded test runs explicitly; this avoids introducing the
    cross-run source-equivalence semantics intentionally deferred by the plan.
    """

    jobs = list(
        db.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.job_type.in_(LEGACY_CERI_JOB_TYPES))
            .where(BackgroundJob.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)))
            .order_by(BackgroundJob.created_at.asc(), BackgroundJob.id.asc())
        ).all()
    )
    known_run_ids = set(db.scalars(select(UploadRun.id)).all())
    duplicate_ids = _duplicate_queued_job_ids(jobs)
    superseded = set(superseded_run_ids)
    preserved = set(preserved_run_ids)

    inspections = tuple(
        _inspect_job(
            job,
            known_run_ids=known_run_ids,
            duplicate_ids=duplicate_ids,
            superseded_run_ids=superseded,
            preserved_run_ids=preserved,
        )
        for job in jobs
    )
    classifications = dict(sorted(Counter(row.classification for row in inspections).items()))
    return CeriBacklogReport(
        generated_at=_utcnow(),
        dry_run=True,
        superseded_run_ids=tuple(sorted(superseded)),
        preserved_run_ids=tuple(sorted(preserved)),
        groups=_group_jobs(jobs),
        classifications=classifications,
        jobs=inspections,
    )


def apply_legacy_ceri_backlog_cleanup(
    db: Session,
    *,
    superseded_run_ids: tuple[int, ...] = (),
    preserved_run_ids: tuple[int, ...] = (),
    reason: str,
    confirmation: str,
) -> CeriBacklogCleanupResult:
    if confirmation != CLEANUP_CONFIRMATION:
        raise ValueError(f"cleanup requires confirmation {CLEANUP_CONFIRMATION!r}")
    clean_reason = reason.replace("\n", " ").strip()[:500]
    if not clean_reason:
        raise ValueError("cleanup reason is required")

    report = inspect_legacy_ceri_backlog(
        db,
        superseded_run_ids=superseded_run_ids,
        preserved_run_ids=preserved_run_ids,
    )
    candidate_ids = report.cleanup_candidate_ids
    if not candidate_ids:
        return CeriBacklogCleanupResult(report, (), ())

    rows = list(
        db.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.id.in_(candidate_ids))
            .with_for_update()
        ).all()
    )
    now = _utcnow()
    cancelled: list[int] = []
    skipped: list[int] = []
    for job in rows:
        if job.status != JobStatus.QUEUED:
            skipped.append(job.id)
            continue
        inspection = next(row for row in report.jobs if row.job_id == job.id)
        metadata = dict(job.operational_metadata_json or {})
        metadata["ceri_backlog_cleanup"] = {
            "classification": inspection.classification,
            "reason": clean_reason,
            "cancelled_at": now.isoformat(),
            "dry_run_reviewed": True,
        }
        job.operational_metadata_json = metadata
        job.status = JobStatus.CANCELLED
        job.requested_cancel = True
        job.completed_at = now
        job.error_message = "Cancelled by reviewed CERI backlog cleanup."
        job.worker_id = None
        job.lease_owner = None
        job.execution_token = None
        job.locked_at = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        cancelled.append(job.id)
    missing = set(candidate_ids) - {job.id for job in rows}
    skipped.extend(sorted(missing))
    db.flush()
    return CeriBacklogCleanupResult(
        report=report,
        cancelled_job_ids=tuple(sorted(cancelled)),
        skipped_job_ids=tuple(sorted(skipped)),
    )


def _inspect_job(
    job: BackgroundJob,
    *,
    known_run_ids: set[int],
    duplicate_ids: set[int],
    superseded_run_ids: set[int],
    preserved_run_ids: set[int],
) -> CeriBacklogJobInspection:
    if job.status == JobStatus.RUNNING:
        classification = "PROTECTED_RUNNING"
    elif job.related_run_id in preserved_run_ids:
        classification = "PRESERVED_COMPARISON_RUN"
    elif job.id in duplicate_ids:
        classification = "DUPLICATE_REQUEST_KEY"
    elif job.related_run_id in superseded_run_ids:
        classification = "EXPLICIT_SUPERSEDED_TEST_RUN"
    elif job.related_run_id is not None and job.related_run_id not in known_run_ids:
        classification = "ORPHANED_RELATED_RUN"
    else:
        classification = "REVIEW_REQUIRED"
    cleanup_candidate = classification in {
        "DUPLICATE_REQUEST_KEY",
        "EXPLICIT_SUPERSEDED_TEST_RUN",
        "ORPHANED_RELATED_RUN",
    }
    payload = job.payload_json or {}
    return CeriBacklogJobInspection(
        job_id=job.id,
        related_run_id=job.related_run_id,
        job_type=job.job_type,
        status=job.status,
        priority=int(job.priority or 100),
        created_at=job.created_at,
        run_after=job.run_after,
        request_key=job.request_key or payload.get("request_key"),
        workflow_key=payload.get("workflow_key"),
        classification=classification,
        cleanup_candidate=cleanup_candidate,
    )


def _duplicate_queued_job_ids(jobs: list[BackgroundJob]) -> set[int]:
    by_identity: dict[tuple[str, str], list[BackgroundJob]] = {}
    for job in jobs:
        request_key = job.request_key or (job.payload_json or {}).get("request_key")
        if request_key:
            by_identity.setdefault((job.job_type, str(request_key)), []).append(job)
    duplicates: set[int] = set()
    for matching in by_identity.values():
        if len(matching) < 2:
            continue
        ordered = sorted(
            matching,
            key=lambda job: (
                job.status != JobStatus.RUNNING,
                job.created_at or datetime.min.replace(tzinfo=UTC),
                job.id,
            ),
        )
        duplicates.update(job.id for job in ordered[1:] if job.status == JobStatus.QUEUED)
    return duplicates


def _group_jobs(jobs: list[BackgroundJob]) -> tuple[CeriBacklogGroup, ...]:
    grouped: dict[tuple[int | None, str, str, int], list[BackgroundJob]] = {}
    for job in jobs:
        key = (job.related_run_id, job.job_type, job.status, int(job.priority or 100))
        grouped.setdefault(key, []).append(job)
    rows = []
    for (run_id, job_type, status, priority), matching in grouped.items():
        created = [job.created_at for job in matching if job.created_at is not None]
        rows.append(
            CeriBacklogGroup(
                related_run_id=run_id,
                job_type=job_type,
                status=status,
                priority=priority,
                count=len(matching),
                oldest_created_at=min(created) if created else None,
                newest_created_at=max(created) if created else None,
            )
        )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.related_run_id is None,
                row.related_run_id or 0,
                row.job_type,
                row.status,
                row.priority,
            ),
        )
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)
