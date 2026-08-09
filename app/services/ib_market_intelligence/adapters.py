from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
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
from app.services.ib_market_intelligence.resilience import (
    RetryEvent,
    RetryPolicy,
    cancellable_sleep,
    is_retryable_ib_error,
    retry_call,
)
from app.settings import Settings, get_settings

SUBSCRIPTION_ERROR_CODES = frozenset({10089, 10090, 10167, 10168, 354})
NOT_SUPPORTED_ERROR_CODES = frozenset({200, 321})
MARKET_DATA_LINE_ERROR_CODES = frozenset({101})


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
    if code == 162 and "no data" in message.lower():
        return AvailabilityStatus.UNAVAILABLE, message
    return AvailabilityStatus.FAILED, message


class IBHistoricalMetricClient:
    def __init__(
        self,
        ib: IB,
        *,
        settings: Settings | None = None,
        budget: IBRequestBudget | None = None,
        retry_policy: RetryPolicy | None = None,
        reconnect: Callable[[], None] | None = None,
        guard: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_retry: Callable[[RetryEvent], None] | None = None,
    ) -> None:
        self.ib = ib
        self.settings = settings or get_settings()
        self.budget = budget
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=1)
        self.reconnect = reconnect
        self.guard = guard
        self.sleep = sleep
        self.on_retry = on_retry

    def fetch(
        self,
        contract: Contract,
        metric_type: str,
        *,
        duration: str,
        bar_size: str = "1 day",
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[HistoricalMetricBarDTO]:
        metric = HistoricalMetricType(metric_type)
        if (start_date is None) != (end_date is None):
            raise ValueError("historical start_date and end_date must be supplied together")
        if start_date and end_date:
            if start_date > end_date:
                raise ValueError("historical start_date cannot be after end_date")
            duration = f"{(end_date - start_date).days + 1} D"
        request_end = (
            datetime.combine(end_date + timedelta(days=1), datetime_time.min, tzinfo=UTC)
            if end_date
            else ""
        )

        def request() -> Any:
            if self.budget:
                self.budget.acquire_historical(metric.value, guard=self.guard)
            return self.ib.reqHistoricalData(
                contract,
                endDateTime=request_end,
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=metric.value,
                useRTH=self.settings.ib_use_rth,
                formatDate=1,
                keepUpToDate=False,
            )

        raw_bars = retry_call(
            request,
            operation_name=f"IBKR historical {metric.value}",
            policy=self.retry_policy,
            sleep=self.sleep,
            guard=self.guard,
            reconnect=self.reconnect,
            on_retry=self.on_retry,
        )
        parsed = [
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
        if start_date and end_date:
            parsed = [bar for bar in parsed if start_date <= bar.session_date <= end_date]
        return parsed


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
    REQUIRED_FIELDS = {
        "SHORTABLE": frozenset({"shortable_indicator"}),
        "OPTIONS_ACTIVITY": frozenset(
            {"call_volume", "put_volume", "call_open_interest", "put_open_interest"}
        ),
        "VOL_LIVE": frozenset({"historical_volatility", "implied_volatility"}),
    }

    def __init__(
        self,
        ib: IB,
        *,
        budget: IBRequestBudget | None = None,
        timeout_seconds: float = 8.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        retry_policy: RetryPolicy | None = None,
        reconnect: Callable[[], None] | None = None,
        guard: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_retry: Callable[[RetryEvent], None] | None = None,
    ) -> None:
        self.ib = ib
        self.budget = budget
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=1)
        self.reconnect = reconnect
        self.guard = guard
        self.sleep = sleep
        self.on_retry = on_retry
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
        slot = (
            _semaphore_slot(self.budget.live_slots, guard=self.guard)
            if self.budget
            else _NullSemaphore()
        )
        with slot:
            best: LiveSnapshotDTO | None = None
            resubscriptions = 0
            for attempt in range(1, self.retry_policy.max_attempts + 1):
                if self.guard:
                    self.guard()
                self.restart_required = False
                try:
                    result = self._capture_once(contract, snapshot_type)
                except _LiveDataLost as exc:
                    resubscriptions += 1
                    result = None
                    retryable = True
                    retry_error: Exception = exc
                except Exception as exc:
                    result = None
                    retryable = is_retryable_ib_error(exc)
                    retry_error = exc
                    if retryable and self.reconnect:
                        self.reconnect()
                if result is not None:
                    if best is None or len(result.values) > len(best.values):
                        best = result
                    if result.availability_status == AvailabilityStatus.AVAILABLE:
                        return self._with_attempts(result, attempt, resubscriptions)
                    if result.availability_status in {
                        AvailabilityStatus.SUBSCRIPTION_REQUIRED,
                        AvailabilityStatus.NOT_SUPPORTED,
                    } or "MARKET_DATA_LINE_CAP" in result.warning_flags:
                        return self._with_attempts(result, attempt, resubscriptions)
                    retryable = True
                    retry_error = TimeoutError(result.capability_reason or "snapshot timed out")
                if not retryable or attempt >= self.retry_policy.max_attempts:
                    if best is not None:
                        return self._with_attempts(best, attempt, resubscriptions)
                    status, reason = capability_status_from_error(retry_error)
                    return self._failed_snapshot(
                        contract,
                        snapshot_type,
                        status,
                        reason,
                        attempt,
                        resubscriptions,
                    )
                event = RetryEvent(
                    attempt,
                    attempt + 1,
                    self.retry_policy.delay(attempt),
                    retry_error,
                )
                if self.on_retry:
                    self.on_retry(event)
                cancellable_sleep(
                    event.delay_seconds,
                    sleep=self.sleep,
                    guard=self.guard,
                )
            raise AssertionError("live snapshot retry loop exited unexpectedly")

    def _capture_once(self, contract: Contract, snapshot_type: str) -> LiveSnapshotDTO:
        observed = self.clock()
        ticker = None
        capability_errors: list[tuple[str, str]] = []
        line_cap_error = False

        def on_error(
            req_id: int, error_code: int, message: str, error_contract: Any = None
        ) -> None:
            nonlocal line_cap_error
            self.on_connection_error(req_id, error_code, message, error_contract)
            if error_code in MARKET_DATA_LINE_ERROR_CODES:
                line_cap_error = True
                if self.budget:
                    self.budget.record_market_data_line_error(error_code)
            if error_code in SUBSCRIPTION_ERROR_CODES | NOT_SUPPORTED_ERROR_CODES:
                capability_errors.append(
                    capability_status_from_error(SimpleIBError(error_code, message))
                )

        error_event = getattr(self.ib, "errorEvent", None)
        if error_event is not None:
            error_event += on_error
        try:
            if self.budget:
                self.budget.acquire_tws_request("LIVE_MARKET_DATA", guard=self.guard)
            ticker = self.ib.reqMktData(
                contract,
                genericTickList=self.GENERIC_TICKS[snapshot_type],
                snapshot=False,
                regulatorySnapshot=False,
            )
            if self.budget:
                self.budget.line_acquired()
            deadline = time.monotonic() + self.timeout_seconds
            values: dict[str, Any] = {}
            while time.monotonic() < deadline:
                if self.guard:
                    self.guard()
                if self.restart_required:
                    raise _LiveDataLost("IBKR reported restored connectivity with data lost (1101)")
                values = self._read_values(ticker, snapshot_type)
                if self._required_fields_present(values, snapshot_type) or line_cap_error:
                    break
                wait = getattr(self.ib, "waitOnUpdate", None)
                if callable(wait):
                    wait(timeout=min(0.25, max(0.0, deadline - time.monotonic())))
                else:
                    break
            missing_fields = sorted(self.REQUIRED_FIELDS[snapshot_type] - values.keys())
            status, capability_reason = (
                (AvailabilityStatus.AVAILABLE, None)
                if not missing_fields
                else capability_errors[-1]
                if capability_errors
                else (
                    AvailabilityStatus.UNAVAILABLE,
                    "Market-data-line allowance was exhausted"
                    if line_cap_error
                    else "Required fields were not returned before timeout: "
                    + ", ".join(missing_fields),
                )
            )
            warnings = [f"MISSING_REQUIRED_FIELD:{field}" for field in missing_fields]
            if line_cap_error:
                warnings.append("MARKET_DATA_LINE_CAP")
            return LiveSnapshotDTO(
                ticker=str(contract.symbol).upper(),
                ib_conid=finite_int(getattr(contract, "conId", None)),
                effective_session=observed.date(),
                observed_at=observed,
                snapshot_type=snapshot_type,
                values=values,
                availability_status=status,
                capability_reason=capability_reason,
                warning_flags=tuple(warnings),
                source_request=self._source_request(snapshot_type, missing_fields),
            )
        finally:
            if ticker is not None:
                with suppress(Exception):
                    self.ib.cancelMktData(contract)
                if self.budget:
                    self.budget.line_released()
            if error_event is not None:
                error_event -= on_error

    def _failed_snapshot(
        self,
        contract: Contract,
        snapshot_type: str,
        status: str,
        reason: str,
        attempts: int,
        resubscriptions: int,
    ) -> LiveSnapshotDTO:
        observed = self.clock()
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
                **self._source_request(snapshot_type, sorted(self.REQUIRED_FIELDS[snapshot_type])),
                "attempts": attempts,
                "retry_count": max(0, attempts - 1),
                "resubscriptions": resubscriptions,
            },
        )

    def _with_attempts(
        self, result: LiveSnapshotDTO, attempts: int, resubscriptions: int
    ) -> LiveSnapshotDTO:
        return LiveSnapshotDTO(
            **{
                **result.__dict__,
                "source_request": {
                    **result.source_request,
                    "attempts": attempts,
                    "retry_count": max(0, attempts - 1),
                    "resubscriptions": resubscriptions,
                    "line_capacity": self.budget.observability() if self.budget else None,
                },
            }
        )

    def _source_request(self, snapshot_type: str, missing_fields: list[str]) -> dict[str, Any]:
        return {
            "generic_ticks": self.GENERIC_TICKS[snapshot_type],
            "streaming": True,
            "cancel_after_completion": True,
            "required_fields": sorted(self.REQUIRED_FIELDS[snapshot_type]),
            "missing_required_fields": missing_fields,
        }

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

    def _required_fields_present(self, values: dict[str, Any], snapshot_type: str) -> bool:
        return self.REQUIRED_FIELDS[snapshot_type].issubset(values)


