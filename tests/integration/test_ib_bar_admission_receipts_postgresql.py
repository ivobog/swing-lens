"""Native acceptance evidence, repair unions and immutable historical projections."""

from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import IBBarAdmissionReceipt, IBFetchItem, PipelineRun, PriceBar, UploadRun
from app.services import ib_bar_admission_service as receipts
from app.services import ib_fetch_executor as executor
from app.services.bar_cache_service import cache_bars
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.core_mutation_authority import (
    _validate_technical_price_source_manifests,
    validate_price_source_manifest,
)
from app.services.ib_bar_admission_service import (
    load_bar_admissions,
    verify_bar_admission_receipt,
)
from app.services.ib_data_fetcher import HistoricalBar
from app.services.ib_fetch_job_service import FetchJobOptions, _admit_fetch_authority
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem
from app.services.ib_historical_request_scope import DAILY_BAR_ADMISSION_V2, DAILY_BAR_ADMISSION_V4
from app.services.market_calculation_context_service import create_pipeline_market_context
from app.services.price_bar_repository import (
    invalid_price_bar_rows_for_context,
    load_price_bar_rows_for_context,
    load_price_bars_frame,
)
from app.services.scope_refresh_adoption import admit_frozen_operation, bind_semantic_authority
from app.services.work_scope_identity import AcquisitionPlanRevisionReason, ScopeMember
from app.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
SESSION = date(2026, 11, 9)
GAP = date(2026, 11, 4)
CUTOFF = datetime(2026, 11, 9, 23, tzinfo=UTC)
CONTRACT = {
    "conId": 257944570, "symbol": "HLT", "secType": "STK", "exchange": "SMART",
    "primaryExchange": "NYSE", "currency": "USD", "localSymbol": "HLT",
    "tradingClass": "HLT",
}


@pytest.fixture
def receipt_engine(disposable_postgres_database):
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    try:
        yield engine
    finally:
        engine.dispose()


def _owner(db):
    upload = UploadRun(filename="gap-admission.csv", status="COMPLETED")
    db.add(upload)
    db.flush()
    parent = admit_frozen_operation(
        db, operation_kind="full-pipeline-run", subject_kind="ticker",
        members=(ScopeMember("TICKER", "HLT"),), cycle_key=f"receipt:{upload.id}",
        business_cutoff=CUTOFF, provider_source_class="PIPELINE", request_type="PIPELINE",
        requirements=(), policy_identity="receipt-native-v1", scope_definition={},
    )
    pipeline = PipelineRun(upload_run_id=upload.id, status="RUNNING")
    bind_semantic_authority(pipeline, parent)
    db.add(pipeline)
    db.flush()
    cutoff = create_pipeline_market_context(db, pipeline, cutoff_at=CUTOFF)
    db.commit()
    assert cutoff.latest_completed_session == SESSION
    return pipeline, cutoff


def _bar(day, close=100):
    return HistoricalBar(
        ticker="HLT", bar_date=day, timeframe="1 day", open=close,
        high=close+1, low=close-1, close=close, volume=1000, source="IB",
        what_to_show="TRADES", adjustment_type=None,
    )


def _plan(upload_id, start, end, *, duration="5 D", policy=DAILY_BAR_ADMISSION_V4):
    item = FetchPlanItem(
        ticker="HLT", contract_status="RESOLVED", what_to_show="TRADES",
        action=FetchAction.TOP_UP_RECENT, duration=duration, bar_size="1 day",
        current_bar_count=0, first_bar_date=None, latest_bar_date=None,
        required_bars=5, reason="native admitted gap window", estimated_request_count=1,
        freshness_threshold_date=SESSION, missing_start_date=start, missing_end_date=end,
        request_start_date=start, request_end_date=end,
        request_end_datetime=f"{end:%Y%m%d}-23:59:59", request_end_mode="EXPLICIT",
        contract_identity=CONTRACT, contract_fingerprint=Canonical.fingerprint(CONTRACT),
        contract_qualified_at=CUTOFF.isoformat(), response_admission_policy=policy,
    )
    return FetchPlan(
        run_id=upload_id, requested_tickers=["HLT"], symbols_including_benchmarks=["HLT"],
        items=[item], estimated_request_count=1, estimated_full_backfills=0,
        estimated_top_ups=1, estimated_refreshes=0, estimated_skips=0, warnings=[],
    )


class _IB:
    connected = False

    def connect(self, *args, **kwargs):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def isConnected(self):
        return self.connected


class _Limiter:
    def wait_before_request(self):
        pass

    def backoff_after_error(self, *args):
        raise AssertionError("Native acceptance must not retry provider calls")


