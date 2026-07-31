from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.tables import (
    OutcomeStatus,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerSimilarityLink,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.config import (
    WinnerProbabilityConfig,
    load_winner_probability_config,
)
from app.services.winner_probability.evidence_service import EvidenceOutcome
from app.services.winner_probability.feature_schema import FeatureSchemaRegistry

SIMILARITY_CACHE_VERSION = "owpe-similarity-v1"
SIMILARITY_EVIDENCE_ROLE = "SUPPORTING"
SIX_PLACES = Decimal("0.000001")
EIGHT_PLACES = Decimal("0.00000001")

NUMERIC_BOUNDS = {
    "fundamental_score": (Decimal("0"), Decimal("10")),
    "technical_score": (Decimal("0"), Decimal("10")),
    "combined_score": (Decimal("0"), Decimal("10")),
    "sector_rank": (Decimal("1"), Decimal("100")),
    "reward_risk": (Decimal("0"), Decimal("10")),
    "fundamental_coverage": (Decimal("0"), Decimal("1")),
}


@dataclass(frozen=True)
class FeatureContribution:
    feature_name: str
    distance: Decimal
    weighted_distance: Decimal
    weight: Decimal


@dataclass(frozen=True)
class SimilarityDistance:
    distance: Decimal | None
    coverage: Decimal
    contributions: tuple[FeatureContribution, ...]


@dataclass(frozen=True)
class SimilarityOutcomeSummary:
    primary_winner: bool | None
    first_event: str | None
    close_return_pct: Decimal | None
    mfe_pct: Decimal | None
    mae_pct: Decimal | None
    target_hit: bool | None
    stop_hit: bool | None


@dataclass(frozen=True)
class SimilarityNeighbor:
    neighbor_prediction_id: int
    outcome_id: int
    outcome_revision: int
    rank: int
    distance: Decimal
    similarity_coverage: Decimal
    top_feature_contributions: tuple[FeatureContribution, ...]
    outcome_summary: SimilarityOutcomeSummary
    evidence_role: str = SIMILARITY_EVIDENCE_ROLE


class SimilarityService:
    def rank_neighbors(
        self,
        *,
        prediction: WinnerPredictionSnapshot,
        evidence: tuple[EvidenceOutcome, ...],
        feature_names: tuple[str, ...] | None = None,
        feature_weights: dict[str, Decimal | int | float] | None = None,
        as_of: datetime | None = None,
        limit: int = 10,
        one_per_episode: bool = True,
        config: WinnerProbabilityConfig | None = None,
    ) -> tuple[SimilarityNeighbor, ...]:
        if limit <= 0:
            return ()
        config = config or load_winner_probability_config()
        feature_names = feature_names or config.feature_schema.core_features
        FeatureSchemaRegistry(config.feature_schema.version).require_feature_names(
            feature_names,
            "similarity.feature_names",
        )
        as_of = as_of or prediction.source_data_cutoff_at
        weights = _feature_weights(feature_names, feature_weights)
        rows: list[SimilarityNeighbor] = []
        seen_episode_ids: set[int] = set()

        for row in evidence:
            if not _candidate_is_safe(row, prediction=prediction, as_of=as_of):
                continue
            episode_id = row.prediction.episode_id
            if one_per_episode and episode_id is not None:
                if episode_id in seen_episode_ids:
                    continue
                seen_episode_ids.add(episode_id)
            distance = distance_between(
                prediction.feature_json or {},
                row.prediction.feature_json or {},
                feature_names=feature_names,
                feature_weights=weights,
            )
            if distance.distance is None:
                continue
            rows.append(
                SimilarityNeighbor(
                    neighbor_prediction_id=row.prediction.id,
                    outcome_id=row.forward_outcome.id,
                    outcome_revision=row.forward_outcome.revision,
                    rank=0,
                    distance=distance.distance,
                    similarity_coverage=distance.coverage,
                    top_feature_contributions=distance.contributions[:5],
                    outcome_summary=_outcome_summary(row),
                )
            )

        ranked = sorted(
            rows,
            key=lambda item: (
                item.distance,
                -item.similarity_coverage,
                item.neighbor_prediction_id,
            ),
        )[:limit]
        return tuple(
            SimilarityNeighbor(
                neighbor_prediction_id=row.neighbor_prediction_id,
                outcome_id=row.outcome_id,
                outcome_revision=row.outcome_revision,
                rank=index,
                distance=row.distance,
                similarity_coverage=row.similarity_coverage,
                top_feature_contributions=row.top_feature_contributions,
                outcome_summary=row.outcome_summary,
            )
            for index, row in enumerate(ranked, start=1)
        )

    def load_and_rank_neighbors(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        feature_names: tuple[str, ...] | None = None,
        as_of: datetime | None = None,
        limit: int = 10,
        config: WinnerProbabilityConfig | None = None,
    ) -> tuple[SimilarityNeighbor, ...]:
        as_of = as_of or prediction.source_data_cutoff_at
        evidence = _load_safe_evidence(
            db,
            prediction=prediction,
            outcome_definition=outcome_definition,
            as_of=as_of,
        )
        return self.rank_neighbors(
            prediction=prediction,
            evidence=evidence,
            feature_names=feature_names,
            as_of=as_of,
            limit=limit,
            config=config,
        )

    def persist_neighbors(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        neighbors: tuple[SimilarityNeighbor, ...],
        source_cutoff_at: datetime | None = None,
        cache_version: str = SIMILARITY_CACHE_VERSION,
    ) -> tuple[WinnerSimilarityLink, ...]:
        source_cutoff_at = source_cutoff_at or prediction.source_data_cutoff_at
        rows: list[WinnerSimilarityLink] = []
        for neighbor in neighbors:
            row = WinnerSimilarityLink(
                prediction_id=prediction.id,
                neighbor_prediction_id=neighbor.neighbor_prediction_id,
                outcome_definition_id=outcome_definition.id,
                outcome_id=neighbor.outcome_id,
                outcome_revision=neighbor.outcome_revision,
                rank=neighbor.rank,
                distance=neighbor.distance,
                similarity_coverage=neighbor.similarity_coverage,
                contribution_json={
                    "evidence_role": neighbor.evidence_role,
                    "top_features": [
                        {
                            "feature_name": item.feature_name,
                            "distance": str(item.distance),
                            "weighted_distance": str(item.weighted_distance),
                            "weight": str(item.weight),
                        }
                        for item in neighbor.top_feature_contributions
                    ],
                    "outcome_summary": {
                        "primary_winner": neighbor.outcome_summary.primary_winner,
                        "first_event": neighbor.outcome_summary.first_event,
                        "close_return_pct": _str_or_none(
                            neighbor.outcome_summary.close_return_pct
                        ),
                        "mfe_pct": _str_or_none(neighbor.outcome_summary.mfe_pct),
                        "mae_pct": _str_or_none(neighbor.outcome_summary.mae_pct),
                        "target_hit": neighbor.outcome_summary.target_hit,
                        "stop_hit": neighbor.outcome_summary.stop_hit,
                    },
                },
                cache_version=cache_version,
                source_cutoff_at=source_cutoff_at,
            )
            db.add(row)
            rows.append(row)
        db.flush()
        return tuple(rows)


def distance_between(
    current: dict[str, Any],
    candidate: dict[str, Any],
    *,
    feature_names: tuple[str, ...],
    feature_weights: dict[str, Decimal] | None = None,
) -> SimilarityDistance:
    weights = _feature_weights(feature_names, feature_weights)
    total_weight = sum(weights.values(), Decimal("0"))
    available_weight = Decimal("0")
    weighted_distance = Decimal("0")
    contributions: list[FeatureContribution] = []

    for feature_name in feature_names:
        left = current.get(feature_name)
        right = candidate.get(feature_name)
        if _missing(left) or _missing(right):
            continue
        weight = weights[feature_name]
        distance = _feature_distance(feature_name, left, right)
        weighted = distance * weight
        available_weight += weight
        weighted_distance += weighted
        contributions.append(
            FeatureContribution(
                feature_name=feature_name,
                distance=_quantize(distance),
                weighted_distance=_quantize(weighted),
                weight=_quantize(weight),
            )
        )

    coverage = _quantize(available_weight / total_weight) if total_weight > 0 else Decimal("0")
    if available_weight <= 0:
        return SimilarityDistance(distance=None, coverage=coverage, contributions=())
    return SimilarityDistance(
        distance=(weighted_distance / available_weight).quantize(EIGHT_PLACES),
        coverage=coverage,
        contributions=tuple(
            sorted(
                contributions,
                key=lambda item: (item.weighted_distance, item.feature_name),
                reverse=True,
            )
        ),
    )


def _load_safe_evidence(
    db: Session,
    *,
    prediction: WinnerPredictionSnapshot,
    outcome_definition: WinnerOutcomeDefinition,
    as_of: datetime,
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
        .where(WinnerPredictionSnapshot.source_data_cutoff_at < as_of)
        .where(WinnerPredictionSnapshot.superseded_at.is_(None))
        .where(WinnerForwardOutcome.entry_model == outcome_definition.entry_model)
        .where(WinnerForwardOutcome.horizon_sessions == outcome_definition.horizon_sessions)
        .where(WinnerForwardOutcome.status == OutcomeStatus.MATURED)
        .where(WinnerForwardOutcome.matured_at < as_of)
        .where(
            or_(
                WinnerForwardOutcome.superseded_at.is_(None),
                WinnerForwardOutcome.superseded_at >= as_of,
            )
        )
        .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition.id)
        .where(WinnerTargetStopOutcome.status == OutcomeStatus.MATURED)
        .where(WinnerTargetStopOutcome.evaluated_at < as_of)
        .where(
            or_(
                WinnerTargetStopOutcome.superseded_at.is_(None),
                WinnerTargetStopOutcome.superseded_at >= as_of,
            )
        )
    )
    return tuple(
        EvidenceOutcome(prediction=row[0], forward_outcome=row[1], target_stop_outcome=row[2])
        for row in rows
    )


