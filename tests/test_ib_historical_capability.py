import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    assert ib.RequestTimeout == 30
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
    assert {row.failure_type for row in status.feeds} == {"NO_DATA"}


def test_capability_uses_frozen_spy_plan_request_contract() -> None:
    ib = FakeIB(responses={"TRADES": [_bar("20260911")], "ADJUSTED_LAST": [_bar("20260911")]})
    plan = SimpleNamespace(
        items=[
            SimpleNamespace(
                ticker="SPY",
                what_to_show="TRADES",
                estimated_request_count=1,
                duration="12 D",
                bar_size="1 day",
                request_end_datetime="20260922-23:59:59",
            ),
            SimpleNamespace(
                ticker="SPY",
                what_to_show="ADJUSTED_LAST",
                estimated_request_count=1,
                duration="12 D",
                bar_size="1 day",
                request_end_datetime="",
            ),
        ]
    )

    status = check_historical_data_capability(
        Settings(_env_file=None), ib_factory=lambda: ib, fetch_plan=plan
    )

    assert status.ready is True
    assert status.probe_plan_source == "PIPELINE_SPY_PLAN"
    assert [
        (call[1]["whatToShow"], call[1]["durationStr"], call[1]["endDateTime"])
        for call in ib.historical_calls
    ] == [
        ("TRADES", "12 D", "20260922-23:59:59"),
        ("ADJUSTED_LAST", "12 D", ""),
    ]


@pytest.mark.parametrize(
    ("code", "message", "failure_type"),
    [
        (321, "Error validating request", "INVALID_REQUEST"),
        (354, "Not subscribed to requested market data", "PERMISSION_DENIED"),
        (162, "Historical data request pacing violation", "PACING_OR_RATE_LIMIT"),
        (500, "Request rejected by provider", "PROVIDER_REJECTED"),
        (500, "Unexpected provider failure", "UNKNOWN_PROVIDER_FAILURE"),
    ],
)
def test_capability_preserves_provider_rejection_class(
    code: int, message: str, failure_type: str
) -> None:
    ib = FakeIB(
        responses={"ADJUSTED_LAST": [_bar("20260911")]},
        errors={"TRADES": (code, message)},
    )

    status = check_historical_data_capability(Settings(_env_file=None), ib_factory=lambda: ib)

    trades = status.feeds[0]
    assert trades.failure_type == failure_type
    assert trades.provider_error_code == code
    assert trades.provider_error_message == message
    assert trades.provider_callbacks[0]["code"] == code
    assert status.ready is False


@pytest.mark.parametrize(
    ("exception", "connected_after", "failure_type"),
    [
        (TimeoutError(), True, "TIMEOUT"),
        (ConnectionError("gateway disconnected"), False, "DISCONNECTED"),
        (ValueError("adapter decode failed"), True, "INTERNAL_ADAPTER_ERROR"),
    ],
)
def test_capability_distinguishes_non_provider_failures(
    exception: Exception, connected_after: bool, failure_type: str
) -> None:
    class BrokenIB(FakeIB):
        def reqHistoricalData(self, contract, **kwargs):  # noqa: N802
            if kwargs["whatToShow"] == "TRADES":
                self.connected = connected_after
                raise exception
            return [_bar("20260911")]

    status = check_historical_data_capability(Settings(_env_file=None), ib_factory=BrokenIB)

    trades = status.feeds[0]
    assert trades.failure_type == failure_type
    assert trades.exception_type is not None
    assert trades.provider_error_message
    assert status.ready is False
    if failure_type == "DISCONNECTED":
        assert status.api_ready is False
        assert status.failure_type == "DISCONNECTED"


def test_capability_request_id_correlation_ignores_other_request_error() -> None:
    class CorrelatedIB(FakeIB):
        def __init__(self):
            super().__init__(
                responses={"TRADES": [_bar("20260911")], "ADJUSTED_LAST": [_bar("20260911")]}
            )
            self.client.reqHistoricalData = lambda *_args: None

        def reqHistoricalData(self, contract, **kwargs):  # noqa: N802
            feed = kwargs["whatToShow"]
            request_id = 11 if feed == "TRADES" else 12
            self.client.reqHistoricalData(
                request_id, contract, "", "2 D", "1 day", feed, True, 1, False, []
            )
            if feed == "TRADES":
                self.errorEvent.emit(12, 321, "error for another request", contract)
                self.errorEvent.emit(11, 321, "TRADES rejected", contract)
            return self.responses[feed]

    status = check_historical_data_capability(Settings(_env_file=None), ib_factory=CorrelatedIB)

    assert status.feeds[0].request_id == 11
    assert status.feeds[0].provider_error_message == "TRADES rejected"
    assert len(status.feeds[0].provider_callbacks) == 1
    assert status.feeds[1].request_id == 12
    assert status.feeds[1].result == "SUCCESS"


def test_late_trades_error_cannot_contaminate_adjusted_last_request() -> None:
    class CorrelatedIB(FakeIB):
        def __init__(self):
            super().__init__(
                responses={"TRADES": [_bar("20260911")], "ADJUSTED_LAST": [_bar("20260911")]}
            )
            self.client.reqHistoricalData = lambda *_args: None

        def reqHistoricalData(self, contract, **kwargs):  # noqa: N802
            feed = kwargs["whatToShow"]
            request_id = 21 if feed == "TRADES" else 22
            self.client.reqHistoricalData(
                request_id, contract, "", "2 D", "1 day", feed, True, 1, False, []
            )
            if feed == "ADJUSTED_LAST":
                self.errorEvent.emit(21, 321, "late TRADES rejection", contract)
            return self.responses[feed]

    status = check_historical_data_capability(Settings(_env_file=None), ib_factory=CorrelatedIB)

    assert status.ready is True
    assert [row.request_id for row in status.feeds] == [21, 22]
    assert all(not row.provider_callbacks for row in status.feeds)


def test_contract_qualification_failure_is_not_misclassified_as_provider_bars() -> None:
    class MissingContractIB(FakeIB):
        def qualifyContracts(self, contract):  # noqa: N802
            return []

    status = check_historical_data_capability(
        Settings(_env_file=None), ib_factory=MissingContractIB
    )

    assert status.failure_type == "CONTRACT_ERROR"
    assert status.ready is False
    assert status.feeds == ()


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
