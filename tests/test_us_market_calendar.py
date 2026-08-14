from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.us_market_calendar import (
    is_daily_bar_fresh,
    is_latest_daily_bar_current,
    latest_completed_us_trading_day,
    next_us_trading_day,
    nth_us_trading_day_from_entry,
    subtract_us_trading_sessions,
)

NY = ZoneInfo("America/New_York")


def test_run_104_zurich_cutoff_resolves_completed_session_and_next_open() -> None:
    cutoff = datetime(2026, 8, 14, 4, 34, 21, tzinfo=ZoneInfo("Europe/Zurich"))

    completed = latest_completed_us_trading_day(cutoff)

    assert completed == date(2026, 8, 13)
    assert next_us_trading_day(completed) == date(2026, 8, 14)


def test_calendar_conversion_is_deterministic_across_us_europe_dst_gap() -> None:
    before_bar_ready = datetime(2026, 3, 9, 21, 0, tzinfo=ZoneInfo("Europe/Zurich"))
    after_bar_ready = datetime(2026, 3, 9, 21, 30, tzinfo=ZoneInfo("Europe/Zurich"))

    assert latest_completed_us_trading_day(before_bar_ready) == date(2026, 3, 6)
    assert latest_completed_us_trading_day(after_bar_ready) == date(2026, 3, 9)


def test_next_session_skips_weekend_and_exchange_holiday() -> None:
    assert next_us_trading_day(date(2026, 7, 2)) == date(2026, 7, 6)
    assert next_us_trading_day(date(2026, 8, 14)) == date(2026, 8, 17)


def test_latest_completed_day_after_us_close_requires_same_session() -> None:
    now = datetime(2026, 7, 7, 17, 45, tzinfo=NY)

    assert latest_completed_us_trading_day(now) == date(2026, 7, 7)
    assert is_latest_daily_bar_current(date(2026, 7, 6), now=now) is False
    assert is_latest_daily_bar_current(date(2026, 7, 7), now=now) is True


def test_latest_completed_day_before_us_close_uses_prior_session() -> None:
    now = datetime(2026, 7, 7, 10, 0, tzinfo=NY)

    assert latest_completed_us_trading_day(now) == date(2026, 7, 6)
    assert is_latest_daily_bar_current(date(2026, 7, 6), now=now) is True


def test_daily_bar_fresh_honors_stale_after_days() -> None:
    now = datetime(2026, 7, 6, 10, 0, tzinfo=NY)

    assert is_daily_bar_fresh(date(2026, 6, 30), 3, now=now) is True
    assert is_daily_bar_fresh(date(2026, 6, 28), 3, now=now) is False
    assert is_daily_bar_fresh(date(2026, 7, 1), 0, now=now) is False
    assert is_daily_bar_fresh(date(2026, 7, 2), 0, now=now) is True


def test_latest_completed_day_skips_weekends_and_observed_holidays() -> None:
    sunday_after_observed_independence_day = datetime(2026, 7, 5, 12, 0, tzinfo=NY)
    monday_before_close = datetime(2026, 7, 6, 10, 0, tzinfo=NY)

    assert latest_completed_us_trading_day(sunday_after_observed_independence_day) == date(
        2026,
        7,
        2,
    )
    assert latest_completed_us_trading_day(monday_before_close) == date(2026, 7, 2)


def test_next_us_trading_day_skips_weekends() -> None:
    assert next_us_trading_day(date(2026, 7, 31)) == date(2026, 8, 3)


def test_subtract_us_trading_sessions_skips_weekends_and_holidays() -> None:
    assert subtract_us_trading_sessions(date(2026, 7, 6), 1) == date(2026, 7, 2)


def test_nth_us_trading_day_counts_entry_session_as_session_one() -> None:
    assert nth_us_trading_day_from_entry(date(2026, 8, 3), 1) == date(2026, 8, 3)
    assert nth_us_trading_day_from_entry(date(2026, 8, 3), 5) == date(2026, 8, 7)