def _candidate_is_safe(
    row: EvidenceOutcome,
    *,
    prediction: WinnerPredictionSnapshot,
    as_of: datetime,
) -> bool:
    if row.prediction.id == prediction.id:
        return False
    if row.prediction.episode_id is not None and row.prediction.episode_id == prediction.episode_id:
        return False
    if row.prediction.source_data_cutoff_at >= as_of:
        return False
    if row.forward_outcome.status != OutcomeStatus.MATURED:
        return False
    if row.target_stop_outcome.status != OutcomeStatus.MATURED:
        return False
    if row.forward_outcome.matured_at is None or row.forward_outcome.matured_at >= as_of:
        return False
    if (
        row.target_stop_outcome.evaluated_at is None
        or row.target_stop_outcome.evaluated_at >= as_of
    ):
        return False
    if row.forward_outcome.superseded_at is not None and row.forward_outcome.superseded_at < as_of:
        return False
    if (
        row.target_stop_outcome.superseded_at is not None
        and row.target_stop_outcome.superseded_at < as_of
    ):
        return False
    return row.prediction.reconstruction_method is None


def _feature_distance(feature_name: str, left: Any, right: Any) -> Decimal:
    if feature_name in NUMERIC_BOUNDS:
        return _numeric_distance(feature_name, left, right)
    return Decimal("0") if str(left).casefold() == str(right).casefold() else Decimal("1")


