from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import (
    AcquisitionPlanRecord,
    IBContract,
    InstrumentLifecycleRecord,
    MarketCalculationContext,
    MarketDataSessionDisposition,
    PipelineRun,
    UploadRun,
)
from app.services.market_calculation_context_service import cutoff_from_row
from app.services.market_data_session_readiness import (
    InstrumentLifecycleState,
    MarketDataDisposition,
    evaluate_and_persist_session_readiness,
    load_current_session_readiness,
)
from scripts.instrument_lifecycle_admin import (
    LifecycleOperatorCommand,
    record_operator_lifecycle,
)

SESSION = date(2026, 10, 6)


@pytest.fixture
def readiness_engine(disposable_postgres_database):
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    from sqlalchemy import create_engine

    engine = create_engine(disposable_postgres_database)
    try:
        yield engine
    finally:
        engine.dispose()


def test_lifecycle_readiness_revision_resume_and_frozen_plan(readiness_engine) -> None:
    cutoff_at = datetime.now(UTC) + timedelta(minutes=1)
    with Session(readiness_engine, expire_on_commit=False) as db:
        run, pipeline, context, plan = _seed_context(db, cutoff_at=cutoff_at)
        contract = IBContract(
            ticker="DEAD",
            ib_conid=321,
            symbol="DEAD",
            exchange="SMART",
            currency="USD",
            sec_type="STK",
            resolution_status="RESOLVED",
        )
        db.add(contract)
        db.commit()

        first = evaluate_and_persist_session_readiness(
            db,
            pipeline_run_id=pipeline.id,
            upload_run_id=run.id,
            tickers=("DEAD",),
            market_cutoff=cutoff_from_row(context),
            fetch_run_id=None,
        )
        assert first.rows[0].disposition == MarketDataDisposition.REQUIRED_DATA_UNAVAILABLE
        assert first.rows[0].revision == 1
        db.commit()

        lifecycle = record_operator_lifecycle(
            db,
            LifecycleOperatorCommand(
                ticker="DEAD",
                lifecycle_state=InstrumentLifecycleState.MERGED,
                actor="certification-operator",
                source="exchange-notice",
                source_reference="notice-2026-10-06",
                reason="Merger completed",
                evidence_note="Issuer ceased trading after the October 5 session.",
                confirm_ticker="DEAD",
                effective_date=SESSION,
                last_trading_date=date(2026, 10, 5),
                successor_ticker="NEXT",
                contract_valid_to=date(2026, 10, 5),
            ),
        )
        db.commit()
        assert lifecycle.revision == 1
        assert lifecycle.evidence_json["actor"] == "certification-operator"
        assert contract.resolution_status == "FAILED"
        assert contract.ib_conid is None
        assert db.get(AcquisitionPlanRecord, plan.plan_id).payload_json == plan.payload_json

        second = evaluate_and_persist_session_readiness(
            db,
            pipeline_run_id=pipeline.id,
            upload_run_id=run.id,
            tickers=("DEAD",),
            market_cutoff=cutoff_from_row(context),
            fetch_run_id=None,
        )
        assert second.rows[0].disposition == MarketDataDisposition.TERMINAL_INACTIVE
        assert second.rows[0].revision == 2
        assert second.technical_tickers == ()
        assert second.downstream_tickers == ()
        db.commit()
        pipeline_id = pipeline.id

    with Session(readiness_engine) as resumed_db:
        resumed = load_current_session_readiness(resumed_db, pipeline_run_id=pipeline_id)
        assert resumed is not None
        assert resumed.rows[0].revision == 2
        assert resumed.rows[0].disposition == MarketDataDisposition.TERMINAL_INACTIVE
        history = tuple(
            resumed_db.scalars(
                select(MarketDataSessionDisposition)
                .where(MarketDataSessionDisposition.pipeline_run_id == pipeline_id)
                .order_by(MarketDataSessionDisposition.revision)
            )
        )
        assert [row.revision for row in history] == [1, 2]
        assert [row.is_current_revision for row in history] == [False, True]