class IBScannerClient:
    def __init__(self, ib: IB, *, budget: IBRequestBudget | None = None) -> None:
        self.ib = ib
        self.budget = budget

    def parameters(self) -> str:
        if self.budget:
            self.budget.acquire_tws_request("SCANNER_PARAMETERS")
        return str(self.ib.reqScannerParameters())

    def run(self, preset: ScannerPreset) -> list[dict[str, Any]]:
        semaphore = self.budget.scanner_slots if self.budget else _NullSemaphore()
        with semaphore:
            if self.budget:
                self.budget.acquire_tws_request("SCANNER")
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
    def __init__(self, ib: IB, *, budget: IBRequestBudget | None = None) -> None:
        self.ib = ib
        self.budget = budget

    def fetch(self, contract: Contract, *, use_rth: bool, period: str) -> list[HistogramLevel]:
        if self.budget:
            self.budget.acquire_tws_request("HISTOGRAM")
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


@contextmanager
def _semaphore_slot(semaphore: Any, *, guard: Callable[[], None] | None = None):
    acquired = False
    try:
        while not acquired:
            if guard:
                guard()
            acquired = semaphore.acquire(timeout=0.25)
        yield
    finally:
        if acquired:
            semaphore.release()


class SimpleIBError:
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message


class _LiveDataLost(ConnectionError):
    pass
