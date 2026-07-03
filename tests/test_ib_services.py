from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.models.tables import IBContract, PriceBar
from app.services.bar_cache_service import cache_bars
from app.services.ib_api import Contract
from app.services.ib_connection import check_ib_connection
from app.services.ib_contract_resolver import cached_contract_to_ib
from app.services.ib_data_fetcher import HistoricalBar, fetch_daily_bars


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
        def reqHistoricalData(self, *args, **kwargs):
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

    bars = fetch_daily_bars(BarsIB(), contract, "TRADES")

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


def test_ib_status_route_does_not_require_gateway() -> None:
    client = TestClient(app)

    response = client.get("/ib/status")

    assert response.status_code == 200
    assert response.json()["order_endpoints"] is False


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
