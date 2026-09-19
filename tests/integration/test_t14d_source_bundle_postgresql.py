"""Batch source authority keeps exact SQL bodies and transaction-local locks."""

import traceback
from datetime import UTC, date, datetime

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session
from test_ceri_batched_workflow_v2 import _execute_handler, _new_job, _seed_fixture

from app.models.ceri_tables import CeriCompany, CeriSourceRecord
from app.models.tables import PipelineRun, RawCompanyRow
from app.services.background_job_service import JobStatus
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.job_handlers import execute_normalize_job, execute_rebuild_features_job
from app.services.market_calculation_context_service import create_pipeline_market_context
from app.services.source_mutation_authority import _source_value, prefetched_source_scope

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


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
def test_pipeline_feature_authority_selects_remain_within_12(contextual_engine, company_count):
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
        pipeline = PipelineRun(upload_run_id=request.run_id, status="RUNNING")
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
        event.listen(contextual_engine, "before_cursor_execute", record)
        try:
            result = _execute_handler(db, job, execute_rebuild_features_job)
        finally:
            event.remove(contextual_engine, "before_cursor_execute", record)
        assert result["companies_rebuilt"] == company_count, result
        assert len(statements) <= 12, len(statements)
