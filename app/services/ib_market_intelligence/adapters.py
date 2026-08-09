from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from app.services.ib_api import IB, Contract, ScannerSubscription, TagValue
from app.services.ib_market_intelligence.calculations import finite_number
from app.services.ib_market_intelligence.config import ScannerPreset
from app.services.ib_market_intelligence.dtos import (
    HistogramLevel,
    HistoricalMetricBarDTO,
    LiveSnapshotDTO,
)
from app.services.ib_market_intelligence.enums import (
    HISTORICAL_METRIC_SEMANTICS,
    AvailabilityStatus,
    HistoricalMetricType,
)
from app.services.ib_market_intelligence.request_budget import IBRequestBudget
from app.settings import Settings, get_settings

SUBSCRIPTION_ERROR_CODES = frozenset({10089, 10090, 10167, 10168, 354})
NOT_SUPPORTED_ERROR_CODES = frozenset({162, 200, 321})


def capability_status_from_error(error: Any) -> tuple[str, str]:
    code = getattr(error, "errorCode", None) or getattr(error, "code", None)
    message = str(getattr(error, "errorString", None) or getattr(error, "message", None) or error)
    if (
        code in SUBSCRIPTION_ERROR_CODES
        or "subscription" in message.lower()
        or "not subscribed" in message.lower()
    ):
        return AvailabilityStatus.SUBSCRIPTION_REQUIRED, message
    if code in NOT_SUPPORTED_ERROR_CODES or "not supported" in message.lower():
        return AvailabilityStatus.NOT_SUPPORTED, message
    return AvailabilityStatus.FAILED, message


class IBHistoricalMetricClient:
    def __init__(
        self,
        ib: IB,
        *,
        settings: Settings | None = None,
        budget: IBRequestBudget | None = None,
    ) -> None:
        self.ib = ib
        self.settings = settings or get_settings()
        self.budget = budget

    def fetch(
        self,
        contract: Contract,
        metric_type: str,
        *,
        duration: str,
        bar_size: str = "1 day",
    ) -> list[HistoricalMetricBarDTO]:
        metric = HistoricalMetricType(metric_type)
        if self.budget:
            self.budget.acquire_historical(metric.value)
        raw_bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=metric.value,
            useRTH=self.settings.ib_use_rth,
            formatDate=1,
            keepUpToDate=False,
        )
        return [
            parse_historical_metric_bar(
                bar,
                ticker=str(contract.symbol).upper(),
                ib_conid=finite_int(getattr(contract, "conId", None)),
                metric_type=metric,
                timeframe=bar_size,
                requested_range=duration,
            )
            for bar in raw_bars
        ]


def parse_historical_metric_bar(
    bar: Any,
    *,
    ticker: str,
    ib_conid: int | None,
    metric_type: HistoricalMetricType,
    timeframe: str,
    requested_range: str,
) -> HistoricalMetricBarDTO:
    values = [
        finite_number(getattr(bar, field, None), nonnegative=True)
        for field in ("open", "high", "low", "close")
    ]
    warnings: list[str] = []
    if metric_type == HistoricalMetricType.BID_ASK:
        bid, ask = values[0], values[3]
        if bid is None or ask is None or ask < bid:
            warnings.append("INVALID_BID_ASK_BAR")
    return HistoricalMetricBarDTO(
        ticker=ticker.upper(),
        ib_conid=ib_conid,
        session_date=_bar_date(getattr(bar, "date", None)),
        timeframe=timeframe,
        metric_type=metric_type.value,
        open_value=values[0],
        high_value=values[1],
        low_value=values[2],
        close_value=values[3],
        requested_range=requested_range,
        source_semantic_type=HISTORICAL_METRIC_SEMANTICS[metric_type],
        warning_flags=tuple(warnings),
    )


