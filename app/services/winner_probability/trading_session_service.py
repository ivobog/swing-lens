from __future__ import annotations

from datetime import date, datetime

from app.services.us_market_calendar import (
    latest_completed_us_trading_day,
    next_us_trading_day,
    nth_us_trading_day_from_entry,
)


class TradingSessionError(ValueError):
    pass


def next_regular_session(day: date) -> date:
    return next_us_trading_day(day)


def horizon_due_session(entry_session: date, horizon_sessions: int) -> date:
    return nth_us_trading_day_from_entry(entry_session, horizon_sessions)


def latest_completed_session(now: datetime | None = None) -> date:
    return latest_completed_us_trading_day(now)


def is_horizon_complete(due_session: date | None, now: datetime | None = None) -> bool:
    if due_session is None:
        return False
    return due_session <= latest_completed_session(now)


def require_completed_horizon(due_session: date | None, now: datetime | None = None) -> None:
    if not is_horizon_complete(due_session, now):
        raise TradingSessionError("horizon is not complete")
