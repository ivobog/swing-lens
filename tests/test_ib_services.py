from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.models.tables import IBContract, PriceBar
from app.routers import ib_routes
from app.services.bar_cache_service import _normalize_symbols, cache_bars
from app.services.ib_api import Contract
from app.services.ib_connection import check_ib_connection
from app.services.ib_contract_resolver import cached_contract_to_ib
from app.services.ib_data_fetcher import HistoricalBar, fetch_daily_bars
from app.settings import Settings


class FakeIB:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.connected = False

    def connect(self, *args, **kwargs) -> None:
        if self.fail:
            raise ConnectionError("IB unavailable")
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def isConnected(self) -> bool:
        return self.connected


def test_check_ib_connection_success() -> None:
    status = check_ib_connection(ib_factory=FakeIB)

    assert status.connected is True
    assert status.message == "Connected to Interactive Brokers."


def test_check_ib_connection_failure() -> None:
    status = check_ib_connection(ib_factory=lambda: FakeIB(fail=True))

    assert status.connected is False
    assert "IB unavailable" in status.message


def test_fetch_daily_bars_converts_ib_bars() -> None:
    class BarsIB:
        def __init__(self) -> None:
            self.request = None

        def reqHistoricalData(self, *args, **kwargs):
            self.request = kwargs
            return [
                SimpleNamespace(
                    date="20260701",
                    open=10,
                    high=12,
                    low=9,
                    close=11,
                    volume=1000,
                )
            ]

    contract = Contract(symbol="MSFT", secType="STK", exchange="SMART", currency="USD")
    ib = BarsIB()

    bars = fetch_daily_bars(
        ib,
        contract,
        "TRADES",
        settings=Settings(ib_full_backfill_duration="4 Y", ib_default_bar_size="1 day"),
    )

    assert ib.request["durationStr"] == "4 Y"
    assert ib.request["barSizeSetting"] == "1 day"
    assert bars == [
        HistoricalBar(
            ticker="MSFT",
            bar_date=date(2026, 7, 1),
            timeframe="1 day",
            open=10.0,
            high=12.0,
            low=9.0,
            close=11.0,
            volume=1000.0,
            source="IB",
            what_to_show="TRADES",
            adjustment_type=None,
        )
    ]


def test_fetch_daily_bars_accepts_duration_and_bar_size_override() -> None:
    class BarsIB:
        def __init__(self) -> None:
            self.request = None

        def reqHistoricalData(self, *args, **kwargs):
            self.request = kwargs
            return [
                SimpleNamespace(
                    date="20260701",
                    open=10,
                    high=12,
                    low=9,
                    close=11,
                    volume=1000,
                )
            ]

    contract = Contract(symbol="MSFT", secType="STK", exchange="SMART", currency="USD")
    ib = BarsIB()

    bars = fetch_daily_bars(
        ib,
        contract,
        "ADJUSTED_LAST",
        settings=Settings(),
        duration="10 D",
        bar_size="1 hour",
    )

    assert ib.request["durationStr"] == "10 D"
    assert ib.request["barSizeSetting"] == "1 hour"
    assert bars[0].timeframe == "1 hour"
    assert bars[0].adjustment_type == "adjusted"


def test_cached_contract_to_ib_rebuilds_resolved_contract() -> None:
    row = IBContract(
        ticker="MSFT",
        ib_conid=123,
        symbol="MSFT",
        exchange="SMART",
        primary_exchange="NASDAQ",
        currency="USD",
        sec_type="STK",
        local_symbol="MSFT",
        trading_class="NMS",
        resolution_status="RESOLVED",
    )

    contract = cached_contract_to_ib(row)

    assert contract is not None
    assert contract.conId == 123
    assert contract.symbol == "MSFT"
    assert contract.secType == "STK"
    assert contract.currency == "USD"


def test_cache_bars_inserts_new_bars_with_revision_metadata() -> None:
    db = FakeDb()
    summary = cache_bars(
        db,
        [_historical_bar("MSFT", close=11, volume=1000)],
    )

    assert summary.inserted == 1
    assert summary.revised == 0
    assert len(db.added) == 1
    assert db.added[0].ticker == "MSFT"
    assert db.added[0].revision_count == 0
    assert db.added[0].data_hash


def test_cache_bars_updates_last_seen_for_unchanged_bars() -> None:
    old_seen = datetime(2026, 7, 1, tzinfo=UTC)
    existing = _price_bar("MSFT", close=11, volume=1000)
    existing.last_seen_at = old_seen
    db = FakeDb(existing=[existing])

    summary = cache_bars(db, [_historical_bar("MSFT", close=11.0, volume=1000.0)])

    assert summary.inserted == 0
    assert summary.unchanged == 1
    assert summary.revised == 0
    assert existing.last_seen_at > old_seen
    assert existing.revision_count == 0
    assert existing.revised_at is None
    assert existing.close == Decimal("11")


