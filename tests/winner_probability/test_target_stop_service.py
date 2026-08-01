from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.tables import FirstEvent
from app.services.winner_probability.target_stop_service import TargetStopService


@pytest.mark.parametrize(
    ("high", "low", "first_event", "primary_winner", "same_bar_conflict"),
    [
        (
            103,
            99,
            FirstEvent.TARGET_FIRST,
            True,
            False,
        ),
        (
            101,
            97.5,
            FirstEvent.STOP_FIRST,
            False,
            False,
        ),
        (
            101,
            99,
            FirstEvent.NEITHER,
            False,
            False,
        ),
        (
            103,
            97.5,
            FirstEvent.SAME_BAR_CONFLICT,
            False,
            True,
        ),
    ],
)
def test_target_stop_first_event_cases(
    high: float,
    low: float,
    first_event: str,
    primary_winner: bool,
    same_bar_conflict: bool,
) -> None:
    bars = [_bar(date(2026, 8, 3), high=high, low=low)]

    result = TargetStopService().evaluate(
        bars=bars,
        entry_price=Decimal("100"),
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
    )

    assert result.first_event == first_event
    assert result.primary_winner is primary_winner
    assert result.same_bar_conflict is same_bar_conflict


def _bar(bar_date: date, *, high: float, low: float):
    return SimpleNamespace(bar_date=bar_date, high=Decimal(str(high)), low=Decimal(str(low)))
