from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    WinnerCohortDefinition,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
)
from app.services.core_mutation_authority import core_writer_member
from app.services.winner_probability.config import (
    CohortLevelConfig,
    WinnerProbabilityConfig,
)

COHORT_BASELINE_SOURCE_VERSION = "cohort_baseline_v1"


@dataclass(frozen=True)
class CohortKey:
    level: str
    dimensions: dict[str, Any]
    key: str


@dataclass(frozen=True)
class CohortOutcomeIdentity:
    id: int
    entry_model: str


class CohortDefinitionService:
    def cohort_keys_for_prediction(
        self,
        prediction: WinnerPredictionSnapshot,
        config: WinnerProbabilityConfig,
    ) -> tuple[CohortKey, ...]:
        return tuple(
            _cohort_key(level, prediction.feature_json or {}) for level in config.cohort.hierarchy
        )

    def cohort_keys_for_features(
        self,
        feature_json: dict[str, Any],
        config: WinnerProbabilityConfig,
    ) -> tuple[CohortKey, ...]:
        return tuple(_cohort_key(level, feature_json) for level in config.cohort.hierarchy)

    @core_writer_member(
        (
            "app.services.winner_probability.cohort_materialization_service:CohortMaterializationService.materialize_slice",
            "app.services.winner_probability.probability_estimator:ProbabilityEstimator._create_estimate",
            "app.services.winner_probability.probability_estimator:ProbabilityEstimator._materialize_cohort_statistic",
        )
    )
    def ensure_definition(
        self,
        db: Session,
        *,
        cohort_key: CohortKey,
        outcome_definition: WinnerOutcomeDefinition | CohortOutcomeIdentity,
        config: WinnerProbabilityConfig,
    ) -> WinnerCohortDefinition:
        if isinstance(db, Session):
            levels = [level for level in config.cohort.hierarchy if level.level == cohort_key.level]
            if len(levels) != 1 or set(cohort_key.dimensions) != set(levels[0].dimensions):
                raise ValueError("MUTATION_WINNER_COHORT_HIERARCHY_REQUIRED")
            payload = {"level": cohort_key.level, "dimensions": cohort_key.dimensions}
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
            ).hexdigest()
            if cohort_key.key != f"{cohort_key.level}:{digest}":
                raise ValueError("MUTATION_WINNER_NATIVE_COHORT_KEY_REQUIRED")
        getter = getattr(db, "get_existing_cohort_definition", None)
        existing = (
            getter(
                cohort_key=cohort_key.key,
                outcome_definition_id=outcome_definition.id,
                source_version=COHORT_BASELINE_SOURCE_VERSION,
            )
            if callable(getter)
            else db.scalar(
                select(WinnerCohortDefinition)
                .where(WinnerCohortDefinition.cohort_key == cohort_key.key)
                .where(WinnerCohortDefinition.outcome_definition_id == outcome_definition.id)
                .where(WinnerCohortDefinition.source_version == COHORT_BASELINE_SOURCE_VERSION)
            )
        )
        if existing is not None:
            if isinstance(db, Session):
                from app.services.winner_probability.cohort_authority import retained_row

                retained_row(db, existing)
                if (
                    existing.dimensions_json != cohort_key.dimensions
                    or existing.level != cohort_key.level
                    or existing.entry_model != outcome_definition.entry_model
                    or existing.feature_schema_version != config.feature_schema.version
                ):
                    raise ValueError("MUTATION_WINNER_COHORT_DEFINITION_SCOPE_MISMATCH")
            return existing
        row = WinnerCohortDefinition(
            cohort_key=cohort_key.key,
            level=cohort_key.level,
            outcome_definition_id=outcome_definition.id,
            entry_model=outcome_definition.entry_model,
            dimensions_json=cohort_key.dimensions,
            feature_schema_version=config.feature_schema.version,
            config_hash=config.config_hash,
            source_version=COHORT_BASELINE_SOURCE_VERSION,
            status="ACTIVE",
        )
        db.add(row)
        db.flush()
        return row


def _cohort_key(level: CohortLevelConfig, feature_json: dict[str, Any]) -> CohortKey:
    dimensions = {
        dimension: "all" if dimension == "global" else _normalize(feature_json.get(dimension))
        for dimension in level.dimensions
    }
    payload = {
        "level": level.level,
        "dimensions": dimensions,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return CohortKey(level=level.level, dimensions=dimensions, key=f"{level.level}:{digest}")


def _normalize(value: Any) -> Any:
    if value is None or value == "":
        return "__MISSING__"
    return value
