from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class RestartDecision:
    role: str
    restart_count: int
    window_failures: int
    backoff_seconds: float
    crash_loop: bool
    last_exit_code: int | None
    last_startup_stage: str
    last_reason_code: str
    first_failure_monotonic: float
    last_failure_monotonic: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RestartBudget:
    role: str
    budget: int
    window_seconds: float
    initial_backoff_seconds: float
    max_backoff_seconds: float
    restart_count: int = 0
    failures: list[float] = field(default_factory=list)
    last_exit_code: int | None = None
    last_startup_stage: str = "not_started"
    last_reason_code: str = "NONE"
    crash_loop: bool = False
    first_failure_at: str | None = None
    last_failure_at: str | None = None

    def record_failure(
        self,
        *,
        now: float,
        exit_code: int | None,
        startup_stage: str,
        reason_code: str,
    ) -> RestartDecision:
        self.failures = [value for value in self.failures if now - value <= self.window_seconds]
        self.failures.append(now)
        observed_at = datetime.now(UTC).isoformat()
        if self.first_failure_at is None or len(self.failures) == 1:
            self.first_failure_at = observed_at
        self.last_failure_at = observed_at
        self.restart_count += 1
        self.last_exit_code = exit_code
        self.last_startup_stage = startup_stage
        self.last_reason_code = reason_code
        self.crash_loop = len(self.failures) >= self.budget
        exponent = max(0, len(self.failures) - 1)
        backoff = min(
            self.max_backoff_seconds,
            self.initial_backoff_seconds * (2**exponent),
        )
        return RestartDecision(
            role=self.role,
            restart_count=self.restart_count,
            window_failures=len(self.failures),
            backoff_seconds=backoff,
            crash_loop=self.crash_loop,
            last_exit_code=exit_code,
            last_startup_stage=startup_stage,
            last_reason_code=reason_code,
            first_failure_monotonic=self.failures[0],
            last_failure_monotonic=self.failures[-1],
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "role": self.role,
            "restart_count": self.restart_count,
            "window_failures": len(self.failures),
            "restart_window_seconds": self.window_seconds,
            "last_exit_code": self.last_exit_code,
            "last_startup_stage": self.last_startup_stage,
            "last_reason_code": self.last_reason_code,
            "first_failure_monotonic": self.failures[0] if self.failures else None,
            "last_failure_monotonic": self.failures[-1] if self.failures else None,
            "first_failure_at": self.first_failure_at,
            "last_failure_at": self.last_failure_at,
            "state": "CRASH_LOOP" if self.crash_loop else "RUNNING",
        }
