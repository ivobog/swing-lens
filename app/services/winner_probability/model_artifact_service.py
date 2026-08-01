from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.models.tables import WinnerModelVersion
from app.services.winner_probability.config import (
    WinnerProbabilityConfig,
    load_winner_probability_config,
)

ALLOWED_ARTIFACT_FORMATS = frozenset({"json", "jsonb", "cohort_baseline", "linear_json"})


class ModelArtifactValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ModelArtifactValidationResult:
    valid: bool
    artifact_hash: str
    warnings: tuple[str, ...] = ()


class ModelArtifactService:
    def validate_model_version(
        self,
        model: WinnerModelVersion,
        *,
        config: WinnerProbabilityConfig | None = None,
    ) -> ModelArtifactValidationResult:
        config = config or load_winner_probability_config()
        _require_text(model.artifact_schema_version, "artifact_schema_version")
        _require_text(model.artifact_hash, "artifact_hash")
        _require_text(model.artifact_format, "artifact_format")
        if model.artifact_format not in ALLOWED_ARTIFACT_FORMATS:
            raise ModelArtifactValidationError(
                f"artifact_format must be one of {', '.join(sorted(ALLOWED_ARTIFACT_FORMATS))}"
            )
        if model.artifact_format.endswith("pickle") or model.artifact_format == "pickle":
            raise ModelArtifactValidationError("pickle artifacts cannot be activated")
        if model.feature_schema_version != config.feature_schema.version:
            raise ModelArtifactValidationError("feature schema is incompatible")
        allowed_entry_models = {
            config.entry_models.production,
            *config.entry_models.diagnostics,
        }
        if model.entry_model not in allowed_entry_models:
            raise ModelArtifactValidationError("entry model is not configured")
        _validate_feature_order(model.preprocessing_json or {}, config)
        _validate_calibration_payload(model.calibration_json or {})
        warnings = _dependency_warnings(model.dependency_versions_json or {})
        return ModelArtifactValidationResult(
            valid=True,
            artifact_hash=model.artifact_hash,
            warnings=warnings,
        )

    def validate_json_payload(
        self,
        payload: dict[str, Any],
        *,
        expected_hash: str,
    ) -> ModelArtifactValidationResult:
        actual = artifact_hash(payload)
        if actual != expected_hash:
            raise ModelArtifactValidationError("artifact hash mismatch")
        return ModelArtifactValidationResult(valid=True, artifact_hash=actual)


def artifact_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_feature_order(
    preprocessing: dict[str, Any],
    config: WinnerProbabilityConfig,
) -> None:
    feature_order = preprocessing.get("feature_order")
    if feature_order is None:
        raise ModelArtifactValidationError("preprocessing_json.feature_order is required")
    if not isinstance(feature_order, list) or not all(
        isinstance(item, str) for item in feature_order
    ):
        raise ModelArtifactValidationError(
            "preprocessing_json.feature_order must be a list of strings"
        )
    unknown = sorted(set(feature_order) - set(config.feature_schema.core_features))
    if unknown:
        raise ModelArtifactValidationError(
            f"feature_order contains unknown feature(s): {', '.join(unknown)}"
        )


def _validate_calibration_payload(calibration: dict[str, Any]) -> None:
    method = calibration.get("method")
    if not isinstance(method, str) or not method:
        raise ModelArtifactValidationError("calibration_json.method is required")
    if calibration.get("version") in {None, ""}:
        raise ModelArtifactValidationError("calibration_json.version is required")


def _dependency_warnings(dependencies: dict[str, Any]) -> tuple[str, ...]:
    if not dependencies:
        return ("dependency_versions_json is empty",)
    return ()


def _require_text(value: str | None, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ModelArtifactValidationError(f"{field_name} is required")
