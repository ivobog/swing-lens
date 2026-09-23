from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.services.ib_api import IB, Contract
from app.services.ib_historical_errors import classify_ib_historical_error
from app.settings import Settings, get_settings

_IB_INFORMATIONAL_CODES = {2104, 2106, 2107, 2108, 2158}


class IBHistoricalRequestError(RuntimeError):
    """A provider callback error emitted for a synchronous historical request."""

    def __init__(
        self,
        *,
        code: int,
        provider_message: str,
        classification: str | None = None,
        retryable: bool | None = None,
        request_id: int | None = None,
    ) -> None:
        policy = classify_ib_historical_error(code, provider_message)
        self.code = code
        self.provider_message = provider_message
        self.classification = classification or policy.category.value
        self.retryable = policy.retryable if retryable is None else retryable
        self.request_id = request_id
        super().__init__(f"IB historical request error {code}: {provider_message}")


class IBHistoricalTimeoutError(TimeoutError):
    """The synchronous IB request exceeded its bounded client timeout."""

    def __init__(
        self, *, symbol: str, feed: str, request_id: int | None, timeout_seconds: float
    ) -> None:
        self.request_id = request_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"IB historical {feed} request for {symbol} timed out after "
            f"{timeout_seconds:g}s (request_id={request_id})"
        )


@dataclass(frozen=True)
class HistoricalBar:
    ticker: str
    bar_date: date
    timeframe: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    source: str
    what_to_show: str
    adjustment_type: str | None


def fetch_daily_bars(
    ib: IB,
    contract: Contract,
    what_to_show: str,
    settings: Settings | None = None,
    duration: str | None = None,
    bar_size: str | None = None,
    end_datetime: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[HistoricalBar]:
    settings = settings or get_settings()
    request_duration = duration or settings.ib_full_backfill_duration
    request_bar_size = bar_size or settings.ib_default_bar_size
    provider_errors: list[IBHistoricalRequestError] = []
    active_request_id: int | None = None
    timeout_seconds = float(getattr(ib, "RequestTimeout", None) or settings.ib_timeout_seconds)
    if diagnostics is not None:
        diagnostics.update(
            {
                "request_id": None,
                "contract": {
                    "symbol": getattr(contract, "symbol", None),
                    "con_id": getattr(contract, "conId", None),
                    "sec_type": getattr(contract, "secType", None),
                    "exchange": getattr(contract, "exchange", None),
                    "primary_exchange": getattr(contract, "primaryExchange", None),
                    "currency": getattr(contract, "currency", None),
                },
                "request": {
                    "end_datetime": end_datetime or "",
                    "duration": request_duration,
                    "bar_size": request_bar_size,
                    "what_to_show": what_to_show,
                    "use_rth": settings.ib_use_rth,
                    "format_date": 1,
                    "keep_up_to_date": False,
                    "chart_options": [],
                },
                "timeout_seconds": timeout_seconds,
                "provider_callbacks": [],
            }
        )

    client = getattr(ib, "client", None)
    original_client_request = getattr(client, "reqHistoricalData", None)

    def capture_request(request_id: int, *args: Any, **kwargs: Any) -> Any:
        nonlocal active_request_id
        active_request_id = int(request_id)
        if diagnostics is not None:
            diagnostics["request_id"] = active_request_id
        return original_client_request(request_id, *args, **kwargs)

    request_wrapped = callable(original_client_request)
    if request_wrapped:
        client.reqHistoricalData = capture_request

    def on_error(
        request_id: int,
        error_code: int,
        error_message: str,
        error_contract: Contract | None = None,
    ) -> None:
        if int(request_id) < 0 or int(error_code) in _IB_INFORMATIONAL_CODES:
            return
        if active_request_id is not None and int(request_id) != active_request_id:
            return
        requested_conid = int(getattr(contract, "conId", 0) or 0)
        error_conid = int(getattr(error_contract, "conId", 0) or 0)
        if requested_conid and error_conid and requested_conid != error_conid:
            return
        if diagnostics is not None:
            diagnostics["provider_callbacks"].append(
                {
                    "request_id": int(request_id),
                    "code": int(error_code),
                    "message": str(error_message),
                }
            )
        policy = classify_ib_historical_error(int(error_code), str(error_message))
        provider_errors.append(
            IBHistoricalRequestError(
                code=int(error_code),
                provider_message=str(error_message),
                classification=policy.category.value,
                retryable=policy.retryable,
                request_id=int(request_id),
            )
        )

    error_event = getattr(ib, "errorEvent", None)
    if error_event is not None:
        error_event += on_error
    try:
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_datetime or "",
                durationStr=request_duration,
                barSizeSetting=request_bar_size,
                whatToShow=what_to_show,
                useRTH=settings.ib_use_rth,
                formatDate=1,
                keepUpToDate=False,
            )
        except Exception as exc:
            if provider_errors:
                raise provider_errors[0] from exc
            if isinstance(exc, TimeoutError):
                if active_request_id is not None:
                    cancel = getattr(client, "cancelHistoricalData", None)
                    if callable(cancel):
                        try:
                            cancel(active_request_id)
                        except Exception as cancel_exc:
                            if diagnostics is not None:
                                diagnostics["cancel_error_type"] = type(cancel_exc).__name__
                raise IBHistoricalTimeoutError(
                    symbol=contract.symbol,
                    feed=what_to_show,
                    request_id=active_request_id,
                    timeout_seconds=timeout_seconds,
                ) from exc
            raise
    finally:
        if error_event is not None:
            error_event -= on_error
        if request_wrapped:
            client.reqHistoricalData = original_client_request
    if provider_errors:
        raise provider_errors[0]

    ticker = contract.symbol.upper()
    adjustment_type = "adjusted" if what_to_show == "ADJUSTED_LAST" else None
    return [
        HistoricalBar(
            ticker=ticker,
            bar_date=_bar_date(bar.date),
            timeframe=request_bar_size,
            open=_optional_float(bar.open),
            high=_optional_float(bar.high),
            low=_optional_float(bar.low),
            close=_optional_float(bar.close),
            volume=_optional_float(bar.volume),
            source="IB",
            what_to_show=what_to_show,
            adjustment_type=adjustment_type,
        )
        for bar in bars
    ]


def _bar_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y%m%d").date()


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number
