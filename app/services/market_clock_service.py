from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.services.us_market_calendar import (
    is_us_trading_day,
    latest_completed_us_trading_day,
    next_us_trading_day,
    previous_us_trading_day,
    us_market_session,
    us_trading_sessions_between,
)

EXCHANGE_TIMEZONE = "America/New_York"
CALENDAR_VERSION = "swinglens-us-equities-v1"
BAR_READINESS_VERSION = "daily-close-plus-15m-v1"


class SessionTimestampPolicy(StrEnum):
    LATEST_COMPLETED_DAILY_SESSION = "LATEST_COMPLETED_DAILY_SESSION"
    EVENT_EFFECTIVE_SESSION = "EVENT_EFFECTIVE_SESSION"
    NEXT_MARKET_OPEN_AFTER_EVENT = "NEXT_MARKET_OPEN_AFTER_EVENT"
    COOLDOWN_SESSION_OF_TIMESTAMP = "COOLDOWN_SESSION_OF_TIMESTAMP"
    TRADING_WEEK_CONFIRMATION = "TRADING_WEEK_CONFIRMATION"


@dataclass(frozen=True)
class MarketCalculationCutoff:
    cutoff_at: datetime
    exchange_timezone: str
    latest_completed_session: date
    daily_bar_ready_at: datetime | None
    calendar_version: str
    bar_readiness_version: str
    cutoff_reason: str
    context_id: int | None = None

    def __post_init__(self) -> None:
        if self.cutoff_at.tzinfo is None or self.cutoff_at.utcoffset() is None:
            raise ValueError("cutoff_at must be timezone-aware")
        object.__setattr__(self, "cutoff_at", self.cutoff_at.astimezone(UTC))
        if self.daily_bar_ready_at is not None:
            if (
                self.daily_bar_ready_at.tzinfo is None
                or self.daily_bar_ready_at.utcoffset() is None
            ):
                raise ValueError("daily_bar_ready_at must be timezone-aware")
            object.__setattr__(
                self,
                "daily_bar_ready_at",
                self.daily_bar_ready_at.astimezone(UTC),
            )
        if self.exchange_timezone != EXCHANGE_TIMEZONE:
            raise ValueError(f"unsupported exchange timezone: {self.exchange_timezone}")
        if not is_us_trading_day(self.latest_completed_session):
            raise ValueError("latest_completed_session must be a valid US exchange session")

    def with_context_id(self, context_id: int) -> MarketCalculationCutoff:
        return MarketCalculationCutoff(
            cutoff_at=self.cutoff_at,
            exchange_timezone=self.exchange_timezone,
            latest_completed_session=self.latest_completed_session,
            daily_bar_ready_at=self.daily_bar_ready_at,
            calendar_version=self.calendar_version,
            bar_readiness_version=self.bar_readiness_version,
            cutoff_reason=self.cutoff_reason,
            context_id=context_id,
        )


class MarketClockService:
    """Authoritative orchestration layer over SwingLens' US market calendar."""

    def cutoff_for(self, cutoff_at: datetime, *, reason: str) -> MarketCalculationCutoff:
        if cutoff_at.tzinfo is None or cutoff_at.utcoffset() is None:
            raise ValueError("cutoff_at must be timezone-aware")
        completed = latest_completed_us_trading_day(cutoff_at)
        schedule = us_market_session(completed)
        if schedule is None:  # defensive: latest_completed_* must always return a session
            raise RuntimeError("shared calendar returned a non-session cutoff")
        return MarketCalculationCutoff(
            cutoff_at=cutoff_at,
            exchange_timezone=EXCHANGE_TIMEZONE,
            latest_completed_session=completed,
            daily_bar_ready_at=schedule.close_at + timedelta(minutes=15),
            calendar_version=CALENDAR_VERSION,
            bar_readiness_version=BAR_READINESS_VERSION,
            cutoff_reason=reason,
        )

    def trading_session_distance(self, older: date, newer: date) -> int:
        if older == newer:
            return 0
        if older < newer:
            return us_trading_sessions_between(older, newer)
        return -us_trading_sessions_between(newer, older)

    def canonical_session_for_timestamp(
        self,
        timestamp: datetime,
        *,
        policy: SessionTimestampPolicy,
    ) -> date:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        if policy in {
            SessionTimestampPolicy.LATEST_COMPLETED_DAILY_SESSION,
            SessionTimestampPolicy.TRADING_WEEK_CONFIRMATION,
        }:
            return latest_completed_us_trading_day(timestamp)

        local = timestamp.astimezone(ZoneInfo(EXCHANGE_TIMEZONE))
        schedule = us_market_session(local.date())
        if policy is SessionTimestampPolicy.NEXT_MARKET_OPEN_AFTER_EVENT:
            if schedule is not None and local < schedule.open_at:
                return schedule.session
            return next_us_trading_day(local.date())

        # Event attribution and cooldown endpoints share the current CERI
        # effective-session contract: after-close/non-session observations roll
        # forward, while regular-session observations retain that session.
        if schedule is None:
            return _session_on_or_after(local.date())
        if local > schedule.close_at:
            return next_us_trading_day(local.date())
        return schedule.session

    def last_us_trading_session_of_week(self, value: date) -> date:
        friday = value + timedelta(days=4 - value.weekday())
        candidate = friday
        while not is_us_trading_day(candidate):
            candidate = previous_us_trading_day(candidate)
        return candidate


def _session_on_or_after(day: date) -> date:
    candidate = day
    while not is_us_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate
