from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.services.setup_lifecycle.dtos import NormalizedSnapshot, SignalValue
from app.services.setup_lifecycle.enums import DataQualityLabel, SignalValueType


def snapshot(
    *,
    setup_score: float | None,
    classification: str | None,
    data_quality: DataQualityLabel = DataQualityLabel.HIGH,
    **signals: Any,
) -> NormalizedSnapshot:
    values = {
        "setup_score": setup_score,
        "technical_score": signals.pop("technical_score", setup_score),
        "classification": classification,
        "distance_to_pivot_pct": signals.pop("distance_to_pivot_pct", None),
        "close_trigger_cross": signals.pop("close_trigger_cross", False),
        "feature_flags": signals.pop("feature_flags", ()),
        **signals,
    }
    return NormalizedSnapshot(
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 1),
        calculated_at=datetime(2026, 8, 1, 21, tzinfo=UTC),
        signals={
            key: SignalValue(
                key=key,
                value_type=value_type(value),
                raw_value=value,
                normalized_value=value,
            )
            for key, value in values.items()
        },
        data_quality_label=data_quality,
        source_lineage={
            "market_regime_as_of": "2026-08-01",
            "sector_rotation_as_of": "2026-08-01",
        },
    )


def value_type(value: Any) -> SignalValueType:
    if isinstance(value, bool):
        return SignalValueType.BOOLEAN
    if isinstance(value, (int, float)):
        return SignalValueType.FLOAT
    return SignalValueType.ENUM
