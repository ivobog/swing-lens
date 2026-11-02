"""Review counterexamples, using real cached readers and native engines."""

from datetime import date, timedelta
from unittest.mock import Mock

import pandas as pd
import pytest
from market_sector_technical_helpers import CUTOFF, NOW, frame, symbols
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_market_sector_intelligence_rotation_storage import rotation_engine as rotation_engine
from test_market_sector_intelligence_technical_pit import seed_cache

from app.models.tables import PriceBar
from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_rotation_math import required_sessions
from app.services.market_sector_intelligence_technical_service import MarketSectorTechnicalService
from app.services.us_market_calendar import is_us_trading_day

# Independent exceptional-session oracle, not an expected value derived from the calendar:
# https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx
# https://www.nasdaqtrader.com/TraderNews.aspx?id=ETA2024-87
CARTER_CLOSURE = date(2025, 1, 9)


def technical_service():
    return MarketSectorTechnicalService(clock=Mock(cutoff_for=Mock(return_value=CUTOFF)))


def test_official_january_ninth_is_not_an_equity_session():
    assert not is_us_trading_day(CARTER_CLOSURE)
    cutoff = MarketClockService().cutoff_for(
        pd.Timestamp("2025-01-09T22:00:00Z").to_pydatetime(), reason="OFFICIAL_CLOSURE"
    )
    assert cutoff.latest_completed_session == date(2025, 1, 8)


def test_official_january_tenth_five_session_anchor():
    assert required_sessions(date(2025, 1, 10))[5] == date(2025, 1, 2)


def long_cache(engine, *, missing=None):
    seed_cache(engine)
    days = [
        stamp.date()
        for stamp in pd.date_range("2023-08-21", CUTOFF.latest_completed_session)
        if is_us_trading_day(stamp.date()) and stamp.date() != CARTER_CLOSURE
    ]
    rows = []
    for ticker in symbols() + ["SPY", "QQQ"]:
        prices = frame(ticker, len(days))
        prices["date"] = days
        for _, bar in prices.iterrows():
            if bar["date"] == missing:
                continue
            rows.append(
                dict(
                    id=len(rows) + 1,
                    ticker=ticker,
                    bar_date=bar["date"],
                    timeframe="1 day",
                    what_to_show="TRADES",
                    source="REVIEW_REAL_SESSION_FIXTURE",
                    **{key: float(bar[key]) for key in ("open", "high", "low", "close", "volume")},
                    created_at=NOW - timedelta(minutes=5),
                    first_seen_at=NOW - timedelta(minutes=5),
                    last_seen_at=NOW - timedelta(minutes=5),
                    revision_count=0,
                )
            )
    with engine.begin() as connection:
        connection.execute(PriceBar.__table__.delete())
        connection.execute(PriceBar.__table__.insert(), rows)


def test_actual_loader_long_history_needs_no_synthetic_closure_bar(rotation_engine):
    long_cache(rotation_engine)
    with Session(rotation_engine) as db:
        result = technical_service().preview(db, cutoff=CUTOFF)
    assert result["v4_ready_count"] == result["v5_ready_count"] == 11
    assert all(row["data_reasons"] == [] for row in result["rows"])


def adjusted_and_trades_cache(engine, *, adjusted_volume=0):
    seed_cache(engine)
    with engine.begin() as connection:
        trades = list(connection.execute(PriceBar.__table__.select()).mappings())
        connection.execute(
            PriceBar.__table__.insert(),
            [
                dict(
                    row,
                    id=row["id"] + 100_000,
                    what_to_show="ADJUSTED_LAST",
                    volume=adjusted_volume,
                )
                for row in trades
            ],
        )


def test_actual_preferred_loader_accepts_effective_trades_volume(rotation_engine):
    adjusted_and_trades_cache(rotation_engine)
    with Session(rotation_engine) as db:
        result = technical_service().preview(db, cutoff=CUTOFF)
        assert result["v4_ready_count"] == result["v5_ready_count"] == 11
        source = technical_service()._load(
            db, "XLK", load_market_sector_intelligence_config(), CUTOFF
        )
    assert source["price_basis"] == "ADJUSTED_LAST"
    assert source["volume_basis"] == "TRADES"
    assert source["reasons"] == []
    assert source["volume"]["volume"].min() > 0


