from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from test_ib_fetch_executor import FakeDb, FakeIB, FakeLimiter, _bar, _fetch_plan, _plan_item

import app.services.ib_fetch_executor as executor
from app.services.bar_cache_service import BarUpsertSummary
from app.services.ib_historical_request_scope import (
    DAILY_BAR_ADMISSION_V2,
    DAILY_BAR_ADMISSION_V3,
    DAILY_BAR_ADMISSION_V4,
    STRICT_RESPONSE_V1,
    build_historical_request_scope,
)
from app.settings import Settings


def _scope(policy):
    return build_historical_request_scope(
        required_start_date=date(2026, 11, 3),
        required_end_date=date(2026, 11, 9),
        reviewed_end_date=date(2026, 11, 9),
        duration="5 D",
        bar_size="1 day",
        what_to_show="TRADES",
        response_admission_policy=policy,
    )


def _gapped_bars():
    return [_bar(date(2026, 11, day)) for day in (3, 5, 6, 9)]


@pytest.mark.parametrize("policy", [DAILY_BAR_ADMISSION_V2, DAILY_BAR_ADMISSION_V3])
def test_retained_policies_still_reject_an_internal_gap(policy):
    with pytest.raises(executor.HistoricalRequestScopeViolation, match="2026-11-04"):
        executor._admit_returned_bars(_gapped_bars(), _scope(policy), ticker="MSFT")


def test_v1_keeps_original_admission_semantics():
    bars = _gapped_bars()
    assert executor._admit_returned_bars(bars, _scope(STRICT_RESPONSE_V1), ticker="MSFT") == bars


def test_v4_accepts_valid_subset_without_claiming_complete_history():
    scope = _scope(DAILY_BAR_ADMISSION_V4)
    bars = executor._admit_returned_bars(_gapped_bars(), scope, ticker="MSFT")
    assert [bar.bar_date.day for bar in bars] == [3, 5, 6, 9]
    assert executor._missing_required_sessions(bars, scope) == [date(2026, 11, 4)]


@pytest.mark.parametrize("failure", ["identity", "duplicate", "invalid_price", "future_session"])
def test_v4_does_not_soften_integrity_violations(failure):
    bars = _gapped_bars()
    if failure == "identity":
        bars[0] = replace(bars[0], ticker="OTHER")
    elif failure == "duplicate":
        bars.append(bars[0])
    elif failure == "invalid_price":
        bars[0] = replace(bars[0], close=-1)
    else:
        bars.append(_bar(date(2026, 11, 10)))
    with pytest.raises(executor.HistoricalRequestScopeViolation):
        executor._admit_returned_bars(bars, _scope(DAILY_BAR_ADMISSION_V4), ticker="MSFT")


@pytest.mark.parametrize("missing", [True, False])
def test_executor_seals_complete_and_partial_v4_with_truthful_counters(monkeypatch, missing):
    db = FakeDb()
    bars = _gapped_bars()
    if not missing:
        bars.append(_bar(date(2026, 11, 4)))
    monkeypatch.setattr(
        executor,
        "resolve_us_stock_contract",
        lambda *_a: SimpleNamespace(
            contract=SimpleNamespace(symbol="MSFT", conId=1),
            error_message=None,
        ),
    )
    monkeypatch.setattr(executor, "fetch_daily_bars", lambda *_a, **_kw: bars)
    monkeypatch.setattr(
        executor,
        "cache_bars",
        lambda *_a, **_kw: BarUpsertSummary(
            inserted=len(bars),
        ),
    )
    sealed = []

    def seal(_db, **kwargs):
        assert kwargs["fetch_item"].fetched == len(bars)
        assert _db.flushes > 0
        sealed.append(kwargs)
        return SimpleNamespace(receipt_hash="a" * 64)

    monkeypatch.setattr(executor, "_seal_bar_admission", seal)
    item = replace(
        _plan_item("MSFT", executor.FetchAction.TOP_UP_RECENT, "5 D"),
        response_admission_policy=DAILY_BAR_ADMISSION_V4,
        missing_start_date=date(2026, 11, 3),
        missing_end_date=date(2026, 11, 9),
        request_start_date=date(2026, 11, 3),
        request_end_date=date(2026, 11, 9),
        request_end_datetime="20261109-23:59:59",
        request_end_mode="EXPLICIT",
        freshness_threshold_date=date(2026, 11, 9),
    )
    run = executor.execute_fetch_plan(
        db,
        _fetch_plan(item),
        settings=Settings(ib_max_retries=1),
        ib_client_factory=FakeIB,
        rate_limiter=FakeLimiter(),
    )
    assert run.status == ("PARTIAL" if missing else "COMPLETED")
    assert run.items[0].status == ("PARTIAL" if missing else "SUCCESS")
    assert run.success_count == int(not missing)
    assert run.failure_count == 0
    assert run.executed_request_count == 1
    assert run.fetched_count == len(bars)
    assert run.decision_counts_json.get("partial_response_count", 0) == int(missing)
    metadata = run.items[0].decision_metadata_json
    assert metadata["missing_required_sessions"] == (["2026-11-04"] if missing else [])
    assert metadata["coverage_complete"] is (not missing)
    assert metadata["admitted_expected_session"] is True
    assert metadata["admission_receipt_hash"] == "a" * 64
    assert sealed[0]["missing_sessions"] == ([date(2026, 11, 4)] if missing else [])


