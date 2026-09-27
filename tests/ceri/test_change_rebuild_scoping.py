from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriGuidanceEvent,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.services.ceri.change_detection_service import ChangeDetectionResult
from app.services.ceri.change_rebuild_service import (
    CeriChangeRebuildCancelled,
    CeriChangeRebuildRequest,
    CeriChangeRebuildService,
    _scoped_scalars,
)

AS_OF = date(2026, 9, 25)
CUTOFF = datetime(2026, 9, 25, 22, tzinfo=UTC)


def test_scoped_change_queries_have_predicates_and_preserve_relationship_semantics() -> None:
    detector = RecordingDetector()
    db = StatementDb(_fixture_rows())

    result = CeriChangeRebuildService(detector=detector).rebuild(
        db,
        CeriChangeRebuildRequest(
            company_ids=(1,),
            run_id=166,
            as_of_session=AS_OF,
            cutoff_at=CUTOFF,
        ),
    )

    assert result.failed == 0
    assert detector.score_pairs == [(102, 101)]
    assert detector.revision_pairs == [(202, 201, 1)]
    assert detector.guidance_pairs == [(301, None, 1), (302, 301, 1)]
    assert set(db.materialized[CeriSourceRecord]) == {11, 12, 13, 14}
    assert 91 not in db.materialized[CeriSourceRecord]
    assert 92 not in db.materialized[CeriSourceRecord]
    assert all(re.search(r"\bWHERE\b", sql, re.IGNORECASE) for sql in db.large_table_sql)


def test_score_comparison_excludes_future_and_other_company_history() -> None:
    detector = RecordingDetector()
    db = StatementDb(_fixture_rows())

    CeriChangeRebuildService(detector=detector).rebuild(
        db,
        CeriChangeRebuildRequest(
            company_ids=(1,),
            run_id=166,
            as_of_session=AS_OF,
            cutoff_at=CUTOFF,
        ),
    )

    assert detector.score_pairs == [(102, 101)]
    assert 103 not in db.materialized[CeriScoreSnapshot]
    assert 901 not in db.materialized[CeriScoreSnapshot]


def test_company_chunks_are_deterministic_complete_and_boundary_invariant() -> None:
    rows = _score_only_rows(7)
    small_detector = RecordingDetector()
    large_detector = RecordingDetector()
    progress: list[tuple[int, int, tuple[int, ...]]] = []
    request = CeriChangeRebuildRequest(
        company_ids=tuple(range(1, 8)),
        run_id=166,
        as_of_session=AS_OF,
        cutoff_at=CUTOFF,
    )

    small = CeriChangeRebuildService(detector=small_detector, company_chunk_size=3).rebuild(
        StatementDb(rows),
        request,
        progress_callback=lambda processed, total, companies: progress.append(
            (processed, total, companies)
        ),
    )
    large = CeriChangeRebuildService(detector=large_detector, company_chunk_size=50).rebuild(
        StatementDb(rows), request
    )

    assert progress == [
        (3, 7, (1, 2, 3)),
        (6, 7, (4, 5, 6)),
        (7, 7, (7,)),
    ]
    assert small_detector.score_pairs == large_detector.score_pairs
    assert small.change_ids == large.change_ids
    assert len(small.change_ids) == 7


def test_cancellation_stops_before_next_company_chunk() -> None:
    detector = RecordingDetector()
    db = StatementDb(_score_only_rows(5))
    checks = 0

    def should_cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    with pytest.raises(CeriChangeRebuildCancelled, match="between chunks"):
        CeriChangeRebuildService(detector=detector, company_chunk_size=2).rebuild(
            db,
            CeriChangeRebuildRequest(
                company_ids=(1, 2, 3, 4, 5),
                run_id=166,
                as_of_session=AS_OF,
                cutoff_at=CUTOFF,
            ),
            should_cancel=should_cancel,
        )

    assert {current for current, _prior in detector.score_pairs} == {102, 202}
    assert checks == 2