class IBLiveSnapshotManager:
    FIELD_MAP = {
        "SHORTABLE": ("shortableShares",),
        "OPTIONS_ACTIVITY": (
            "callVolume",
            "putVolume",
            "callOpenInterest",
            "putOpenInterest",
            "avOptionVolume",
        ),
        "VOL_LIVE": ("histVolatility", "impliedVolatility"),
    }
    GENERIC_TICKS = {"SHORTABLE": "236", "OPTIONS_ACTIVITY": "100,101,105", "VOL_LIVE": "104,106"}

    def __init__(
        self,
        ib: IB,
        *,
        budget: IBRequestBudget | None = None,
        timeout_seconds: float = 8.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.ib = ib
        self.budget = budget
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.restart_required = False

    def on_connection_error(
        self, _req_id: int, error_code: int, _message: str, _contract: Any = None
    ) -> None:
        if error_code == 1101:
            self.restart_required = True
        elif error_code == 1102:
            self.restart_required = False

    def capture(self, contract: Contract, snapshot_type: str) -> LiveSnapshotDTO:
        if snapshot_type not in self.FIELD_MAP:
            raise ValueError(f"Unsupported live snapshot type: {snapshot_type}")
        semaphore = self.budget.live_slots if self.budget else _NullSemaphore()
        with semaphore:
            observed = self.clock()
            ticker = None
            capability_errors: list[tuple[str, str]] = []

            def on_error(
                req_id: int, error_code: int, message: str, error_contract: Any = None
            ) -> None:
                self.on_connection_error(req_id, error_code, message, error_contract)
                if error_code in SUBSCRIPTION_ERROR_CODES | NOT_SUPPORTED_ERROR_CODES:
                    capability_errors.append(
                        capability_status_from_error(SimpleIBError(error_code, message))
                    )

            error_event = getattr(self.ib, "errorEvent", None)
            if error_event is not None:
                error_event += on_error
            try:
                ticker = self.ib.reqMktData(
                    contract,
                    genericTickList=self.GENERIC_TICKS[snapshot_type],
                    snapshot=False,
                    regulatorySnapshot=False,
                )
                deadline = time.monotonic() + self.timeout_seconds
                values: dict[str, Any] = {}
                while time.monotonic() < deadline:
                    values = self._read_values(ticker, snapshot_type)
                    if values:
                        break
                    wait = getattr(self.ib, "waitOnUpdate", None)
                    if callable(wait):
                        wait(timeout=min(0.25, max(0.0, deadline - time.monotonic())))
                    else:
                        break
                status, capability_reason = (
                    (AvailabilityStatus.AVAILABLE, None)
                    if values
                    else capability_errors[-1]
                    if capability_errors
                    else (
                        AvailabilityStatus.UNAVAILABLE,
                        "Required fields were not returned before timeout.",
                    )
                )
                return LiveSnapshotDTO(
                    ticker=str(contract.symbol).upper(),
                    ib_conid=finite_int(getattr(contract, "conId", None)),
                    effective_session=observed.date(),
                    observed_at=observed,
                    snapshot_type=snapshot_type,
                    values=values,
                    availability_status=status,
                    capability_reason=capability_reason,
                    source_request={
                        "generic_ticks": self.GENERIC_TICKS[snapshot_type],
                        "streaming": False,
                    },
                )
            except Exception as exc:
                status, reason = capability_status_from_error(exc)
                return LiveSnapshotDTO(
                    ticker=str(contract.symbol).upper(),
                    ib_conid=finite_int(getattr(contract, "conId", None)),
                    effective_session=observed.date(),
                    observed_at=observed,
                    snapshot_type=snapshot_type,
                    values={},
                    availability_status=status,
                    capability_reason=reason,
                    source_request={
                        "generic_ticks": self.GENERIC_TICKS[snapshot_type],
                        "streaming": False,
                    },
                )
            finally:
                if ticker is not None:
                    self.ib.cancelMktData(contract)
                if error_event is not None:
                    error_event -= on_error

    def _read_values(self, ticker: Any, snapshot_type: str) -> dict[str, Any]:
        aliases = {
            "callVolume": "call_volume",
            "putVolume": "put_volume",
            "callOpenInterest": "call_open_interest",
            "putOpenInterest": "put_open_interest",
            "avOptionVolume": "average_option_volume",
            "shortableShares": "shortable_shares",
            "histVolatility": "historical_volatility",
            "impliedVolatility": "implied_volatility",
        }
        values: dict[str, Any] = {}
        for field in self.FIELD_MAP[snapshot_type]:
            value = finite_number(getattr(ticker, field, None), nonnegative=True)
            if value is not None:
                values[aliases[field]] = value
        if snapshot_type == "SHORTABLE":
            shortable = next(
                (
                    finite_number(getattr(tick, "price", None), nonnegative=True)
                    for tick in reversed(list(getattr(ticker, "ticks", []) or []))
                    if getattr(tick, "tickType", None) == 46
                ),
                None,
            )
            if shortable is not None:
                values["shortable_indicator"] = shortable
                values["shortable_state"] = (
                    "EASY_TO_BORROW"
                    if shortable > 2.5
                    else "LOCATE_MAY_BE_REQUIRED"
                    if shortable > 1.5
                    else "NOT_SHORTABLE"
                )
        return values


class IBScannerClient:
    def __init__(self, ib: IB, *, budget: IBRequestBudget | None = None) -> None:
        self.ib = ib
        self.budget = budget

    def parameters(self) -> str:
        return str(self.ib.reqScannerParameters())

    def run(self, preset: ScannerPreset) -> list[dict[str, Any]]:
        semaphore = self.budget.scanner_slots if self.budget else _NullSemaphore()
        with semaphore:
            subscription = ScannerSubscription(
                instrument=preset.instrument,
                locationCode=preset.location,
                scanCode=preset.scan_code,
                numberOfRows=min(preset.max_results, 50),
            )
            filters = [TagValue(item["tag"], item["value"]) for item in preset.filters]
            # reqScannerData blocks until the initial result set is complete and
            # cancels its temporary subscription before returning.
            rows = self.ib.reqScannerData(
                subscription,
                scannerSubscriptionOptions=[],
                scannerSubscriptionFilterOptions=filters,
            )
            return [scanner_row(row) for row in list(rows)[: preset.max_results]]


def scanner_row(row: Any) -> dict[str, Any]:
    details = getattr(row, "contractDetails", None)
    contract = getattr(details, "contract", None)
    return {
        "rank": int(getattr(row, "rank", 0)),
        "ticker": str(getattr(contract, "symbol", "")).upper(),
        "ib_conid": finite_int(getattr(contract, "conId", None)),
        "contract_metadata": {
            "exchange": getattr(contract, "exchange", None),
            "primary_exchange": getattr(contract, "primaryExchange", None),
            "currency": getattr(contract, "currency", None),
            "sec_type": getattr(contract, "secType", None),
        },
        "scanner_metadata": {
            "distance": getattr(row, "distance", None),
            "benchmark": getattr(row, "benchmark", None),
            "projection": getattr(row, "projection", None),
            "legs": getattr(row, "legsStr", None),
        },
    }


class IBHistogramClient:
    def __init__(self, ib: IB) -> None:
        self.ib = ib

    def fetch(self, contract: Contract, *, use_rth: bool, period: str) -> list[HistogramLevel]:
        rows = self.ib.reqHistogramData(contract, useRTH=use_rth, period=period)
        return [
            HistogramLevel(price=float(row.price), activity_count=float(row.size))
            for row in rows
            if finite_number(getattr(row, "price", None), positive=True) is not None
            and finite_number(getattr(row, "size", None), nonnegative=True) is not None
        ]


def _bar_date(value: date | datetime | str | None) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported IB historical bar date: {value}")


def finite_int(value: Any) -> int | None:
    number = finite_number(value, positive=True)
    return int(number) if number is not None else None


class _NullSemaphore:
    def __enter__(self) -> _NullSemaphore:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class SimpleIBError:
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
