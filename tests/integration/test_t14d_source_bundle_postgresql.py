"""Batch source authority keeps exact SQL bodies and transaction-local locks."""

import traceback
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.orm import Session
from test_ceri_batched_workflow_v2 import _execute_handler, _new_job, _seed_fixture

from app.models.ceri_tables import CeriCompany, CeriSourceRecord
from app.models.tables import PipelineRun, PriceBar, PriceBarRevision, RawCompanyRow
from app.services.background_job_service import JobStatus
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.job_handlers import execute_normalize_job, execute_rebuild_features_job
from app.services.market_calculation_context_service import create_pipeline_market_context
from app.services.price_bar_repository import project_price_bar_rows_as_of
from app.services.scope_refresh_adoption import admit_frozen_operation, bind_semantic_authority
from app.services.source_mutation_authority import (
    SOURCE_REFRESH_QUERY_PARAMETER_BUDGET,
    PrefetchedSourceBodies,
    _source_value,
    prefetched_source_scope,
)
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_historical_price_bar_projection_requires_retained_revision_body(contextual_engine):
    cutoff = datetime(2026, 9, 9, 20, tzinfo=UTC)
    revised_at = datetime(2026, 9, 10, 20, tzinfo=UTC)
    with Session(contextual_engine, expire_on_commit=False) as db:
        current = PriceBar(
            ticker="PITGUARD",
            bar_date=date(2026, 9, 9),
            timeframe="1 day",
            open=Decimal("100"),
            high=Decimal("103"),
            low=Decimal("99"),
            close=Decimal("102"),
            volume=Decimal("1000"),
            source="IB",
            what_to_show="TRADES",
            created_at=datetime(2026, 9, 9, 19, tzinfo=UTC),
            first_seen_at=datetime(2026, 9, 9, 19, tzinfo=UTC),
            last_seen_at=revised_at,
            revised_at=revised_at,
            revision_count=1,
            data_hash="after",
        )
        db.add(current)
        db.flush()
        db.add(
            PriceBarRevision(
                price_bar_id=current.id,
                ticker=current.ticker,
                bar_date=current.bar_date,
                timeframe=current.timeframe,
                what_to_show=current.what_to_show,
                revision_number=1,
                previous_data_hash="before",
                new_data_hash="after",
                previous_values_json={
                    "open": "100",
                    "high": "103",
                    "low": "99",
                    "close": "101",
                    "volume": "1000",
                    "source": "IB",
                    "what_to_show": "TRADES",
                },
                new_values_json={},
                observed_at=revised_at,
                created_at=revised_at,
            )
        )
        db.commit()
        historical = project_price_bar_rows_as_of(db, [current], as_of=cutoff)[0]
        assert historical.close == Decimal("101")
        assert _source_value(db, historical)["pit_projection"]["as_of"] == cutoff

        historical.close = Decimal("999")
        with pytest.raises(ValueError, match="MUTATION_SOURCE_PIT_PROJECTION_MISMATCH"):
            _source_value(db, historical)


def _normalized_fixture(db):
    run_id, ingestion_id = _seed_fixture(db, request_key="t14d-bundle:ingest:MSFT")
    job = _new_job(
        db,
        job_type="CERI_NORMALIZE",
        related_run_id=run_id,
        request_key="t14d-bundle:normalize:MSFT",
        status=JobStatus.RUNNING,
        payload_json={
            "request_key": "t14d-bundle:normalize:MSFT",
            "ingestion_run_id": ingestion_id,
            "provider": "eodhd",
            "dataset": "estimates",
            "ticker": "MSFT",
            "run_id": run_id,
            "scope": {"ticker": "MSFT", "run_id": run_id},
        },
    )
    _execute_handler(db, job, execute_normalize_job)
    return CeriFeatureRebuildRequest(ticker="MSFT", run_id=run_id, as_of_session=date(2026, 8, 12))


@pytest.mark.parametrize("populated", [False, True])
def test_all_selects_including_authority_are_within_12(contextual_engine, populated):
    statements = []

    def record(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)

    with Session(contextual_engine, expire_on_commit=False) as db:
        if populated:
            request = _normalized_fixture(db)
        else:
            db.add(CeriCompany(ticker="MSFT"))
            db.commit()
            request = CeriFeatureRebuildRequest(ticker="MSFT", as_of_session=date(2026, 8, 12))
        event.listen(contextual_engine, "before_cursor_execute", record)
        try:
            result = CeriFeatureRebuildService().rebuild(db, request)
            db.commit()
        finally:
            event.remove(contextual_engine, "before_cursor_execute", record)
        assert result.failed == 0 and result.companies_rebuilt == 1, result.as_dict()
        assert len(statements) <= 12
        assert not any("WHERE ceri_source_records.id =" in sql for sql in statements)