def test_structural_guard_rejects_predicate_free_large_ceri_read() -> None:
    with pytest.raises(ValueError, match="CERI_CHANGE_UNSCOPED_READ_FORBIDDEN"):
        _scoped_scalars(StatementDb({}), __import__("sqlalchemy").select(CeriSourceRecord))


class RecordingDetector:
    def __init__(self) -> None:
        self.score_pairs: list[tuple[int, int | None]] = []
        self.revision_pairs: list[tuple[int, int | None, int]] = []
        self.guidance_pairs: list[tuple[int, int | None, int]] = []

    def detect_score_changes(self, _db, *, current, prior, scope):
        del scope
        self.score_pairs.append((current.id, prior.id if prior is not None else None))
        return ChangeDetectionResult(1, 0, change_ids=(current.id,))

    def detect_catalyst_revision(self, _db, *, revision, prior_revision, company_id, market_cutoff):
        del market_cutoff
        self.revision_pairs.append(
            (revision.id, prior_revision.id if prior_revision is not None else None, company_id)
        )
        return ChangeDetectionResult(1, 0)

    def detect_guidance_change(
        self,
        _db,
        *,
        guidance,
        company_id,
        prior_action,
        prior_guidance_event_id,
        market_cutoff,
    ):
        del prior_action, market_cutoff
        self.guidance_pairs.append((guidance.id, prior_guidance_event_id, company_id))
        return ChangeDetectionResult(1, 0)


class Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class StatementDb:
    def __init__(self, rows_by_model):
        self.rows_by_model = rows_by_model
        self.statements = []
        self.materialized = {model: [] for model in rows_by_model}

    @property
    def large_table_sql(self) -> list[str]:
        tables = tuple(
            model.__tablename__
            for model in (
                CeriScoreSnapshot,
                CeriCatalystEvent,
                CeriCatalystEventRevision,
                CeriGuidanceEvent,
                CeriSourceRecord,
            )
        )
        return [sql for sql in self.statements if any(table in sql for table in tables)]

    def scalars(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        self.statements.append(str(compiled))
        entity = statement.column_descriptions[0]["entity"]
        rows = list(self.rows_by_model.get(entity, ()))
        params = compiled.params
        requested = [set(value) for value in params.values() if isinstance(value, (list, tuple))]
        if entity is CeriScoreSnapshot:
            company_ids = _matching_identity_set(requested, {row.company_id for row in rows})
            if company_ids is not None:
                rows = [row for row in rows if row.company_id in company_ids]
            run_ids = {value for key, value in params.items() if "run_id" in key}
            if run_ids:
                rows = [row for row in rows if row.run_id in run_ids]
            rows = [row for row in rows if row.as_of_session <= AS_OF and row.cutoff_at <= CUTOFF]
        elif entity is CeriCatalystEvent:
            company_ids = _matching_identity_set(requested, {row.company_id for row in rows})
            if company_ids is not None:
                rows = [row for row in rows if row.company_id in company_ids]
        elif entity is CeriCatalystEventRevision:
            event_ids = _matching_identity_set(requested, {row.catalyst_event_id for row in rows})
            if event_ids is not None:
                rows = [row for row in rows if row.catalyst_event_id in event_ids]
        elif entity is CeriGuidanceEvent:
            id_values = _matching_identity_set(requested, {row.id for row in rows})
            company_values = _matching_identity_set(requested, {row.company_id for row in rows})
            if "ceri_guidance_events.id IN" in str(statement) and id_values is not None:
                rows = [row for row in rows if row.id in id_values]
            elif company_values is not None:
                rows = [row for row in rows if row.company_id in company_values]
        elif entity is CeriSourceRecord:
            source_ids = _matching_identity_set(requested, {row.id for row in rows})
            if source_ids is not None:
                rows = [row for row in rows if row.id in source_ids]
        self.materialized.setdefault(entity, []).extend(
            row.id for row in rows if getattr(row, "id", None) is not None
        )
        return Rows(rows)


def _matching_identity_set(requested: list[set], universe: set) -> set | None:
    matches = [values for values in requested if values and values <= universe]
    return min(matches, key=len) if matches else None


def _fixture_rows():
    known = CUTOFF - timedelta(days=3)
    companies = [CeriCompany(id=index, ticker=f"T{index}") for index in range(1, 5)]
    snapshots = [
        _snapshot(101, 1, 165, AS_OF - timedelta(days=1), known),
        _snapshot(102, 1, 166, AS_OF, CUTOFF - timedelta(minutes=1)),
        _snapshot(103, 1, 167, AS_OF + timedelta(days=1), CUTOFF + timedelta(days=1)),
        _snapshot(901, 2, 166, AS_OF, CUTOFF - timedelta(minutes=1)),
    ]
    sources = [_source(value, known) for value in (11, 12, 13, 14, 91, 92)]
    events = [
        CeriCatalystEvent(id=20, company_id=1, category="PRODUCT", subject_key="target"),
        CeriCatalystEvent(id=90, company_id=2, category="PRODUCT", subject_key="other"),
    ]
    revisions = [
        _revision(201, 20, 11, 1),
        _revision(202, 20, 12, 2),
        _revision(901, 90, 91, 1),
    ]
    guidance = [
        _guidance(301, 1, 13, None, "RAISED", known),
        _guidance(302, 1, 14, 301, "LOWERED", known + timedelta(hours=1)),
        _guidance(901, 2, 91, None, "RAISED", known),
    ]
    return {
        CeriCompany: companies,
        CeriScoreSnapshot: snapshots,
        CeriSourceRecord: sources,
        CeriCatalystEvent: events,
        CeriCatalystEventRevision: revisions,
        CeriGuidanceEvent: guidance,
    }


def _score_only_rows(company_count: int):
    companies = [CeriCompany(id=value, ticker=f"T{value}") for value in range(1, company_count + 1)]
    snapshots = [
        snapshot
        for value in range(1, company_count + 1)
        for snapshot in (
            _snapshot(
                value * 100 + 1, value, 165, AS_OF - timedelta(days=1), CUTOFF - timedelta(days=1)
            ),
            _snapshot(value * 100 + 2, value, 166, AS_OF, CUTOFF - timedelta(minutes=1)),
        )
    ]
    return {
        CeriCompany: companies,
        CeriScoreSnapshot: snapshots,
        CeriSourceRecord: [],
        CeriCatalystEvent: [],
        CeriCatalystEventRevision: [],
        CeriGuidanceEvent: [],
    }


def _snapshot(snapshot_id, company_id, run_id, session, cutoff):
    return CeriScoreSnapshot(
        id=snapshot_id,
        company_id=company_id,
        ticker=f"T{company_id}",
        run_id=run_id,
        as_of_session=session,
        cutoff_at=cutoff,
        opportunity_score=5,
        data_confidence="Normal",
        coverage_pct=100,
        posture="Mixed",
        config_version="test",
        config_hash="test",
        calculation_version="test",
        evidence_contract_version="test",
        comparison_state="COMPARABLE",
        evidence_hash=f"score-{snapshot_id}",
    )


def _source(source_id, known_at):
    return CeriSourceRecord(
        id=source_id,
        provider="test",
        dataset="test",
        provider_record_id=str(source_id),
        retrieved_at=known_at,
        ingested_at=known_at,
        content_hash=f"source-{source_id}",
        idempotency_key=f"source-{source_id}",
    )


def _revision(revision_id, event_id, source_id, number):
    return CeriCatalystEventRevision(
        id=revision_id,
        catalyst_event_id=event_id,
        source_record_id=source_id,
        revision_number=number,
        is_current=number == 2,
        announced_at=CUTOFF - timedelta(days=number),
        effective_session=AS_OF,
        status="ANNOUNCED",
        direction="POSITIVE",
    )


def _guidance(guidance_id, company_id, source_id, supersedes_id, action, effective_at):
    return CeriGuidanceEvent(
        id=guidance_id,
        source_record_id=source_id,
        company_id=company_id,
        action=action,
        effective_at=effective_at,
        effective_session=AS_OF,
        supersedes_id=supersedes_id,
        accepted_for_scoring=True,
    )
