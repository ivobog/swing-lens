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


class CohortDefinitionService:
    def cohort_keys_for_prediction(
        self,
        prediction: WinnerPredictionSnapshot,
        config: WinnerProbabilityConfig,
    ) -> tuple[CohortKey, ...]:
        return tuple(
            _cohort_key(level, prediction.feature_json or {})
            for level in config.cohort.hierarchy
        )

    def ensure_definition(
        self,
        db: Session,
        *,
        cohort_key: CohortKey,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
    ) -> WinnerCohortDefinition:
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
