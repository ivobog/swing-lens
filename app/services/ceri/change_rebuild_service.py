from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriGuidanceEvent,
    CeriScoreSnapshot,
)
from app.services.ceri.change_detection_service import CeriChangeDetectionService
from app.services.ceri.change_semantics import select_prior_comparison
from app.services.ceri.config import CeriConfig, load_ceri_config


@dataclass(frozen=True)
class CeriChangeRebuildRequest:
    company_ids: tuple[int, ...] | None = None
    ticker: str | None = None
    run_id: int | None = None
    from_session: date | None = None
    to_session: date | None = None
    changed_since: datetime | None = None


@dataclass(frozen=True)
class CeriChangeRebuildResult:
    changes: int = 0
    duplicates: int = 0
    warnings: int = 0
    failed: int = 0
    errors: tuple[dict[str, Any], ...] = ()
    change_ids: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["change_count"] = self.changes
        value["errors"] = list(self.errors)
        value["change_ids"] = list(self.change_ids)
        return value


class CeriChangeRebuildService:
    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        detector: CeriChangeDetectionService | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        self.detector = detector or CeriChangeDetectionService(config=self.config)

    def rebuild(self, db: Session, request: CeriChangeRebuildRequest) -> CeriChangeRebuildResult:
        snapshots = self._snapshots(db, request)
        scoped_company_ids = self._scoped_company_ids(db, request, snapshots)
        changes = duplicates = failed = 0
        change_ids: list[int] = []
        errors: list[dict[str, Any]] = []
        grouped: dict[int, list[CeriScoreSnapshot]] = {}
        for snapshot in snapshots:
            grouped.setdefault(snapshot.company_id, []).append(snapshot)
        for company_id, rows in grouped.items():
            try:
                rows.sort(key=lambda row: (row.as_of_session, row.cutoff_at, row.id or 0))
                for index, current in enumerate(rows):
                    prior, _comparison_state, _excluded = select_prior_comparison(
                        current, rows[:index]
                    )
                    result = self.detector.detect_score_changes(
                        db,
                        current=current,
                        prior=prior,
                        scope="standalone",
                    )
                    changes += result.changes
                    duplicates += result.duplicates
                    change_ids.extend(result.change_ids)
            except Exception as exc:
                failed += 1
                errors.append(
                    {"company_id": company_id, "error": str(exc).replace("\n", " ")[:500]}
                )
        revisions = self._current_revisions(db, request, scoped_company_ids)
        for revision in revisions:
            try:
                prior = (
                    _get(db, CeriCatalystEventRevision, revision.prior_revision_id)
                    if revision.prior_revision_id
                    else None
                )
                result = self.detector.detect_catalyst_revision(
                    db,
                    revision=revision,
                    prior_revision=prior,
                    company_id=_company_id(db, revision),
                )
                changes += result.changes
                duplicates += result.duplicates
                change_ids.extend(result.change_ids)
            except Exception as exc:
                failed += 1
                errors.append(
                    {"revision_id": revision.id, "error": str(exc).replace("\n", " ")[:500]}
                )
        for company_id, guidance_rows in self._guidance(db, request, scoped_company_ids).items():
            prior_action = None
            for guidance in guidance_rows:
                try:
                    result = self.detector.detect_guidance_change(
                        db,
                        guidance=guidance,
                        company_id=company_id,
                        prior_action=prior_action,
                    )
                    changes += result.changes
                    duplicates += result.duplicates
                    change_ids.extend(result.change_ids)
                    prior_action = guidance.action
                except Exception as exc:
                    failed += 1
                    errors.append(
                        {"guidance_id": guidance.id, "error": str(exc).replace("\n", " ")[:500]}
                    )
        return CeriChangeRebuildResult(
            changes=changes,
            duplicates=duplicates,
            warnings=0,
            failed=failed,
            errors=tuple(errors),
            change_ids=tuple(dict.fromkeys(change_ids)),
        )

    def _snapshots(self, db: Session, request: CeriChangeRebuildRequest) -> list[CeriScoreSnapshot]:
        rows = _load(db, CeriScoreSnapshot)
        ids = set(request.company_ids or ())
        if ids:
            rows = [row for row in rows if row.company_id in ids]
        if request.ticker:
            rows = [row for row in rows if row.ticker.upper() == request.ticker.upper()]
        if request.run_id is not None:
            rows = [row for row in rows if row.run_id == request.run_id]
        if request.from_session:
            rows = [row for row in rows if row.as_of_session >= request.from_session]
        if request.to_session:
            rows = [row for row in rows if row.as_of_session <= request.to_session]
        if request.changed_since:
            rows = [row for row in rows if row.created_at >= request.changed_since]
        return rows

    def _scoped_company_ids(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        snapshots: list[CeriScoreSnapshot],
    ) -> set[int] | None:
        if request.company_ids:
            return set(request.company_ids)
        if request.ticker:
            return {
                company.id
                for company in _load(db, CeriCompany)
                if company.ticker.upper() == request.ticker.upper()
            }
        if request.run_id is not None:
            return {snapshot.company_id for snapshot in snapshots}
        return None

    def _current_revisions(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        scoped_company_ids: set[int] | None,
    ) -> list[CeriCatalystEventRevision]:
        revisions = [row for row in _load(db, CeriCatalystEventRevision) if row.is_current]
        events = {event.id: event for event in _load(db, CeriCatalystEvent)}
        revisions = [
            row
            for row in revisions
            if row.catalyst_event_id in events
            and (
                scoped_company_ids is None
                or events[row.catalyst_event_id].company_id in scoped_company_ids
            )
        ]
        if request.from_session:
            revisions = [
                row
                for row in revisions
                if _revision_date(row) is None or _revision_date(row) >= request.from_session
            ]
        if request.to_session:
            revisions = [
                row
                for row in revisions
                if _revision_date(row) is None or _revision_date(row) <= request.to_session
            ]
        if request.changed_since:
            revisions = [
                row
                for row in revisions
                if row.created_at is not None and row.created_at >= request.changed_since
            ]
        revisions.sort(key=lambda row: (_revision_date(row) or date.min, row.id or 0))
        return revisions

    def _guidance(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        scoped_company_ids: set[int] | None,
    ) -> dict[int, list[CeriGuidanceEvent]]:
        rows = _load(db, CeriGuidanceEvent)
        if scoped_company_ids is not None:
            rows = [row for row in rows if row.company_id in scoped_company_ids]
        if request.from_session:
            rows = [
                row
                for row in rows
                if row.effective_session is None or row.effective_session >= request.from_session
            ]
        if request.to_session:
            rows = [
                row
                for row in rows
                if row.effective_session is None or row.effective_session <= request.to_session
            ]
        if request.changed_since:
            rows = [
                row
                for row in rows
                if row.created_at is not None and row.created_at >= request.changed_since
            ]
        grouped: dict[int, list[CeriGuidanceEvent]] = {}
        for row in rows:
            grouped.setdefault(row.company_id, []).append(row)
        for company_rows in grouped.values():
            company_rows.sort(key=lambda row: (row.effective_session or date.min, row.id or 0))
        return grouped


def _load(db: Session, model: Any) -> list[Any]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)


def _get(db: Session, model: Any, identifier: int | None) -> Any | None:
    get = getattr(db, "get", None)
    return get(model, identifier) if callable(get) and identifier is not None else None


def _company_id(db: Session, revision: CeriCatalystEventRevision) -> int:
    event = _get(db, CeriCatalystEvent, revision.catalyst_event_id)
    return int(event.company_id) if event is not None else 0


def _revision_date(revision: CeriCatalystEventRevision) -> date | None:
    if revision.effective_session is not None:
        return revision.effective_session
    if revision.announced_at is not None:
        return revision.announced_at.date()
    return None