def test_effective_volume_matches_both_native_engines(rotation_engine):
    from app.services import market_sector_intelligence_technical_adapter as adapter
    from app.services.price_bar_repository import load_preferred_ohlcv_frames
    from app.services.technical_score_v4 import technical_score_v4_from_base_score
    from app.services.technical_score_v5 import technical_score_v5_from_base_score

    adjusted_and_trades_cache(rotation_engine, adjusted_volume=-1)
    policy = adapter.scoped_policy(symbols())
    with Session(rotation_engine) as db:
        actual = technical_service().preview(db, cutoff=CUTOFF)
        inputs = {}
        for ticker in symbols() + ["SPY", "QQQ"]:
            price, volume = load_preferred_ohlcv_frames(
                db, ticker, max_session=CUTOFF.latest_completed_session, as_of=CUTOFF.cutoff_at
            )
            assert list(price["date"]) == list(volume["date"])
            assert (price["volume"] == -1).all()
            # Independent assembly from the selected cache sources, before native validation.
            price.attrs = {"price_basis": price.attrs["price_basis"]}
            volume.attrs = {"volume_basis": volume.attrs["volume_basis"]}
            price["volume"] = volume["volume"].to_numpy(copy=True)
            inputs[ticker] = price, volume
        market = {
            ticker: adapter.local_features(
                *inputs[ticker], ticker, CUTOFF.latest_completed_session, policy
            ).latest
            for ticker in ("SPY", "QQQ")
        }
        bases = {
            ticker: adapter.build_base(
                ticker,
                *inputs[ticker],
                inputs["SPY"][0],
                market["SPY"],
                market["QQQ"],
                CUTOFF.latest_completed_session,
                policy,
            )
            for ticker in symbols()
        }
        leadership = adapter.fixed_leadership(
            [
                dict(adapter.leadership_input(base, policy)[0], ticker=ticker)
                for ticker, base in bases.items()
            ],
            symbols(),
            policy,
        )
    assert actual["v4_ready_count"] == actual["v5_ready_count"] == 11
    for row in actual["rows"]:
        ticker = row["etf_symbol"]
        v4 = technical_score_v4_from_base_score(bases[ticker], v4_params=policy.v4)
        v5 = technical_score_v5_from_base_score(
            bases[ticker],
            leadership=leadership[ticker],
            sector_resolution=adapter.SECTOR_RESOLUTION,
            v5_config=policy.v5,
            pine_config=policy.pine,
        )
        assert row["v4"]["final_v4_score"] == v4.final_v4_score
        assert row["v4"]["classification"] == v4.final_v4_classification
        assert row["v4"]["confidence"] == bases[ticker].technical_confidence
        assert row["v5"]["technical_composite_score"] == v5.technical_composite_score
        assert row["v5"]["confidence_adjusted_score"] == v5.confidence_adjusted_score
        assert row["v5"]["classification"] == v5.classification
        assert row["v5"]["confidence"] == v5.technical_confidence


