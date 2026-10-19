"""Run 43 regression: provider endpoints do not expand admitted coverage."""

from dataclasses import replace
from datetime import date

import pytest
from test_ib_fetch_executor import FakeDb, FakeIB, FakeLimiter, _bar, _fetch_plan, _plan_item

import app.services.ib_fetch_executor as executor
from app.services.bar_cache_service import BarUpsertSummary
from app.services.ib_fetch_plan_service import FetchAction, fetch_plan_from_dict, fetch_plan_to_dict
from app.services.ib_historical_request_scope import DAILY_BAR_ADMISSION_V2, STRICT_RESPONSE_V1
from app.services.market_data_session_readiness import classify_session_readiness
from app.services.us_market_calendar import next_us_trading_day
from app.settings import Settings


def daily_case(feed="TRADES", ticker="DDS", start=date(2026, 9, 28)):
    plan = replace(
        _plan_item(ticker, FetchAction.TOP_UP_RECENT, "14 D"),
        response_admission_policy=DAILY_BAR_ADMISSION_V2,
        what_to_show=feed,
        latest_bar_date=date(2026, 10, 5),
        missing_start_date=date(2026, 10, 6),
        missing_end_date=date(2026, 10, 16),
        request_start_date=date(2026, 9, 29),
        request_end_date=date(2026, 10, 16),
        request_end_datetime="" if feed == "ADJUSTED_LAST" else "20261016-23:59:59",
        request_end_mode="CURRENT" if feed == "ADJUSTED_LAST" else "EXPLICIT",
        reviewed_session_expiry=date(2026, 10, 16) if feed == "ADJUSTED_LAST" else None,
    )
    bars = []
    day = start
    while day <= plan.request_end_date:
        bars.append(
            replace(
                _bar(day),
                ticker=ticker,
                what_to_show=feed,
                adjustment_type="adjusted" if feed == "ADJUSTED_LAST" else None,
            )
        )
        day = next_us_trading_day(day)
    return plan, bars


def run_case(monkeypatch, plan, bars):
    persisted = []
    calls = []

    def fetch(*_args, **kwargs):
        calls.append(kwargs)
        return bars

    def cache(_db, values, **_kwargs):
        persisted.extend(values)
        return BarUpsertSummary(inserted=len(values))

    monkeypatch.setattr(executor, "fetch_daily_bars", fetch)
    monkeypatch.setattr(executor, "cache_bars", cache)
    monkeypatch.setattr(executor, "resolve_us_stock_contract", lambda *_: None)
    # A valid frozen C1 prevents lookup/requalification and preserves contract checks.
    identity = dict(
        conId=6390,
        symbol=plan.ticker,
        secType="STK",
        exchange="SMART",
        currency="USD",
        primaryExchange="NYSE",
    )
    plan = replace(
        plan,
        contract_identity=identity,
        contract_fingerprint=executor.Canonical.fingerprint(identity),
    )
    monkeypatch.setattr(executor, "latest_completed_us_trading_day", lambda: plan.request_end_date)
    run = executor.execute_fetch_plan(
        FakeDb(),
        _fetch_plan(plan),
        ib_client_factory=FakeIB,
        rate_limiter=FakeLimiter(),
        settings=Settings(ib_max_retries=3),
    )
    return run.items[0], persisted, calls


@pytest.mark.parametrize("feed", ["ADJUSTED_LAST", "TRADES"])
@pytest.mark.parametrize(
    "ticker,start,discarded",
    [
        ("DDS", date(2026, 9, 28), 1),
        ("DDS", date(2026, 9, 25), 2),
        ("PK", date(2026, 9, 29), 0),
    ],
)
def test_admits_only_reviewed_daily_subset(monkeypatch, feed, ticker, start, discarded):
    plan, bars = daily_case(feed, ticker, start)
    item, persisted, calls = run_case(monkeypatch, plan, bars)
    assert item.status == "SUCCESS", item.error_message
    assert persisted == [bar for bar in bars if bar.bar_date >= plan.request_start_date]
    assert len(persisted) == item.fetched == 14
    assert len(calls) == 1 and calls[0]["end_datetime"] == plan.request_end_datetime
    metadata = item.decision_metadata_json
    assert metadata["response_admission_policy"] == DAILY_BAR_ADMISSION_V2
    assert metadata["received_bar_count"] == len(bars)
    assert metadata["admitted_bar_count"] == 14
    assert metadata["discarded_early_bar_count"] == discarded
    assert metadata["actual_start_date"] == start.isoformat()
    assert metadata["admitted_start_date"] == "2026-09-29"
    assert metadata["admitted_end_date"] == "2026-10-16"
    assert (
        classify_session_readiness(
            ticker=ticker,
            expected_session=plan.request_end_date,
            latest_bar_session=max(b.bar_date for b in persisted),
            lifecycle=None,
            fetch_items=(item,),
            contract_resolution_status="RESOLVED",
        )["disposition"]
        == "READY"
    )


