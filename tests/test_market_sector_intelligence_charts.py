import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pandas as pd
import pytest
from market_sector_technical_helpers import CUTOFF, frame, receipt, symbols

from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_chart_service import (
    MarketSectorChartService,
    chart_sessions,
    price_window,
    relative_return,
)
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config


def read_frames(overrides=None, calls=None, edit=None):
    def read(_db, symbol, **kwargs):
        if calls is not None:
            calls.append((symbol, kwargs))
        value = (overrides or {}).get(symbol, frame(symbol))
        if isinstance(value, Exception):
            raise value
        price = receipt(deepcopy(value), symbol, kwargs)
        if edit:
            edit(price, symbol)
        return price, None

    return read


@pytest.mark.parametrize("window", [5, 21, 63, 126])
def test_exact_prices_ratio_of_ratios_common_start_and_units(window):
    days = chart_sessions(CUTOFF.latest_completed_session)[-window - 1 :]
    etf = {day: 100 + 20 * i / window for i, day in enumerate(days)}
    spy = {day: 100 + 10 * i / window for i, day in enumerate(days)}
    result, reason = price_window(etf, days)
    assert reason is None and result["absolute_pct"] == pytest.approx(20)
    assert relative_return(etf, spy, days) == pytest.approx(100 * (1.2 / 1.1 - 1))
    assert relative_return(etf, spy, days) != pytest.approx(10)
    assert len(result["points"]) == window + 1
    assert result["points"][0] == {"session": days[0].isoformat(), "index_100": 100}
    assert result["points"][-1]["index_100"] == pytest.approx(120)
    del etf[days[1]]
    assert price_window(etf, days) == (None, "MISSING_SESSION")


@pytest.mark.parametrize(
    "stamp,target",
    [
        (datetime(2026, 7, 6, 13, tzinfo=UTC), date(2026, 7, 2)),
        (datetime(2026, 3, 9, 20, 10, tzinfo=UTC), date(2026, 3, 6)),
        (datetime(2026, 3, 9, 20, 30, tzinfo=UTC), date(2026, 3, 9)),
        (datetime(2026, 11, 27, 18, 20, tzinfo=UTC), date(2026, 11, 27)),
    ],
)
def test_calendar_holiday_weekend_dst_and_early_close(stamp, target):
    cutoff = MarketClockService().cutoff_for(stamp, reason="D4_TEST")
    assert cutoff.latest_completed_session == target
    assert len(chart_sessions(target)) == len(set(chart_sessions(target))) == 127


