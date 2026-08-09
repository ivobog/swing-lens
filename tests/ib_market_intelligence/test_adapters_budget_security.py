from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ib_market_intelligence.adapters import (
    IBHistogramClient,
    IBLiveSnapshotManager,
    IBScannerClient,
    capability_status_from_error,
    parse_historical_metric_bar,
)
from app.services.ib_market_intelligence.config import ScannerPreset
from app.services.ib_market_intelligence.enums import (
    AvailabilityStatus,
    HistoricalMetricType,
)
from app.services.ib_market_intelligence.orchestration import (
    _latest_historical_availability,
    effective_histogram_period,
)
from app.services.ib_market_intelligence.request_budget import WeightedWindowBudget
from app.services.ib_market_intelligence.scanner_identity import (
    canonical_scanner_identity,
    scanner_conids_by_ticker,
)
from app.settings import Settings


def test_typed_historical_parser_preserves_non_price_semantics():
    raw = SimpleNamespace(date="20260807", open=0.2, high=0.5, low=0.1, close=0.4)
    dto = parse_historical_metric_bar(
        raw,
        ticker="XYZ",
        ib_conid=123,
        metric_type=HistoricalMetricType.OPTION_IMPLIED_VOLATILITY,
        timeframe="1 day",
        requested_range="252 D",
    )
    assert dto.close_value == 0.4
    assert dto.source_semantic_type == "OPTION_IMPLIED_VOLATILITY_RATE"
    assert dto.session_date == date(2026, 8, 7)


def test_bid_ask_parser_quarantines_inverted_bar():
    raw = SimpleNamespace(date="20260807", open=101, high=102, low=99, close=100)
    dto = parse_historical_metric_bar(
        raw,
        ticker="XYZ",
        ib_conid=123,
        metric_type=HistoricalMetricType.BID_ASK,
        timeframe="1 day",
        requested_range="60 D",
    )
    assert "INVALID_BID_ASK_BAR" in dto.warning_flags


def test_capability_error_mapping_is_entitlement_aware():
    assert (
        capability_status_from_error(SimpleNamespace(code=354, message="not subscribed"))[0]
        == AvailabilityStatus.SUBSCRIPTION_REQUIRED
    )
    assert (
        capability_status_from_error(SimpleNamespace(code=200, message="not supported"))[0]
        == AvailabilityStatus.NOT_SUPPORTED
    )
    assert (
        capability_status_from_error(RuntimeError("gateway down"))[0] == AvailabilityStatus.FAILED
    )


def test_live_snapshot_cancels_subscription_and_tracks_reconnect_semantics():
    contract = SimpleNamespace(symbol="XYZ", conId=1)

    class FakeIB:
        def __init__(self):
            self.cancelled = []

        def reqMktData(self, *_args, **_kwargs):
            return SimpleNamespace(
                callVolume=100,
                putVolume=50,
                callOpenInterest=200,
                putOpenInterest=150,
                avOptionVolume=75,
            )

        def cancelMktData(self, item):
            self.cancelled.append(item)

    ib = FakeIB()
    manager = IBLiveSnapshotManager(ib)
    result = manager.capture(contract, "OPTIONS_ACTIVITY")
    assert result.availability_status == AvailabilityStatus.AVAILABLE
    assert ib.cancelled == [contract]
    manager.on_connection_error(-1, 1101, "data lost")
    assert manager.restart_required is True
    manager.on_connection_error(-1, 1102, "data maintained")
    assert manager.restart_required is False


