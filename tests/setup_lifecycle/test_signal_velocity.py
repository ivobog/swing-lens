from __future__ import annotations

from datetime import UTC, date, datetime

from app.models.tables import SetupSignalSnapshot
from app.services.setup_lifecycle.change_detector import velocity_by_window
from app.services.setup_lifecycle.enums import SignalCategory, SignalValueType
from app.services.setup_lifecycle.signal_registry import SignalDefinition


def test_velocity_windows_use_exact_completed_us_trading_sessions() -> None:
    definition = SignalDefinition(
        key="technical_score",
        source="dual_score",
        value_type=SignalValueType.FLOAT,
        category=SignalCategory.SCORE,
        direction="higher_is_better",
        velocity_windows=(1, 3, 5, 10),
    )
    current = _snapshot(date(2026, 8, 13), "8.0", identity=10)
    history = (
        _snapshot(date(2026, 8, 12), "7.5", identity=9),
        _snapshot(date(2026, 8, 10), "7.0", identity=8),
        _snapshot(date(2026, 8, 6), "6.5", identity=7),
        _snapshot(date(2026, 7, 30), "6.0", identity=6),
    )

    velocity = velocity_by_window(definition, current, history)

    assert velocity["1"]["normalized_delta"] == "0.5"
    assert velocity["1"]["target_date"] == "2026-08-12"
    assert velocity["3"]["normalized_delta"] == "1.0"
    assert velocity["3"]["target_date"] == "2026-08-10"
    assert velocity["5"]["normalized_delta"] == "1.5"
    assert velocity["5"]["target_date"] == "2026-08-06"
    assert velocity["10"]["normalized_delta"] == "2.0"
    assert velocity["10"]["target_date"] == "2026-07-30"


def test_velocity_gap_does_not_substitute_latest_stored_snapshot() -> None:
    definition = SignalDefinition(
        key="technical_score",
        source="dual_score",
        value_type=SignalValueType.FLOAT,
        category=SignalCategory.SCORE,
        direction="higher_is_better",
        velocity_windows=(1, 3, 5, 10),
    )

    velocity = velocity_by_window(
        definition,
        _snapshot(date(2026, 8, 13), "8.0", identity=10),
        (_snapshot(date(2026, 8, 5), "7.5", identity=9),),
    )

    assert velocity["1"]["target_date"] == "2026-08-12"
    assert velocity["1"]["normalized_delta"] is None
    assert velocity["1"]["prior_snapshot_id"] is None
    assert velocity["1"]["missing_reason"] == "EXACT_TARGET_SESSION_SNAPSHOT_UNAVAILABLE"


def test_velocity_uses_rank_direction_for_lower_rank_is_better() -> None:
    definition = SignalDefinition(
        key="sector_rank",
        source="sector_rank",
        value_type=SignalValueType.INTEGER_RANK,
        category=SignalCategory.LEADERSHIP,
        direction="lower_rank_is_better",
        velocity_windows=(1,),
    )

    velocity = velocity_by_window(
        definition,
        _snapshot(date(2026, 8, 10), 3, identity=10, signal_key="sector_rank"),
        (_snapshot(date(2026, 8, 7), 7, identity=9, signal_key="sector_rank"),),
    )

    assert velocity["1"]["normalized_delta"] == "4.0"


def _snapshot(
    as_of: date,
    value,
    *,
    identity: int,
    signal_key: str = "technical_score",
) -> SetupSignalSnapshot:
    return SetupSignalSnapshot(
        id=identity,
        run_id=7,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=as_of,
        calculated_at=datetime.combine(as_of, datetime.min.time(), tzinfo=UTC),
        origin_type="LIVE_RUN",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        source_data_hash=f"source-{identity}",
        schema_version="snapshot-v1",
        data_quality_label="NORMAL",
        signals_json={signal_key: {"value": value}},
        is_canonical=True,
    )