def test_unflushed_prefetched_source_argument_is_not_authoritative(contextual_engine):
    with Session(contextual_engine, expire_on_commit=False) as db:
        request = _normalized_fixture(db)
        service = CeriFeatureRebuildService()
        context = service.prepare_batch(db, request)
        source = next(iter(context.source_records_by_id.values()))
        original = source.content_hash
        source.content_hash = "unflushed-caller-forgery"
        result = service.rebuild(db, request, batch_context=context)
        assert result.failed == 1, result.as_dict()
        assert any(
            code in result.errors[0]["error"]
            for code in (
                "MUTATION_SOURCE_RECORD_ARGUMENT_MISMATCH",
                "MUTATION_SOURCE_BUNDLE_CHANGED",
            )
        )
        assert db.get(CeriSourceRecord, source.id).content_hash == original


def test_commit_revalidates_exact_sql_bodies_before_bundle_reuse(contextual_engine):
    with Session(contextual_engine, expire_on_commit=False) as db:
        request = _normalized_fixture(db)
        context = CeriFeatureRebuildService().prepare_batch(db, request)
        source = next(iter(context.source_records_by_id.values()))
        source_id = source.id
        db.commit()  # Releases the bundle's original locks.
        with Session(contextual_engine) as changed:
            changed.execute(
                update(CeriSourceRecord)
                .where(CeriSourceRecord.id == source_id)
                .values(content_hash="changed-retained-SQL-body")
            )
            changed.commit()
        with pytest.raises(ValueError, match="MUTATION_SOURCE_BUNDLE_CHANGED"):
            with prefetched_source_scope(db, context.source_bodies):
                _source_value(db, source)


def test_bundle_cannot_cross_validating_sessions(contextual_engine):
    with Session(contextual_engine, expire_on_commit=False) as db:
        request = _normalized_fixture(db)
        context = CeriFeatureRebuildService().prepare_batch(db, request)
        db.commit()
        with Session(contextual_engine) as other:
            with pytest.raises(ValueError, match="MUTATION_SOURCE_BUNDLE_SESSION_MISMATCH"):
                with prefetched_source_scope(other, context.source_bodies):
                    pytest.fail("foreign bundle reached a semantic operation")


def test_same_transaction_sql_change_invalidates_prefetched_body(contextual_engine):
    with Session(contextual_engine, expire_on_commit=False) as db:
        request = _normalized_fixture(db)
        service = CeriFeatureRebuildService()
        context = service.prepare_batch(db, request)
        source = next(iter(context.source_records_by_id.values()))
        original = source.content_hash
        db.execute(
            update(CeriSourceRecord)
            .where(CeriSourceRecord.id == source.id)
            .values(content_hash="changed-own-SQL-body")
            .execution_options(synchronize_session=False)
        )
        assert source.content_hash == original
        with pytest.raises(ValueError, match="MUTATION_SOURCE_BUNDLE_CHANGED"):
            service.rebuild(db, request, batch_context=context)
        assert db.get(CeriSourceRecord, source.id).content_hash == original


def test_advisory_lock_exclusion_is_exact_and_dml_cte_cannot_retain_witness(contextual_engine):
    with Session(contextual_engine, expire_on_commit=False) as db:
        request = _normalized_fixture(db)
        bundle = CeriFeatureRebuildService().prepare_batch(db, request).source_bodies
        source = next(iter(bundle._rows.values()))
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": "t14d-exact-nonmutating-lock"},
        )
        assert bundle._requires_revalidation is False
        for sql in (
            "SELECT 1",
            "SELECT pg_advisory_xact_lock(hashtextextended('different-text', 0))",
            "UPDATE ceri_source_records SET content_hash=content_hash WHERE false",
            "INSERT INTO ceri_source_records SELECT * FROM ceri_source_records WHERE false",
            "DELETE FROM ceri_source_records WHERE false",
        ):
            db.execute(text(sql))
            assert bundle._requires_revalidation is True, sql
            bundle.refresh()
            assert bundle._requires_revalidation is False
        source_id = next(key[1][0] for key in bundle.bodies if key[0] == "ceri_source_records")
        db.execute(
            text(
                "WITH changed AS (UPDATE ceri_source_records "
                "SET content_hash='T14D_CTE_FORGED' WHERE id=:id RETURNING id) "
                "SELECT id FROM changed"
            ),
            {"id": source_id},
        )
        assert bundle._requires_revalidation is True
        with pytest.raises(ValueError, match="MUTATION_SOURCE_BUNDLE_CHANGED"):
            with prefetched_source_scope(db, bundle):
                _source_value(db, source)
        assert db.get(CeriSourceRecord, source_id).content_hash != "T14D_CTE_FORGED"