def test_live_options_waits_for_all_required_fields_before_available():
    contract = SimpleNamespace(symbol="XYZ", conId=1)
    ticker = SimpleNamespace(
        callVolume=100,
        putVolume=50,
        callOpenInterest=None,
        putOpenInterest=None,
        avOptionVolume=75,
    )

    class FakeIB:
        def __init__(self):
            self.cancelled = []
            self.waits = 0

        def reqMktData(self, *_args, **_kwargs):
            return ticker

        def waitOnUpdate(self, **_kwargs):
            self.waits += 1
            ticker.callOpenInterest = 200
            ticker.putOpenInterest = 150

        def cancelMktData(self, item):
            self.cancelled.append(item)

    ib = FakeIB()
    result = IBLiveSnapshotManager(ib, timeout_seconds=0.1).capture(
        contract, "OPTIONS_ACTIVITY"
    )
    assert ib.waits == 1
    assert result.availability_status == AvailabilityStatus.AVAILABLE
    assert result.source_request["missing_required_fields"] == []
    assert ib.cancelled == [contract]


def test_live_options_partial_fields_remain_raw_but_are_not_available():
    contract = SimpleNamespace(symbol="XYZ", conId=1)

    class FakeIB:
        def __init__(self):
            self.cancelled = []

        def reqMktData(self, *_args, **_kwargs):
            return SimpleNamespace(
                callVolume=100,
                putVolume=50,
                callOpenInterest=None,
                putOpenInterest=None,
                avOptionVolume=75,
            )

        def cancelMktData(self, item):
            self.cancelled.append(item)

    result = IBLiveSnapshotManager(FakeIB(), timeout_seconds=0.1).capture(
        contract, "OPTIONS_ACTIVITY"
    )
    assert result.values["call_volume"] == 100
    assert result.availability_status == AvailabilityStatus.UNAVAILABLE
    assert result.source_request["missing_required_fields"] == [
        "call_open_interest",
        "put_open_interest",
    ]
    assert result.warning_flags == (
        "MISSING_REQUIRED_FIELD:call_open_interest",
        "MISSING_REQUIRED_FIELD:put_open_interest",
    )


def test_weighted_window_budget_accounts_for_weight():
    state = {"now": 0.0, "sleeps": []}

    def sleep(seconds):
        state["sleeps"].append(seconds)
        state["now"] += seconds

    budget = WeightedWindowBudget(2, monotonic=lambda: state["now"], sleep=sleep)
    budget.acquire(2)
    budget.acquire(1)
    assert state["sleeps"] == [60.0]


def test_weighted_window_budget_enforces_minimum_request_spacing():
    state = {"now": 0.0, "sleeps": []}

    def sleep(seconds):
        state["sleeps"].append(seconds)
        state["now"] += seconds

    budget = WeightedWindowBudget(
        10,
        min_spacing_seconds=3.0,
        monotonic=lambda: state["now"],
        sleep=sleep,
    )
    budget.acquire()
    budget.acquire(2)
    assert state["sleeps"] == [3.0]


def test_scanner_waits_for_initial_results_supports_zero_and_repeated_runs():
    rows = [
        SimpleNamespace(
            rank=rank,
            contractDetails=SimpleNamespace(
                contract=SimpleNamespace(
                    symbol=symbol,
                    conId=conid,
                    exchange="SMART",
                    primaryExchange="NASDAQ",
                    currency="USD",
                    secType="STK",
                )
            ),
        )
        for rank, symbol, conid in ((0, "AAPL", 1), (1, "MSFT", 2))
    ]

    class FakeIB:
        def __init__(self):
            self.results = [rows, [], rows[:1]]
            self.calls = 0

        def reqScannerData(self, *_args, **_kwargs):
            result = self.results[self.calls]
            self.calls += 1
            return result

    preset = ScannerPreset("TEST", "1", "STK", "STK.US", "MOST_ACTIVE", 50, ())
    ib = FakeIB()
    client = IBScannerClient(ib)
    assert [row["ticker"] for row in client.run(preset)] == ["AAPL", "MSFT"]
    assert client.run(preset) == []
    assert [row["ticker"] for row in client.run(preset)] == ["AAPL"]


