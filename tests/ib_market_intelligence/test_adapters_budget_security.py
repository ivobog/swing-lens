from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ib_market_intelligence.adapters import (
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
from app.services.ib_market_intelligence.request_budget import WeightedWindowBudget


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


def test_weighted_window_budget_accounts_for_weight():
    state = {"now": 0.0, "sleeps": []}

    def sleep(seconds):
        state["sleeps"].append(seconds)
        state["now"] += seconds

    budget = WeightedWindowBudget(2, monotonic=lambda: state["now"], sleep=sleep)
    budget.acquire(2)
    budget.acquire(1)
    assert state["sleeps"] == [60.0]


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
