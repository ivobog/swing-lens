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

    def wait_before_request(self) -> None:
        now = self._monotonic()
        self._sleep_for_minimum_gap(now)
        now = self._monotonic()
        self._sleep_for_minute_window(now)
        now = self._monotonic()
        self._request_times.append(now)
        self._last_request_at = now

    def backoff_after_error(self, error: Exception, attempt: int) -> None:
        if attempt <= 0:
            attempt = 1
        multiplier = min(attempt, max(self.config.max_retries, 1))
        self._sleep(self.config.backoff_seconds * multiplier)

    def _sleep_for_minimum_gap(self, now: float) -> None:
        if self._last_request_at is None:
            return
        elapsed = now - self._last_request_at
        remaining = self.config.min_seconds_between_requests - elapsed
        if remaining > 0:
            self._sleep(remaining)

    def _sleep_for_minute_window(self, now: float) -> None:
        if self.config.requests_per_minute <= 0:
            return
        while self._request_times and now - self._request_times[0] >= 60:
            self._request_times.popleft()
        if len(self._request_times) < self.config.requests_per_minute:
            return
        wait_seconds = 60 - (now - self._request_times[0])
        if wait_seconds > 0:
            self._sleep(wait_seconds)


def rate_limit_config_from_settings(settings) -> IbRateLimitConfig:
    return IbRateLimitConfig(
        requests_per_minute=settings.ib_requests_per_minute,
        min_seconds_between_requests=settings.ib_min_seconds_between_requests,
        backoff_seconds=settings.ib_backoff_seconds,
        max_retries=settings.ib_max_retries,
        conservative_mode=settings.ib_force_conservative_mode,
    )