def test_postgresql_rejects_invalid_lifecycle_and_readiness_authorities(
    readiness_engine,
) -> None:
    with Session(readiness_engine) as db:
        run, pipeline, context, _plan = _seed_context(
            db, cutoff_at=datetime.now(UTC) + timedelta(minutes=1)
        )
        db.commit()

        invalid_lifecycle = InstrumentLifecycleRecord(
            ticker="INVALID",
            lifecycle_state="MERGED",
            evidence_json={"source": "certification"},
            revision=1,
            is_current_revision=True,
        )
        with pytest.raises(IntegrityError) as lifecycle_error, db.begin_nested():
            db.add(invalid_lifecycle)
            db.flush()
        assert (
            lifecycle_error.value.orig.diag.constraint_name
            == "ck_instrument_lifecycle_terminal_boundary"
        )

        invalid_ready = _disposition(
            pipeline,
            run,
            context,
            ticker="BADREADY",
            revision=1,
            disposition="READY",
            lifecycle_state="ACTIVE",
            technical_eligible=True,
            downstream_eligible=True,
            latest_bar_session=None,
        )
        with pytest.raises(IntegrityError) as ready_error, db.begin_nested():
            db.add(invalid_ready)
            db.flush()
        assert ready_error.value.orig.diag.constraint_name == "ck_market_data_session_ready_fresh"

        db.add(
            _disposition(
                pipeline,
                run,
                context,
                ticker="DUP",
                revision=1,
                disposition="UNKNOWN",
                lifecycle_state="UNKNOWN",
                technical_eligible=False,
                downstream_eligible=False,
            )
        )
        db.flush()
        duplicate_current = _disposition(
            pipeline,
            run,
            context,
            ticker="DUP",
            revision=2,
            disposition="REQUIRED_DATA_UNAVAILABLE",
            lifecycle_state="UNKNOWN",
            technical_eligible=False,
            downstream_eligible=False,
        )
        with pytest.raises(IntegrityError) as current_error, db.begin_nested():
            db.add(duplicate_current)
            db.flush()
        assert (
            current_error.value.orig.diag.constraint_name
            == "uq_market_data_disposition_one_current"
        )


def test_lifecycle_evidence_is_point_in_time_bounded(readiness_engine) -> None:
    with Session(readiness_engine, expire_on_commit=False) as db:
        active = record_operator_lifecycle(
            db,
            LifecycleOperatorCommand(
                ticker="PIT",
                lifecycle_state=InstrumentLifecycleState.ACTIVE,
                actor="certification-operator",
                source="exchange-status",
                source_reference="active-reference",
                reason="Active at observation time",
                evidence_note="Pre-freeze evidence.",
                confirm_ticker="PIT",
                effective_date=SESSION,
            ),
        )
        db.commit()
        frozen_at = datetime.now(UTC)
        assert active.observed_at <= frozen_at

        merged = record_operator_lifecycle(
            db,
            LifecycleOperatorCommand(
                ticker="PIT",
                lifecycle_state=InstrumentLifecycleState.MERGED,
                actor="certification-operator",
                source="exchange-notice",
                source_reference="post-freeze-merger",
                reason="Merger learned after freeze",
                evidence_note="Post-freeze evidence must not rewrite the frozen run.",
                confirm_ticker="PIT",
                effective_date=SESSION,
                last_trading_date=date(2026, 10, 5),
            ),
        )
        db.commit()
        assert merged.observed_at > frozen_at
        run, pipeline, context, _plan = _seed_context(db, cutoff_at=frozen_at)
        db.commit()

        result = evaluate_and_persist_session_readiness(
            db,
            pipeline_run_id=pipeline.id,
            upload_run_id=run.id,
            tickers=("PIT",),
            market_cutoff=cutoff_from_row(context),
            fetch_run_id=None,
        )

        assert result.rows[0].lifecycle_state == InstrumentLifecycleState.ACTIVE
        assert result.rows[0].disposition == MarketDataDisposition.REQUIRED_DATA_UNAVAILABLE
        assert result.rows[0].evidence_json["lifecycle_record_id"] == active.id


def _seed_context(db: Session, *, cutoff_at: datetime):
    plan = AcquisitionPlanRecord(
        plan_id=("readiness-certification-" + uuid.uuid4().hex).ljust(64, "0")[:64],
        plan_kind="READINESS_CERTIFICATION",
        plan_version="v1",
        payload_json={"ticker": "DEAD", "contract_identity": {"conId": 321}},
    )
    run = UploadRun(filename="readiness-certification.csv", row_count=1, status="COMPLETED")
    db.add_all([plan, run])
    db.flush()
    pipeline = PipelineRun(
        upload_run_id=run.id,
        acquisition_plan_id=plan.plan_id,
        status="PREPARING",
    )
    db.add(pipeline)
    db.flush()
    context = MarketCalculationContext(
        pipeline_run_id=pipeline.id,
        upload_run_id=run.id,
        cutoff_at=cutoff_at,
        exchange_timezone="America/New_York",
        latest_completed_session=SESSION,
        calendar_version="certification-v1",
        bar_readiness_version="certification-v1",
        cutoff_reason="READINESS_CERTIFICATION",
    )
    db.add(context)
    db.flush()
    return run, pipeline, context, plan


