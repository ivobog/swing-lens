from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

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
    live_snapshot_concurrency: int = 10
    scanner_concurrency: int = 10
    flex_send_per_minute: int = 10


class WeightedWindowBudget:
    def __init__(
        self,
        limit: int,
        *,
        window_seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if limit < 1:
            raise ValueError("request-budget limit must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def acquire(self, weight: int = 1) -> None:
        if not 1 <= weight <= self.limit:
            raise ValueError("request weight must be between one and the configured limit")
        while True:
            with self._lock:
                now = self._monotonic()
                self._expire(now)
                used = sum(event_weight for _, event_weight in self._events)
                if used + weight <= self.limit:
                    self._events.append((now, weight))
                    return
                wait = max(0.0, self.window_seconds - (now - self._events[0][0]))
            self._sleep(wait)

    def _expire(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= self.window_seconds:
            self._events.popleft()


class IBRequestBudget:
    """Module-aware budgets; it supplements IB's server pacing and preserves OHLCV priority."""

    def __init__(self, config: RequestBudgetConfig) -> None:
        self.historical = WeightedWindowBudget(config.historical_weighted_tokens_per_minute)
        self.live_slots = threading.BoundedSemaphore(config.live_snapshot_concurrency)
        self.scanner_slots = threading.BoundedSemaphore(config.scanner_concurrency)
        self.flex_send = WeightedWindowBudget(config.flex_send_per_minute)
        self._last_flex_send = 0.0
        self._flex_lock = threading.Lock()

    def acquire_historical(self, what_to_show: str) -> None:
        self.historical.acquire(HISTORICAL_WEIGHTS.get(what_to_show, 1))

    def acquire_flex_send(self) -> None:
        self.flex_send.acquire()
        with self._flex_lock:
            now = time.monotonic()
            wait = 1.0 - (now - self._last_flex_send)
            if self._last_flex_send and wait > 0:
                time.sleep(wait)
            self._last_flex_send = time.monotonic()
