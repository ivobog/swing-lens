import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class IbRateLimitConfig:
    requests_per_minute: int
    min_seconds_between_requests: float
    backoff_seconds: float
    max_retries: int
    conservative_mode: bool


class IbHistoricalRateLimiter:
    def __init__(
        self,
        config: IbRateLimitConfig,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._monotonic = monotonic
        self._sleep = sleep
        self._request_times: deque[float] = deque()
        self._last_request_at: float | None = None

    def wait_before_request(
        self,
        should_cancel: Callable[[], bool] | None = None,
    ) -> bool:
        now = self._monotonic()
        if not self._sleep_for_minimum_gap(now, should_cancel):
            return False
        now = self._monotonic()
        if not self._sleep_for_minute_window(now, should_cancel):
            return False
        now = self._monotonic()
        self._request_times.append(now)
        self._last_request_at = now
        return True

    def backoff_after_error(
        self,
        error: Exception,
        attempt: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> bool:
        if attempt <= 0:
            attempt = 1
        multiplier = min(attempt, max(self.config.max_retries, 1))
        return self._sleep_interruptibly(
            self.config.backoff_seconds * multiplier,
            should_cancel,
        )

    def _sleep_for_minimum_gap(
        self,
        now: float,
        should_cancel: Callable[[], bool] | None,
    ) -> bool:
        if self._last_request_at is None:
            return True
        elapsed = now - self._last_request_at
        remaining = self.config.min_seconds_between_requests - elapsed
        if remaining > 0:
            return self._sleep_interruptibly(remaining, should_cancel)
        return True

    def _sleep_for_minute_window(
        self,
        now: float,
        should_cancel: Callable[[], bool] | None,
    ) -> bool:
        if self.config.requests_per_minute <= 0:
            return True
        while self._request_times and now - self._request_times[0] >= 60:
            self._request_times.popleft()
        if len(self._request_times) < self.config.requests_per_minute:
            return True
        wait_seconds = 60 - (now - self._request_times[0])
        if wait_seconds > 0:
            return self._sleep_interruptibly(wait_seconds, should_cancel)
        return True

    def _sleep_interruptibly(
        self,
        seconds: float,
        should_cancel: Callable[[], bool] | None,
    ) -> bool:
        if seconds <= 0:
            return not (should_cancel and should_cancel())
        if should_cancel is None:
            self._sleep(seconds)
            return True
        deadline = self._monotonic() + seconds
        while True:
            if should_cancel():
                return False
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return True
            self._sleep(min(0.25, remaining))


def rate_limit_config_from_settings(settings) -> IbRateLimitConfig:
    return IbRateLimitConfig(
        requests_per_minute=settings.ib_requests_per_minute,
        min_seconds_between_requests=settings.ib_min_seconds_between_requests,
        backoff_seconds=settings.ib_backoff_seconds,
        max_retries=settings.ib_max_retries,
        conservative_mode=settings.ib_force_conservative_mode,
    )
