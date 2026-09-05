from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.ceri.enums import DateConfidence

NY = ZoneInfo("America/New_York")


def test_pre_market_regular_and_after_hours_effective_sessions() -> None:
    service = CeriEffectiveSessionService()

    pre_market = service.resolve(timestamp=datetime(2026, 8, 3, 8, 0, tzinfo=NY))
    regular = service.resolve(timestamp=datetime(2026, 8, 3, 10, 0, tzinfo=NY))
    after_hours = service.resolve(timestamp=datetime(2026, 8, 3, 16, 30, tzinfo=NY))

    assert pre_market.effective_session == date(2026, 8, 3)
    assert regular.effective_session == date(2026, 8, 3)
    assert after_hours.effective_session == date(2026, 8, 4)


def test_weekend_holiday_and_missing_time_effective_sessions() -> None:
    service = CeriEffectiveSessionService()

    weekend = service.resolve(timestamp=datetime(2026, 8, 1, 12, 0, tzinfo=NY))
    holiday = service.resolve(timestamp=datetime(2026, 7, 3, 12, 0, tzinfo=NY))
    missing = service.resolve(timestamp=None, source_date=date(2026, 8, 1))

    assert weekend.effective_session == date(2026, 8, 3)
    assert holiday.effective_session == date(2026, 7, 6)
    assert missing.effective_session == date(2026, 8, 3)
    assert missing.date_confidence is DateConfidence.EXACT_DATE
    assert "missing_timestamp" in missing.warnings


def test_early_close_uses_shared_exchange_schedule() -> None:
    service = CeriEffectiveSessionService()

    result = service.resolve(timestamp=datetime(2026, 11, 27, 15, 0, tzinfo=NY))

    assert result.effective_session == date(2026, 11, 30)