def _numeric_distance(feature_name: str, left: Any, right: Any) -> Decimal:
    low, high = NUMERIC_BOUNDS[feature_name]
    span = high - low
    if span <= 0:
        return Decimal("0")
    left_value = _clamp(Decimal(str(left)), low, high)
    right_value = _clamp(Decimal(str(right)), low, high)
    return abs(left_value - right_value) / span


def _feature_weights(
    feature_names: tuple[str, ...],
    feature_weights: dict[str, Decimal | int | float] | None,
) -> dict[str, Decimal]:
    raw_weights = feature_weights or {}
    return {
        feature_name: Decimal(str(raw_weights.get(feature_name, 1)))
        for feature_name in feature_names
    }


def _outcome_summary(row: EvidenceOutcome) -> SimilarityOutcomeSummary:
    return SimilarityOutcomeSummary(
        primary_winner=row.target_stop_outcome.primary_winner,
        first_event=row.target_stop_outcome.first_event,
        close_return_pct=row.forward_outcome.close_return_pct,
        mfe_pct=row.forward_outcome.mfe_pct,
        mae_pct=row.forward_outcome.mae_pct,
        target_hit=row.target_stop_outcome.target_hit,
        stop_hit=row.target_stop_outcome.stop_hit,
    )


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(max(value, low), high)


def _missing(value: Any) -> bool:
    return value is None or value == ""


def _quantize(value: Decimal) -> Decimal:
    return Decimal(value).quantize(SIX_PLACES)


def _str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None
