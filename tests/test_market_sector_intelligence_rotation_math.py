from datetime import UTC, date, datetime

import pytest

from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_rotation_math import (
    evaluate_pair,
    quadrant,
    rank_sectors,
    required_sessions,
)


def test_hand_calculated_ratio_returns_and_units():
    etf = {0: 120.0, 5: 115.0, 21: 110.0, 42: 105.0, 63: 100.0}
    spy = {0: 110.0, 5: 108.0, 21: 105.0, 42: 102.0, 63: 100.0}
    result = evaluate_pair(etf, spy)
    assert result.rs_5_pct == pytest.approx(2.45059288537548)
    assert result.rs_21_pct == pytest.approx(4.13223140495868)
    assert result.rs_63_pct == pytest.approx(9.09090909090909)
    assert result.rs_63_pct != pytest.approx(10.0)  # not ETF return minus SPY return
    assert result.rs_prior_21_pct == pytest.approx(1.76870748299319)
    assert result.relative_momentum_pp == pytest.approx(2.36352392196549)
    assert result.etf_return_63_pct == pytest.approx(20.0)
    assert result.spy_return_63_pct == pytest.approx(10.0)


@pytest.mark.parametrize(
    ("level", "momentum", "expected"),
    [
        (1, 1, "Leading"),
        (-1, 1, "Improving"),
        (1, -1, "Weakening"),
        (-1, -1, "Lagging"),
        (0, 0, "Leading"),
        (-1e-12, 0, "Improving"),
        (0, -1e-12, "Weakening"),
    ],
)
def test_quadrants_use_unrounded_signs(level, momentum, expected):
    assert quadrant(level, momentum) == expected


def test_every_rank_tie_break_and_partial_ordinal():
    def row(ticker, rs63, rs21, rs5, quality="ELIGIBLE"):
        return dict(
            etf_symbol=ticker,
            rs_63_pct=rs63,
            rs_21_pct=rs21,
            rs_5_pct=rs5,
            quality_status=quality,
            rank=None,
        )

    rows = [
        row("XLK", 2, 3, 4),
        row("XLC", 2, 3, 4),
        row("XLE", 2, 3, 5),
        row("XLF", 2, 4, 0),
        row("XLI", 3, 0, 0),
        row("XLU", None, None, None, "INSUFFICIENT_DATA"),
    ]
    rank_sectors(rows)
    assert [row["etf_symbol"] for row in sorted(rows[:-1], key=lambda row: row["rank"])] == [
        "XLI",
        "XLF",
        "XLE",
        "XLC",
        "XLK",
    ]
    assert rows[-1]["rank"] is None


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True])
def test_invalid_anchors_cannot_make_metrics(bad):
    valid = dict.fromkeys((0, 5, 21, 42, 63), 100.0)
    with pytest.raises(ValueError):
        evaluate_pair({**valid, 42: bad}, valid)


def test_overflow_cannot_escape_to_json():
    with pytest.raises(ValueError):
        evaluate_pair(
            {0: 1e308, 5: 1e-308, 21: 1, 42: 1, 63: 1}, dict.fromkeys((0, 5, 21, 42, 63), 1.0)
        )


def test_exact_session_offsets_skip_holidays_and_weekends():
    anchors = required_sessions(date(2026, 7, 6))
    assert anchors[0] == date(2026, 7, 6)
    assert anchors[5] == date(2026, 6, 26)
    assert anchors[21] == date(2026, 6, 3)
    assert anchors[42] == date(2026, 5, 4)
    assert anchors[63] == date(2026, 4, 2)


@pytest.mark.parametrize("instant", ["2026-03-09T20:30:00+00:00", "2026-11-27T18:30:00+00:00"])
def test_calendar_cutoffs_and_anchor_origin_share_completed_session(instant):
    cutoff = MarketClockService().cutoff_for(
        datetime.fromisoformat(instant).astimezone(UTC), reason="TEST"
    )
    assert required_sessions(cutoff.latest_completed_session)[0] == cutoff.latest_completed_session
