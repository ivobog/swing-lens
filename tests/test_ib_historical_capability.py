import ast
from pathlib import Path
from types import SimpleNamespace

from app.services.ib_historical_capability import check_historical_data_capability
from app.settings import Settings


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def emit(self, *args) -> None:
        for handler in list(self.handlers):
            handler(*args)


class FakeIB:
    def __init__(self, *, responses=None, errors=None, info=None) -> None:
        self.responses = responses or {}
        self.errors = errors or {}
        self.info = info
        self.errorEvent = FakeEvent()
        self.connected = False
        self.historical_calls = []
        self.disconnect_calls = 0
        self.RequestTimeout = None
        self.client = SimpleNamespace(serverVersion=lambda: 176)

    def connect(self, *_args, **_kwargs) -> None:
        self.connected = True

    def isConnected(self) -> bool:  # noqa: N802
        return self.connected

    def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1

    def qualifyContracts(self, contract):  # noqa: N802
        return [
            SimpleNamespace(
                conId=756733,
                symbol=contract.symbol,
                secType="STK",
                exchange="SMART",
                primaryExchange="ARCA",
                currency="USD",
            )
        ]

    def reqHistoricalData(self, contract, **kwargs):  # noqa: N802
        self.historical_calls.append((contract, kwargs))
        feed = kwargs["whatToShow"]
        if self.info:
            self.errorEvent.emit(-1, 2106, self.info, contract)
        if feed in self.errors:
            code, message = self.errors[feed]
            self.errorEvent.emit(10, code, message, contract)
            return []
        return self.responses.get(feed, [])


def _bar(day: str):
    return SimpleNamespace(
        date=day,
        open=1,
        high=2,
        low=1,
        close=2,
        volume=100,
    )


def test_capability_ready_uses_two_bounded_non_persisting_requests() -> None:
    ib = FakeIB(responses={"TRADES": [_bar("20260911")], "ADJUSTED_LAST": [_bar("20260911")]})

    status = check_historical_data_capability(
        Settings(_env_file=None, ib_health_timeout_seconds=2),
        ib_factory=lambda: ib,
    )

    assert status.status == "IB_HISTORICAL_DATA_READY"
    assert status.ready is True
    assert [row.bar_count for row in status.feeds] == [1, 1]
    assert len(ib.historical_calls) == 2
    assert all(call[1]["durationStr"] == "2 D" for call in ib.historical_calls)
    assert all(call[1]["endDateTime"] == "" for call in ib.historical_calls)
    assert all(call[1]["barSizeSetting"] == "1 day" for call in ib.historical_calls)
    assert ib.RequestTimeout == 2
    assert ib.disconnect_calls == 1


def test_capability_deterministic_162_is_unavailable() -> None:
    message = "No data of type EODChart is available for the exchange 'BEST'"
    ib = FakeIB(errors={"TRADES": (162, message), "ADJUSTED_LAST": (162, message)})

    status = check_historical_data_capability(Settings(_env_file=None), ib_factory=lambda: ib)

    assert status.status == "IB_HISTORICAL_DATA_UNAVAILABLE"
    assert status.api_ready is True
    assert {row.provider_error_category for row in status.feeds} == {"HISTORICAL_DATA_UNAVAILABLE"}
    assert len(ib.historical_calls) == 2


def test_capability_zero_bars_fails() -> None:
    status = check_historical_data_capability(
        Settings(_env_file=None),
        ib_factory=lambda: FakeIB(),
    )

    assert status.status == "IB_HISTORICAL_DATA_UNAVAILABLE"
    assert [row.result for row in status.feeds] == ["NO_BARS", "NO_BARS"]


def test_informational_2106_does_not_fail_successful_probe() -> None:
    ib = FakeIB(
        responses={"TRADES": [_bar("20260911")], "ADJUSTED_LAST": [_bar("20260911")]},
        info="HMDS data farm connection is OK:ushmds",
    )

    status = check_historical_data_capability(Settings(_env_file=None), ib_factory=lambda: ib)

    assert status.ready is True
    assert all(row.provider_error_code is None for row in status.feeds)


def test_capability_module_has_no_database_or_persistence_dependencies() -> None:
    import app.services.ib_historical_capability as capability

    tree = ast.parse(Path(capability.__file__).read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not {
        module
        for module in modules
        if any(marker in module for marker in ("database", "models", "cache", "job"))
    }
