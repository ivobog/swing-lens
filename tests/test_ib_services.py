from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.models.tables import IBContract
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


def test_cache_bars_uses_conflict_safe_insert() -> None:
    class Result:
        def scalars(self):
            return self

        def all(self):
            return [1]

    class FakeDb:
        def __init__(self) -> None:
            self.statement = None

        def execute(self, statement):
            self.statement = statement
            return Result()

    db = FakeDb()
    inserted = cache_bars(
        db,
        [
            HistoricalBar(
                ticker="MSFT",
                bar_date=date(2026, 7, 1),
                timeframe="1 day",
                open=10,
                high=12,
                low=9,
                close=11,
                volume=1000,
                source="IB",
                what_to_show="TRADES",
                adjustment_type=None,
            )
        ],
    )

    assert inserted == 1
    assert db.statement is not None


def test_ib_status_route_does_not_require_gateway() -> None:
    client = TestClient(app)

    response = client.get("/ib/status")

    assert response.status_code == 200
    assert response.json()["order_endpoints"] is False