def test_twelve_sources_one_cutoff_bounded_points_and_independent_window_readiness():
    calls = []
    bad = frame("XLK").iloc[-126:].copy()  # 126 anchor absent; short windows remain real.
    result = MarketSectorChartService(frame_loader=read_frames({"XLK": bad}, calls)).build(
        object(), cutoff=CUTOFF
    )
    assert len(calls) == 12 and len(set(symbol for symbol, _ in calls)) == 12
    assert {symbol for symbol, _ in calls} == set(symbols()) | {"SPY"}
    assert all(
        kwargs
        == dict(
            timeframe="1 day", max_session=CUTOFF.latest_completed_session, as_of=CUTOFF.cutoff_at
        )
        for _, kwargs in calls
    )
    row = next(row for row in result["rows"] if row["etf_symbol"] == "XLK")
    assert row["returns"]["126"]["absolute_pct"] is None and row["points"] == []
    assert all(row["returns"][str(n)]["spy_relative_pct"] is not None for n in (5, 21, 63))
    assert len(result["benchmark"]["points"]) == 127
    assert all(len(row["points"]) <= 127 and row["bar_count"] <= 127 for row in result["rows"])
    assert result["source_type"] == "CHART_PRICE_HISTORY"
    assert result["relative_method"] == "ratio_of_ratios_vs_spy_v1"
    assert "not archived signals" in result["history_semantics"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "fault",
    [
        "gap",
        "zero",
        "nan",
        "boolean",
        "ohlc",
        "duplicate",
        "future",
        "unknown_basis",
        "mixed_basis",
        "manifest",
        "asof",
        "acquisition",
        "fingerprint",
        "receipt",
        "exception",
    ],
)
def test_bad_source_never_fakes_current_comparison(fault):
    def edit(price, symbol):
        if symbol != "XLK":
            return
        if fault == "manifest":
            del price.attrs["pit_source_manifest"]
        elif fault == "asof":
            price.attrs["pit_source_manifest"]["as_of"] = (
                CUTOFF.cutoff_at + timedelta(seconds=1)
            ).isoformat()
        elif fault == "acquisition":
            price.attrs["pit_source_manifest"]["acquisition_authority"] = {"pipeline": 3}
        elif fault == "fingerprint":
            price.attrs["pit_source_manifest"]["states"][-1]["fingerprint"] = "bad"
        elif fault == "receipt":
            price["first_seen_at"] = CUTOFF.cutoff_at + timedelta(seconds=1)

    bad = frame("XLK")
    if fault == "gap":
        bad = bad.drop(bad.index[-3])
    elif fault in {"zero", "nan", "boolean"}:
        bad["close"] = bad["close"].astype(object)
        bad.loc[bad.index[-1], "close"] = {"zero": 0, "nan": float("nan"), "boolean": True}[fault]
    elif fault == "ohlc":
        bad.loc[bad.index[-1], "high"] = 1
    elif fault == "duplicate":
        bad = pd.concat([bad, bad.iloc[-1:]])
    elif fault == "future":
        bad.loc[bad.index[-1], "date"] = CUTOFF.latest_completed_session + timedelta(days=1)
    elif fault in {"unknown_basis", "mixed_basis"}:
        bad.attrs["price_basis"] = "INVALID" if fault == "unknown_basis" else "ADJUSTED_LAST"
    elif fault == "exception":
        bad = RuntimeError("private provider password")
    result = MarketSectorChartService(frame_loader=read_frames({"XLK": bad}, edit=edit)).build(
        object(), cutoff=CUTOFF, symbols="XLK"
    )
    row = result["rows"][0]
    assert row["status"] != "CURRENT" and row["points"] == []
    assert all(value["spy_relative_pct"] is None for value in row["returns"].values())
    assert "private provider" not in json.dumps(result)


def test_missing_benchmark_does_not_invent_relative_returns_or_common_lines():
    result = MarketSectorChartService(
        frame_loader=read_frames({"SPY": frame("SPY").iloc[:0]})
    ).build(object(), symbols="XLK", cutoff=CUTOFF, horizon=21)
    row = result["rows"][0]
    assert row["returns"]["21"]["absolute_pct"] is not None
    assert row["returns"]["21"]["spy_relative_pct"] is None
    assert row["points"] == result["benchmark"]["points"] == []


@pytest.mark.parametrize("selection", ["QQQ", "SPY", "XLK,XLK", "", "XLK,", "MSFT"])
def test_invalid_symbols_fail_before_any_price_read(selection):
    read = Mock(side_effect=AssertionError("must not load"))
    with pytest.raises(ValueError, match="INVALID_SECTOR_SELECTION"):
        MarketSectorChartService(frame_loader=read).build(
            object(), symbols=selection, cutoff=CUTOFF
        )
    read.assert_not_called()


def test_disabled_zero_reads_and_stable_material_digest_tracks_pit_revisions():
    read = Mock(side_effect=AssertionError("must not load"))
    config = replace(load_market_sector_intelligence_config(), enabled=False)
    disabled = MarketSectorChartService(frame_loader=read).build(
        object(), config=config, cutoff=CUTOFF
    )
    assert disabled["cohort_status"] == "DISABLED" and len(disabled["rows"]) == 11
    read.assert_not_called()
    service = MarketSectorChartService(frame_loader=read_frames())
    first = service.build(object(), symbols="XLK", cutoff=CUTOFF)
    later = service.build(
        object(),
        symbols="XLK",
        cutoff=replace(CUTOFF, cutoff_at=CUTOFF.cutoff_at + timedelta(hours=1)),
    )
    assert first["evidence_digest"] == later["evidence_digest"]

    def edit(price, symbol):
        for state in price.attrs["pit_source_manifest"]["states"]:
            state["id"] += 10000
        if symbol == "XLK":
            price.attrs["pit_source_manifest"]["states"][-1]["fingerprint"] = "a" * 64

    changed = MarketSectorChartService(frame_loader=read_frames(edit=edit)).build(
        object(), symbols="XLK", cutoff=CUTOFF
    )
    assert changed["evidence_digest"] != first["evidence_digest"]