@pytest.mark.parametrize("feed", ["ADJUSTED_LAST", "TRADES"])
@pytest.mark.parametrize(
    "invalid",
    [
        "future",
        "missing_end",
        "missing_middle",
        "duplicate",
        "ohlc",
        "nan",
        "missing_ohlc",
        "negative_volume",
        "ticker",
        "feed",
        "timeframe",
        "source",
    ],
)
def test_rejects_invalid_response_without_any_persistence(monkeypatch, feed, invalid):
    plan, bars = daily_case(feed)
    last = bars[-1]
    if invalid == "future":
        bars.append(replace(last, bar_date=next_us_trading_day(last.bar_date)))
    elif invalid == "missing_end":
        bars.pop()
    elif invalid == "missing_middle":
        bars = [b for b in bars if b.bar_date != date(2026, 10, 8)]
    elif invalid == "duplicate":
        bars.append(last)
    else:
        changes = {
            "ohlc": {"high": 1},
            "nan": {"close": float("nan")},
            "missing_ohlc": {"open": None},
            "negative_volume": {"volume": -1},
            "ticker": {"ticker": "OTHER"},
            "feed": {"what_to_show": "BID"},
            "timeframe": {"timeframe": "1 hour"},
            "source": {"source": "OTHER"},
        }
        bars[-1] = replace(last, **changes[invalid])
    item, persisted, calls = run_case(monkeypatch, plan, bars)
    assert item.status == "FAILED" and persisted == []
    assert item.attempt_count == len(calls) == 1
    assert item.decision_metadata_json["retryable"] is False
    assert item.decision_metadata_json["boundary_status"] == "FAIL"
    assert (
        item.decision_metadata_json["failure_classification"] == "HISTORICAL_BAR_ADMISSION_REJECTED"
    )


def test_legacy_policy_and_serialized_plan_identity_are_preserved(monkeypatch):
    plan, bars = daily_case()
    old_payload = fetch_plan_to_dict(
        _fetch_plan(replace(plan, response_admission_policy=STRICT_RESPONSE_V1))
    )
    assert "response_admission_policy" not in old_payload["items"][0]
    restored = fetch_plan_from_dict(old_payload)
    assert restored.items[0].response_admission_policy == STRICT_RESPONSE_V1
    assert fetch_plan_to_dict(restored) == old_payload
    item, persisted, _ = run_case(monkeypatch, restored.items[0], bars)
    assert item.status == "FAILED" and persisted == []
    assert "before reviewed scope" in item.error_message
    new_payload = fetch_plan_to_dict(_fetch_plan(plan))
    assert new_payload["items"][0]["response_admission_policy"] == DAILY_BAR_ADMISSION_V2
    assert fetch_plan_from_dict(new_payload).items[0] == plan


