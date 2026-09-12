from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.services.us_market_calendar import is_us_trading_day
from single_run_certification.fixtures import certification_as_of_session


@pytest.mark.parametrize(
    ("reference_timestamp", "expected_session"),
    (
        pytest.param(
            datetime(2026, 9, 14, 21, 0, tzinfo=UTC),
            date(2026, 9, 14),
            id="weekday-after-daily-bar-ready",
        ),
        pytest.param(
            datetime(2026, 9, 12, 16, 0, tzinfo=UTC),
            date(2026, 9, 11),
            id="saturday",
        ),
        pytest.param(
            datetime(2026, 9, 13, 16, 0, tzinfo=UTC),
            date(2026, 9, 11),
            id="sunday",
        ),
        pytest.param(
            datetime(2026, 9, 7, 21, 0, tzinfo=UTC),
            date(2026, 9, 4),
            id="labor-day-market-holiday",
        ),
    ),
)
def test_certification_session_uses_canonical_exchange_calendar(
    reference_timestamp: datetime,
    expected_session: date,
) -> None:
    session = certification_as_of_session(reference_timestamp)

    assert session == expected_session
    assert is_us_trading_day(session)
