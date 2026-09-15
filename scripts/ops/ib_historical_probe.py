from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.services.ib_api import IB, Stock
from app.settings import Settings

EXIT_READY = 0
EXIT_CONNECTION = 2
EXIT_QUALIFICATION = 3
EXIT_HISTORICAL = 4
EXIT_UNEXPECTED = 5
DEFAULT_CLIENT_ID = 29
MAX_TIMEOUT_SECONDS = 30.0
INFORMATIONAL_PROVIDER_CODES = frozenset({2104, 2106, 2107, 2108, 2158})


@dataclass(frozen=True)
class ProbeConfig:
    tickers: tuple[str, ...]
    feeds: tuple[str, ...]
    duration: str
    bar_size: str
    end_datetime: str
    host: str
    port: int
    client_id: int
    timeout: float
    use_rth: bool


@dataclass(frozen=True)
class ProviderError:
    request_id: int | None
    code: int | None
    message: str


@dataclass(frozen=True)
class ProbeResult:
    ticker: str
    feed: str
    contract: dict[str, Any]
    request_parameters: dict[str, Any]
    request_started_at: str
    request_finished_at: str
    elapsed_ms: int
    bar_count: int
    first_bar_date: str | None
    last_bar_date: str | None
    request_id: int | None
    error_code: int | None
    error_message: str | None
    provider_errors: tuple[ProviderError, ...]
    provider_info: tuple[ProviderError, ...]

    @property
    def succeeded(self) -> bool:
        return self.bar_count > 0


@dataclass(frozen=True)
class ProbeReport:
    config: ProbeConfig
    contracts: tuple[dict[str, Any], ...]
    results: tuple[ProbeResult, ...]
    connection_error: str | None = None
    qualification_error: str | None = None

    @property
    def verdict(self) -> str:
        if self.results and all(result.succeeded for result in self.results):
            return "HISTORICAL_DATA_READY"
        return "HISTORICAL_DATA_UNAVAILABLE"


IBFactory = Callable[[], Any]


def build_config(args: argparse.Namespace, settings: Settings) -> ProbeConfig:
    return ProbeConfig(
        tickers=_parse_csv(args.tickers, "tickers"),
        feeds=_parse_csv(args.feeds, "feeds"),
        duration=args.duration.strip(),
        bar_size=args.bar_size.strip(),
        end_datetime=args.end_datetime,
        host=(args.host or settings.ib_host).strip(),
        port=args.port if args.port is not None else int(settings.ib_port),
        client_id=args.client_id if args.client_id is not None else DEFAULT_CLIENT_ID,
        timeout=(
            args.timeout
            if args.timeout is not None
            else min(max(float(settings.ib_timeout_seconds), 0.25), MAX_TIMEOUT_SECONDS)
        ),
        use_rth=bool(settings.ib_use_rth),
    )


