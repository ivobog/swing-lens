from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from time import perf_counter
from typing import Any

from app.observability.logging import log_event
from app.services.ib_api import IB, Stock
from app.services.ib_connection import create_ib_client
from app.services.ib_data_fetcher import (
    IBHistoricalRequestError,
    IBHistoricalTimeoutError,
    fetch_daily_bars,
)
from app.services.ib_fetch_plan_service import FetchPlan
from app.services.redaction import redact_text
from app.settings import ProcessRole, Settings, get_settings

logger = logging.getLogger(__name__)

PROBE_TICKER = "SPY"
PROBE_FEEDS = ("TRADES", "ADJUSTED_LAST")
PROBE_DURATION = "2 D"
PROBE_BAR_SIZE = "1 day"


class IBHistoricalCapabilityState(StrEnum):
    IB_HISTORICAL_DATA_READY = "IB_HISTORICAL_DATA_READY"
    IB_HISTORICAL_DATA_UNAVAILABLE = "IB_HISTORICAL_DATA_UNAVAILABLE"
    IB_HISTORICAL_DATA_DEGRADED = "IB_HISTORICAL_DATA_DEGRADED"


class IBHistoricalFailureType(StrEnum):
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_REQUEST = "INVALID_REQUEST"
    NO_DATA = "NO_DATA"
    TIMEOUT = "TIMEOUT"
    DISCONNECTED = "DISCONNECTED"
    CONTRACT_ERROR = "CONTRACT_ERROR"
    PACING_OR_RATE_LIMIT = "PACING_OR_RATE_LIMIT"
    INTERNAL_ADAPTER_ERROR = "INTERNAL_ADAPTER_ERROR"
    UNKNOWN_PROVIDER_FAILURE = "UNKNOWN_PROVIDER_FAILURE"


@dataclass(frozen=True)
class IBHistoricalFeedProbe:
    feed: str
    bar_count: int
    elapsed_ms: int
    result: str
    provider_error_code: int | None = None
    provider_error_category: str | None = None
    provider_error_message: str | None = None
    failure_type: str | None = None
    request_id: int | None = None
    contract: dict[str, Any] | None = None
    request: dict[str, Any] | None = None
    timeout_seconds: float | None = None
    exception_type: str | None = None
    provider_callbacks: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class IBHistoricalCapabilityStatus:
    status: str
    checked_at: datetime
    ticker: str
    feeds: tuple[IBHistoricalFeedProbe, ...]
    server_version: int | None
    api_ready: bool
    result: str
    message: str
    failure_type: str | None = None
    probe_plan_source: str = "DEFAULT_SPY_CAPABILITY"

    @property
    def ready(self) -> bool:
        return self.status == IBHistoricalCapabilityState.IB_HISTORICAL_DATA_READY.value

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checked_at"] = self.checked_at.isoformat()
        return payload


IBFactory = Callable[[], IB]
_CAPABILITY_PROBE_LOCK = Lock()
_ROLE_OFFSET = {
    ProcessRole.WEB: 0,
    ProcessRole.DURABLE_WORKER: 1,
    ProcessRole.SUPERVISOR: 2,
    ProcessRole.CLI_OR_MAINTENANCE: 3,
}