def test_empty_exact_run_does_not_expand_to_all_companies(contextual_engine):
    from app.models.tables import UploadRun

    with Session(contextual_engine) as db:
        db.add(UploadRun(id=7, filename="t14d-empty-run.csv", status="COMPLETED"))
        db.add(CeriCompany(ticker="UNRELATED"))
        db.commit()
        result = CeriFeatureRebuildService().rebuild(
            db, CeriFeatureRebuildRequest(run_id=7, as_of_session=date(2026, 8, 12))
        )
        assert result.processed_companies == result.companies_rebuilt == result.failed == 0


def test_cached_body_edits_cannot_admit_an_unstored_source(contextual_engine):
    with Session(contextual_engine, expire_on_commit=False) as db:
        request = _normalized_fixture(db)
        service = CeriFeatureRebuildService()
        context = service.prepare_batch(db, request)
        source = next(iter(context.source_records_by_id.values()))
        key = (CeriSourceRecord.__tablename__, (source.id,))
        original = source.content_hash
        copied_body = context.source_bodies.bodies[key]
        copied_body["content_hash"] = "T14D_UNSTORED_SOURCE"
        assert context.source_bodies.bodies[key]["content_hash"] == original
        with pytest.raises(TypeError):
            context.source_bodies.bodies[key] = copied_body
        with pytest.raises(ValueError, match="BUNDLE_FROZEN"):
            context.source_bodies.load(CeriSourceRecord, select(CeriSourceRecord))
        with pytest.raises(AttributeError):
            context.source_bodies.transaction = db.get_transaction()
        with pytest.raises(AttributeError):
            context.source_bodies.db = db
        source.content_hash = copied_body["content_hash"]
        result = service.rebuild_from_context(
            db, company=context.companies[0], request=request, context=context
        )
        assert result.failed == 1
        assert source.content_hash == original


def test_physical_connection_commit_invalidates_source_locks(contextual_engine):
    with Session(contextual_engine, expire_on_commit=False) as db:
        request = _normalized_fixture(db)
        context = CeriFeatureRebuildService().prepare_batch(db, request)
        source = next(iter(context.source_records_by_id.values()))
        source_id = source.id
        db.connection().commit()
        with Session(contextual_engine) as changed:
            changed.execute(
                update(CeriSourceRecord)
                .where(CeriSourceRecord.id == source_id)
                .values(content_hash="changed-after-physical-commit")
            )
            changed.commit()
        with pytest.raises(ValueError, match="MUTATION_SOURCE_BUNDLE_CHANGED"):
            with prefetched_source_scope(db, context.source_bodies):
                _source_value(db, source)


