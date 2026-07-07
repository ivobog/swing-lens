from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_NY_TZ = ZoneInfo("America/New_York")
_DAILY_BAR_READY_TIME = time(16, 15)


def latest_completed_us_trading_day(now: datetime | None = None) -> date:
    """Return the latest US equity trading day with a complete daily bar."""
    ny_now = _ny_datetime(now)
    candidate = ny_now.date()
    if not _is_us_trading_day(candidate) or ny_now.time() < _DAILY_BAR_READY_TIME:
        candidate = _previous_us_trading_day(candidate)
    return candidate


def is_latest_daily_bar_current(
    latest: date | None,
    *,
    now: datetime | None = None,
) -> bool:
    if latest is None:
        return False
    return latest >= latest_completed_us_trading_day(now)


def _ny_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(_NY_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=_NY_TZ)
    return value.astimezone(_NY_TZ)


def _previous_us_trading_day(day: date) -> date:
    candidate = day - timedelta(days=1)
    while not _is_us_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def _is_us_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in _nyse_holidays(day.year)


def _nyse_holidays(year: int) -> set[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Presidents' Day
        _easter_date(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))
    return holidays


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    candidate = date(year, month, 1)
    while candidate.weekday() != weekday:
        candidate += timedelta(days=1)
    return candidate + timedelta(days=7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    candidate = date(year, month + 1, 1) - timedelta(days=1)
    while candidate.weekday() != weekday:
        candidate -= timedelta(days=1)
    return candidate


def _easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    correction = (a + 11 * h + 22 * weekday_offset) // 451
    month = (h + weekday_offset - 7 * correction + 114) // 31
    day = ((h + weekday_offset - 7 * correction + 114) % 31) + 1
    return date(year, month, day)
