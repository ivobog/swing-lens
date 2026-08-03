from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, UploadRun
from app.services.background_job_service import TERMINAL_JOB_STATUSES
from app.settings import Settings


@dataclass(frozen=True)
class CleanupCandidate:
    kind: str
    identifier: str
    reason: str
    path: str | None = None
    bytes: int | None = None
    modified_at: datetime | None = None
    job_id: int | None = None
    status: str | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "identifier": self.identifier,
            "reason": self.reason,
            "path": self.path,
            "bytes": self.bytes,
            "modified_at": self.modified_at.isoformat() if self.modified_at else None,
            "job_id": self.job_id,
            "status": self.status,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


@dataclass(frozen=True)
class CleanupReport:
    dry_run: bool
    candidates: list[CleanupCandidate]
    deleted: list[CleanupCandidate] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "candidate_count": len(self.candidates),
            "deleted_count": len(self.deleted),
            "bytes_reclaimable": sum(candidate.bytes or 0 for candidate in self.candidates),
            "bytes_deleted": sum(candidate.bytes or 0 for candidate in self.deleted),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "deleted": [candidate.to_dict() for candidate in self.deleted],
            "errors": self.errors,
        }


def cleanup_rebuildable_artifacts(
    db: Session,
    settings: Settings,
    *,
    dry_run: bool = True,
    now: datetime | None = None,
) -> CleanupReport:
    current_time = now or datetime.now(UTC)
    candidates = [
        *_file_candidates(
            root=settings.export_dir,
            kind="export_file",
            retention_days=settings.cleanup_export_retention_days,
            reason="Expired export artifact.",
            now=current_time,
        ),
        *_file_candidates(
            root=settings.cache_dir,
            kind="cache_file",
            retention_days=settings.cleanup_cache_retention_days,
            reason="Rebuildable cache artifact exceeded retention.",
            now=current_time,
        ),
        *_orphan_upload_candidates(db, settings=settings, now=current_time),
        *_terminal_job_candidates(db, settings=settings, now=current_time),
    ]

    if dry_run:
        return CleanupReport(dry_run=True, candidates=candidates)

    deleted: list[CleanupCandidate] = []
    errors: list[dict[str, str]] = []
    jobs_by_id = {job.id: job for job in _load_terminal_jobs(db)}
    for candidate in candidates:
        try:
            if candidate.path:
                path = Path(candidate.path)
                if path.exists():
                    path.unlink()
                deleted.append(candidate)
            elif candidate.job_id is not None:
                job = jobs_by_id.get(candidate.job_id)
                if job is not None:
                    db.delete(job)
                deleted.append(candidate)
        except OSError as exc:
            errors.append({"identifier": candidate.identifier, "message": str(exc)})

    flush = getattr(db, "flush", None)
    if callable(flush):
        flush()
    return CleanupReport(
        dry_run=False,
        candidates=candidates,
        deleted=deleted,
        errors=errors,
    )


def preview_cleanup(db: Session, settings: Settings) -> dict[str, Any]:
    return cleanup_rebuildable_artifacts(db, settings, dry_run=True).to_dict()


def execute_cleanup(db: Session, settings: Settings) -> dict[str, Any]:
    return cleanup_rebuildable_artifacts(db, settings, dry_run=False).to_dict()


def _file_candidates(
    *,
    root: Path,
    kind: str,
    retention_days: int,
    reason: str,
    now: datetime,
) -> list[CleanupCandidate]:
    resolved_root = root.resolve()
    if not resolved_root.exists():
        return []

    cutoff = now - timedelta(days=retention_days)
    candidates: list[CleanupCandidate] = []
    for path in resolved_root.rglob("*"):
        if not path.is_file():
            continue
        resolved_path = path.resolve()
        if not _is_under(resolved_path, resolved_root):
            continue
        modified_at = datetime.fromtimestamp(resolved_path.stat().st_mtime, tz=UTC)
        if modified_at >= cutoff:
            continue
        candidates.append(
            CleanupCandidate(
                kind=kind,
                identifier=str(resolved_path),
                path=str(resolved_path),
                bytes=resolved_path.stat().st_size,
                modified_at=modified_at,
                reason=reason,
            )
        )
    return candidates


def _orphan_upload_candidates(
    db: Session,
    *,
    settings: Settings,
    now: datetime,
) -> list[CleanupCandidate]:
    referenced = _referenced_upload_paths(db)
    return [
        candidate
        for candidate in _file_candidates(
            root=settings.upload_dir,
            kind="orphan_upload_file",
            retention_days=settings.cleanup_orphan_upload_grace_days,
            reason="Upload artifact is not referenced by an upload run.",
            now=now,
        )
        if candidate.path not in referenced
    ]


def _terminal_job_candidates(
    db: Session,
    *,
    settings: Settings,
    now: datetime,
) -> list[CleanupCandidate]:
    cutoff = now - timedelta(days=settings.cleanup_job_retention_days)
    candidates = []
    for job in _load_terminal_jobs(db):
        completed_at = _aware_utc(job.completed_at)
        if completed_at is None or completed_at >= cutoff:
            continue
        candidates.append(
            CleanupCandidate(
                kind="terminal_background_job",
                identifier=f"background_job:{job.id}",
                job_id=job.id,
                status=job.status,
                completed_at=completed_at,
                reason="Terminal background job exceeded retention.",
            )
        )
    return candidates


def _referenced_upload_paths(db: Session) -> set[str]:
    local_runs = getattr(db, "upload_runs", None)
    if local_runs is not None:
        rows = local_runs.values() if isinstance(local_runs, dict) else local_runs
        return {
            str(Path(row.file_path).resolve())
            for row in rows
            if getattr(row, "file_path", None)
        }

    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return set()
    result = scalars(select(UploadRun.file_path).where(UploadRun.file_path.is_not(None)))
    rows = result.all() if hasattr(result, "all") else list(result)
    return {str(Path(path).resolve()) for path in rows if path}


def _load_terminal_jobs(db: Session) -> list[BackgroundJob]:
    local_jobs = getattr(db, "background_jobs", None)
    if local_jobs is not None:
        rows = local_jobs.values() if isinstance(local_jobs, dict) else local_jobs
        return [job for job in rows if getattr(job, "status", None) in TERMINAL_JOB_STATUSES]

    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(BackgroundJob).where(BackgroundJob.status.in_(TERMINAL_JOB_STATUSES)))
    return list(result.all() if hasattr(result, "all") else result)


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
