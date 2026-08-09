from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from app.services.operational_metrics import operational_metrics

HISTORICAL_WEIGHTS = {
    "ADJUSTED_LAST": 1,
    "TRADES": 1,
    "FEE_RATE": 1,
    "HISTORICAL_VOLATILITY": 1,
    "OPTION_IMPLIED_VOLATILITY": 1,
    "BID_ASK": 2,
}


@dataclass(frozen=True)
class RequestBudgetConfig:
    historical_weighted_tokens_per_minute: int = 15
    historical_min_spacing_seconds: float = 3.0
    tws_min_spacing_seconds: float = 0.25
    live_snapshot_concurrency: int = 10
    market_data_line_cap: int = 100
    scanner_concurrency: int = 10
    flex_send_per_minute: int = 10


class WeightedWindowBudget:
    def __init__(
        self,
        limit: int,
        *,
        window_seconds: float = 60.0,
        min_spacing_seconds: float = 0.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if limit < 1:
            raise ValueError("request-budget limit must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self.min_spacing_seconds = max(0.0, min_spacing_seconds)
        self._monotonic = monotonic
        self._sleep = sleep
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def acquire(self, weight: int = 1, *, guard: Callable[[], None] | None = None) -> None:
        if not 1 <= weight <= self.limit:
            raise ValueError("request weight must be between one and the configured limit")
        with self._lock:
            while True:
                now = self._monotonic()
                self._expire(now)
                used = sum(event_weight for _, event_weight in self._events)
                spacing_wait = (
                    max(0.0, self.min_spacing_seconds - (now - self._events[-1][0]))
                    if self._events
                    else 0.0
                )
                window_wait = (
                    max(0.0, self.window_seconds - (now - self._events[0][0]))
                    if used + weight > self.limit
                    else 0.0
                )
                wait = max(spacing_wait, window_wait)
                if wait <= 0:
                    self._events.append((now, weight))
                    return
                operational_metrics.increment(
                    "swinglens_ibmi_pacing_waits_total", weight=weight
                )
                operational_metrics.increment(
                    "swinglens_ibmi_pacing_wait_seconds_total", value=wait
                )
                if guard:
                    remaining = wait
                    while remaining > 0:
                        guard()
                        step = min(0.25, remaining)
                        self._sleep(step)
                        remaining -= step
                    guard()
                else:
                    self._sleep(wait)

    def _expire(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= self.window_seconds:
            self._events.popleft()


class IBRequestBudget:
    """Module-aware budgets; it supplements IB's server pacing and preserves OHLCV priority."""

    def __init__(self, config: RequestBudgetConfig) -> None:
        self.config = config
        self.historical = WeightedWindowBudget(
            config.historical_weighted_tokens_per_minute,
            min_spacing_seconds=config.historical_min_spacing_seconds,
        )
        self.tws_spacing = WeightedWindowBudget(
            60_000,
            min_spacing_seconds=config.tws_min_spacing_seconds,
        )
        live_limit = min(config.live_snapshot_concurrency, config.market_data_line_cap)
        self.live_slots = threading.BoundedSemaphore(live_limit)
        self.scanner_slots = threading.BoundedSemaphore(config.scanner_concurrency)
        self.flex_send = WeightedWindowBudget(config.flex_send_per_minute)
        self._last_flex_send = 0.0
        self._flex_lock = threading.Lock()
        self._line_lock = threading.Lock()
        self._active_lines = 0
        self._peak_lines = 0
        self._subscriptions = 0
        self._line_cap_errors = 0
        self._last_line_error_code: int | None = None

    def acquire_historical(
        self, what_to_show: str, *, guard: Callable[[], None] | None = None
    ) -> None:
        self.tws_spacing.acquire(guard=guard)
        self.historical.acquire(HISTORICAL_WEIGHTS.get(what_to_show, 1), guard=guard)

    def acquire_tws_request(
        self, request_family: str, *, guard: Callable[[], None] | None = None
    ) -> None:
        self.tws_spacing.acquire(guard=guard)
        operational_metrics.increment(
            "swinglens_ibmi_tws_requests_total", request_family=request_family
        )

    def line_acquired(self) -> None:
        with self._line_lock:
            self._active_lines += 1
            self._subscriptions += 1
            self._peak_lines = max(self._peak_lines, self._active_lines)
        operational_metrics.increment("swinglens_ibmi_market_data_subscriptions_total")

    def line_released(self) -> None:
        with self._line_lock:
            self._active_lines = max(0, self._active_lines - 1)

    def record_market_data_line_error(self, code: int | None) -> None:
        with self._line_lock:
            self._line_cap_errors += 1
            self._last_line_error_code = code
        operational_metrics.increment("swinglens_ibmi_market_data_line_cap_errors_total")

    def observability(self) -> dict[str, int | float | None]:
        with self._line_lock:
            return {
                "configured_account_line_cap": self.config.market_data_line_cap,
                "configured_live_concurrency": self.config.live_snapshot_concurrency,
                "effective_live_concurrency": min(
                    self.config.live_snapshot_concurrency,
                    self.config.market_data_line_cap,
                ),
                "active_lines": self._active_lines,
                "peak_active_lines": self._peak_lines,
                "subscriptions_started": self._subscriptions,
                "line_cap_errors": self._line_cap_errors,
                "last_line_error_code": self._last_line_error_code,
                "historical_weighted_limit_per_minute": (
                    self.config.historical_weighted_tokens_per_minute
                ),
                "historical_min_spacing_seconds": self.config.historical_min_spacing_seconds,
                "tws_min_spacing_seconds": self.config.tws_min_spacing_seconds,
            }

    def acquire_flex_send(self, *, guard: Callable[[], None] | None = None) -> None:
        self.flex_send.acquire(guard=guard)
        with self._flex_lock:
            now = time.monotonic()
            wait = 1.0 - (now - self._last_flex_send)
            if self._last_flex_send and wait > 0:
                if guard:
                    remaining = wait
                    while remaining > 0:
                        guard()
                        step = min(0.25, remaining)
                        time.sleep(step)
                        remaining -= step
                    guard()
                else:
                    time.sleep(wait)
            self._last_flex_send = time.monotonic()
