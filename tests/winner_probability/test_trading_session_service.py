from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.services.winner_probability.trading_session_service import (
    TradingSessionError,
    horizon_due_session,
    is_horizon_complete,
    next_regular_session,
    require_completed_horizon,
)


def test_weekend_entry_resolves_to_next_regular_session() -> None:
    assert next_regular_session(date(2026, 7, 31)) == date(2026, 8, 3)


def test_entry_day_inclusive_horizon_due_session() -> None:
    assert horizon_due_session(date(2026, 8, 3), 5) == date(2026, 8, 7)


def test_completed_horizon_uses_latest_completed_market_session() -> None:
    now = datetime(2026, 8, 10, 21, 0, tzinfo=UTC)

    assert is_horizon_complete(date(2026, 8, 7), now)


def test_incomplete_horizon_raises() -> None:
    with pytest.raises(TradingSessionError):
        require_completed_horizon(date(2026, 8, 11), datetime(2026, 8, 10, 21, 0, tzinfo=UTC))
