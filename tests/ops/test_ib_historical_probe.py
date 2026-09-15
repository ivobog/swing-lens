from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from app.settings import Settings
from scripts.ops import ib_historical_probe as probe


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
        for handler in tuple(self.handlers):
            handler(*args)


@dataclass
class FakeBar:
    date: str


class FakeIB:
    def __init__(
        self,
        *,
        responses=None,
        error_feeds=None,
        callbacks=None,
        qualified_count: int = 1,
        connect_error: Exception | None = None,
    ) -> None:
        self.responses = responses or {}
        self.error_feeds = error_feeds or {}
        self.callbacks = callbacks or {}
        self.qualified_count = qualified_count
        self.connect_error = connect_error
        self.errorEvent = FakeEvent()
        self.connected = False
        self.connect_calls = []
        self.qualification_calls = []
        self.historical_calls = []
        self.disconnect_calls = 0
        self.RequestTimeout = None
        self.RaiseRequestErrors = False

    def connect(self, host, port, **kwargs) -> None:
        self.connect_calls.append((host, port, kwargs))
        if self.connect_error:
            raise self.connect_error
        self.connected = True

    def isConnected(self) -> bool:  # noqa: N802
        return self.connected

    def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1

    def qualifyContracts(self, contract):  # noqa: N802
        self.qualification_calls.append(contract)
        return [
            SimpleNamespace(
                conId=756733,
                symbol=contract.symbol,
                secType="STK",
                exchange="SMART",
                primaryExchange="ARCA",
                currency="USD",
                localSymbol=contract.symbol,
                tradingClass=contract.symbol,
            )
            for _ in range(self.qualified_count)
        ]

    def reqHistoricalData(self, contract, **kwargs):  # noqa: N802
        self.historical_calls.append((contract, kwargs))
        feed = kwargs["whatToShow"]
        if feed in self.error_feeds:
            request_id, code, message = self.error_feeds[feed]
            self.errorEvent.emit(request_id, code, message, contract)
            return []
        if feed in self.callbacks:
            request_id, code, message = self.callbacks[feed]
            self.errorEvent.emit(request_id, code, message, contract)
        return self.responses.get(feed, [])


def _config(*, tickers=("SPY",), feeds=("TRADES",)) -> probe.ProbeConfig:
    return probe.ProbeConfig(
        tickers=tickers,
        feeds=feeds,
        duration="10 D",
        bar_size="1 day",
        end_datetime="",
        host="127.0.0.1",
        port=4002,
        client_id=29,
        timeout=5.0,
        use_rth=True,
    )


def test_successful_trades_request() -> None:
    ib = FakeIB(responses={"TRADES": [FakeBar("2026-09-10"), FakeBar("2026-09-11")]})

    exit_code, report = probe.run_probe(_config(), ib_factory=lambda: ib)

    assert exit_code == probe.EXIT_READY
    assert report.verdict == "HISTORICAL_DATA_READY"
    assert report.results[0].bar_count == 2
    assert report.results[0].first_bar_date == "2026-09-10"
    assert report.results[0].last_bar_date == "2026-09-11"


def test_successful_adjusted_last_request() -> None:
    ib = FakeIB(responses={"ADJUSTED_LAST": [FakeBar("2026-09-11")]})

    exit_code, report = probe.run_probe(_config(feeds=("ADJUSTED_LAST",)), ib_factory=lambda: ib)

    assert exit_code == probe.EXIT_READY
    assert report.results[0].feed == "ADJUSTED_LAST"
    assert report.results[0].bar_count == 1


def test_error_162_is_recorded_without_retry() -> None:
    ib = FakeIB(error_feeds={"TRADES": (41, 162, "EODChart data farm unavailable")})

    exit_code, report = probe.run_probe(_config(), ib_factory=lambda: ib)

    assert exit_code == probe.EXIT_HISTORICAL
    assert report.results[0].error_code == 162
    assert report.results[0].error_message == "EODChart data farm unavailable"
    assert report.results[0].request_id == 41
    assert len(ib.historical_calls) == 1


def test_zero_bars_without_callback_error() -> None:
    ib = FakeIB()

    exit_code, report = probe.run_probe(_config(), ib_factory=lambda: ib)

    assert exit_code == probe.EXIT_HISTORICAL
    assert report.results[0].bar_count == 0
    assert report.results[0].error_code is None
    assert report.results[0].error_message is None