@pytest.mark.parametrize(
    "fault", ["zero", "negative", "null", "missing", "stale", "unaligned", "late"]
)
def test_bad_effective_trades_volume_still_fails_closed(rotation_engine, fault):
    adjusted_and_trades_cache(rotation_engine)
    with rotation_engine.begin() as connection:
        target = (PriceBar.ticker == "XLK") & (PriceBar.what_to_show == "TRADES")
        current = target & (PriceBar.bar_date == CUTOFF.latest_completed_session)
        if fault in {"zero", "negative", "null"}:
            connection.execute(
                PriceBar.__table__.update()
                .where(current)
                .values(volume={"zero": 0, "negative": -1, "null": None}[fault])
            )
        elif fault == "missing":
            connection.execute(PriceBar.__table__.delete().where(target))
        elif fault == "stale":
            connection.execute(PriceBar.__table__.delete().where(current))
        elif fault == "late":
            connection.execute(
                PriceBar.__table__.update()
                .where(current)
                .values(first_seen_at=NOW + timedelta(seconds=1))
            )
        else:
            first = connection.scalar(
                select(PriceBar.bar_date).where(target).order_by(PriceBar.bar_date)
            )
            connection.execute(
                PriceBar.__table__.delete().where(target, PriceBar.bar_date == first)
            )
    with Session(rotation_engine) as db:
        source = technical_service()._load(
            db, "XLK", load_market_sector_intelligence_config(), CUTOFF
        )
    expected = (
        "STALE"
        if fault in {"stale", "late"}
        else "MISSING_VOLUME_SESSION"
        if fault == "unaligned"
        else "MISSING_VOLUME"
    )
    assert source["reasons"] == [expected]


def test_invalid_etf_volume_retains_other_v4_but_withholds_whole_v5(rotation_engine):
    adjusted_and_trades_cache(rotation_engine)
    with rotation_engine.begin() as connection:
        connection.execute(
            PriceBar.__table__.update()
            .where(
                PriceBar.ticker == "XLK",
                PriceBar.what_to_show == "TRADES",
                PriceBar.bar_date == CUTOFF.latest_completed_session,
            )
            .values(volume=0)
        )
    with Session(rotation_engine) as db:
        result = technical_service().preview(db, cutoff=CUTOFF)
    assert result["v4_ready_count"] == 10
    assert result["v5_ready_count"] == 0
    assert result["v5_cohort_status"] == "V5_COHORT_NOT_COMPARABLE"
    assert (
        "MISSING_VOLUME"
        in next(r for r in result["rows"] if r["etf_symbol"] == "XLK")["data_reasons"]
    )


def test_real_session_gap_inside_technical_window_is_rejected(rotation_engine):
    long_cache(rotation_engine, missing=date(2026, 10, 15))
    with Session(rotation_engine) as db:
        source = technical_service()._load(
            db, "SPY", load_market_sector_intelligence_config(), CUTOFF
        )
    assert source["reasons"] == ["MISSING_TRADING_SESSION"]


def test_obsolete_gap_is_outside_bounded_sql_and_reader_scope_is_restored(rotation_engine):
    from sqlalchemy import event

    from app.services.market_sector_intelligence_read_window import bounded_cached_read
    from app.services.price_bar_repository import load_preferred_ohlcv_frames

    long_cache(rotation_engine, missing=date(2023, 9, 5))
    sql = []
    event.listen(
        rotation_engine,
        "before_cursor_execute",
        lambda _c, _s, statement, *_a: sql.append(statement),
    )
    with Session(rotation_engine) as db:
        source = technical_service()._load(
            db, "SPY", load_market_sector_intelligence_config(), CUTOFF
        )
        assert source["reasons"] == []
        assert source["bar_count"] == 756
        assert all("bar_date >=" in query and "bar_date <=" in query for query in sql)
        with pytest.raises(RuntimeError, match="FORCED_READ_EXIT"):
            with bounded_cached_read(db, date(2026, 10, 1)):
                raise RuntimeError("FORCED_READ_EXIT")
        full, _ = load_preferred_ohlcv_frames(
            db, "SPY", max_session=CUTOFF.latest_completed_session, as_of=CUTOFF.cutoff_at
        )
        assert len(full) > source["bar_count"]


def test_declared_window_expands_with_enabled_prerequisites():
    from copy import deepcopy

    from app.services import market_sector_intelligence_technical_adapter as adapter

    default = adapter.scoped_policy(symbols())
    assert default.identity["history_window_sessions"] == 756
    v4 = deepcopy(default.v4)
    v4["adaptive_percentiles"]["long_lookback"] = 1200
    expanded = adapter.scoped_policy(symbols(), v4=v4)
    assert expanded.identity["history_window_sessions"] >= 1452
    assert expanded.identity["adapter_policy_digest"] != default.identity["adapter_policy_digest"]


