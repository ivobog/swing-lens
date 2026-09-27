"""Production-shape regression for scoped CERI change detection."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriGuidanceEvent,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import UploadRun
from app.services.ceri.change_detection_service import ChangeDetectionResult
from app.services.ceri.change_rebuild_service import (
    CeriChangeRebuildRequest,
    CeriChangeRebuildService,
)

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]

TABLES = (
    "ceri_score_snapshots",
    "ceri_catalyst_events",
    "ceri_catalyst_event_revisions",
    "ceri_guidance_events",
    "ceri_source_records",
)


def test_high_cardinality_unrelated_rows_never_enter_scoped_change_reads(
    contextual_engine, monkeypatch, record_property
):
    target_count = 5
    unrelated_count = 250
    as_of = date(2026, 9, 25)
    cutoff = datetime(2026, 9, 25, 22, tzinfo=UTC)
    with Session(contextual_engine) as db:
        target_ids, target_source_ids, target_run_id = _seed_population(
            db,
            target_count=target_count,
            unrelated_count=unrelated_count,
            as_of=as_of,
            cutoff=cutoff,
        )
        db.commit()
        detector = RecordingDetector()
        captured: list[tuple[str, object]] = []
        materialized: Counter[str] = Counter()

        from app.services.ceri import change_rebuild_service as module

        original = module._scoped_scalars

        def observe_materialization(session, statement):
            rows = original(session, statement)
            entity = statement.column_descriptions[0].get("entity")
            table = getattr(entity, "__tablename__", None)
            if table in TABLES:
                materialized[table] += len(rows)
            return rows

        def observe_sql(_connection, _cursor, sql, parameters, _context, _many):
            if sql.lstrip().upper().startswith("SELECT") and any(table in sql for table in TABLES):
                captured.append((sql, parameters))

        monkeypatch.setattr(module, "_scoped_scalars", observe_materialization)
        event.listen(contextual_engine, "before_cursor_execute", observe_sql)
        try:
            result = CeriChangeRebuildService(
                detector=detector,
                company_chunk_size=2,
            ).rebuild(
                db,
                CeriChangeRebuildRequest(
                    company_ids=tuple(target_ids),
                    run_id=target_run_id,
                    as_of_session=as_of,
                    cutoff_at=cutoff,
                ),
            )
        finally:
            event.remove(contextual_engine, "before_cursor_execute", observe_sql)

        assert result.failed == 0
        assert set(detector.company_ids) == set(target_ids)
        assert len(detector.score_ids) == target_count
        assert len(detector.revision_ids) == target_count
        assert len(detector.guidance_ids) == target_count
        assert captured
        assert not [sql for sql, _params in captured if not re.search(r"\bWHERE\b", sql, re.I)]

        source_body_queries = [
            (sql, params)
            for sql, params in captured
            if re.match(r"\s*SELECT\s+ceri_source_records\.id", sql, re.I)
        ]
        assert source_body_queries
        loaded_source_ids = {
            value
            for _sql, params in source_body_queries
            for value in _integer_parameters(params)
            if value in target_source_ids
        }
        assert loaded_source_ids == target_source_ids
        assert not {
            value
            for _sql, params in source_body_queries
            for value in _integer_parameters(params)
            if value not in target_source_ids
        }

        query_counts = {table: sum(table in sql for sql, _params in captured) for table in TABLES}
        max_parameters = max(_parameter_count(params) for _sql, params in captured)
        record_property("unrelated_companies", unrelated_count)
        record_property("unrelated_source_records", unrelated_count * 2)
        record_property("scoped_companies", target_count)
        record_property("materialized_rows", dict(materialized))
        record_property("source_body_rows", len(loaded_source_ids))
        record_property("query_counts", query_counts)
        record_property("maximum_parameter_count", max_parameters)
        record_property("predicate_free_large_ceri_reads", 0)

        assert materialized["ceri_score_snapshots"] <= target_count * 2
        assert materialized["ceri_catalyst_events"] == target_count
        assert materialized["ceri_catalyst_event_revisions"] == target_count
        assert materialized["ceri_guidance_events"] == target_count
        assert max_parameters <= 10


class RecordingDetector:
    def __init__(self):
        self.company_ids: list[int] = []
        self.score_ids: list[int] = []
        self.revision_ids: list[int] = []
        self.guidance_ids: list[int] = []

    def detect_score_changes(self, _db, *, current, prior, scope):
        del prior, scope
        self.company_ids.append(current.company_id)
        self.score_ids.append(current.id)
        return ChangeDetectionResult(0, 0)

    def detect_catalyst_revision(self, _db, *, revision, prior_revision, company_id, market_cutoff):
        del prior_revision, company_id, market_cutoff
        self.revision_ids.append(revision.id)
        return ChangeDetectionResult(0, 0)

    def detect_guidance_change(self, _db, *, guidance, **_kwargs):
        self.guidance_ids.append(guidance.id)
        return ChangeDetectionResult(0, 0)


def _seed_population(db, *, target_count, unrelated_count, as_of, cutoff):
    target_ids: list[int] = []
    target_source_ids: set[int] = set()
    target_run = UploadRun(filename="ceri-change-target.csv", status="COMPLETED", row_count=0)
    unrelated_run = UploadRun(filename="ceri-change-unrelated.csv", status="COMPLETED", row_count=0)
    db.add_all((target_run, unrelated_run))
    db.flush()
    total = target_count + unrelated_count
    for index in range(total):
        company = CeriCompany(ticker=f"SCOPE{index:04d}", exchange="US")
        db.add(company)
        db.flush()
        is_target = index < target_count
        if is_target:
            target_ids.append(company.id)
        revision_source = _source(f"revision-{index}", cutoff - timedelta(days=2))
        guidance_source = _source(f"guidance-{index}", cutoff - timedelta(days=2))
        db.add_all((revision_source, guidance_source))
        db.flush()
        if is_target:
            target_source_ids.update((revision_source.id, guidance_source.id))
        catalyst = CeriCatalystEvent(
            company_id=company.id,
            category="PRODUCT",
            subject_key=f"subject-{index}",
        )
        db.add(catalyst)
        db.flush()
        db.add(
            CeriCatalystEventRevision(
                catalyst_event_id=catalyst.id,
                source_record_id=revision_source.id,
                revision_number=1,
                is_current=True,
                announced_at=cutoff - timedelta(days=1),
                effective_session=as_of,
                status="ANNOUNCED",
                direction="POSITIVE",
            )
        )
        db.add(
            CeriGuidanceEvent(
                source_record_id=guidance_source.id,
                company_id=company.id,
                action="RAISED",
                effective_at=cutoff - timedelta(days=1),
                effective_session=as_of,
                accepted_for_scoring=True,
            )
        )
        db.add(
            CeriScoreSnapshot(
                run_id=target_run.id if is_target else unrelated_run.id,
                company_id=company.id,
                ticker=company.ticker,
                as_of_session=as_of,
                cutoff_at=cutoff - timedelta(minutes=1),
                opportunity_score=5,
                data_confidence="Normal",
                coverage_pct=100,
                posture="Mixed",
                config_version="test",
                config_hash="test",
                calculation_version="test",
                evidence_contract_version="test",
                comparison_state="NO_PRIOR_COMPARABLE_SNAPSHOT",
                evidence_hash=f"score-{index}",
            )
        )
    return target_ids, target_source_ids, target_run.id


def _source(key, known_at):
    return CeriSourceRecord(
        provider="test",
        dataset="test",
        provider_record_id=key,
        retrieved_at=known_at,
        ingested_at=known_at,
        raw_json={"key": key},
        content_hash=f"hash-{key}",
        idempotency_key=f"scope-{key}",
    )


def _integer_parameters(parameters) -> set[int]:
    values: set[int] = set()
    if isinstance(parameters, dict):
        items = parameters.values()
    elif isinstance(parameters, (tuple, list)):
        items = parameters
    else:
        items = ()
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            values.add(item)
        elif isinstance(item, (tuple, list, dict)):
            values.update(_integer_parameters(item))
    return values


def _parameter_count(parameters) -> int:
    if isinstance(parameters, dict):
        return sum(_parameter_count(value) for value in parameters.values())
    if isinstance(parameters, (tuple, list)):
        return sum(_parameter_count(value) for value in parameters)
    return 1