def run_probe(config: ProbeConfig, *, ib_factory: IBFactory = IB) -> tuple[int, ProbeReport]:
    ib = ib_factory()
    contracts: list[dict[str, Any]] = []
    qualified_contracts: list[tuple[str, Any]] = []
    results: list[ProbeResult] = []

    try:
        if hasattr(ib, "RequestTimeout"):
            ib.RequestTimeout = config.timeout
        if hasattr(ib, "RaiseRequestErrors"):
            ib.RaiseRequestErrors = True

        connection_error = _connect_once(ib, config)
        if connection_error is not None:
            report = ProbeReport(
                config=config,
                contracts=(),
                results=(),
                connection_error=connection_error,
            )
            return EXIT_CONNECTION, report

        for ticker in config.tickers:
            try:
                requested = Stock(ticker, "SMART", "USD")
                qualified = list(ib.qualifyContracts(requested))
            except Exception as exc:
                report = ProbeReport(
                    config=config,
                    contracts=tuple(contracts),
                    results=(),
                    qualification_error=f"{ticker}: {exc}",
                )
                return EXIT_QUALIFICATION, report
            if len(qualified) != 1:
                report = ProbeReport(
                    config=config,
                    contracts=tuple(contracts),
                    results=(),
                    qualification_error=(
                        f"{ticker}: expected exactly one qualified contract, got {len(qualified)}"
                    ),
                )
                return EXIT_QUALIFICATION, report
            contract = qualified[0]
            contract_payload = _contract_payload(contract)
            contracts.append(contract_payload)
            qualified_contracts.append((ticker, contract))

        for ticker, contract in qualified_contracts:
            contract_payload = _contract_payload(contract)
            for feed in config.feeds:
                results.append(_request_once(ib, config, ticker, feed, contract, contract_payload))

        report = ProbeReport(
            config=config,
            contracts=tuple(contracts),
            results=tuple(results),
        )
        exit_code = EXIT_READY if report.verdict == "HISTORICAL_DATA_READY" else EXIT_HISTORICAL
        return exit_code, report
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def _request_once(
    ib: Any,
    config: ProbeConfig,
    ticker: str,
    feed: str,
    contract: Any,
    contract_payload: dict[str, Any],
) -> ProbeResult:
    request_parameters = {
        "endDateTime": config.end_datetime,
        "durationStr": config.duration,
        "barSizeSetting": config.bar_size,
        "whatToShow": feed,
        "useRTH": config.use_rth,
        "formatDate": 1,
        "keepUpToDate": False,
    }
    provider_errors: list[ProviderError] = []
    provider_info: list[ProviderError] = []

    def capture_error(*event_args: Any) -> None:
        callback = _provider_error(event_args)
        if callback.code in INFORMATIONAL_PROVIDER_CODES:
            provider_info.append(callback)
        else:
            provider_errors.append(callback)

    error_event = getattr(ib, "errorEvent", None)
    subscribed = error_event is not None
    if subscribed:
        error_event += capture_error

    started_at = datetime.now(UTC)
    started_counter = perf_counter()
    bars: Sequence[Any] = ()
    raised_error: ProviderError | None = None
    try:
        bars = ib.reqHistoricalData(contract, **request_parameters) or ()
    except Exception as exc:
        raised_error = ProviderError(
            _optional_int(getattr(exc, "reqId", None)),
            _optional_int(getattr(exc, "code", None)),
            str(getattr(exc, "message", exc)),
        )
        if raised_error not in provider_errors:
            provider_errors.append(raised_error)
    finally:
        finished_counter = perf_counter()
        finished_at = datetime.now(UTC)
        if subscribed:
            error_event -= capture_error

    bars = list(bars)
    selected_error = _select_error(provider_errors, raised_error)
    return ProbeResult(
        ticker=ticker,
        feed=feed,
        contract=contract_payload,
        request_parameters=request_parameters,
        request_started_at=started_at.isoformat(),
        request_finished_at=finished_at.isoformat(),
        elapsed_ms=max(0, round((finished_counter - started_counter) * 1000)),
        bar_count=len(bars),
        first_bar_date=_bar_date(bars[0]) if bars else None,
        last_bar_date=_bar_date(bars[-1]) if bars else None,
        request_id=selected_error.request_id if selected_error else None,
        error_code=selected_error.code if selected_error else None,
        error_message=selected_error.message if selected_error else None,
        provider_errors=tuple(provider_errors),
        provider_info=tuple(provider_info),
    )


def _connect_once(ib: Any, config: ProbeConfig) -> str | None:
    provider_errors: list[ProviderError] = []

    def capture_error(*event_args: Any) -> None:
        callback = _provider_error(event_args)
        if callback.code not in INFORMATIONAL_PROVIDER_CODES:
            provider_errors.append(callback)

    error_event = getattr(ib, "errorEvent", None)
    subscribed = error_event is not None
    if subscribed:
        error_event += capture_error
    try:
        ib.connect(
            config.host,
            config.port,
            clientId=config.client_id,
            timeout=config.timeout,
            readonly=True,
        )
        if not ib.isConnected():
            raise ConnectionError("IB API connect returned without an active connection")
        return None
    except Exception as exc:
        exception_message = str(exc).strip() or type(exc).__name__
        details = [
            f"{error.code if error.code is not None else '-'} {error.message}".strip()
            for error in provider_errors
        ]
        provider_suffix = f"; provider errors: {' | '.join(details)}" if details else ""
        return f"diagnostic client ID {config.client_id}: {exception_message}{provider_suffix}"
    finally:
        if subscribed:
            error_event -= capture_error


def _provider_error(event_args: Sequence[Any]) -> ProviderError:
    request_id = _optional_int(event_args[0]) if len(event_args) >= 1 else None
    code = _optional_int(event_args[1]) if len(event_args) >= 2 else None
    message = str(event_args[2]) if len(event_args) >= 3 else "Unknown IB provider error"
    return ProviderError(request_id, code, message)