def _disposition(
    pipeline,
    run,
    context,
    *,
    ticker,
    revision,
    disposition,
    lifecycle_state,
    technical_eligible,
    downstream_eligible,
    latest_bar_session=None,
):
    return MarketDataSessionDisposition(
        pipeline_run_id=pipeline.id,
        upload_run_id=run.id,
        market_calculation_context_id=context.id,
        ticker=ticker,
        expected_session=SESSION,
        latest_bar_session=latest_bar_session,
        disposition=disposition,
        lifecycle_state=lifecycle_state,
        technical_eligible=technical_eligible,
        downstream_eligible=downstream_eligible,
        reason_code="CERTIFICATION",
        reason_message="Certification fixture",
        evidence_json={"source": "certification"},
        revision=revision,
        is_current_revision=True,
    )


def test_explicit_partial_admission_freezes_subset_and_is_idempotent(readiness_engine, monkeypatch):
    from app.models.tables import RawCompanyRow, WorkScopeRecord
    from app.services import pipeline_service
    from app.services.pipeline_partial_results import filter_pipeline_rows, pipeline_scope_tickers

    with Session(readiness_engine, expire_on_commit=False) as db:
        run = UploadRun(filename="partial.csv", row_count=2, status="COMPLETED")
        db.add(run)
        db.flush()
        raw = [RawCompanyRow(run_id=run.id, ticker=ticker, row_number=i, raw_json={"ticker":ticker})
               for i, ticker in enumerate(("MSFT", "ROKU"), 1)]
        db.add_all(raw)
        source = pipeline_service.start_pipeline(
            db, run.id, ceri_run_capture_enabled=False, ceri_provider_ingest_enabled=False,
            setup_lifecycle_pipeline_step_enabled=False,
        )
        source.status = "BLOCKED"
        source.current_step = "FETCHING_MARKET_DATA"
        context = db.scalar(select(MarketCalculationContext).where(
            MarketCalculationContext.pipeline_run_id == source.id))
        for ticker, ready in (("MSFT", True), ("ROKU", False)):
            db.add(MarketDataSessionDisposition(
                pipeline_run_id=source.id, upload_run_id=run.id,
                market_calculation_context_id=context.id, ticker=ticker,
                expected_session=context.latest_completed_session,
                latest_bar_session=context.latest_completed_session if ready else None,
                disposition="READY" if ready else "REQUIRED_DATA_UNAVAILABLE",
                lifecycle_state="UNKNOWN", technical_eligible=ready, downstream_eligible=ready,
                reason_code=(
                    "EXPECTED_SESSION_AVAILABLE" if ready else "MARKET_DATA_PROVIDER_REJECTED"
                ),
                reason_message="fixture", evidence_json={"source":"fixture"},
                revision=1, is_current_revision=True,
            ))
        db.commit()
        original_scope = source.scope_id
        original_result = dict(source.result_json)
        child = pipeline_service.start_pipeline(
            db, run.id, partial_source_pipeline_id=source.id, excluded_tickers=("ROKU",),
            ceri_run_capture_enabled=False, ceri_provider_ingest_enabled=False,
            setup_lifecycle_pipeline_step_enabled=False,
        )
        db.commit()
        assert child.id != source.id
        assert child.upload_run_id != source.upload_run_id
        assert db.get(UploadRun, child.upload_run_id).row_count == 1
        assert list(db.scalars(select(RawCompanyRow.ticker).where(
            RawCompanyRow.run_id == child.upload_run_id))) == ["MSFT"]
        assert child.scope_id != original_scope
        assert pipeline_scope_tickers(db, source.id) == ("MSFT", "ROKU")
        assert pipeline_scope_tickers(db, child.id) == ("MSFT",)
        assert [row.ticker for row in filter_pipeline_rows(db, raw, child.id)] == ["MSFT"]
        assert source.status == "BLOCKED" and source.scope_id == original_scope
        assert source.result_json == original_result
        scope = db.get(WorkScopeRecord, child.scope_id)
        assert scope.parent_scope_id == original_scope
        again = pipeline_service.start_pipeline(
            db, run.id, partial_source_pipeline_id=source.id, excluded_tickers=("ROKU",),
        )
        assert again.id == child.id