def check_historical_data_capability(
    settings: Settings | None = None,
    *,
    ib_factory: IBFactory = IB,
    fetch_plan: FetchPlan | None = None,
) -> IBHistoricalCapabilityStatus:
    """Run a two-request, non-persisting SPY historical capability preflight."""

    settings = settings or get_settings()
    checked_at = datetime.now(UTC)
    log_event(
        logger,
        "ib.historical_capability.started",
        ticker=PROBE_TICKER,
        feeds=list(PROBE_FEEDS),
        checked_at=checked_at.isoformat(),
    )
    with _CAPABILITY_PROBE_LOCK:
        ib = create_ib_client(ib_factory)
        server_version = None
        feed_results: list[IBHistoricalFeedProbe] = []
        plan_source = "PIPELINE_SPY_PLAN" if fetch_plan is not None else "DEFAULT_SPY_CAPABILITY"
        try:
            connect_timeout = float(settings.ib_health_timeout_seconds)
            # A historical request is not an API handshake. Use the same bounded
            # request timeout as the real acquisition executor, not the 3s health limit.
            if hasattr(ib, "RequestTimeout"):
                ib.RequestTimeout = settings.ib_timeout_seconds
            ib.connect(
                settings.ib_host,
                settings.ib_port,
                clientId=_capability_client_id(settings),
                timeout=connect_timeout,
                readonly=True,
            )
            if not ib.isConnected():
                raise ConnectionError("IB API connect returned without an active connection")
            server_version = _server_version(ib)
            qualified = list(ib.qualifyContracts(Stock(PROBE_TICKER, "SMART", "USD")))
            if len(qualified) != 1:
                raise RuntimeError(
                    f"SPY capability contract qualification returned {len(qualified)} contracts"
                )
            contract = qualified[0]
            for feed in PROBE_FEEDS:
                started = perf_counter()
                trace: dict[str, Any] = {}
                duration, bar_size, end_datetime = _probe_request(fetch_plan, feed)
                try:
                    bars = fetch_daily_bars(
                        ib,
                        contract,
                        feed,
                        settings=settings,
                        duration=duration,
                        bar_size=bar_size,
                        end_datetime=end_datetime,
                        diagnostics=trace,
                    )
                    feed_results.append(
                        IBHistoricalFeedProbe(
                            feed=feed,
                            bar_count=len(bars),
                            elapsed_ms=_elapsed_ms(started),
                            result="SUCCESS" if bars else "NO_BARS",
                            failure_type=(None if bars else IBHistoricalFailureType.NO_DATA.value),
                            **_trace_fields(trace),
                        )
                    )
                except IBHistoricalRequestError as exc:
                    feed_results.append(
                        IBHistoricalFeedProbe(
                            feed=feed,
                            bar_count=0,
                            elapsed_ms=_elapsed_ms(started),
                            result="FAILED",
                            provider_error_code=exc.code,
                            provider_error_category=exc.classification,
                            provider_error_message=_safe_message(exc.provider_message),
                            failure_type=_provider_failure_type(exc).value,
                            **_trace_fields(trace),
                        )
                    )
                except IBHistoricalTimeoutError as exc:
                    feed_results.append(
                        IBHistoricalFeedProbe(
                            feed=feed,
                            bar_count=0,
                            elapsed_ms=_elapsed_ms(started),
                            result="FAILED",
                            provider_error_category=IBHistoricalFailureType.TIMEOUT.value,
                            provider_error_message=_safe_message(str(exc)),
                            failure_type=IBHistoricalFailureType.TIMEOUT.value,
                            exception_type=type(exc).__name__,
                            **_trace_fields(trace),
                        )
                    )
                except Exception as exc:
                    failure_type = (
                        IBHistoricalFailureType.DISCONNECTED
                        if not ib.isConnected()
                        else IBHistoricalFailureType.INTERNAL_ADAPTER_ERROR
                    )
                    feed_results.append(
                        IBHistoricalFeedProbe(
                            feed=feed,
                            bar_count=0,
                            elapsed_ms=_elapsed_ms(started),
                            result="FAILED",
                            provider_error_category=failure_type.value,
                            provider_error_message=_safe_message(str(exc) or repr(exc)),
                            failure_type=failure_type.value,
                            exception_type=type(exc).__name__,
                            **_trace_fields(trace),
                        )
                    )
            return _complete_status(
                checked_at=checked_at,
                feed_results=feed_results,
                server_version=server_version,
                api_ready=ib.isConnected(),
                probe_plan_source=plan_source,
            )
        except Exception as exc:
            failure_type = (
                IBHistoricalFailureType.TIMEOUT
                if isinstance(exc, TimeoutError)
                else (
                    IBHistoricalFailureType.CONTRACT_ERROR
                    if server_version is not None and ib.isConnected()
                    else IBHistoricalFailureType.DISCONNECTED
                )
            )
            status = IBHistoricalCapabilityStatus(
                status=IBHistoricalCapabilityState.IB_HISTORICAL_DATA_DEGRADED.value,
                checked_at=checked_at,
                ticker=PROBE_TICKER,
                feeds=tuple(feed_results),
                server_version=server_version,
                api_ready=False,
                result="FAILED",
                message=(
                    "IB historical capability preflight could not run: "
                    f"{_safe_message(str(exc) or repr(exc))}"
                ),
                failure_type=failure_type.value,
                probe_plan_source=plan_source,
            )
            _log_status(status)
            return status
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass


def _complete_status(
    *,
    checked_at: datetime,
    feed_results: list[IBHistoricalFeedProbe],
    server_version: int | None,
    api_ready: bool,
    probe_plan_source: str,
) -> IBHistoricalCapabilityStatus:
    if not api_ready:
        state = IBHistoricalCapabilityState.IB_HISTORICAL_DATA_DEGRADED
        result = "FAILED"
        message = "IB Gateway disconnected during the historical capability probe."
    elif len(feed_results) == len(PROBE_FEEDS) and all(row.bar_count > 0 for row in feed_results):
        state = IBHistoricalCapabilityState.IB_HISTORICAL_DATA_READY
        result = "SUCCESS"
        message = "IB historical market data is ready for SPY TRADES and ADJUSTED_LAST."
    elif any(
        row.failure_type
        in {
            IBHistoricalFailureType.NO_DATA.value,
            IBHistoricalFailureType.PERMISSION_DENIED.value,
        }
        or row.provider_error_category == "HISTORICAL_DATA_UNAVAILABLE"
        for row in feed_results
    ):
        state = IBHistoricalCapabilityState.IB_HISTORICAL_DATA_UNAVAILABLE
        result = "FAILED"
        message = (
            "IB historical market data is currently unavailable. Gateway API connectivity is "
            "healthy, but the historical-data capability probe failed for SPY."
        )
    else:
        state = IBHistoricalCapabilityState.IB_HISTORICAL_DATA_DEGRADED
        result = "FAILED"
        message = (
            "IB historical market data is degraded. Gateway API connectivity is healthy, but "
            "the historical-data capability probe failed for SPY."
        )
    status = IBHistoricalCapabilityStatus(
        status=state.value,
        checked_at=checked_at,
        ticker=PROBE_TICKER,
        feeds=tuple(feed_results),
        server_version=server_version,
        api_ready=api_ready,
        result=result,
        message=message,
        failure_type=(
            IBHistoricalFailureType.DISCONNECTED.value
            if not api_ready
            else next((row.failure_type for row in feed_results if row.failure_type), None)
        ),
        probe_plan_source=probe_plan_source,
    )
    _log_status(status)
    return status


def _log_status(status: IBHistoricalCapabilityStatus) -> None:
    event = (
        "ib.historical_capability.completed" if status.ready else "ib.historical_capability.failed"
    )
    payload = status.to_dict()
    payload["capability_message"] = payload.pop("message")
    log_event(
        logger,
        event,
        level=logging.INFO if status.ready else logging.ERROR,
        **payload,
    )


def _capability_client_id(settings: Settings) -> int:
    client_id = int(settings.ib_health_client_id) + 4 + _ROLE_OFFSET[settings.process_role]
    if not 0 <= client_id <= 65535:
        raise ValueError("IB historical capability client ID is outside the valid range")
    return client_id


def _server_version(ib: IB) -> int | None:
    method = getattr(getattr(ib, "client", None), "serverVersion", None)
    value = int(method()) if callable(method) else 0
    return value or None


def _probe_request(fetch_plan: FetchPlan | None, feed: str) -> tuple[str, str, str]:
    if fetch_plan is not None:
        for item in fetch_plan.items:
            if item.ticker.upper() == PROBE_TICKER and item.what_to_show == feed:
                if item.estimated_request_count and item.duration:
                    return item.duration, item.bar_size, item.request_end_datetime or ""
    return PROBE_DURATION, PROBE_BAR_SIZE, ""


def _trace_fields(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": trace.get("request_id"),
        "contract": trace.get("contract"),
        "request": trace.get("request"),
        "timeout_seconds": trace.get("timeout_seconds"),
        "provider_callbacks": tuple(
            {
                "request_id": row.get("request_id"),
                "code": row.get("code"),
                "message": _safe_message(row.get("message") or ""),
            }
            for row in trace.get("provider_callbacks", ())
        ),
    }


def _provider_failure_type(exc: IBHistoricalRequestError) -> IBHistoricalFailureType:
    message = exc.provider_message.casefold()
    if exc.code in {354, 10167} or any(
        marker in message for marker in ("not subscribed", "permission", "subscription")
    ):
        return IBHistoricalFailureType.PERMISSION_DENIED
    if exc.classification == "HISTORICAL_PACING" or "rate limit" in message:
        return IBHistoricalFailureType.PACING_OR_RATE_LIMIT
    if exc.classification == "HISTORICAL_DATA_UNAVAILABLE":
        return IBHistoricalFailureType.NO_DATA
    if exc.code == 321 or "invalid request" in message:
        return IBHistoricalFailureType.INVALID_REQUEST
    if exc.classification == "PROVIDER_REJECTED" or "rejected" in message:
        return IBHistoricalFailureType.PROVIDER_REJECTED
    return IBHistoricalFailureType.UNKNOWN_PROVIDER_FAILURE


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _safe_message(message: str) -> str:
    return redact_text(str(message)).replace("\n", " ").strip()[:500]