def test_partial_response_cannot_use_whole_plan_resume(monkeypatch):
    from app.services import ib_fetch_job_service as service

    previous = SimpleNamespace(status="PARTIAL", items=[SimpleNamespace(status="PARTIAL")])
    monkeypatch.setattr(service, "_load_fetch_run", lambda *_a: previous)
    with pytest.raises(ValueError, match="PARTIAL_HISTORY_REQUIRES_GAP_REPAIR"):
        service.resume_fetch_job(SimpleNamespace(), 41)


def test_v4_discards_only_current_forming_adjusted_bar():
    scope = build_historical_request_scope(
        required_start_date=date(2026, 11, 9),
        required_end_date=date(2026, 11, 9),
        reviewed_end_date=date(2026, 11, 9),
        duration="2 D",
        bar_size="1 day",
        what_to_show="ADJUSTED_LAST",
        response_admission_policy=DAILY_BAR_ADMISSION_V4,
    )
    bars = [replace(_bar(date(2026, 11, day)), what_to_show="ADJUSTED_LAST") for day in (9, 10)]
    admitted = executor._admit_returned_bars(
        bars,
        scope,
        ticker="MSFT",
        observed_at=datetime(2026, 11, 10, 16, tzinfo=UTC),
    )
    assert [bar.bar_date for bar in admitted] == [date(2026, 11, 9)]
    with pytest.raises(executor.HistoricalRequestScopeViolation, match="after reviewed scope"):
        executor._admit_returned_bars(
            bars,
            scope,
            ticker="MSFT",
            observed_at=datetime(2026, 11, 10, 22, tzinfo=UTC),
        )


@pytest.mark.parametrize("feed", ["ADJUSTED_LAST", "TRADES"])
def test_new_policy_selection_is_explicit_and_legacy_default_stays_frozen(feed):
    from app.services.ib_fetch_plan_service import _build_plan_item
    from app.services.ohlcv_coverage_service import OhlcvCoverageItem

    coverage_item = OhlcvCoverageItem(
        ticker="MSFT",
        adjusted_bars=0,
        trades_bars=0,
        has_price=False,
        has_volume=False,
        sufficient_history=False,
        status="missing",
    )
    kwargs = dict(
        coverage_item=coverage_item,
        contract_status="RESOLVED",
        what_to_show=feed,
        coverage=SimpleNamespace(required_rows=252),
        settings=Settings(),
        force_refresh=False,
        force_full_backfill=False,
        freshness_threshold=date(2026, 11, 9),
    )
    legacy = _build_plan_item(**kwargs)
    fresh = _build_plan_item(**kwargs, allow_history_gaps=True)
    assert legacy.response_admission_policy == (
        DAILY_BAR_ADMISSION_V3 if feed == "ADJUSTED_LAST" else DAILY_BAR_ADMISSION_V2
    )
    assert fresh.response_admission_policy == DAILY_BAR_ADMISSION_V4
    for roles in (("BENCHMARK",), ("REQUESTED", "BENCHMARK"), ("SECTOR",)):
        dependency = _build_plan_item(**kwargs, allow_history_gaps=True, dependency_roles=roles)
        assert dependency.response_admission_policy == legacy.response_admission_policy