@pytest.mark.parametrize("company_count", [1, 50])
def test_pipeline_feature_authority_selects_remain_within_14_after_explicit_scope_mode(
    contextual_engine, company_count
):
    statements = []

    def record(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().upper().startswith("SELECT") and any(
            frame.filename.endswith("feature_rebuild_service.py")
            for frame in traceback.extract_stack()
        ):
            statements.append(sql)

    with Session(contextual_engine, expire_on_commit=False) as db:
        request = _normalized_fixture(db)
        if company_count > 1:
            db.add_all(CeriCompany(ticker=f"T14DP{i:03d}") for i in range(company_count - 1))
            db.add_all(
                RawCompanyRow(
                    run_id=request.run_id,
                    row_number=i + 2,
                    ticker=f"T14DP{i:03d}",
                    raw_json={"Symbol": f"T14DP{i:03d}"},
                )
                for i in range(company_count - 1)
            )
        db.flush()
        authority = admit_frozen_operation(
            db,
            operation_kind="full-pipeline-run",
            subject_kind="ticker",
            members=tuple(
                ScopeMember("TICKER", ticker)
                for ticker in db.scalars(
                    select(RawCompanyRow.ticker).where(RawCompanyRow.run_id == request.run_id)
                )
            ),
            cycle_key=f"t14d-pipeline-budget:{company_count}",
            business_cutoff=date(2026, 8, 12),
            provider_source_class="CERI",
            request_type="FEATURE_REBUILD",
            requirements=(AcquisitionRequirement("CERI_SOURCE_RECORDS"),),
            policy_identity="t14d-pipeline-budget-v1",
            scope_definition={"run_id": request.run_id},
        )
        pipeline = PipelineRun(upload_run_id=request.run_id, status="RUNNING")
        bind_semantic_authority(pipeline, authority)
        db.add(pipeline)
        db.flush()
        cutoff = create_pipeline_market_context(
            db, pipeline, cutoff_at=datetime(2026, 8, 12, 20, tzinfo=UTC)
        )
        job = _new_job(
            db,
            job_type="CERI_REBUILD_FEATURES",
            related_run_id=request.run_id,
            request_key="t14d-pipeline-budget",
            status=JobStatus.RUNNING,
            priority=1,
            payload_json={
                "request_key": "t14d-pipeline-budget",
                "run_id": request.run_id,
                "company_ids": list(db.scalars(select(CeriCompany.id))),
                "pipeline_run_id": pipeline.id,
                "calculation_context_id": cutoff.context_id,
                "cutoff_at": cutoff.cutoff_at.isoformat(),
                "as_of_session": cutoff.latest_completed_session.isoformat(),
                "calendar_version": cutoff.calendar_version,
            },
        )
        bind_semantic_authority(job, authority)
        db.flush()
        event.listen(contextual_engine, "before_cursor_execute", record)
        try:
            result = _execute_handler(db, job, execute_rebuild_features_job)
        finally:
            event.remove(contextual_engine, "before_cursor_execute", record)
        assert result["companies_rebuilt"] == company_count, result
        # The explicit membership-mode gate adds one set-based WorkScopeRecord
        # authority read. The 1/50 bound remains identical and no per-company
        # query may appear.
        assert len(statements) <= 14, len(statements)


def test_price_bar_source_bundle_refresh_chunks_75k_identities_and_detects_mutation(
    contextual_engine,
):
    refresh_parameter_counts = []

    def record(_conn, _cursor, sql, parameters, _context, _many):
        normalized = sql.upper()
        if "FROM PRICE_BARS" in normalized and " IN (" in normalized and "FOR UPDATE" in normalized:
            refresh_parameter_counts.append(len(parameters))

    with Session(contextual_engine, expire_on_commit=False) as db:
        db.execute(
            text(
                """
                INSERT INTO price_bars (
                    ticker, bar_date, timeframe, open, high, low, close, volume,
                    source, what_to_show, created_at, first_seen_at, last_seen_at,
                    revision_count
                )
                SELECT
                    'T14DS' || lpad(((value - 1) / 1500)::text, 2, '0'),
                    DATE '2020-01-01' + ((value - 1) % 1500)::int,
                    '1 day', 100, 101, 99, 100, 1000,
                    'IB', 'TRADES', now(), now(), now(), 0
                FROM generate_series(1, 75000) AS value
                """
            )
        )
        db.commit()

        bundle = PrefetchedSourceBodies(db)
        rows = bundle.load(
            PriceBar,
            select(PriceBar).where(PriceBar.ticker.like("T14DS%")),
        )
        assert len(rows) == 75_000
        bundle.seal()
        highest_id = max(row.id for row in rows)
        db.commit()  # Releases the original source locks and forces refresh.

        event.listen(contextual_engine, "before_cursor_execute", record)
        try:
            with prefetched_source_scope(db, bundle):
                assert _source_value(db, bundle)["exact_prefetched_source_count"] == 75_000
            db.commit()

            with Session(contextual_engine) as changed:
                changed.execute(
                    update(PriceBar)
                    .where(PriceBar.id == highest_id)
                    .values(data_hash="changed-in-second-refresh-chunk")
                )
                changed.commit()

            with pytest.raises(ValueError, match="MUTATION_SOURCE_BUNDLE_CHANGED"):
                with prefetched_source_scope(db, bundle):
                    _source_value(db, bundle)
        finally:
            event.remove(contextual_engine, "before_cursor_execute", record)

    assert refresh_parameter_counts == [50_000, 25_000, 50_000, 25_000]
    assert max(refresh_parameter_counts) <= SOURCE_REFRESH_QUERY_PARAMETER_BUDGET


def test_source_bundle_refresh_detects_missing_retained_identity(contextual_engine):
    with Session(contextual_engine, expire_on_commit=False) as db:
        row = PriceBar(
            ticker="T14DMISSING",
            bar_date=date(2026, 9, 10),
            timeframe="1 day",
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1000"),
            source="IB",
            what_to_show="TRADES",
        )
        db.add(row)
        db.commit()

        bundle = PrefetchedSourceBodies(db)
        retained = bundle.load(PriceBar, select(PriceBar).where(PriceBar.id == row.id))
        assert len(retained) == 1
        bundle.seal()
        retained_id = row.id
        db.commit()

        with Session(contextual_engine) as changed:
            changed.execute(delete(PriceBar).where(PriceBar.id == retained_id))
            changed.commit()

        with pytest.raises(ValueError, match="MUTATION_SOURCE_RECORD_MISSING"):
            with prefetched_source_scope(db, bundle):
                _source_value(db, bundle)
