from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services.ceri.constants import CERI_DAILY_CUTOFF_TIMEZONE
from app.services.ceri.enums import DateConfidence

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


@dataclass(frozen=True)
class EffectiveSessionResult:
    effective_at: datetime | None
    effective_session: date
    date_confidence: DateConfidence
    warnings: tuple[str, ...] = ()


class CeriEffectiveSessionService:
    def __init__(self, timezone_name: str = CERI_DAILY_CUTOFF_TIMEZONE) -> None:
        self.timezone = ZoneInfo(timezone_name)

    def resolve(
        self,
        *,
        timestamp: datetime | None,
        source_date: date | None = None,
    ) -> EffectiveSessionResult:
        if timestamp is None:
            if source_date is None:
                raise ValueError("timestamp or source_date is required for effective session")
            return EffectiveSessionResult(
                effective_at=None,
                effective_session=self.next_trading_session(source_date),
                date_confidence=DateConfidence.EXACT_DATE,
                warnings=("missing_timestamp",),
            )

        local = self._localize(timestamp)
        session_day = local.date()
        if not self.is_trading_session(session_day):
            session_day = self.next_trading_session(session_day)
        elif local.timetz().replace(tzinfo=None) > MARKET_CLOSE:
            session_day = self.next_trading_session(session_day + timedelta(days=1))

        return EffectiveSessionResult(
            effective_at=timestamp,
            effective_session=session_day,
            date_confidence=DateConfidence.EXACT_TIMESTAMP,
            warnings=(),
        )

    def next_trading_session(self, day: date) -> date:
        candidate = day
        while not self.is_trading_session(candidate):
            candidate += timedelta(days=1)
        return candidate

    def is_trading_session(self, day: date) -> bool:
        return day.weekday() < 5 and day not in _us_market_holidays(day.year)

    def _localize(self, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=self.timezone)
        return timestamp.astimezone(self.timezone)


def _us_market_holidays(year: int) -> set[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _good_friday(year),
        _last_weekday(year, 5, 0),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    holidays.update(_cross_year_observed(year))
    return holidays


def _cross_year_observed(year: int) -> set[date]:
    observed = set()
    for raw in (date(year - 1, 1, 1), date(year - 1, 12, 25), date(year + 1, 1, 1)):
        value = _observed(raw)
        if value.year == year:
            observed.add(value)
    return observed


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, ordinal: int) -> date:
    candidate = date(year, month, 1)
    while candidate.weekday() != weekday:
        candidate += timedelta(days=1)
    return candidate + timedelta(days=7 * (ordinal - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        candidate = date(year, 12, 31)
    else:
        candidate = date(year, month + 1, 1) - timedelta(days=1)
    while candidate.weekday() != weekday:
        candidate -= timedelta(days=1)
    return candidate


def _good_friday(year: int) -> date:
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
    offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * offset) // 451
    month = (h + offset - 7 * m + 114) // 31
    day = ((h + offset - 7 * m + 114) % 31) + 1
    return date(year, month, day) - timedelta(days=2)