def test_scanner_propagates_request_failure():
    class FakeIB:
        def reqScannerData(self, *_args, **_kwargs):
            raise RuntimeError("scanner unavailable")

    preset = ScannerPreset("TEST", "1", "STK", "STK.US", "MOST_ACTIVE", 50, ())
    with pytest.raises(RuntimeError, match="scanner unavailable"):
        IBScannerClient(FakeIB()).run(preset)


def test_scanner_identity_merges_missing_conid_with_one_canonical_contract():
    known = scanner_conids_by_ticker([("aapl", None), ("AAPL", 265598)])
    resolved = canonical_scanner_identity(
        ticker="AAPL", ib_conid=265598, known_conids_by_ticker=known
    )
    unresolved = canonical_scanner_identity(
        ticker="aapl",
        ib_conid=None,
        contract_metadata={"sec_type": "STK", "currency": "USD"},
        known_conids_by_ticker=known,
    )
    assert resolved == unresolved == "CONID:265598"


def test_scanner_identity_keeps_ambiguous_symbol_contracts_separate():
    known = scanner_conids_by_ticker([("XYZ", 1), ("XYZ", 2)])
    unresolved = canonical_scanner_identity(
        ticker="XYZ",
        ib_conid=None,
        contract_metadata={
            "sec_type": "STK",
            "currency": "USD",
            "primary_exchange": "NASDAQ",
        },
        known_conids_by_ticker=known,
    )
    assert unresolved == "SYMBOL:XYZ:STK:USD:NASDAQ"


def test_histogram_request_period_override_is_the_effective_persisted_period():
    settings = Settings(_env_file=None, job_worker_enabled=False, ib_histogram_period="20 days")
    assert (
        effective_histogram_period(
            {"period": "5 days"}, {"period": "10 days"}, settings
        )
        == "5 days"
    )
    assert effective_histogram_period({}, {"period": "10 days"}, settings) == "10 days"


def test_histogram_capture_retains_malformed_raw_bins():
    class FakeIB:
        def reqHistogramData(self, *_args, **_kwargs):
            return [
                SimpleNamespace(price=100, size=50),
                SimpleNamespace(price="bad-price", size=25),
                SimpleNamespace(price=102, size=float("nan")),
            ]

    capture = IBHistogramClient(FakeIB()).fetch_capture(
        SimpleNamespace(), use_rth=True, period="20 days"
    )
    assert len(capture.raw_bins) == 3
    assert len(capture.valid_levels) == 1
    assert capture.malformed_bin_count == 2
    assert capture.raw_bins[1]["raw_price"] == "bad-price"
    assert capture.raw_bins[1]["validation_warnings"] == ["INVALID_PRICE"]


def test_latest_historical_entitlement_status_overrides_existing_iv_rows():
    db = SimpleNamespace(scalar=lambda _statement: AvailabilityStatus.SUBSCRIPTION_REQUIRED)
    assert (
        _latest_historical_availability(
            db,
            "AAPL",
            HistoricalMetricType.OPTION_IMPLIED_VOLATILITY,
            has_rows=True,
        )
        == AvailabilityStatus.SUBSCRIPTION_REQUIRED
    )


def test_market_intelligence_package_contains_no_order_execution_calls():
    root = Path("app/services/ib_market_intelligence")
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    forbidden = ("placeOrder(", "cancelOrder(", "reqOpenOrders(", "reqExecutions(")
    assert not any(call in source for call in forbidden)
    assert "readonly=True" in (root / "orchestration.py").read_text(encoding="utf-8")


def test_intelligence_routes_are_feature_flagged_and_admin_csrf_protected(
    app_client_factory,
):
    disabled = app_client_factory(ib_market_intelligence_enabled=False)
    assert disabled.get("/ib-intelligence").status_code == 404

    client = app_client_factory(
        ib_market_intelligence_enabled=True,
        ib_liquidity_enabled=True,
    )
    response = client.post(
        "/api/ib-intelligence/refresh",
        json={"module": "LIQUIDITY", "tickers": ["AAPL"]},
    )
    assert response.status_code == 403
