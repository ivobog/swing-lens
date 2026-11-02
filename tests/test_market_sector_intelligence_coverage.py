from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pandas as pd
import pytest

from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_coverage import MarketSectorIntelligenceCoverageService

NOW = datetime(2026, 10, 21, 20, 30, tzinfo=UTC)
CUTOFF = MarketClockService().cutoff_for(NOW, reason="TEST")


def frame(sessions=(), closes=None, basis="TRADES"):
    result = pd.DataFrame({"date": list(sessions), "close": closes or [100] * len(sessions)})
    result.attrs["price_basis"] = basis
    return result


def test_empty_cache_has_twelve_missing_rows_and_one_cutoff():
    clock = Mock(wraps=MarketClockService())
    clock.cutoff_for.return_value = CUTOFF
    loader = Mock(return_value=(frame(), None))
    service = MarketSectorIntelligenceCoverageService(clock=clock, frame_loader=loader)
    result = service.build(object())

    assert len(result["symbols"]) == 12
    assert {row["status"] for row in result["symbols"]} == {"MISSING"}
    assert result["symbols"][-1]["kind"] == "benchmark"
    assert result["symbols"][-1]["ticker"] == "SPY"
    assert result["as_of"] == "2026-10-21T20:30:00Z"
    assert clock.cutoff_for.call_count == 1
    assert clock.cutoff_for.call_args.args[0].tzinfo == UTC
    assert loader.call_count == 12
    assert len({call.args[1] for call in loader.call_args_list}) == 12
    for call in loader.call_args_list:
        assert call.kwargs == {
            "timeframe": "1 day",
            "max_session": CUTOFF.latest_completed_session,
            "as_of": CUTOFF.cutoff_at,
        }
    for row in result["symbols"]:
        assert row["bar_count"] == 0
        assert (
            row["first_session"]
            is row["last_session"]
            is row["session_lag"]
            is row["price_basis"]
            is None
        )


def test_all_current_uses_actual_selected_basis():
    loader = Mock(
        return_value=(frame([date(2026, 10, 20), date(2026, 10, 21)], basis="ADJUSTED_LAST"), None)
    )
    result = MarketSectorIntelligenceCoverageService(frame_loader=loader).build(
        object(), cutoff=CUTOFF
    )
    assert {row["status"] for row in result["symbols"]} == {"CURRENT"}
    assert all(
        row["bar_count"] == 2 and row["price_basis"] == "ADJUSTED_LAST" and row["session_lag"] == 0
        for row in result["symbols"]
    )


def test_mixed_coverage_and_isolated_errors_are_truthful(caplog):
    def load(_db, ticker, **_kwargs):
        if ticker == "XLK":
            return frame(), None
        if ticker == "XLF":
            return frame([date(2026, 10, 19), date(2026, 10, 16)]), None
        if ticker == "XLE":
            raise RuntimeError("secret connection string")
        if ticker == "XLU":
            return frame([date(2026, 10, 21)], closes=[0]), None
        return frame([date(2026, 10, 21)]), None

    result = MarketSectorIntelligenceCoverageService(frame_loader=load).build(
        object(), cutoff=CUTOFF
    )
    rows = {row["ticker"]: row for row in result["symbols"]}
    assert rows["XLK"]["status"] == "MISSING"
    assert rows["XLF"]["status"] == "STALE"
    assert rows["XLF"]["first_session"] == "2026-10-16"
    assert rows["XLF"]["last_session"] == "2026-10-19"
    assert rows["XLF"]["bar_count"] == 2
    assert rows["XLF"]["session_lag"] == 2
    assert rows["XLE"]["reason"] == "cached_price_evaluation_failed"
    assert rows["XLU"]["reason"] == "invalid_latest_close"
    assert rows["SPY"]["status"] == "CURRENT"
    assert "secret" not in str(result) + caplog.text


@pytest.mark.parametrize("close", [None, 0, -1, float("nan"), float("inf"), "bad", True])
def test_latest_close_must_be_finite_and_positive(close):
    loader = Mock(return_value=(frame([date(2026, 10, 21)], [close]), None))
    result = MarketSectorIntelligenceCoverageService(frame_loader=loader).build(
        object(), cutoff=CUTOFF
    )
    assert {row["status"] for row in result["symbols"]} == {"UNAVAILABLE"}
    assert {row["reason"] for row in result["symbols"]} == {"invalid_latest_close"}


@pytest.mark.parametrize("session", [date(2026, 10, 22), "bad", None, 123, date(2026, 10, 18)])
def test_invalid_or_future_sessions_never_report_current(session):
    loader = Mock(return_value=(frame([session]), None))
    result = MarketSectorIntelligenceCoverageService(frame_loader=loader).build(
        object(), cutoff=CUTOFF
    )
    assert {row["status"] for row in result["symbols"]} == {"UNAVAILABLE"}


def test_post_cutoff_receipt_is_a_failure():
    price = frame([date(2026, 10, 21)])
    price["first_seen_at"] = [NOW + timedelta(seconds=1)]
    result = MarketSectorIntelligenceCoverageService(
        frame_loader=lambda *_a, **_k: (price, None)
    ).build(object(), cutoff=CUTOFF)
    assert {row["reason"] for row in result["symbols"]} == {"post_cutoff_observation_detected"}


def test_disabled_has_no_price_reads_and_retains_universe():
    config = replace(load_market_sector_intelligence_config(), enabled=False)
    loader = Mock(side_effect=AssertionError("disabled must not read prices"))
    result = MarketSectorIntelligenceCoverageService(frame_loader=loader).build(
        object(), config=config, cutoff=CUTOFF
    )
    assert len(result["symbols"]) == 12
    assert {row["status"] for row in result["symbols"]} == {"DISABLED"}
    assert loader.call_count == 0
    assert len(config.crosswalk) > 11


@pytest.mark.parametrize(
    ("instant", "completed"),
    [
        ("2026-10-25T12:00:00+00:00", "2026-10-23"),  # Sunday
        ("2026-07-03T20:30:00+00:00", "2026-07-02"),  # observed holiday
        ("2026-03-09T20:00:00+00:00", "2026-03-06"),  # US DST, before ready
        ("2026-03-09T20:30:00+00:00", "2026-03-09"),
        ("2026-11-02T21:30:00+00:00", "2026-11-02"),  # after DST ends
        ("2026-11-27T18:30:00+00:00", "2026-11-27"),  # early close
    ],
)
def test_market_calendar_boundaries_are_identical_for_every_symbol(instant, completed):
    cutoff = MarketClockService().cutoff_for(datetime.fromisoformat(instant), reason="TEST")
    loader = Mock(return_value=(frame(), None))
    result = MarketSectorIntelligenceCoverageService(frame_loader=loader).build(
        object(), cutoff=cutoff
    )
    assert result["latest_completed_session"] == completed
    assert len(loader.call_args_list) == 12
    assert all(
        call.kwargs["as_of"] is cutoff.cutoff_at
        and call.kwargs["max_session"].isoformat() == completed
        for call in loader.call_args_list
    )


def test_session_lag_skips_holiday_and_weekend():
    cutoff = MarketClockService().cutoff_for(
        datetime(2026, 7, 6, 20, 30, tzinfo=UTC), reason="TEST"
    )
    result = MarketSectorIntelligenceCoverageService(
        frame_loader=lambda *_a, **_k: (frame([date(2026, 7, 2)]), None)
    ).build(object(), cutoff=cutoff)
    assert {row["session_lag"] for row in result["symbols"]} == {1}