def test_calendar_revision_preserves_frozen_v1_context_identity():
    from app.models.tables import MarketCalculationContext
    from app.services.market_calculation_context_service import (
        cutoff_from_row,
        market_calculation_context_fingerprint,
    )
    from app.services.us_market_calendar import CALENDAR_VERSION, LEGACY_CALENDAR_VERSION

    instant = pd.Timestamp("2025-01-09T22:00:00Z").to_pydatetime()
    old = (
        MarketClockService(calendar_version=LEGACY_CALENDAR_VERSION)
        .cutoff_for(instant, reason="FROZEN_V1")
        .with_context_id(77)
    )
    assert old.latest_completed_session == CARTER_CLOSURE
    retained = MarketCalculationContext(
        id=77,
        cutoff_at=old.cutoff_at,
        exchange_timezone=old.exchange_timezone,
        latest_completed_session=old.latest_completed_session,
        daily_bar_ready_at=old.daily_bar_ready_at,
        calendar_version=old.calendar_version,
        bar_readiness_version=old.bar_readiness_version,
        cutoff_reason=old.cutoff_reason,
    )
    restored = cutoff_from_row(retained)
    assert restored == old
    assert market_calculation_context_fingerprint(
        restored
    ) == market_calculation_context_fingerprint(old)
    corrected = MarketClockService().cutoff_for(instant, reason="FROZEN_V1").with_context_id(77)
    assert corrected.calendar_version == CALENDAR_VERSION != LEGACY_CALENDAR_VERSION
    assert corrected.latest_completed_session == date(2025, 1, 8)
    assert market_calculation_context_fingerprint(
        corrected
    ) != market_calculation_context_fingerprint(old)
    assert retained.latest_completed_session == CARTER_CLOSURE
    assert retained.calendar_version == LEGACY_CALENDAR_VERSION


def test_shared_consumers_agree_on_the_exceptional_closure():
    from app.services.ceri.effective_session_service import CeriEffectiveSessionService
    from app.services.market_sector_intelligence_chart_service import chart_sessions
    from app.services.us_market_calendar import first_us_market_open_after
    from app.services.winner_probability.trading_session_service import horizon_due_session

    clock = MarketClockService()
    assert clock.trading_session_distance(date(2025, 1, 8), date(2025, 1, 10)) == 1
    assert CARTER_CLOSURE not in chart_sessions(date(2025, 1, 10))
    assert horizon_due_session(date(2025, 1, 8), 2) == date(2025, 1, 10)
    assert first_us_market_open_after(
        pd.Timestamp("2025-01-08T22:00:00Z").to_pydatetime()
    ).session == date(2025, 1, 10)
    assert CeriEffectiveSessionService().resolve(
        timestamp=pd.Timestamp("2025-01-09T15:00:00Z").to_pydatetime()
    ).effective_session == date(2025, 1, 10)


def test_stock_local_cache_keys_distinguish_calendar_semantics():
    from app.services.technical_artifact_cache import build_local_artifact_key
    from app.services.us_market_calendar import CALENDAR_VERSION, LEGACY_CALENDAR_VERSION

    inputs = dict(
        ticker="MSFT",
        adjusted_series_version=1,
        trades_series_version=1,
        feature_config_hash="unchanged-features",
        scoring_config_hash="unchanged-scoring",
        technical_engine_version="3.2.0",
        input_as_of_session=date(2025, 1, 10),
    )
    legacy = build_local_artifact_key(**inputs, calendar_version=LEGACY_CALENDAR_VERSION)
    revised = build_local_artifact_key(**inputs, calendar_version=CALENDAR_VERSION)
    unversioned = build_local_artifact_key(**inputs)
    assert len({legacy.input_signature, revised.input_signature, unversioned.input_signature}) == 3
    assert revised.input_versions["calendar_version"] == CALENDAR_VERSION
    assert legacy.input_versions["calendar_version"] == LEGACY_CALENDAR_VERSION