def _execute(db, pipeline, monkeypatch, bars, *, start=date(2026, 11, 3), end=SESSION,
             duration="5 D", previous_plan_id=None, policy=DAILY_BAR_ADMISSION_V4):
    plan = _plan(pipeline.upload_run_id, start, end, duration=duration, policy=policy)
    authority = _admit_fetch_authority(
        db, plan, FetchJobOptions(include_benchmarks=False), parent_scope_id=pipeline.scope_id,
        previous_plan_id=previous_plan_id,
        revision_reason=(AcquisitionPlanRevisionReason.MISSING_SOURCE
                         if previous_plan_id is not None else None),
    )
    monkeypatch.setattr(executor, "fetch_daily_bars", lambda *args, **kwargs: list(bars))
    monkeypatch.setattr(executor, "latest_completed_us_trading_day", lambda: SESSION)
    monkeypatch.setattr(
        executor, "resolve_us_stock_contract",
        lambda *args, **kwargs: pytest.fail("Frozen contract must not be requalified"),
    )
    return executor.execute_fetch_plan(
        db=db, plan=plan, semantic_authority=authority, include_benchmarks=False,
        ib_client_factory=_IB, rate_limiter=_Limiter(),
        settings=Settings(ib_max_retries=1, ib_request_delay_seconds=0,
                          ib_min_seconds_between_requests=0,
                          ib_intelligence_historical_min_spacing_seconds=0),
    )


def _frame(db, cutoff):
    return load_price_bars_frame(
        db, "HLT", "TRADES", as_of=cutoff.cutoff_at, max_session=SESSION,
        calculation_context_id=cutoff.context_id,
    )


def _batch(db, cutoff):
    return load_price_bar_rows_for_context(
        db, ("HLT",), what_to_show=("TRADES",), timeframes=("1 day",),
        as_of=cutoff.cutoff_at, max_session=SESSION, calculation_context_id=cutoff.context_id,
    )


def test_partial_fetch_receipt_union_and_exact_historical_versions(receipt_engine, monkeypatch):
    with Session(receipt_engine) as db:
        pipeline, cutoff = _owner(db)
        assert _frame(db, cutoff).empty  # Negative authority cached before admission.
        bars = [_bar(date(2026, 11, day)) for day in (3, 5, 6, 9)]
        fetched = _execute(db, pipeline, monkeypatch, bars)
        item = db.scalar(select(IBFetchItem).where(IBFetchItem.fetch_run_id == fetched.id))
        assert item.status == "PARTIAL"
        assert (item.fetched, item.inserted, fetched.success_count, fetched.failure_count) == (
            4, 4, 0, 0,
        )
        receipt = load_bar_admissions(db, pipeline_run_id=pipeline.id)[0]
        assert receipt.missing_sessions == (GAP,)
        assert receipt.accepted_at <= item.completed_at
        assert item.decision_metadata_json["admission_receipt_hash"] == receipt.receipt_hash
        original = _frame(db, cutoff)
        original_manifest = deepcopy(original.attrs["pit_source_manifest"])
        assert list(original["date"]) == [bar.bar_date for bar in bars]
        validate_price_source_manifest(db, original_manifest, cutoff.cutoff_at, SESSION)
        original_rows = _batch(db, cutoff)
        assert len(original_rows) == 4
        assert invalid_price_bar_rows_for_context(
            db, original_rows, as_of=cutoff.cutoff_at, max_session=SESSION,
            calculation_context_id=cutoff.context_id,
        ) == ()
        # A repair with an earlier end must union with the original latest session.
        repair = _execute(db, pipeline, monkeypatch, [_bar(GAP)], start=GAP, end=GAP,
                          duration="1 D", previous_plan_id=receipt.acquisition_plan_id)
        repaired = _frame(db, cutoff)
        assert repair.success_count == 1
        assert list(repaired["date"]) == [date(2026, 11, day) for day in (3, 4, 5, 6, 9)]
        assert len(_batch(db, cutoff)) == 5
        assert len(load_bar_admissions(db, pipeline_run_id=pipeline.id)) == 2
        validate_price_source_manifest(db, original_manifest, cutoff.cutoff_at, SESSION)
        # New accepted revision is available to new loads; an old manifest pins its receipt.
        _execute(db, pipeline, monkeypatch, [_bar(SESSION, 120)], start=SESSION, end=SESSION,
                 duration="1 D", previous_plan_id=receipt.acquisition_plan_id)
        assert _frame(db, cutoff).iloc[-1]["close"] == 120
        validate_price_source_manifest(db, original_manifest, cutoff.cutoff_at, SESSION)
        assert invalid_price_bar_rows_for_context(
            db, original_rows, as_of=cutoff.cutoff_at, max_session=SESSION,
            calculation_context_id=cutoff.context_id,
        ) == ()
        # An unreceipted later cache revision cannot replace the latest accepted version.
        cache_bars(db, [_bar(SESSION, 140)])
        db.commit()
        assert db.scalar(select(PriceBar.close).where(PriceBar.bar_date == SESSION)) == 140
        assert _frame(db, cutoff).iloc[-1]["close"] == 120
        assert _batch(db, cutoff)[-1].close == 120
        verified = []
        native_verify = receipts.verify_bar_admission_receipt

        def observed_verify(value):
            verified.append(value.receipt_hash)
            return native_verify(value)

        db.commit()  # A fresh transaction must respect the captured receipt SQL filter.
        with monkeypatch.context() as filtered:
            filtered.setattr(receipts, "verify_bar_admission_receipt", observed_verify)
            validate_price_source_manifest(db, original_manifest, cutoff.cutoff_at, SESSION)
            _validate_technical_price_source_manifests(
                db, [original_manifest], cutoff=cutoff.cutoff_at, session=SESSION,
                checkpoint_callback=None, should_cancel=lambda: False,
                source_items=[{"digest": Canonical.fingerprint(original_manifest)}],
            )
        assert verified == [receipt.receipt_hash]
        # Another pipeline never inherits this pipeline's post-cutoff acceptance.
        _, unrelated = _owner(db)
        assert _frame(db, unrelated).empty
        altered = SimpleNamespace(**{name: getattr(receipt, name) for name in (
            "receipt_hash", "fetch_item_id", "fetch_run_id", "upload_run_id", "pipeline_run_id",
            "calculation_context_id", "acquisition_plan_id", "scope_id", "refresh_cycle_id",
            "ticker", "what_to_show", "timeframe", "policy_version", "accepted_at",
        )}, payload_json=deepcopy(receipt.payload_json))
        altered.payload_json["missing_sessions"] = []
        with pytest.raises(ValueError, match="RECEIPT_INTEGRITY_MISMATCH"):
            verify_bar_admission_receipt(altered)
        # Immutability and restrictive provenance FKs are native database contracts.
        for statement, identifier in (
            ("UPDATE ib_bar_admission_receipts SET ticker='FAKE' WHERE receipt_hash=:id",
             receipt.receipt_hash),
            ("DELETE FROM ib_bar_admission_receipts WHERE receipt_hash=:id", receipt.receipt_hash),
            ("DELETE FROM ib_fetch_items WHERE id=:id", receipt.fetch_item_id),
        ):
            with pytest.raises(DBAPIError), db.begin_nested():
                db.execute(text(statement), {"id": identifier})


