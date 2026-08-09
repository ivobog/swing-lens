from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    multiplier: float = 2.0

    def delay(self, failed_attempt: int) -> float:
        exponent = max(0, failed_attempt - 1)
        return min(
            self.max_backoff_seconds,
            self.initial_backoff_seconds * (self.multiplier**exponent),
        )


@dataclass(frozen=True)
class RetryEvent:
    failed_attempt: int
    next_attempt: int
    delay_seconds: float
    error: Exception


class RetryExhausted(RuntimeError):
    def __init__(self, operation: str, attempts: int, error: Exception) -> None:
        super().__init__(
            f"{operation} exhausted its retry budget after {attempts} attempts: {error}"
        )
        self.operation = operation
        self.attempts = attempts
        self.error = error
        self.error_code = error_code(error)


def retry_call[T](
    operation: Callable[[], T],
    *,
    operation_name: str,
    policy: RetryPolicy,
    retryable: Callable[[Exception], bool] = lambda exc: is_retryable_ib_error(exc),
    sleep: Callable[[float], None] = time.sleep,
    guard: Callable[[], None] | None = None,
    reconnect: Callable[[], None] | None = None,
    on_retry: Callable[[RetryEvent], None] | None = None,
) -> T:
    if policy.max_attempts < 1:
        raise ValueError("retry max_attempts must be positive")
    for attempt in range(1, policy.max_attempts + 1):
        if guard:
            guard()
        try:
            return operation()
        except Exception as exc:
            if not retryable(exc):
                raise
            if attempt >= policy.max_attempts:
                raise RetryExhausted(operation_name, attempt, exc) from exc
            delay = policy.delay(attempt)
            event = RetryEvent(attempt, attempt + 1, delay, exc)
            if on_retry:
                on_retry(event)
            if reconnect:
                reconnect()
            cancellable_sleep(delay, sleep=sleep, guard=guard)
    raise AssertionError("retry loop exited unexpectedly")


def cancellable_sleep(
    seconds: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    guard: Callable[[], None] | None = None,
    interval_seconds: float = 0.25,
) -> None:
    remaining = max(0.0, seconds)
    while remaining > 0:
        if guard:
            guard()
        step = min(interval_seconds, remaining)
        sleep(step)
        remaining -= step
    if guard:
        guard()


def error_code(error: Any) -> int | None:
    for name in ("errorCode", "error_code", "code"):
        value = getattr(error, name, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    cause = getattr(error, "error", None) or getattr(error, "__cause__", None)
    return error_code(cause) if cause is not None and cause is not error else None


def is_retryable_ib_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    code = error_code(error)
    message = str(error).lower()
    if code in {1100, 1101, 1300, 420}:
        return True
    if code == 162:
        return any(term in message for term in ("pacing", "timeout", "temporar", "service"))
    return any(
        term in message
        for term in (
            "timed out",
            "timeout",
            "not connected",
            "disconnected",
            "connection reset",
            "connection lost",
            "pacing violation",
            "temporarily unavailable",
        )
    )
