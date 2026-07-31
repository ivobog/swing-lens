from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.services.winner_probability.feature_schema import (
    FEATURE_SCHEMA_VERSION,
    FeatureSchemaError,
    FeatureSchemaRegistry,
)


def test_feature_schema_registry_loads_v1_metadata() -> None:
    registry = FeatureSchemaRegistry()

    assert registry.version == FEATURE_SCHEMA_VERSION
    technical_score = registry.get("technical_score")
    assert technical_score.source_path == "TechnicalScore.total_score"
    assert technical_score.availability_rule == "completed signal session only"
    assert technical_score.indexed_core_column is True
    assert "technical_score" in {feature.name for feature in registry.list_features()}


def test_feature_schema_rejects_unknown_versions_and_features() -> None:
    with pytest.raises(FeatureSchemaError, match="Unsupported"):
        FeatureSchemaRegistry("owpe-features-9.9.9")

    registry = FeatureSchemaRegistry()
    with pytest.raises(FeatureSchemaError, match="Unknown OWPE feature"):
        registry.get("future_alpha_signal")

    with pytest.raises(FeatureSchemaError, match="unknown feature"):
        registry.require_feature_names(["setup_family", "future_alpha_signal"], "features")


def test_feature_schema_rejects_future_dated_feature_sources() -> None:
    registry = FeatureSchemaRegistry()
    cutoff = datetime(2026, 7, 31, 21, 0, tzinfo=UTC)

    registry.validate_source_available_at(
        "technical_score",
        source_available_at=cutoff - timedelta(minutes=1),
        prediction_cutoff_at=cutoff,
    )

    with pytest.raises(FeatureSchemaError, match="after prediction cutoff"):
        registry.validate_source_available_at(
            "technical_score",
            source_available_at=cutoff + timedelta(seconds=1),
            prediction_cutoff_at=cutoff,
        )


def test_feature_schema_date_sources_use_end_of_day_availability() -> None:
    registry = FeatureSchemaRegistry()

    registry.validate_source_available_at(
        "universe_provenance",
        source_available_at=date(2026, 7, 31),
        prediction_cutoff_at=datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC),
    )

    with pytest.raises(FeatureSchemaError, match="after prediction cutoff"):
        registry.validate_source_available_at(
            "universe_provenance",
            source_available_at=date(2026, 8, 1),
            prediction_cutoff_at=datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC),
        )
