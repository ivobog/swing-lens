from __future__ import annotations

from datetime import UTC, date, datetime

from app.models.tables import SetupSignalSnapshot
from app.services.setup_lifecycle.change_detector import velocity_by_window
from app.services.setup_lifecycle.enums import SignalCategory, SignalValueType
from app.services.setup_lifecycle.signal_registry import SignalDefinition


def test_velocity_windows_produce_expected_normalized_deltas() -> None:
    definition = SignalDefinition(
        key="technical_score",
        source="dual_score",
        value_type=SignalValueType.FLOAT,
        category=SignalCategory.SCORE,
        direction="higher_is_better",
        velocity_windows=(1, 3, 5, 10),
    )
    current = _snapshot(10, "8.0")
    history = (
        _snapshot(9, "7.5"),
        _snapshot(8, "7.0"),
        _snapshot(7, "6.5"),
        _snapshot(6, "6.0"),
        _snapshot(5, "5.5"),
    )

    velocity = velocity_by_window(definition, current, history)

    assert velocity["1"]["normalized_delta"] == "0.5"
    assert velocity["3"]["normalized_delta"] == "1.5"
    assert velocity["5"]["normalized_delta"] == "2.5"
    assert "10" not in velocity


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
        _snapshot(10, 3, signal_key="sector_rank"),
        (_snapshot(9, 7, signal_key="sector_rank"),),
    )

    assert velocity["1"]["normalized_delta"] == "4.0"


def _snapshot(
    day: int,
    value,
    *,
    signal_key: str = "technical_score",
) -> SetupSignalSnapshot:
    return SetupSignalSnapshot(
        id=day,
        run_id=7,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, day),
        calculated_at=datetime(2026, 8, day, 21, tzinfo=UTC),
        origin_type="LIVE_RUN",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        source_data_hash=f"source-{day}",
        schema_version="snapshot-v1",
        data_quality_label="NORMAL",
        signals_json={signal_key: {"value": value}},
        is_canonical=True,
    )