def print_report(report: ProbeReport) -> None:
    print(
        f"Connection: host={report.config.host} port={report.config.port} "
        f"clientId={report.config.client_id} timeout={report.config.timeout:g}s "
        f"readonly=true useRTH={str(report.config.use_rth).lower()}"
    )
    for contract in report.contracts:
        print(f"QUALIFIED_CONTRACT {json.dumps(contract, sort_keys=True)}")

    if report.connection_error:
        print(f"CONNECTION_ERROR {report.connection_error}")
    if report.qualification_error:
        print(f"QUALIFICATION_ERROR {report.qualification_error}")

    for result in report.results:
        request_payload = {
            "method": "reqHistoricalData",
            "ticker": result.ticker,
            "contract": result.contract,
            "parameters": result.request_parameters,
        }
        print(f"REQUEST {json.dumps(request_payload, sort_keys=True)}")
        print(f"RESULT {json.dumps(_result_payload(result), sort_keys=True)}")

    if report.results:
        print()
        print(f"{'Ticker':<8} {'Feed':<14} {'Result':<8} {'Bars':>5} {'Elapsed':>10}  Error")
        for result in report.results:
            status = "SUCCESS" if result.succeeded else "ERROR"
            error = "-"
            if result.error_code is not None or result.error_message:
                error = f"{result.error_code or '-'} {result.error_message or ''}".rstrip()
            elif not result.succeeded:
                error = "No bars returned"
            print(
                f"{result.ticker:<8} {result.feed:<14} {status:<8} "
                f"{result.bar_count:>5} {result.elapsed_ms:>7} ms  {error}"
            )

    print()
    print(report.verdict)


def write_json_artifact(report: ProbeReport, root: Path = Path("artifacts/diagnostics")) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    path = root / f"ib_historical_probe_{timestamp}.json"
    payload = {
        "diagnostic": "ib_historical_probe",
        "generated_at": datetime.now(UTC).isoformat(),
        "config": asdict(report.config),
        "contracts": list(report.contracts),
        "results": [_result_payload(result) for result in report.results],
        "connection_error": report.connection_error,
        "qualification_error": report.qualification_error,
        "verdict": report.verdict,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _result_payload(result: ProbeResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["result"] = "SUCCESS" if result.succeeded else "ERROR"
    return payload


def _contract_payload(contract: Any) -> dict[str, Any]:
    return {
        "conId": getattr(contract, "conId", None),
        "symbol": getattr(contract, "symbol", ""),
        "secType": getattr(contract, "secType", ""),
        "exchange": getattr(contract, "exchange", ""),
        "primaryExchange": getattr(contract, "primaryExchange", ""),
        "currency": getattr(contract, "currency", ""),
        "localSymbol": getattr(contract, "localSymbol", ""),
        "tradingClass": getattr(contract, "tradingClass", ""),
    }


def _select_error(
    errors: Sequence[ProviderError], raised_error: ProviderError | None
) -> ProviderError | None:
    if raised_error is not None:
        return raised_error
    for error in errors:
        if error.code == 162:
            return error
    for error in errors:
        if error.request_id is not None and error.request_id >= 0:
            return error
    return errors[0] if errors else None


def _bar_date(bar: Any) -> str | None:
    value = getattr(bar, "date", None)
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_csv(value: str, label: str) -> tuple[str, ...]:
    normalized = tuple(
        dict.fromkeys(part.strip().upper() for part in value.split(",") if part.strip())
    )
    if not normalized:
        raise ValueError(f"{label} must contain at least one value")
    return normalized


def _bounded_timeout(value: str) -> float:
    timeout = float(value)
    if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError("timeout must be greater than 0 and at most 30 seconds")
    return timeout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only, non-persisting IB Gateway historical daily-bar probe."
    )
    parser.add_argument("--tickers", required=True, help="Comma-separated stock tickers")
    parser.add_argument("--feeds", default="TRADES,ADJUSTED_LAST", help="Comma-separated feeds")
    parser.add_argument("--duration", default="10 D")
    parser.add_argument("--bar-size", default="1 day")
    parser.add_argument("--end-datetime", default="")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--client-id", type=int)
    parser.add_argument("--timeout", type=_bounded_timeout)
    parser.add_argument(
        "--json-artifact",
        action="store_true",
        help="Write a secrets-free diagnostic JSON file under artifacts/diagnostics",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        config = build_config(args, settings)
        if not config.duration or not config.bar_size:
            parser.error("duration and bar-size must not be empty")
        if not config.host or not 1 <= config.port <= 65535:
            parser.error("host and port must identify a valid IB Gateway endpoint")
        if not 0 <= config.client_id <= 65535:
            parser.error("client-id must be between 0 and 65535")
        exit_code, report = run_probe(config)
        print_report(report)
        if args.json_artifact:
            print(f"JSON_ARTIFACT {write_json_artifact(report).resolve()}")
        return exit_code
    except SystemExit:
        raise
    except Exception as exc:
        print(f"UNEXPECTED_DIAGNOSTIC_ERROR {exc}", file=sys.stderr)
        print("HISTORICAL_DATA_UNAVAILABLE")
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    raise SystemExit(main())