def test_v4_empty_response_is_failure_with_exact_zero_count_and_no_receipt(monkeypatch):
    monkeypatch.setattr(
        executor,
        "resolve_us_stock_contract",
        lambda *_a: SimpleNamespace(
            contract=SimpleNamespace(symbol="MSFT", conId=1),
            error_message=None,
        ),
    )
    monkeypatch.setattr(executor, "fetch_daily_bars", lambda *_a, **_kw: [])
    monkeypatch.setattr(executor, "cache_bars", lambda *_a, **_kw: pytest.fail("must not cache"))
    monkeypatch.setattr(
        executor,
        "_seal_bar_admission",
        lambda *_a, **_kw: pytest.fail("empty response has no receipt"),
    )
    item = replace(
        _plan_item("MSFT", executor.FetchAction.TOP_UP_RECENT, "5 D"),
        response_admission_policy=DAILY_BAR_ADMISSION_V4,
        request_end_date=date(2026, 11, 9),
        missing_start_date=date(2026, 11, 3),
        missing_end_date=date(2026, 11, 9),
        request_start_date=date(2026, 11, 3),
        request_end_datetime="20261109-23:59:59",
        request_end_mode="EXPLICIT",
    )
    run = executor.execute_fetch_plan(
        FakeDb(),
        _fetch_plan(item),
        settings=Settings(ib_max_retries=1),
        ib_client_factory=FakeIB,
        rate_limiter=FakeLimiter(),
    )
    assert run.status == "FAILED" and run.success_count == 0 and run.failure_count == 1
    metadata = run.items[0].decision_metadata_json
    assert metadata["provider_result"] == "PROVIDER_NO_DATA"
    assert metadata["received_bar_count"] == 0 and metadata["retryable"] is False


@pytest.mark.parametrize("roles", [("BENCHMARK",), ("REQUESTED", "BENCHMARK"), ("SECTOR",)])
def test_v4_dependency_cannot_use_gap_continuation_even_if_payload_requests_it(monkeypatch, roles):
    monkeypatch.setattr(
        executor,
        "fetch_daily_bars",
        lambda *_a, **_kw: pytest.fail("Dependency reached gap-aware provider path"),
    )
    item = replace(
        _plan_item("MSFT", executor.FetchAction.TOP_UP_RECENT, "5 D"),
        response_admission_policy=DAILY_BAR_ADMISSION_V4,
        dependency_roles=roles,
        request_start_date=date(2026, 11, 3),
        request_end_date=date(2026, 11, 9),
        request_end_datetime="20261109-23:59:59",
        request_end_mode="EXPLICIT",
        missing_start_date=date(2026, 11, 3),
        missing_end_date=date(2026, 11, 9),
    )
    run = executor.execute_fetch_plan(
        FakeDb(),
        _fetch_plan(item),
        settings=Settings(ib_max_retries=1),
        ib_client_factory=FakeIB,
        rate_limiter=FakeLimiter(),
    )
    assert run.status == "FAILED" and run.failure_count == 1
    assert "GAP_POLICY_DEPENDENCY_NOT_ALLOWED" in run.items[0].error_message


def test_global_connection_failure_has_hard_execution_failure_marker():
    class OfflineIB(FakeIB):
        def connect(self, *args, **kwargs):
            raise ConnectionError("offline fixture")

    run = executor.execute_fetch_plan(
        FakeDb(),
        _fetch_plan(_plan_item("MSFT", executor.FetchAction.TOP_UP_RECENT, "10 D")),
        settings=Settings(ib_max_retries=1),
        ib_client_factory=OfflineIB,
        rate_limiter=FakeLimiter(),
    )
    assert run.status == "FAILED"
    assert run.decision_counts_json["execution_failure"] == {
        "classification": "IB_CONNECTION_FAILED"
    }
