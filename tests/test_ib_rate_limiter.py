from app.services.ib_rate_limiter import IbHistoricalRateLimiter, IbRateLimitConfig


def test_rate_limiter_enforces_minimum_gap() -> None:
    clock = FakeClock()
    limiter = IbHistoricalRateLimiter(
        IbRateLimitConfig(
            requests_per_minute=20,
            min_seconds_between_requests=3,
            backoff_seconds=90,
            max_retries=3,
            conservative_mode=True,
        ),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    limiter.wait_before_request()
    limiter.wait_before_request()

    assert clock.sleeps == [3]


def test_rate_limiter_applies_retry_backoff() -> None:
    clock = FakeClock()
    limiter = IbHistoricalRateLimiter(
        IbRateLimitConfig(
            requests_per_minute=20,
            min_seconds_between_requests=3,
            backoff_seconds=10,
            max_retries=3,
            conservative_mode=True,
        ),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    limiter.backoff_after_error(RuntimeError("pacing"), attempt=2)

    assert clock.sleeps == [20]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
