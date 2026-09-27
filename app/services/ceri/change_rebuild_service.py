from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, or_, select
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
from app.services.ceri.evidence_eligibility import (
    eligible_snapshot_select,
    filter_eligible_snapshots,
)
from app.services.ceri.pit_eligibility import source_record_is_eligible
from app.services.market_clock_service import MarketClockService, SessionTimestampPolicy
from app.services.redaction import redact_text

CHANGE_REBUILD_COMPANY_CHUNK_SIZE = 25

_LARGE_CERI_MODELS = {
    CeriScoreSnapshot,
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriGuidanceEvent,
    CeriSourceRecord,
}


class CeriChangeRebuildCancelled(RuntimeError):
    pass


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


@dataclass(frozen=True)
class _ChangeChunk:
    snapshots: tuple[CeriScoreSnapshot, ...]
    history: tuple[CeriScoreSnapshot, ...]
    revisions: tuple[tuple[CeriCatalystEventRevision, CeriCatalystEventRevision | None, int], ...]
    guidance: tuple[tuple[CeriGuidanceEvent, CeriGuidanceEvent | None], ...]
    events: tuple[CeriCatalystEvent, ...]


class CeriChangeRebuildService:
    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        detector: CeriChangeDetectionService | None = None,
        company_chunk_size: int = CHANGE_REBUILD_COMPANY_CHUNK_SIZE,
    ) -> None:
        if company_chunk_size <= 0:
            raise ValueError("company_chunk_size must be positive")
        self.config = config or load_ceri_config()
        self.detector = detector or CeriChangeDetectionService(config=self.config)
        self.company_chunk_size = company_chunk_size

    def rebuild(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        *,
        should_cancel: Callable[[], bool] | None = None,
        progress_callback: Callable[[int, int, tuple[int, ...]], None] | None = None,
    ) -> CeriChangeRebuildResult:
        target_session, cutoff_at = _required_boundary(request)
        if isinstance(db, Session):
            with db.no_autoflush:
                return self._rebuild_batches(
                    db,
                    request,
                    target_session=target_session,
                    cutoff_at=cutoff_at,
                    should_cancel=should_cancel,
                    progress_callback=progress_callback,
                )
        return self._rebuild_batches(
            db,
            request,
            target_session=target_session,
            cutoff_at=cutoff_at,
            should_cancel=should_cancel,
            progress_callback=progress_callback,
        )

    def _rebuild_batches(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        *,
        target_session: date,
        cutoff_at: datetime,
        should_cancel: Callable[[], bool] | None,
        progress_callback: Callable[[int, int, tuple[int, ...]], None] | None,
    ) -> CeriChangeRebuildResult:
        authority_tickers = self._authority_tickers(db, request)
        company_ids = self._scope_company_ids(
            db,
            request,
            authority_tickers=authority_tickers,
            target_session=target_session,
            cutoff_at=cutoff_at,
        )
        aggregate = CeriChangeRebuildResult()
        processed = 0
        for company_chunk in _chunks(company_ids, self.company_chunk_size):
            if callable(should_cancel) and should_cancel():
                raise CeriChangeRebuildCancelled("CERI change detection cancelled between chunks.")
            prepared = self._prepare_chunk(
                db,
                request,
                company_chunk,
                authority_tickers=authority_tickers,
                target_session=target_session,
                cutoff_at=cutoff_at,
            )
            if isinstance(db, Session):
                result = self._rebuild_locked_chunk(
                    db,
                    prepared,
                    target_session=target_session,
                    cutoff_at=cutoff_at,
                )
            else:
                result = self._rebuild(
                    db,
                    prepared,
                    target_session=target_session,
                    cutoff_at=cutoff_at,
                )
            aggregate = _merge_results(aggregate, result)
            processed += len(company_chunk)
            if callable(progress_callback):
                progress_callback(processed, len(company_ids), company_chunk)
        return aggregate

    def _rebuild_locked_chunk(
        self,
        db: Session,
        prepared: _ChangeChunk,
        *,
        target_session: date,
        cutoff_at: datetime,
    ) -> CeriChangeRebuildResult:
        from app.services.source_mutation_authority import (
            PrefetchedSourceBodies,
            prefetched_source_scope,
        )

        bundle = PrefetchedSourceBodies(db)
        selected_history: list[CeriScoreSnapshot] = []
        history_by_company: dict[int, list[CeriScoreSnapshot]] = {}
        for row in prepared.history:
            history_by_company.setdefault(row.company_id, []).append(row)
        for current in prepared.snapshots:
            prior, _state, _excluded = select_prior_comparison(
                current,
                [
                    candidate
                    for candidate in history_by_company.get(current.company_id, ())
                    if _snapshot_sort_key(candidate) < _snapshot_sort_key(current)
                ],
            )
            if prior is not None:
                selected_history.append(prior)
        snapshots = (*prepared.snapshots, *selected_history)
        snapshot_ids = {int(row.id) for row in snapshots if row.id is not None}
        evidence_ids = {int(row.evidence_id) for row in snapshots if row.evidence_id is not None}
        revisions = [
            row
            for current, prior, _company_id in prepared.revisions
            for row in (current, prior)
            if row is not None
        ]
        guidance = [
            row
            for current, prior in prepared.guidance
            for row in (current, prior)
            if row is not None
        ]
        source_ids = {
            int(row.source_record_id)
            for row in (*revisions, *guidance)
            if row.source_record_id is not None
        }
        exact_loads = (
            (CeriScoreSnapshot, snapshot_ids),
            (CoreCalculationEvidence, evidence_ids),
            (CeriCatalystEvent, {int(row.id) for row in prepared.events if row.id is not None}),
            (
                CeriCatalystEventRevision,
                {int(row.id) for row in revisions if row.id is not None},
            ),
            (CeriGuidanceEvent, {int(row.id) for row in guidance if row.id is not None}),
            (CeriSourceRecord, source_ids),
        )
        for model, identifiers in exact_loads:
            if identifiers:
                bundle.load(model, select(model).where(model.id.in_(sorted(identifiers))))
        bundle.seal()
        # The semantic transaction is fenced again immediately before commit.
        # Do not retain the background-job row lock during long company chunks;
        # the detached control plane must be able to publish progress/cancel.
        with db.no_autoflush, prefetched_source_scope(
            db,
            bundle,
            retain_execution_ownership=False,
        ):
            return self._rebuild(
                db,
                prepared,
                target_session=target_session,
                cutoff_at=cutoff_at,
            )

    def _rebuild(
        self,
        db: Session,
        prepared: _ChangeChunk,
        *,
        target_session: date,
        cutoff_at: datetime,
    ) -> CeriChangeRebuildResult:
        del target_session
        market_cutoff = MarketClockService().cutoff_for(
            cutoff_at, reason="EXPLICIT_CERI_CHANGE_REBUILD"
        )
        changes = duplicates = failed = 0
        change_ids: list[int] = []
        errors: list[dict[str, Any]] = []
        comparison_history: dict[int, list[CeriScoreSnapshot]] = {}
        for snapshot in prepared.history:
            comparison_history.setdefault(snapshot.company_id, []).append(snapshot)
        grouped: dict[int, list[CeriScoreSnapshot]] = {}
        for snapshot in prepared.snapshots:
            grouped.setdefault(snapshot.company_id, []).append(snapshot)
        for company_id in sorted(grouped):
            try:
                rows = sorted(grouped[company_id], key=_snapshot_sort_key)
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
        for revision, prior, company_id in prepared.revisions:
            try:
                result = self.detector.detect_catalyst_revision(
                    db,
                    revision=revision,
                    prior_revision=prior,
                    company_id=company_id,
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
        for guidance, prior_guidance in prepared.guidance:
            try:
                result = self.detector.detect_guidance_change(
                    db,
                    guidance=guidance,
                    company_id=guidance.company_id,
                    prior_action=prior_guidance.action if prior_guidance is not None else None,
                    prior_guidance_event_id=guidance.supersedes_id,
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

    def _prepare_chunk(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        company_ids: tuple[int, ...],
        *,
        authority_tickers: set[str],
        target_session: date,
        cutoff_at: datetime,
    ) -> _ChangeChunk:
        snapshots = self._snapshots(
            db,
            request,
            company_ids,
            authority_tickers=authority_tickers,
            target_session=target_session,
            cutoff_at=cutoff_at,
        )
        history_statement = eligible_snapshot_select(CeriScoreSnapshot).where(
            CeriScoreSnapshot.company_id.in_(company_ids),
            CeriScoreSnapshot.as_of_session <= target_session,
            CeriScoreSnapshot.cutoff_at <= cutoff_at,
        )
        history = self._eligible_snapshots(db, history_statement)
        history = [
            row
            for row in history
            if row.company_id in company_ids
            and row.as_of_session <= target_session
            and _aware(row.cutoff_at) <= cutoff_at
        ]
        revisions, events = self._eligible_revisions(
            db,
            request,
            company_ids,
            target_session=target_session,
            cutoff_at=cutoff_at,
        )
        guidance = self._guidance(
            db,
            request,
            company_ids,
            target_session=target_session,
            cutoff_at=cutoff_at,
        )
        return _ChangeChunk(
            snapshots=tuple(sorted(snapshots, key=_snapshot_sort_key)),
            history=tuple(sorted(history, key=_snapshot_sort_key)),
            revisions=tuple(revisions),
            guidance=tuple(guidance),
            events=tuple(events),
        )

    def _snapshots(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        company_ids: tuple[int, ...],
        *,
        authority_tickers: set[str],
        target_session: date,
        cutoff_at: datetime,
    ) -> list[CeriScoreSnapshot]:
        statement = eligible_snapshot_select(CeriScoreSnapshot).where(
            CeriScoreSnapshot.company_id.in_(company_ids),
            CeriScoreSnapshot.as_of_session <= target_session,
            CeriScoreSnapshot.cutoff_at <= cutoff_at,
        )
        if request.ticker:
            statement = statement.where(
                func.upper(CeriScoreSnapshot.ticker) == request.ticker.upper()
            )
        if authority_tickers:
            statement = statement.where(func.upper(CeriScoreSnapshot.ticker).in_(authority_tickers))
        if request.run_id is not None:
            statement = statement.where(CeriScoreSnapshot.run_id == request.run_id)
        if request.from_session:
            statement = statement.where(CeriScoreSnapshot.as_of_session >= request.from_session)
        if request.to_session:
            statement = statement.where(CeriScoreSnapshot.as_of_session <= request.to_session)
        if request.changed_since:
            statement = statement.where(CeriScoreSnapshot.created_at >= request.changed_since)
        rows = self._eligible_snapshots(db, statement)
        return [
            row
            for row in rows
            if row.company_id in company_ids
            and (not request.ticker or row.ticker.upper() == request.ticker.upper())
            and (not authority_tickers or row.ticker.upper() in authority_tickers)
            and (request.run_id is None or row.run_id == request.run_id)
            and (request.from_session is None or row.as_of_session >= request.from_session)
            and (request.to_session is None or row.as_of_session <= request.to_session)
            and (request.changed_since is None or row.created_at >= request.changed_since)
            and row.as_of_session <= target_session
            and _aware(row.cutoff_at) <= cutoff_at
        ]

    def _scope_company_ids(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        *,
        authority_tickers: set[str],
        target_session: date,
        cutoff_at: datetime,
    ) -> tuple[int, ...]:
        if request.company_ids:
            return tuple(sorted({int(value) for value in request.company_ids}))
        if request.ticker:
            statement = select(CeriCompany).where(
                func.upper(CeriCompany.ticker) == request.ticker.upper()
            )
            return tuple(
                sorted(
                    int(company.id)
                    for company in _scoped_scalars(db, statement)
                    if company.ticker.upper() == request.ticker.upper()
                )
            )
        if request.run_id is not None:
            statement = eligible_snapshot_select(CeriScoreSnapshot).where(
                CeriScoreSnapshot.run_id == request.run_id,
                CeriScoreSnapshot.as_of_session <= target_session,
                CeriScoreSnapshot.cutoff_at <= cutoff_at,
            )
            if authority_tickers:
                statement = statement.where(
                    func.upper(CeriScoreSnapshot.ticker).in_(authority_tickers)
                )
            snapshots = self._eligible_snapshots(db, statement)
            return tuple(
                sorted(
                    {
                        int(row.company_id)
                        for row in snapshots
                        if row.run_id == request.run_id
                        and row.as_of_session <= target_session
                        and _aware(row.cutoff_at) <= cutoff_at
                        and (not authority_tickers or row.ticker.upper() in authority_tickers)
                    }
                )
            )
        if authority_tickers:
            statement = select(CeriCompany).where(
                func.upper(CeriCompany.ticker).in_(authority_tickers)
            )
            return tuple(
                sorted(
                    int(company.id)
                    for company in _scoped_scalars(db, statement)
                    if company.ticker.upper() in authority_tickers
                )
            )
        statement = select(CeriCompany).order_by(CeriCompany.id)
        return tuple(sorted(int(company.id) for company in _scoped_scalars(db, statement)))

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
        company_ids: tuple[int, ...],
        *,
        target_session: date,
        cutoff_at: datetime,
    ) -> tuple[
        list[tuple[CeriCatalystEventRevision, CeriCatalystEventRevision | None, int]],
        list[CeriCatalystEvent],
    ]:
        event_statement = select(CeriCatalystEvent).where(
            CeriCatalystEvent.company_id.in_(company_ids)
        )
        events = [
            row for row in _scoped_scalars(db, event_statement) if row.company_id in company_ids
        ]
        event_by_id = {int(row.id): row for row in events if row.id is not None}
        if not event_by_id:
            return [], []
        statement = (
            select(CeriCatalystEventRevision)
            .join(
                CeriSourceRecord,
                CeriSourceRecord.id == CeriCatalystEventRevision.source_record_id,
            )
            .where(
                CeriCatalystEventRevision.catalyst_event_id.in_(sorted(event_by_id)),
                CeriCatalystEventRevision.effective_session.is_not(None),
                CeriCatalystEventRevision.effective_session <= target_session,
                or_(
                    CeriCatalystEventRevision.announced_at.is_(None),
                    CeriCatalystEventRevision.announced_at <= cutoff_at,
                ),
                *_source_temporal_predicates(cutoff_at),
            )
        )
        if request.from_session:
            statement = statement.where(
                CeriCatalystEventRevision.effective_session >= request.from_session
            )
        if request.to_session:
            statement = statement.where(
                CeriCatalystEventRevision.effective_session <= request.to_session
            )
        if request.changed_since:
            statement = statement.where(
                CeriCatalystEventRevision.created_at >= request.changed_since
            )
        revisions = list(_scoped_scalars(db, statement))
        if not isinstance(db, Session):
            source_ids = {
                int(row.source_record_id) for row in revisions if row.source_record_id is not None
            }
            source_rows = (
                _scoped_scalars(
                    db,
                    select(CeriSourceRecord).where(CeriSourceRecord.id.in_(sorted(source_ids))),
                )
                if source_ids
                else []
            )
            sources = {int(row.id): row for row in source_rows if row.id is not None}
            revisions = [
                row
                for row in revisions
                if row.catalyst_event_id in event_by_id
                and row.effective_session is not None
                and row.effective_session <= target_session
                and (row.announced_at is None or _aware(row.announced_at) <= cutoff_at)
                and row.source_record_id in sources
                and source_record_is_eligible(sources[row.source_record_id], cutoff_at)
                and (
                    request.from_session is None
                    or _revision_date(row) is None
                    or _revision_date(row) >= request.from_session
                )
                and (
                    request.to_session is None
                    or _revision_date(row) is None
                    or _revision_date(row) <= request.to_session
                )
                and (
                    request.changed_since is None
                    or (row.created_at is not None and row.created_at >= request.changed_since)
                )
            ]
        by_event: dict[int, list[CeriCatalystEventRevision]] = {}
        for row in revisions:
            by_event.setdefault(row.catalyst_event_id, []).append(row)
        selected: list[tuple[CeriCatalystEventRevision, CeriCatalystEventRevision | None, int]] = []
        for event_id, rows in by_event.items():
            rows.sort(key=lambda row: (row.revision_number, row.id or 0))
            selected.append(
                (
                    rows[-1],
                    rows[-2] if len(rows) > 1 else None,
                    int(event_by_id[event_id].company_id),
                )
            )
        selected.sort(key=lambda pair: (_revision_date(pair[0]) or date.min, pair[0].id or 0))
        selected_event_ids = {pair[0].catalyst_event_id for pair in selected}
        return selected, [event_by_id[value] for value in sorted(selected_event_ids)]

    def _guidance(
        self,
        db: Session,
        request: CeriChangeRebuildRequest,
        company_ids: tuple[int, ...],
        *,
        target_session: date,
        cutoff_at: datetime,
    ) -> list[tuple[CeriGuidanceEvent, CeriGuidanceEvent | None]]:
        statement = (
            select(CeriGuidanceEvent)
            .join(CeriSourceRecord, CeriSourceRecord.id == CeriGuidanceEvent.source_record_id)
            .where(
                CeriGuidanceEvent.company_id.in_(company_ids),
                CeriGuidanceEvent.effective_session.is_not(None),
                CeriGuidanceEvent.effective_session <= target_session,
                or_(
                    CeriGuidanceEvent.effective_at.is_(None),
                    CeriGuidanceEvent.effective_at <= cutoff_at,
                ),
                or_(
                    CeriGuidanceEvent.accepted_at.is_(None),
                    CeriGuidanceEvent.accepted_at <= cutoff_at,
                ),
                *_source_temporal_predicates(cutoff_at),
            )
        )
        if request.from_session:
            statement = statement.where(CeriGuidanceEvent.effective_session >= request.from_session)
        if request.to_session:
            statement = statement.where(CeriGuidanceEvent.effective_session <= request.to_session)
        if request.changed_since:
            statement = statement.where(CeriGuidanceEvent.created_at >= request.changed_since)
        rows = list(_scoped_scalars(db, statement))
        if not isinstance(db, Session):
            source_ids = {int(row.source_record_id) for row in rows}
            source_rows = (
                _scoped_scalars(
                    db,
                    select(CeriSourceRecord).where(CeriSourceRecord.id.in_(sorted(source_ids))),
                )
                if source_ids
                else []
            )
            sources = {int(row.id): row for row in source_rows if row.id is not None}
            rows = [
                row
                for row in rows
                if row.company_id in company_ids
                and _guidance_session(row) is not None
                and _guidance_session(row) <= target_session
                and (row.effective_at is None or _aware(row.effective_at) <= cutoff_at)
                and (row.accepted_at is None or _aware(row.accepted_at) <= cutoff_at)
                and row.source_record_id in sources
                and source_record_is_eligible(sources[row.source_record_id], cutoff_at)
                and (
                    request.from_session is None
                    or row.effective_session is None
                    or row.effective_session >= request.from_session
                )
                and (
                    request.to_session is None
                    or row.effective_session is None
                    or row.effective_session <= request.to_session
                )
                and (
                    request.changed_since is None
                    or (row.created_at is not None and row.created_at >= request.changed_since)
                )
            ]
        rows.sort(key=lambda row: (row.company_id, row.effective_session or date.min, row.id or 0))
        prior_ids = {int(row.supersedes_id) for row in rows if row.supersedes_id is not None}
        priors = (
            {
                int(row.id): row
                for row in _scoped_scalars(
                    db,
                    select(CeriGuidanceEvent).where(CeriGuidanceEvent.id.in_(sorted(prior_ids))),
                )
                if row.id in prior_ids
            }
            if prior_ids
            else {}
        )
        return [(row, priors.get(row.supersedes_id)) for row in rows]

    @staticmethod
    def _eligible_snapshots(db: Session, statement: Any) -> list[CeriScoreSnapshot]:
        rows = list(_scoped_scalars(db, statement))
        return rows if isinstance(db, Session) else filter_eligible_snapshots(db, rows)


def _source_temporal_predicates(cutoff_at: datetime) -> tuple[Any, ...]:
    return (
        func.coalesce(CeriSourceRecord.retrieved_at, CeriSourceRecord.ingested_at) <= cutoff_at,
        or_(CeriSourceRecord.published_at.is_(None), CeriSourceRecord.published_at <= cutoff_at),
        or_(CeriSourceRecord.observed_at.is_(None), CeriSourceRecord.observed_at <= cutoff_at),
        or_(
            CeriSourceRecord.source_timestamp.is_(None),
            CeriSourceRecord.source_timestamp <= cutoff_at,
        ),
    )


def _scoped_scalars(db: Session, statement: Any) -> list[Any]:
    descriptions = getattr(statement, "column_descriptions", ())
    entity = descriptions[0].get("entity") if descriptions else None
    if entity in _LARGE_CERI_MODELS and not getattr(statement, "_where_criteria", ()):
        raise ValueError(f"CERI_CHANGE_UNSCOPED_READ_FORBIDDEN:{entity.__tablename__}")
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(statement)
    return list(result.all() if hasattr(result, "all") else result)


def _chunks(values: tuple[int, ...], size: int) -> Iterable[tuple[int, ...]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _merge_results(
    left: CeriChangeRebuildResult,
    right: CeriChangeRebuildResult,
) -> CeriChangeRebuildResult:
    return CeriChangeRebuildResult(
        changes=left.changes + right.changes,
        duplicates=left.duplicates + right.duplicates,
        warnings=left.warnings + right.warnings,
        failed=left.failed + right.failed,
        errors=(*left.errors, *right.errors),
        change_ids=tuple(dict.fromkeys((*left.change_ids, *right.change_ids))),
    )


def _snapshot_sort_key(snapshot: CeriScoreSnapshot) -> tuple[date, datetime, int]:
    return (snapshot.as_of_session, snapshot.cutoff_at, snapshot.id or 0)


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
