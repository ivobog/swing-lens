from __future__ import annotations

import pytest

from app.models.tables import WinnerModelVersion
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.model_artifact_service import (
    ModelArtifactService,
    ModelArtifactValidationError,
    artifact_hash,
)


def test_model_artifact_validation_accepts_compatible_json_artifact() -> None:
    config = load_winner_probability_config()
    model = _model()

    result = ModelArtifactService().validate_model_version(model, config=config)

    assert result.valid
    assert result.artifact_hash == "artifact-hash"
    assert result.warnings == ()


def test_model_artifact_validation_rejects_corrupt_payload_hash() -> None:
    payload = {"coefficients": {"setup_family": 0.4}, "intercept": -0.2}

    with pytest.raises(ModelArtifactValidationError, match="artifact hash mismatch"):
        ModelArtifactService().validate_json_payload(
            payload,
            expected_hash="not-the-real-hash",
        )

    assert artifact_hash(payload) != "not-the-real-hash"


def test_model_artifact_validation_rejects_incompatible_schema_and_unknown_feature() -> None:
    config = load_winner_probability_config()
    model = _model()
    model.feature_schema_version = "owpe-features-2.0.0"

    with pytest.raises(ModelArtifactValidationError, match="feature schema"):
        ModelArtifactService().validate_model_version(model, config=config)

    model = _model()
    model.preprocessing_json = {"feature_order": ["future_feature"]}
    with pytest.raises(ModelArtifactValidationError, match="unknown feature"):
        ModelArtifactService().validate_model_version(model, config=config)


def test_model_artifact_validation_rejects_pickle_and_missing_calibration() -> None:
    config = load_winner_probability_config()
    model = _model()
    model.artifact_format = "pickle"

    with pytest.raises(ModelArtifactValidationError, match="artifact_format"):
        ModelArtifactService().validate_model_version(model, config=config)

    model = _model()
    model.calibration_json = {}
    with pytest.raises(ModelArtifactValidationError, match="calibration_json.method"):
        ModelArtifactService().validate_model_version(model, config=config)


def _model() -> WinnerModelVersion:
    return WinnerModelVersion(
        id=1,
        model_key="cohort-baseline",
        algorithm="cohort",
        status="SHADOW",
        outcome_definition_id=1,
        entry_model="NEXT_OPEN",
        feature_schema_version="owpe-features-1.0.0",
        calculation_version="owpe-calc-1.0.0",
        config_hash="config-hash",
        training_cutoff_at="2026-07-31T21:00:00+00:00",
        preprocessing_json={
            "feature_order": ["setup_family", "ranking_profile", "technical_score"]
        },
        calibration_json={"method": "isotonic", "version": "1"},
        artifact_schema_version="winner-model-artifact-v1",
        artifact_format="json",
        artifact_hash="artifact-hash",
        dependency_versions_json={"python": "3.12"},
    )
