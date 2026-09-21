from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select, union
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriGuidanceEvent,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import CoreCalculationEvidence
from app.services.ceri.change_detection_service import CeriChangeDetectionService
from app.services.ceri.change_semantics import select_prior_comparison
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.evidence_eligibility import filter_eligible_snapshots
from app.services.ceri.pit_eligibility import source_record_is_eligible
from app.services.market_clock_service import MarketClockService, SessionTimestampPolicy
from app.services.redaction import redact_text


@dataclass(frozen=True)
class CeriChangeRebuildRequest:
    company_ids: tuple[int, ...] | None = None
    ticker: str | None = None
    run_id: int | None = None
    from_session: date | None = None
    to_session: date | None = None
    changed_since: datetime | None = None
    as_of_session: date | None = None
    cutoff_at: datetime | None = None
    semantic_authority: Any | None = None


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
        _required_boundary(request)
        if not isinstance(db, Session):
            return self._rebuild(db, request, authority_tickers=set())
        from app.services.source_mutation_authority import (
            PrefetchedSourceBodies,
            prefetched_source_scope,
        )

        bundle = PrefetchedSourceBodies(db)
        companies = select(CeriCompany.id)
        authority_tickers = self._authority_tickers(db, request)
        if request.company_ids:
            companies = companies.where(CeriCompany.id.in_(request.company_ids))
        elif request.ticker:
            companies = companies.where(func.upper(CeriCompany.ticker) == request.ticker.upper())
        elif authority_tickers:
            companies = companies.where(func.upper(CeriCompany.ticker).in_(authority_tickers))
        elif request.run_id is not None:
            companies = select(CeriScoreSnapshot.company_id).where(
                CeriScoreSnapshot.run_id == request.run_id
            )
        guidance = select(CeriGuidanceEvent).where(CeriGuidanceEvent.company_id.in_(companies))
        events = select(CeriCatalystEvent).where(CeriCatalystEvent.company_id.in_(companies))
        revisions = select(CeriCatalystEventRevision).where(
            CeriCatalystEventRevision.catalyst_event_id.in_(
                events.with_only_columns(CeriCatalystEvent.id)
            )
        )
        source_ids = union(
            guidance.with_only_columns(CeriGuidanceEvent.source_record_id),
            revisions.with_only_columns(CeriCatalystEventRevision.source_record_id),
        )
        scores = select(CeriScoreSnapshot).where(CeriScoreSnapshot.company_id.in_(companies))
        for model, statement in (
            (CeriGuidanceEvent, guidance),
            (CeriCatalystEvent, events),
            (CeriCatalystEventRevision, revisions),
            (CeriSourceRecord, select(CeriSourceRecord).where(CeriSourceRecord.id.in_(source_ids))),
            (CeriScoreSnapshot, scores),
            (
                CoreCalculationEvidence,
                select(CoreCalculationEvidence).where(
                    CoreCalculationEvidence.id.in_(
                        scores.with_only_columns(CeriScoreSnapshot.evidence_id)
                    )
                ),
            ),
        ):
            bundle.load(model, statement)
        bundle.seal()
        with db.no_autoflush, prefetched_source_scope(db, bundle):
            return self._rebuild(db, request, authority_tickers=authority_tickers)

    def _rebuild(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        *,
        authority_tickers: set[str],
    ) -> CeriChangeRebuildResult:
        target_session, cutoff_at = _required_boundary(request)
        market_cutoff = MarketClockService().cutoff_for(
            cutoff_at, reason="EXPLICIT_CERI_CHANGE_REBUILD"
        )
        snapshots = self._snapshots(db, request, authority_tickers=authority_tickers)
        scoped_company_ids = self._scoped_company_ids(
            db,
            request,
            snapshots,
            authority_tickers=authority_tickers,
        )
        changes = duplicates = failed = 0
        change_ids: list[int] = []
        errors: list[dict[str, Any]] = []
        comparison_history: dict[int, list[CeriScoreSnapshot]] = {}
        for snapshot in filter_eligible_snapshots(db, _load(db, CeriScoreSnapshot)):
            comparison_history.setdefault(snapshot.company_id, []).append(snapshot)
        grouped: dict[int, list[CeriScoreSnapshot]] = {}
        for snapshot in snapshots:
            grouped.setdefault(snapshot.company_id, []).append(snapshot)
        for company_id, rows in grouped.items():
            try:
                rows.sort(key=_snapshot_sort_key)
                company_history = comparison_history.get(company_id, [])
                for current in rows:
                    prior, _comparison_state, _excluded = select_prior_comparison(
                        current,
                        [
                            candidate
                            for candidate in company_history
                            if _snapshot_sort_key(candidate) < _snapshot_sort_key(current)
                        ],
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
                    {
                        "company_id": company_id,
                        "error": redact_text(str(exc)).replace("\n", " ")[:500],
                    }
                )
        revisions = self._eligible_revisions(
            db,
            request,
            scoped_company_ids,
            target_session=target_session,
            cutoff_at=cutoff_at,
        )
        for revision, prior in revisions:
            try:
                result = self.detector.detect_catalyst_revision(
                    db,
                    revision=revision,
                    prior_revision=prior,
                    company_id=_company_id(db, revision),
                    market_cutoff=market_cutoff,
                )
                changes += result.changes
                duplicates += result.duplicates
                change_ids.extend(result.change_ids)
            except Exception as exc:
                failed += 1
                errors.append(
                    {
                        "revision_id": revision.id,
                        "error": redact_text(str(exc)).replace("\n", " ")[:500],
                    }
                )
        for company_id, guidance_rows in self._guidance(
            db,
            request,
            scoped_company_ids,
            target_session=target_session,
            cutoff_at=cutoff_at,
        ).items():
            for guidance in guidance_rows:
                try:
                    prior_guidance_event_id = guidance.supersedes_id
                    prior_guidance = _get(db, CeriGuidanceEvent, prior_guidance_event_id)
                    result = self.detector.detect_guidance_change(
                        db,
                        guidance=guidance,
                        company_id=company_id,
                        prior_action=prior_guidance.action if prior_guidance is not None else None,
                        prior_guidance_event_id=prior_guidance_event_id,
                        market_cutoff=market_cutoff,
                    )
                    changes += result.changes
                    duplicates += result.duplicates
                    change_ids.extend(result.change_ids)
                except Exception as exc:
                    failed += 1
                    errors.append(
                        {
                            "guidance_id": guidance.id,
                            "error": redact_text(str(exc)).replace("\n", " ")[:500],
                        }
                    )
        return CeriChangeRebuildResult(
            changes=changes,
            duplicates=duplicates,
            warnings=0,
            failed=failed,
            errors=tuple(errors),
            change_ids=tuple(dict.fromkeys(change_ids)),
        )

    def _snapshots(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        *,
        authority_tickers: set[str],
    ) -> list[CeriScoreSnapshot]:
        rows = filter_eligible_snapshots(db, _load(db, CeriScoreSnapshot))
        ids = set(request.company_ids or ())
        if ids:
            rows = [row for row in rows if row.company_id in ids]
        if request.ticker:
            rows = [row for row in rows if row.ticker.upper() == request.ticker.upper()]
        if authority_tickers:
            rows = [row for row in rows if row.ticker.upper() in authority_tickers]
        if request.run_id is not None:
            rows = [row for row in rows if row.run_id == request.run_id]
        if request.from_session:
            rows = [row for row in rows if row.as_of_session >= request.from_session]
        if request.to_session:
            rows = [row for row in rows if row.as_of_session <= request.to_session]
        if request.changed_since:
            rows = [row for row in rows if row.created_at >= request.changed_since]
        target_session, cutoff_at = _required_boundary(request)
        rows = [
            row
            for row in rows
            if row.as_of_session <= target_session and _aware(row.cutoff_at) <= cutoff_at
        ]
        return rows

    def _scoped_company_ids(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        snapshots: list[CeriScoreSnapshot],
        *,
        authority_tickers: set[str],
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
        if authority_tickers:
            return {
                company.id
                for company in _load(db, CeriCompany)
                if company.ticker.upper() in authority_tickers
            }
        return None

    def _authority_tickers(self, db: Session, request: CeriChangeRebuildRequest) -> set[str]:
        if request.semantic_authority is None or not isinstance(db, Session):
            return set()
        from app.services.scope_refresh_adoption import retained_scope_members

        tickers = {
            member.subject_id.upper()
            for member in retained_scope_members(db, request.semantic_authority.scope_id)
            if member.subject_type == "TICKER"
        }
        if not tickers:
            raise ValueError("CERI_FROZEN_SCOPE_HAS_NO_TICKER_MEMBERS")
        return tickers

    def _eligible_revisions(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        scoped_company_ids: set[int] | None,
        *,
        target_session: date,
        cutoff_at: datetime,
    ) -> list[tuple[CeriCatalystEventRevision, CeriCatalystEventRevision | None]]:
        revisions = _load(db, CeriCatalystEventRevision)
        events = {event.id: event for event in _load(db, CeriCatalystEvent)}
        sources = {row.id: row for row in _load(db, CeriSourceRecord) if row.id is not None}
        revisions = [
            row
            for row in revisions
            if row.catalyst_event_id in events
            and (
                scoped_company_ids is None
                or events[row.catalyst_event_id].company_id in scoped_company_ids
            )
            and row.effective_session is not None
            and row.effective_session <= target_session
            and (row.announced_at is None or _aware(row.announced_at) <= cutoff_at)
            and row.source_record_id in sources
            and source_record_is_eligible(sources[row.source_record_id], cutoff_at)
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
        by_event: dict[int, list[CeriCatalystEventRevision]] = {}
        for row in revisions:
            by_event.setdefault(row.catalyst_event_id, []).append(row)
        selected: list[tuple[CeriCatalystEventRevision, CeriCatalystEventRevision | None]] = []
        for rows in by_event.values():
            rows.sort(key=lambda row: (row.revision_number, row.id or 0))
            selected.append((rows[-1], rows[-2] if len(rows) > 1 else None))
        selected.sort(key=lambda pair: (_revision_date(pair[0]) or date.min, pair[0].id or 0))
        return selected

    def _guidance(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        scoped_company_ids: set[int] | None,
        *,
        target_session: date,
        cutoff_at: datetime,
    ) -> dict[int, list[CeriGuidanceEvent]]:
        rows = _load(db, CeriGuidanceEvent)
        sources = {row.id: row for row in _load(db, CeriSourceRecord) if row.id is not None}
        if scoped_company_ids is not None:
            rows = [row for row in rows if row.company_id in scoped_company_ids]
        rows = [
            row
            for row in rows
            if _guidance_session(row) is not None
            and _guidance_session(row) <= target_session
            and (row.effective_at is None or _aware(row.effective_at) <= cutoff_at)
            and (row.accepted_at is None or _aware(row.accepted_at) <= cutoff_at)
            and row.source_record_id in sources
            and source_record_is_eligible(sources[row.source_record_id], cutoff_at)
        ]
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


def _snapshot_sort_key(snapshot: CeriScoreSnapshot) -> tuple[date, datetime, int]:
    return (snapshot.as_of_session, snapshot.cutoff_at, snapshot.id or 0)


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


def _guidance_session(guidance: CeriGuidanceEvent) -> date | None:
    return guidance.effective_session


def _required_boundary(request: CeriChangeRebuildRequest) -> tuple[date, datetime]:
    target_session = request.as_of_session or request.to_session
    if target_session is None or request.cutoff_at is None:
        raise ValueError(
            "historical CERI change rebuild requires as_of_session/to_session and cutoff_at"
        )
    if request.cutoff_at.tzinfo is None or request.cutoff_at.utcoffset() is None:
        raise ValueError("cutoff_at must be timezone-aware")
    if (
        request.as_of_session is not None
        and request.to_session is not None
        and request.as_of_session != request.to_session
    ):
        raise ValueError("as_of_session and to_session must identify the same upper boundary")
    latest_completed = MarketClockService().canonical_session_for_timestamp(
        request.cutoff_at,
        policy=SessionTimestampPolicy.LATEST_COMPLETED_DAILY_SESSION,
    )
    if target_session > latest_completed:
        raise ValueError("CERI change target session is later than cutoff_at permits")
    return target_session, request.cutoff_at


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("historical CERI evidence timestamps must be timezone-aware")
    return value
