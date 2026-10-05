from __future__ import annotations

import pytest

from app.services import pipeline_stage_registry as registry


def test_every_canonical_pipeline_stage_has_exactly_one_watchdog_classification() -> None:
    identities = [item.identity for item in registry._PIPELINE_STAGE_METADATA_ENTRIES]

    assert set(registry.CANONICAL_PIPELINE_STAGES).issubset(registry.PIPELINE_STAGE_METADATA)
    assert all(identities.count(stage) == 1 for stage in registry.CANONICAL_PIPELINE_STAGES)
    registry.validate_canonical_stage_registry()


def test_missing_canonical_stage_fails_registry_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete = dict(registry.PIPELINE_STAGE_METADATA)
    incomplete.pop(registry.SECTOR_ROTATION_PIPELINE_STEP)
    monkeypatch.setattr(registry, "PIPELINE_STAGE_METADATA", incomplete)

    with pytest.raises(RuntimeError, match="SECTOR_ROTATION_SNAPSHOT"):
        registry.validate_canonical_stage_registry()


def test_sector_rotation_uses_long_running_timeout_without_changing_bounded_default() -> None:
    timeout = registry.progress_timeout_seconds(
        registry.SECTOR_ROTATION_PIPELINE_STEP,
        default_timeout_seconds=300,
        market_data_timeout_seconds=360,
        long_stage_timeout_seconds=1800,
    )
    bounded = registry.progress_timeout_seconds(
        "NON_PIPELINE_BACKGROUND_PHASE",
        default_timeout_seconds=300,
        market_data_timeout_seconds=360,
        long_stage_timeout_seconds=1800,
    )

    assert timeout == 1800
    assert bounded == 300


def test_decision_handoff_uses_long_running_timeout_after_live_scale_regression() -> None:
    metadata = registry.stage_metadata(registry.DECISION_HANDOFF_PIPELINE_STEP)
    timeout = registry.progress_timeout_seconds(
        registry.DECISION_HANDOFF_PIPELINE_STEP,
        default_timeout_seconds=300,
        market_data_timeout_seconds=360,
        long_stage_timeout_seconds=1800,
    )

    assert metadata.execution_class is registry.StageExecutionClass.LONG_RUNNING
    assert timeout == 1800
