"""Regression probes closing the certified 2026-09 session-integrity findings.

The forensic fixtures remain read-only; assertions encode the intentionally
superseding production semantics introduced by the remediation.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.services.ceri.alert_service import _trading_sessions_between
from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.price_response_service import CeriPriceResponseService
from app.services.ib_market_intelligence.orchestration import _historical_date_ranges
from app.services.market_clock_service import MarketClockService
from app.services.relative_leadership import _market_session_dates as leadership_dates
from app.services.sector_etf_rotation_service import SectorEtfRotationService
from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.sector_rotation_dtos import SectorUniverseMetrics
from app.services.technical_artifact_cache import build_local_artifact_key
from app.services.technical_indicators import (
    _market_session_dates as indicator_dates,
)
from app.services.technical_indicators import (
    calculate_htf_trend_features,
    resample_weekly_ohlcv,
)
from app.services.us_market_calendar import (
    first_us_market_open_after,
    latest_completed_us_trading_day,
    us_market_session,
)

NY = ZoneInfo("America/New_York")


def _ny(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=NY).replace(
        hour=hour, minute=minute, second=second
    )


def test_shared_calendar_boundaries_and_cross_year_observed_holiday() -> None:
    regular = us_market_session(date(2026, 9, 8))
    assert regular is not None
    assert regular.open_at == _ny(date(2026, 9, 8), 9, 30)
    assert regular.close_at == _ny(date(2026, 9, 8), 16)
    assert latest_completed_us_trading_day(_ny(date(2026, 9, 8), 16, 14, 59)) == date(2026, 9, 4)
    assert latest_completed_us_trading_day(_ny(date(2026, 9, 8), 16, 15)) == date(2026, 9, 8)
    next_open = first_us_market_open_after(_ny(date(2026, 9, 8), 9, 30))
    assert next_open.session == date(2026, 9, 9)
    assert next_open.open_at == _ny(date(2026, 9, 9), 9, 30)
    assert us_market_session(date(2021, 12, 31)) is None


@pytest.mark.parametrize(
    ("instant", "offset"),
    [
        (datetime(2026, 3, 20, 9, 30, tzinfo=NY), timedelta(hours=-4)),
        (datetime(2026, 10, 30, 9, 30, tzinfo=NY), timedelta(hours=-4)),
        (datetime(2026, 11, 6, 9, 30, tzinfo=NY), timedelta(hours=-5)),
    ],
)
def test_shared_calendar_preserves_new_york_dst_offsets(
    instant: datetime, offset: timedelta
) -> None:
    session = us_market_session(instant.date())
    assert session is not None
    assert session.open_at.utcoffset() == offset


def test_sti_f003_artifact_key_expresses_eligible_session() -> None:
    inputs = dict(
        ticker="MSFT",
        adjusted_series_version=12,
        trades_series_version=13,
        feature_config_hash="features",
        scoring_config_hash="scores",
        technical_engine_version="5",
    )
    artifact_through_s = build_local_artifact_key(**inputs, input_as_of_session=date(2026, 9, 4))
    artifact_through_s_plus_one_but_bounded_to_s = build_local_artifact_key(
        **inputs, input_as_of_session=date(2026, 9, 8)
    )

    assert (
        artifact_through_s.input_signature
        != artifact_through_s_plus_one_but_bounded_to_s.input_signature
    )
    assert artifact_through_s.input_versions["input_as_of_session"] == "2026-09-04"


def test_sti_f006_enabled_sector_etf_loader_enforces_cutoff(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    frame = _bars(date(2025, 8, 1), 280)
    future = pd.concat([frame, _bars(date(2026, 9, 8), 1)], ignore_index=True)

    def fake_loader(_db, ticker, **kwargs):
        calls.append((ticker, kwargs))
        return future, None

    monkeypatch.setattr(
        "app.services.sector_etf_rotation_service.load_preferred_ohlcv_frames", fake_loader
    )
    config = load_sector_rotation_config()
    config["etf_score"]["enabled"] = True
    with pytest.raises(ValueError, match="temporal integrity violation"):
        SectorEtfRotationService().build(
            object(),
            [_sector_metrics()],
            config,
            market_cutoff=MarketClockService().cutoff_for(
                _ny(date(2026, 9, 8), 15), reason="FORENSIC_FIXTURE"
            ),
        )
    assert calls and all(kwargs.get("max_session") == date(2026, 9, 4) for _, kwargs in calls)


def test_sti_f008_feature_cutoff_is_market_session_aware() -> None:
    cutoff_at = _ny(date(2026, 9, 8), 15)
    context = CeriFeatureRebuildService().prepare_batch(
        object(), CeriFeatureRebuildRequest(cutoff_at=cutoff_at)
    )
    assert context.cutoff == date(2026, 9, 4)
    assert context.cutoff_at == cutoff_at


@pytest.mark.parametrize(
    ("label", "event_at", "expected_session", "open_before_event"),
    [
        ("pre_market", _ny(date(2026, 9, 8), 8), date(2026, 9, 8), False),
        ("one_second_before_open", _ny(date(2026, 9, 8), 9, 29, 59), date(2026, 9, 8), False),
        ("exact_open", _ny(date(2026, 9, 8), 9, 30), date(2026, 9, 9), False),
        ("regular_1000", _ny(date(2026, 9, 8), 10), date(2026, 9, 9), False),
        ("regular_1559", _ny(date(2026, 9, 8), 15, 59), date(2026, 9, 9), False),
        ("exact_close", _ny(date(2026, 9, 8), 16), date(2026, 9, 9), False),
        ("after_hours", _ny(date(2026, 9, 8), 16, 30), date(2026, 9, 9), False),
        ("weekend", _ny(date(2026, 9, 12), 12), date(2026, 9, 14), False),
        ("early_close_midday", _ny(date(2026, 11, 27), 12), date(2026, 11, 30), False),
        ("early_close_exact", _ny(date(2026, 11, 27), 13), date(2026, 11, 30), False),
    ],
)
def test_sti_f010_f011_reaction_open_characterization(
    label: str,
    event_at: datetime,
    expected_session: date,
    open_before_event: bool,
) -> None:
    del label
    service = CeriPriceResponseService()
    reaction = service.reaction_session(event_at, None)
    schedule = us_market_session(reaction)
    assert schedule is not None
    assert reaction == expected_session
    assert (schedule.open_at < event_at) is open_before_event


def test_sti_f012_connection_calendar_date_changes_cooldown_result() -> None:
    sessions = CeriEffectiveSessionService("America/New_York")
    prior = datetime(2026, 8, 9, 0, 44, tzinfo=UTC)  # Saturday evening in New York.
    change = datetime(2026, 8, 14, 8, 33, tzinfo=UTC)
    current_age = _trading_sessions_between(prior.date(), change.date(), sessions)
    correct_age = _trading_sessions_between(
        sessions.resolve(timestamp=prior).effective_session,
        sessions.resolve(timestamp=change).effective_session,
        sessions,
    )

    assert current_age == 5
    assert correct_age == 4


@pytest.mark.parametrize(
    ("last_day", "expected_week"),
    [
        (date(2026, 9, 4), date(2026, 9, 4)),  # Completed Friday is confirmed.
        (date(2026, 9, 10), date(2026, 9, 4)),  # Partial current week present.
        (date(2026, 9, 11), date(2026, 9, 11)),  # Completed Friday is confirmed.
    ],
)
def test_sti_f013_confirmed_week_selection_is_exchange_session_aware(
    last_day: date, expected_week: date
) -> None:
    frame = _weekday_bars(date(2025, 1, 6), last_day)
    result = calculate_htf_trend_features(frame, params=_htf_params())
    weekly = resample_weekly_ohlcv(frame)
    selected = weekly.loc[weekly["date"] == pd.Timestamp(expected_week)].iloc[0]
    assert result["close"] == selected["close"]


def test_sti_f013_one_partial_week_is_insufficient_confirmed_history() -> None:
    frame = _weekday_bars(date(2026, 9, 7), date(2026, 9, 10))
    result = calculate_htf_trend_features(frame, params=_htf_params())
    weekly = resample_weekly_ohlcv(frame)
    assert len(weekly) == 1
    assert result["htf_insufficient_confirmed_history"] is True
    assert result["htf_confirmed"] is False


@pytest.mark.parametrize("normalizer", [indicator_dates, leadership_dates])
def test_sti_f014_late_new_york_timestamp_retains_exchange_session(normalizer) -> None:
    values = pd.Series([datetime(2026, 3, 9, 21, 0, tzinfo=NY)])
    assert normalizer(values).iloc[0] == pd.Timestamp("2026-03-09")


def test_sti_f015_historical_default_uses_completed_session() -> None:
    settings = SimpleNamespace(
        ib_intelligence_historical_chunk_days=60,
        ib_liquidity_lookback_sessions=60,
        ib_fee_rate_lookback_sessions=60,
        ib_volatility_lookback_sessions=60,
    )
    from app.services.market_clock_service import MarketClockService

    cutoff = MarketClockService().cutoff_for(_ny(date(2026, 8, 10), 13, 36), reason="test")
    ranges = _historical_date_ranges(
        {}, SimpleNamespace(value="LIQUIDITY"), settings, market_cutoff=cutoff
    )
    assert ranges[-1][1] == date(2026, 8, 7)


def test_sti_f017_pipeline_dependencies_have_frozen_market_cutoff() -> None:
    from app.services.pipeline_executor import PipelineExecutionDependencies

    parameters = inspect.signature(PipelineExecutionDependencies).parameters
    assert "market_cutoff" in parameters


def test_sti_f018_ib_intelligence_feature_rebuild_uses_market_cutoff() -> None:
    from app.services.ib_market_intelligence.orchestration import execute_feature_rebuild

    source = inspect.getsource(execute_feature_rebuild)
    assert "_rebuild_ticker_feature" in source
    assert "date.today()" not in source


def _bars(start: date, count: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": start + timedelta(days=index),
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100.5 + index,
                "volume": 1_000_000 + index,
            }
            for index in range(count)
        ]
    )


def _weekday_bars(start: date, end: date) -> pd.DataFrame:
    rows = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            close = 100.0 + len(rows) / 10
            rows.append(
                {
                    "date": cursor,
                    "open": close - 0.2,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1_000_000,
                }
            )
        cursor += timedelta(days=1)
    return pd.DataFrame(rows)


def _htf_params() -> dict[str, object]:
    return {
        "htf": {
            "htfFastLen": 2,
            "htfMidLen": 3,
            "htfSlowLen": 4,
            "htfSlopeLookback": 1,
            "htfRocLookback": 1,
            "useConfirmedHtf": True,
        }
    }


def _sector_metrics() -> SectorUniverseMetrics:
    return SectorUniverseMetrics(
        sector="Technology",
        sector_slug="technology",
        ticker_count=1,
        universe_share=1.0,
        average_fundamental_score=8.0,
        average_technical_score=8.0,
        average_final_score=8.0,
        average_profile_score=8.0,
        top_counts={},
        setup_distribution={},
        warning_distribution={},
        buyable_count=1,
        watch_count=0,
        danger_count=0,
        buyable_share=1.0,
        watch_share=0.0,
        danger_share=0.0,
        clean_pullback_count=0,
        breakout_count=0,
        vcp_count=0,
        tight_base_breakout_count=0,
        extended_or_overheated_count=0,
        missing_fundamental_count=0,
        missing_technical_count=0,
        universe_leadership_score=8.0,
        confidence="high",
    )