def test_cache_bars_revises_changed_existing_bars() -> None:
    existing = _price_bar("MSFT", close=10, volume=1000)
    db = FakeDb(existing=[existing])

    summary = cache_bars(db, [_historical_bar("MSFT", close=11, volume=1000)])

    assert summary.inserted == 0
    assert summary.updated == 1
    assert summary.revised == 1
    assert summary.unchanged == 0
    assert existing.close == Decimal("11")
    assert existing.revision_count == 1
    assert existing.revised_at is not None
    assert existing.data_hash


def test_normalize_symbols_uses_configured_benchmarks() -> None:
    symbols = _normalize_symbols(
        ["msft", "SPY", "msft"],
        include_benchmarks=True,
        benchmarks=("QQQ", "IWM"),
    )

    assert symbols == ["IWM", "MSFT", "QQQ", "SPY"]


def test_ib_fetch_route_uses_fetch_plan_executor(monkeypatch) -> None:
    calls = {}
    plan = SimpleNamespace(id="plan")
    fetch_run = SimpleNamespace(
        id=42,
        status="COMPLETED",
        message="Executed 1 IB requests; skipped 0 items.",
        symbols_including_benchmarks=["MSFT", "SPY"],
        planned_request_count=1,
        executed_request_count=1,
        fetched_count=10,
        inserted_count=8,
        updated_count=1,
        revised_count=1,
        unchanged_count=1,
        items=[
            SimpleNamespace(
                ticker="MSFT",
                what_to_show="TRADES",
                fetched=10,
                inserted=8,
                updated=1,
                revised=1,
                unchanged=1,
                status="SUCCESS",
                action="FULL_BACKFILL",
                duration="4 Y",
                reason="MSFT has no cached TRADES daily bars.",
                error_message=None,
            )
        ],
    )

    def fake_build_fetch_plan(**kwargs):
        calls["build"] = kwargs
        return plan

    def fake_execute_fetch_plan(db, received_plan, **kwargs):
        calls["execute"] = {
            "db": db,
            "plan": received_plan,
            **kwargs,
        }
        return fetch_run

    monkeypatch.setattr(ib_routes, "build_fetch_plan", fake_build_fetch_plan)
    monkeypatch.setattr(ib_routes, "execute_fetch_plan", fake_execute_fetch_plan)

    db = FakeDb()
    response = ib_routes.fetch_bars(
        db=db,
        tickers="msft, MSFT",
        include_benchmarks=True,
        force_refresh=True,
        force_full_backfill=False,
        what_to_show=["TRADES", "UNSUPPORTED"],
    )

    assert calls["build"] == {
        "db": db,
        "tickers": ["msft", "MSFT"],
        "include_benchmarks": True,
        "force_refresh": True,
        "force_full_backfill": False,
        "what_to_show_values": ("TRADES",),
    }
    assert calls["execute"] == {
        "db": db,
        "plan": plan,
        "include_benchmarks": True,
        "force_refresh": True,
        "force_full_backfill": False,
    }
    assert response["fetch_run_id"] == 42
    assert response["status"] == "COMPLETED"
    assert response["symbols_including_benchmarks"] == ["MSFT", "SPY"]
    assert response["items"][0]["status"] == "SUCCESS"
    assert response["failures"] == []


def test_ib_status_route_does_not_require_gateway() -> None:
    client = TestClient(app)

    response = client.get("/ib/status")

    assert response.status_code == 200
    assert response.json()["order_endpoints"] is False
    assert response.json()["full_backfill_duration"]
    assert response.json()["top_up_duration"]
    assert response.json()["refresh_duration"]
    assert "SPY" in response.json()["benchmarks"]


class FakeDb:
    def __init__(self, existing: list[PriceBar] | None = None) -> None:
        self.existing = existing or []
        self.added = []

    def scalars(self, statement):
        return FakeScalarResult(self.existing)

    def add(self, row) -> None:
        self.added.append(row)


class FakeScalarResult:
    def __init__(self, rows: list[PriceBar]) -> None:
        self.rows = rows

    def all(self) -> list[PriceBar]:
        return self.rows


def _historical_bar(ticker: str, close: float, volume: float) -> HistoricalBar:
    return HistoricalBar(
        ticker=ticker,
        bar_date=date(2026, 7, 1),
        timeframe="1 day",
        open=10,
        high=12,
        low=9,
        close=close,
        volume=volume,
        source="IB",
        what_to_show="TRADES",
        adjustment_type=None,
    )


def _price_bar(ticker: str, close: float, volume: float) -> PriceBar:
    seen_at = datetime(2026, 7, 1, tzinfo=UTC)
    return PriceBar(
        ticker=ticker,
        bar_date=date(2026, 7, 1),
        timeframe="1 day",
        open=Decimal("10"),
        high=Decimal("12"),
        low=Decimal("9"),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
        source="IB",
        what_to_show="TRADES",
        adjustment_type=None,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        revision_count=0,
    )
