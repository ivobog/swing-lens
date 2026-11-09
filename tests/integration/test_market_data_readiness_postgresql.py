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


def test_symbol_change_recovery_retains_failed_attempt_and_source_plan(
    readiness_engine, monkeypatch,
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from app.models.tables import IBFetchItem, PriceBar
    from app.services import ib_contract_resolver, ib_fetch_executor, instrument_symbol_changes
    from app.services.ib_api import Contract
    from app.services.ib_fetch_plan_service import (
        FetchAction,
        FetchPlan,
        FetchPlanItem,
        fetch_plan_from_dict,
        fetch_plan_to_dict,
    )
    from app.services.ib_historical_request_scope import DAILY_BAR_ADMISSION_V3
    from app.settings import Settings

    session = date(2026, 11, 6)
    monkeypatch.setattr(instrument_symbol_changes, "datetime", SimpleNamespace(
        now=lambda _tz: datetime(2026, 11, 9, 10, tzinfo=UTC),
    ))
    monkeypatch.setattr(ib_fetch_executor, "latest_completed_us_trading_day", lambda: session)
    settings = Settings(_env_file=None, ib_max_retries=1,
                        technical_series_version_maintenance_enabled=False)
    item = FetchPlanItem(
        ticker="TCBI", contract_status="MISSING", what_to_show="TRADES",
        action=FetchAction.CONTRACT_RESOLUTION_REQUIRED, duration=None, bar_size="1 day",
        current_bar_count=0, first_bar_date=None, latest_bar_date=None, required_bars=252,
        reason="Frozen qualification-deferred plan", estimated_request_count=1,
        post_resolution_action=FetchAction.FULL_BACKFILL, post_resolution_duration="3 Y",
        request_end_date=session, freshness_threshold_date=session,
        response_admission_policy=DAILY_BAR_ADMISSION_V3,
    )
    plan = FetchPlan(
        run_id=None, requested_tickers=["TCBI"], symbols_including_benchmarks=["TCBI"],
        items=[item, replace(item, what_to_show="ADJUSTED_LAST")], estimated_request_count=2,
        estimated_full_backfills=2, estimated_top_ups=0, estimated_refreshes=0,
        estimated_skips=0, warnings=[],
    )

    class Gateway:
        RequestTimeout = 0

        def connect(self, *_a, **kwargs):
            assert kwargs["readonly"] is True
            self.connected = True

        def isConnected(self):
            return getattr(self, "connected", False)

        def disconnect(self):
            self.connected = False

        def qualifyContracts(self, requested):
            if requested.symbol == "TCBI":
                return []
            assert requested.conId == 21521400
            return [Contract(conId=21521400, symbol="TXCP", secType="STK", currency="USD",
                             exchange="SMART", primaryExchange="TXSE")]

        def reqHistoricalData(self, *_a, **_kwargs):
            return [SimpleNamespace(date=session, open=10, high=11, low=9, close=10, volume=100)]

    class Limiter:
        def wait_before_request(self):
            pass

    with Session(readiness_engine, expire_on_commit=False) as db:
        upload = UploadRun(filename="pre-rename.csv", status="COMPLETED", row_count=1)
        db.add(upload)
        db.commit()
        plan = replace(plan, run_id=upload.id)
        frozen = fetch_plan_to_dict(plan)
        record = AcquisitionPlanRecord(
            plan_id=uuid.uuid4().hex.ljust(64, "0"), plan_kind="SYMBOL_CHANGE_TEST",
            plan_version="v1", payload_json=frozen,
        )
        db.add(record)
        db.commit()
        pipeline = PipelineRun(
            upload_run_id=upload.id, status="BLOCKED", acquisition_plan_id=record.plan_id,
            result_json={"ib_fetch_plan": frozen},
        )
        db.add(pipeline)
        db.flush()
        context = MarketCalculationContext(
            pipeline_run_id=pipeline.id, upload_run_id=upload.id,
            cutoff_at=datetime.now(UTC) + timedelta(minutes=2),
            latest_completed_session=session, exchange_timezone="America/New_York",
            calendar_version="swinglens-us-equities-v2", bar_readiness_version="test",
            cutoff_reason="FROZEN_SYMBOL_CHANGE_RECOVERY_TEST",
        )
        db.add(context)
        db.commit()
        with monkeypatch.context() as before_fix:
            before_fix.setattr(ib_contract_resolver, "active_symbol_change", lambda _t: None)
            failed = ib_fetch_executor.execute_fetch_plan(
                db, plan, ib_client_factory=Gateway, rate_limiter=Limiter(), settings=settings,
            )
        assert failed.status == "FAILED"
        assert failed.executed_request_count == 0
        failed_id = failed.id
        old_items = list(db.scalars(
            select(IBFetchItem).where(IBFetchItem.fetch_run_id == failed_id)
        ))
        old_evidence = [(row.id, row.status, row.error_message, row.decision_metadata_json)
                        for row in old_items]
        assert len(old_items) == 2
        first = evaluate_and_persist_session_readiness(
            db, pipeline_run_id=pipeline.id, upload_run_id=upload.id, tickers=("TCBI",),
            market_cutoff=cutoff_from_row(context), fetch_run_id=failed_id,
        )
        assert first.rows[0].disposition == "UNKNOWN"
        db.commit()

        recovered = ib_fetch_executor.execute_fetch_plan(
            db, fetch_plan_from_dict(frozen), ib_client_factory=Gateway,
            rate_limiter=Limiter(), settings=settings,
        )
        assert recovered.status == "COMPLETED", recovered.message
        assert recovered.id != failed_id
        assert recovered.success_count == 2
        assert db.scalar(select(IBContract).where(IBContract.ticker == "TCBI")).symbol == "TXCP"
        bars = list(db.scalars(select(PriceBar)))
        assert {(bar.ticker, bar.bar_date, bar.what_to_show) for bar in bars} == {
            ("TCBI", session, "TRADES"), ("TCBI", session, "ADJUSTED_LAST"),
        }
        db.expire_all()
        assert db.get(AcquisitionPlanRecord, record.plan_id).payload_json == frozen
        assert [(row.id, row.status, row.error_message, row.decision_metadata_json)
                for row in old_items] == old_evidence
        assert all(row.decision_metadata_json["runtime_symbol_change"]["ib_conid"] == 21521400
                   for row in recovered.items)
        second = evaluate_and_persist_session_readiness(
            db, pipeline_run_id=pipeline.id, upload_run_id=upload.id, tickers=("TCBI",),
            market_cutoff=cutoff_from_row(context), fetch_run_id=recovered.id,
        )
        assert second.rows[0].revision == 2
        assert second.rows[0].disposition == "READY"
        assert second.accepted_inactive_tickers == ()
        assert db.scalar(select(InstrumentLifecycleRecord).where(
            InstrumentLifecycleRecord.ticker == "TCBI"
        )) is None


def test_contract_preflight_honors_only_pit_visible_terminal_evidence(readiness_engine):
    from types import SimpleNamespace

    from app.services.ib_fetch_executor import _preflight_terminal_exclusion

    observed = datetime.now(UTC)
    with Session(readiness_engine) as db:
        db.add(InstrumentLifecycleRecord(
            ticker="DEAD", lifecycle_state="DELISTED", revision=1, is_current_revision=True,
            effective_date=SESSION, observed_at=observed,
            evidence_json={"source": "test exchange notice"},
        ))
        db.commit()
        item = SimpleNamespace(ticker="DEAD", request_end_date=SESSION,
                               freshness_threshold_date=SESSION)
        assert not _preflight_terminal_exclusion(db, item, as_of=observed-timedelta(seconds=1))
        assert _preflight_terminal_exclusion(db, item, as_of=observed+timedelta(seconds=1))
        item.request_end_date = SESSION-timedelta(days=1)
        assert not _preflight_terminal_exclusion(db, item, as_of=observed+timedelta(seconds=1))


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