def test_native_fetch_rolls_back_bars_when_receipt_sealing_fails(receipt_engine, monkeypatch):
    with Session(receipt_engine) as db:
        pipeline, cutoff = _owner(db)

        def rejected(*args, **kwargs):
            raise ValueError("IB_BAR_ADMISSION_CACHE_VERSION_MISMATCH")

        monkeypatch.setattr(executor, "_seal_bar_admission", rejected)
        fetched = _execute(db, pipeline, monkeypatch, [_bar(SESSION)], start=SESSION,
                           duration="1 D")
        item = db.scalar(select(IBFetchItem).where(IBFetchItem.fetch_run_id == fetched.id))
        assert item.status == "FAILED"
        assert (item.fetched, item.inserted, fetched.success_count, fetched.inserted_count) == (
            0, 0, 0, 0,
        )
        assert db.scalar(select(func.count()).select_from(PriceBar)) == 0
        assert db.scalar(select(func.count()).select_from(IBBarAdmissionReceipt)) == 0
        assert _frame(db, cutoff).empty
        # Status alone never grants V4 visibility, even if a cache row exists.
        cache_bars(db, [_bar(SESSION)], fetch_run_id=fetched.id, fetch_item_id=item.id)
        for status in ("FAILED", "PARTIAL", "SUCCESS"):
            item.status = status
            db.commit()
            assert _frame(db, cutoff).empty


def test_original_legacy_authority_shape_validates_after_v4_receipt(receipt_engine, monkeypatch):
    with Session(receipt_engine) as db:
        pipeline, cutoff = _owner(db)
        bars = [_bar(date(2026, 11, day)) for day in (3, 4, 5, 6, 9)]
        fetched = _execute(db, pipeline, monkeypatch, bars, policy=DAILY_BAR_ADMISSION_V2)
        assert fetched.success_count == 1
        assert load_bar_admissions(db, pipeline_run_id=pipeline.id) == []
        old_manifest = deepcopy(_frame(db, cutoff).attrs["pit_source_manifest"])
        assert set(old_manifest["acquisition_authority"]) == {
            "pipeline_run_id", "upload_run_id", "calculation_context_id", "acquisition_plan_id",
            "scope_id", "refresh_cycle_id", "fetch_run_id", "fetch_item_id", "completed_at",
        }

        def validate_original():
            validate_price_source_manifest(db, old_manifest, cutoff.cutoff_at, SESSION)
            _validate_technical_price_source_manifests(
                db, [old_manifest], cutoff=cutoff.cutoff_at, session=SESSION,
                checkpoint_callback=None, should_cancel=lambda: False,
                source_items=[{"digest": Canonical.fingerprint(old_manifest)}],
            )

        validate_original()
        _execute(db, pipeline, monkeypatch, [_bar(GAP)], start=GAP, end=GAP, duration="1 D")
        validate_original()
        assert len(_frame(db, cutoff)) == 5
        altered = deepcopy(old_manifest)
        altered["acquisition_authority"]["fetch_item_id"] += 100
        with pytest.raises(ValueError, match="ACQUISITION_AUTHORITY_MISMATCH"):
            validate_price_source_manifest(db, altered, cutoff.cutoff_at, SESSION)
