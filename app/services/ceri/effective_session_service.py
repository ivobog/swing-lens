from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services.ceri.constants import CERI_DAILY_CUTOFF_TIMEZONE
from app.services.ceri.enums import DateConfidence
from app.services.us_market_calendar import is_us_trading_day, us_market_session

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
        schedule = us_market_session(session_day)
        if schedule is None:
            session_day = self.next_trading_session(session_day)
        elif local > schedule.close_at:
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
        return is_us_trading_day(day)

    def _localize(self, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=self.timezone)
        return timestamp.astimezone(self.timezone)
