from __future__ import annotations

import pytest

from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.setup_lifecycle.enums import SignalCategory, SignalValueType
from app.services.setup_lifecycle.signal_registry import (
    SignalDefinitionRegistry,
    SignalRegistryError,
)


def test_default_registry_contains_required_srs_signals() -> None:
    registry = load_setup_lifecycle_config().signal_registry

    assert {
        "technical_score",
        "setup_score",
        "classification",
        "stage",
        "relative_strength",
        "sector_rank",
        "market_regime",
        "earnings_risk",
        "liquidity",
        "data_quality",
        "distance_to_pivot_pct",
        "close_trigger_cross",
        "intraday_high_trigger_cross_diagnostic",
    }.issubset(registry.keys())


def test_registry_parses_value_types_categories_thresholds_and_velocity() -> None:
    technical = load_setup_lifecycle_config().signal_registry.require("technical_score")

    assert technical.value_type is SignalValueType.FLOAT
    assert technical.category is SignalCategory.SCORE
    assert technical.direction == "higher_is_better"
    assert technical.absolute_change == 0.5
    assert technical.percentage_change == 0.10
    assert technical.velocity_windows == (1, 3, 5, 10)
    assert technical.crossings == (7.0, 7.5, 8.0)


def test_normalized_delta_honors_signal_direction() -> None:
    registry = load_setup_lifecycle_config().signal_registry

    assert registry.require("technical_score").normalized_delta(6.0, 7.0) == 1.0
    assert registry.require("distance_to_pivot_pct").normalized_delta(3.0, 2.0) == 1.0
    assert registry.require("sector_rank").normalized_delta(10, 7) == 3.0
    assert registry.require("liquidity").normalized_delta(False, True) == -1.0
    assert registry.require("close_trigger_cross").normalized_delta(False, True) == 1.0


def test_unknown_signal_lookup_fails_with_clear_error() -> None:
    registry = load_setup_lifecycle_config().signal_registry

    with pytest.raises(SignalRegistryError, match="unknown signal"):
        registry.require("made_up")


def test_registry_requires_close_and_diagnostic_trigger_separation() -> None:
    raw = {
        "close_trigger_cross": {
            "source": "close_above_trigger",
            "type": "boolean",
            "category": "SETUP",
            "direction": "true_is_better",
            "trigger_authority": "close",
        }
    }

    with pytest.raises(SignalRegistryError, match="diagnostic high-cross"):
        SignalDefinitionRegistry.from_config(raw)