@pytest.mark.integration
@pytest.mark.parametrize("feed", ["ADJUSTED_LAST", "TRADES"])
def test_persisted_subset_and_frozen_context_visibility(
    monkeypatch,
    disposable_postgres_database,
    feed,
):
    from datetime import UTC, datetime, timedelta

    from alembic.config import Config
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from alembic import command
    from app.database_safety import configure_guarded_alembic
    from app.models.tables import MarketCalculationContext, PipelineRun, PriceBar, UploadRun
    from app.services.ib_fetch_job_service import FetchJobOptions, _admit_fetch_authority
    from app.services.market_calculation_context_service import cutoff_from_row
    from app.services.market_data_session_readiness import evaluate_and_persist_session_readiness
    from app.services.scope_refresh_adoption import admit_frozen_operation, bind_semantic_authority
    from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember

    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine, expire_on_commit=False) as db:
            plan_item, bars = daily_case(feed)
            upload = UploadRun(filename="run43-regression.csv", status="COMPLETED", row_count=1)
            db.add(upload)
            db.flush()
            parent = admit_frozen_operation(
                db,
                operation_kind="full-pipeline-run",
                subject_kind="ticker",
                members=(ScopeMember("TICKER", "DDS"),),
                cycle_key="run43-regression",
                business_cutoff=plan_item.request_end_date,
                provider_source_class="INTERACTIVE_BROKERS",
                request_type="HISTORICAL_BARS",
                requirements=(AcquisitionRequirement("DDS"),),
                policy_identity="regression-v1",
                scope_definition={"run_id": upload.id},
            )
            pipeline = PipelineRun(upload_run_id=upload.id, status="BLOCKED")
            bind_semantic_authority(pipeline, parent)
            db.add(pipeline)
            db.flush()
            context = MarketCalculationContext(
                pipeline_run_id=pipeline.id,
                upload_run_id=upload.id,
                cutoff_at=datetime.now(UTC) - timedelta(seconds=1),
                latest_completed_session=plan_item.request_end_date,
                exchange_timezone="America/New_York",
                calendar_version="test-v1",
                bar_readiness_version="test-v1",
                cutoff_reason="RUN43_REGRESSION",
            )
            db.add(context)
            db.commit()
            plan = replace(_fetch_plan(plan_item), run_id=upload.id)
            authority = _admit_fetch_authority(
                db, plan, FetchJobOptions(False), parent_scope_id=parent.scope_id
            )
            db.commit()
            monkeypatch.setattr(executor, "fetch_daily_bars", lambda *_args, **_kwargs: bars)
            from types import SimpleNamespace

            monkeypatch.setattr(
                executor,
                "resolve_us_stock_contract",
                lambda *_: SimpleNamespace(
                    contract=SimpleNamespace(symbol="DDS", conId=6390), error_message=None
                ),
            )
            monkeypatch.setattr(
                executor, "latest_completed_us_trading_day", lambda: plan_item.request_end_date
            )
            result = executor.execute_fetch_plan(
                db,
                plan,
                ib_client_factory=FakeIB,
                rate_limiter=FakeLimiter(),
                settings=Settings(ib_max_retries=1),
                semantic_authority=authority,
            )
            assert result.status == "COMPLETED", result.message
            stored = list(db.scalars(select(PriceBar).order_by(PriceBar.bar_date)))
            assert [bar.bar_date for bar in stored] == [
                b.bar_date for b in bars if b.bar_date >= plan_item.request_start_date
            ]
            assert all(bar.first_fetch_run_id == result.id for bar in stored)
            assert all(bar.first_fetch_item_id == result.items[0].id for bar in stored)
            assert all(bar.first_seen_at > context.cutoff_at for bar in stored)
            readiness = evaluate_and_persist_session_readiness(
                db,
                pipeline_run_id=pipeline.id,
                upload_run_id=upload.id,
                tickers=["DDS"],
                market_cutoff=cutoff_from_row(context),
                fetch_run_id=result.id,
            )
            assert not readiness.blockers
            assert readiness.rows[0].latest_bar_session == plan_item.request_end_date
            # An unrelated acquisition cannot make its post-cutoff rows visible.
            foreign_pipeline = PipelineRun(
                upload_run_id=upload.id,
                status="BLOCKED",
                acquisition_plan_id=parent.acquisition_plan_id,
            )
            db.add(foreign_pipeline)
            db.flush()
            foreign_context = MarketCalculationContext(
                pipeline_run_id=foreign_pipeline.id,
                upload_run_id=upload.id,
                cutoff_at=context.cutoff_at,
                latest_completed_session=plan_item.request_end_date,
                exchange_timezone="America/New_York",
                calendar_version="test-v1",
                bar_readiness_version="test-v1",
                cutoff_reason="UNRELATED_CONTEXT",
            )
            db.add(foreign_context)
            db.flush()
            foreign = evaluate_and_persist_session_readiness(
                db,
                pipeline_run_id=foreign_pipeline.id,
                upload_run_id=upload.id,
                tickers=["DDS"],
                market_cutoff=cutoff_from_row(foreign_context),
                fetch_run_id=result.id,
            )
            assert foreign.blockers and foreign.rows[0].latest_bar_session is None
            db.rollback()
    finally:
        engine.dispose()