def test_qualification_failure_makes_no_historical_request() -> None:
    ib = FakeIB(qualified_count=0)

    exit_code, report = probe.run_probe(_config(), ib_factory=lambda: ib)

    assert exit_code == probe.EXIT_QUALIFICATION
    assert "expected exactly one" in (report.qualification_error or "")
    assert ib.historical_calls == []


def test_connection_failure() -> None:
    ib = FakeIB(connect_error=ConnectionError("client id already in use"))

    exit_code, report = probe.run_probe(_config(), ib_factory=lambda: ib)

    assert exit_code == probe.EXIT_CONNECTION
    assert report.connection_error == "diagnostic client ID 29: client id already in use"
    assert ib.qualification_calls == []
    assert ib.historical_calls == []


def test_two_tickers_two_feeds_make_exactly_four_requests() -> None:
    ib = FakeIB(
        responses={
            "TRADES": [FakeBar("2026-09-11")],
            "ADJUSTED_LAST": [FakeBar("2026-09-11")],
        }
    )

    exit_code, report = probe.run_probe(
        _config(tickers=("SPY", "QQQ"), feeds=("TRADES", "ADJUSTED_LAST")),
        ib_factory=lambda: ib,
    )

    assert exit_code == probe.EXIT_READY
    assert len(report.results) == 4
    assert len(ib.historical_calls) == 4
    assert [(call[0].symbol, call[1]["whatToShow"]) for call in ib.historical_calls] == [
        ("SPY", "TRADES"),
        ("SPY", "ADJUSTED_LAST"),
        ("QQQ", "TRADES"),
        ("QQQ", "ADJUSTED_LAST"),
    ]


def test_omitted_end_datetime_sends_empty_string_and_exact_safe_parameters() -> None:
    ib = FakeIB(responses={"TRADES": [FakeBar("2026-09-11")]})
    args = probe.build_parser().parse_args(["--tickers", "SPY", "--feeds", "TRADES"])
    config = probe.build_config(args, Settings(_env_file=None))

    probe.run_probe(config, ib_factory=lambda: ib)

    assert ib.historical_calls[0][1] == {
        "endDateTime": "",
        "durationStr": "10 D",
        "barSizeSetting": "1 day",
        "whatToShow": "TRADES",
        "useRTH": True,
        "formatDate": 1,
        "keepUpToDate": False,
    }
    assert ib.RequestTimeout == 30.0
    assert ib.RaiseRequestErrors is True


def test_explicit_end_datetime_is_passed_unchanged_and_reported(tmp_path, capsys) -> None:
    ib = FakeIB(responses={"TRADES": [FakeBar("2026-09-11")]})
    supplied = "20260911-23:59:59"
    args = probe.build_parser().parse_args(
        [
            "--tickers",
            "SPY",
            "--feeds",
            "TRADES",
            "--duration",
            "7 D",
            "--end-datetime",
            supplied,
        ]
    )
    config = probe.build_config(args, Settings(_env_file=None))

    _, report = probe.run_probe(config, ib_factory=lambda: ib)
    probe.print_report(report)
    artifact = probe.write_json_artifact(report, root=tmp_path)

    assert config.end_datetime == supplied
    assert ib.historical_calls[0][1]["endDateTime"] == supplied
    assert f'"endDateTime": "{supplied}"' in capsys.readouterr().out
    artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert artifact_payload["results"][0]["request_parameters"]["endDateTime"] == supplied


def test_2106_is_provider_info_and_not_a_success_error(capsys) -> None:
    message = "HMDS data farm connection is OK:ushmds"
    ib = FakeIB(
        responses={"TRADES": [FakeBar("2026-09-11")]},
        callbacks={"TRADES": (-1, 2106, message)},
    )

    exit_code, report = probe.run_probe(_config(), ib_factory=lambda: ib)
    probe.print_report(report)

    result = report.results[0]
    assert exit_code == probe.EXIT_READY
    assert result.error_code is None
    assert result.error_message is None
    assert result.provider_errors == ()
    assert result.provider_info == (probe.ProviderError(-1, 2106, message),)
    table_row = next(
        line for line in capsys.readouterr().out.splitlines() if line.startswith("SPY")
    )
    assert table_row.endswith("-")
    assert "2106" not in table_row


def test_script_has_no_database_session_pipeline_cache_or_job_imports() -> None:
    source_path = Path(probe.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert {module for module in imported_modules if module.startswith("app.")} == {
        "app.settings",
        "app.services.ib_api",
    }
