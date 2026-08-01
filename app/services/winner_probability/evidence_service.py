from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.tables import (
    OutcomeStatus,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.cohort_definition import CohortKey


@dataclass(frozen=True)
class EvidenceOutcome:
    prediction: WinnerPredictionSnapshot
    forward_outcome: WinnerForwardOutcome
    target_stop_outcome: WinnerTargetStopOutcome
    inclusion_weight: Decimal = Decimal("1")

    @property
    def won(self) -> bool:
        return bool(self.target_stop_outcome.primary_winner)


class EvidenceService:
    def load_evidence(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        cohort_key: CohortKey,
        training_cutoff_at: datetime,
    ) -> tuple[EvidenceOutcome, ...]:
        rows = db.execute(
            select(WinnerPredictionSnapshot, WinnerForwardOutcome, WinnerTargetStopOutcome)
            .join(
                WinnerForwardOutcome,
                WinnerForwardOutcome.prediction_id == WinnerPredictionSnapshot.id,
            )
            .join(
                WinnerTargetStopOutcome,
                WinnerTargetStopOutcome.forward_outcome_id == WinnerForwardOutcome.id,
            )
            .where(WinnerPredictionSnapshot.id != prediction.id)
            .where(WinnerPredictionSnapshot.source_data_cutoff_at < training_cutoff_at)
            .where(WinnerPredictionSnapshot.superseded_at.is_(None))
            .where(WinnerForwardOutcome.entry_model == outcome_definition.entry_model)
            .where(WinnerForwardOutcome.horizon_sessions == outcome_definition.horizon_sessions)
            .where(WinnerForwardOutcome.status == OutcomeStatus.MATURED)
            .where(WinnerForwardOutcome.matured_at < training_cutoff_at)
            .where(
                or_(
                    WinnerForwardOutcome.superseded_at.is_(None),
                    WinnerForwardOutcome.superseded_at >= training_cutoff_at,
                )
            )
            .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition.id)
            .where(WinnerTargetStopOutcome.status == OutcomeStatus.MATURED)
            .where(WinnerTargetStopOutcome.evaluated_at < training_cutoff_at)
            .where(
                or_(
                    WinnerTargetStopOutcome.superseded_at.is_(None),
                    WinnerTargetStopOutcome.superseded_at >= training_cutoff_at,
                )
            )
            .order_by(WinnerPredictionSnapshot.prediction_as_of_date, WinnerPredictionSnapshot.id)
        )
        candidates = (
            EvidenceOutcome(prediction=row[0], forward_outcome=row[1], target_stop_outcome=row[2])
            for row in rows
            if row[0].id != prediction.id
            and row[0].source_data_cutoff_at < training_cutoff_at
            and row[1].matured_at is not None
            and row[1].matured_at < training_cutoff_at
            and (
                row[1].superseded_at is None or row[1].superseded_at >= training_cutoff_at
            )
            and row[2].evaluated_at is not None
            and row[2].evaluated_at < training_cutoff_at
            and (
                row[2].superseded_at is None or row[2].superseded_at >= training_cutoff_at
            )
            and _matches(row[0], cohort_key)
            and not _is_dependent(row[0])
            and row[0].reconstruction_method is None
        )
        return _one_per_episode(tuple(candidates))


def _matches(prediction: WinnerPredictionSnapshot, cohort_key: CohortKey) -> bool:
    features = prediction.feature_json or {}
    for dimension, expected in cohort_key.dimensions.items():
        actual = "all" if dimension == "global" else features.get(dimension) or "__MISSING__"
        if actual != expected:
            return False
    return True


def _is_dependent(prediction: WinnerPredictionSnapshot) -> bool:
    return bool((prediction.lineage_json or {}).get("dependent_episode"))


def _one_per_episode(rows: tuple[EvidenceOutcome, ...]) -> tuple[EvidenceOutcome, ...]:
    selected: list[EvidenceOutcome] = []
    seen_episode_ids: set[int] = set()
    for row in rows:
        episode_id = row.prediction.episode_id
        if episode_id is not None:
            if episode_id in seen_episode_ids:
                continue
            seen_episode_ids.add(episode_id)
        selected.append(row)
    return tuple(selected)
