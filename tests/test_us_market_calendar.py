from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.us_market_calendar import (
    is_latest_daily_bar_current,
    latest_completed_us_trading_day,
)

NY = ZoneInfo("America/New_York")


def test_latest_completed_day_after_us_close_requires_same_session() -> None:
    now = datetime(2026, 7, 7, 17, 45, tzinfo=NY)

    assert latest_completed_us_trading_day(now) == date(2026, 7, 7)
    assert is_latest_daily_bar_current(date(2026, 7, 6), now=now) is False
    assert is_latest_daily_bar_current(date(2026, 7, 7), now=now) is True


def test_latest_completed_day_before_us_close_uses_prior_session() -> None:
    now = datetime(2026, 7, 7, 10, 0, tzinfo=NY)

    assert latest_completed_us_trading_day(now) == date(2026, 7, 6)
    assert is_latest_daily_bar_current(date(2026, 7, 6), now=now) is True


def test_latest_completed_day_skips_weekends_and_observed_holidays() -> None:
    sunday_after_observed_independence_day = datetime(2026, 7, 5, 12, 0, tzinfo=NY)
    monday_before_close = datetime(2026, 7, 6, 10, 0, tzinfo=NY)

    assert latest_completed_us_trading_day(sunday_after_observed_independence_day) == date(
        2026,
        7,
        2,
    )
    assert latest_completed_us_trading_day(monday_before_close) == date(2026, 7, 2)
