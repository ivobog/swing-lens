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
from app.services.ib_data_fetcher import IBHistoricalRequestError, fetch_daily_bars
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


@dataclass(frozen=True)
class IBHistoricalFeedProbe:
    feed: str
    bar_count: int
    elapsed_ms: int
    result: str
    provider_error_code: int | None = None
    provider_error_category: str | None = None
    provider_error_message: str | None = None


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
        try:
            timeout = float(settings.ib_health_timeout_seconds)
            if hasattr(ib, "RequestTimeout"):
                ib.RequestTimeout = timeout
            ib.connect(
                settings.ib_host,
                settings.ib_port,
                clientId=_capability_client_id(settings),
                timeout=timeout,
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
                try:
                    bars = fetch_daily_bars(
                        ib,
                        contract,
                        feed,
                        settings=settings,
                        duration=PROBE_DURATION,
                        bar_size=PROBE_BAR_SIZE,
                        end_datetime="",
                    )
                    feed_results.append(
                        IBHistoricalFeedProbe(
                            feed=feed,
                            bar_count=len(bars),
                            elapsed_ms=_elapsed_ms(started),
                            result="SUCCESS" if bars else "NO_BARS",
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
                        )
                    )
                except Exception as exc:
                    feed_results.append(
                        IBHistoricalFeedProbe(
                            feed=feed,
                            bar_count=0,
                            elapsed_ms=_elapsed_ms(started),
                            result="FAILED",
                            provider_error_category="PROVIDER_ERROR",
                            provider_error_message=_safe_message(str(exc)),
                        )
                    )
            return _complete_status(
                checked_at=checked_at,
                feed_results=feed_results,
                server_version=server_version,
                api_ready=True,
            )
        except Exception as exc:
            status = IBHistoricalCapabilityStatus(
                status=IBHistoricalCapabilityState.IB_HISTORICAL_DATA_DEGRADED.value,
                checked_at=checked_at,
                ticker=PROBE_TICKER,
                feeds=tuple(feed_results),
                server_version=server_version,
                api_ready=False,
                result="FAILED",
                message=(
                    f"IB historical capability preflight could not run: {_safe_message(str(exc))}"
                ),
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
) -> IBHistoricalCapabilityStatus:
    if len(feed_results) == len(PROBE_FEEDS) and all(row.bar_count > 0 for row in feed_results):
        state = IBHistoricalCapabilityState.IB_HISTORICAL_DATA_READY
        result = "SUCCESS"
        message = "IB historical market data is ready for SPY TRADES and ADJUSTED_LAST."
    elif any(
        row.provider_error_category == "HISTORICAL_DATA_UNAVAILABLE" or row.result == "NO_BARS"
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


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _safe_message(message: str) -> str:
    return redact_text(str(message)).replace("\n", " ").strip()[:500]
