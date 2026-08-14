from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    OutcomeStatus,
    PredictionEligibility,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
    WinnerTrainingEligibilityDecision,
    WinnerTrainingOutcomeReplay,
)
from app.services.winner_probability.cohort_definition import CohortKey
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.services.winner_probability.pre11_compatibility_service import (
    BRIDGE_VERSION,
    EVIDENCE_ORIGIN_NATIVE,
    EVIDENCE_ORIGIN_PRE11,
    TRAINING_FAMILY,
)
from app.services.winner_probability.trading_session_service import latest_completed_session
from app.services.winner_probability.training_eligibility import TrainingEligibilityPolicy


@dataclass(frozen=True)
class EvidenceOutcome:
    prediction: WinnerPredictionSnapshot
    forward_outcome: WinnerForwardOutcome
    target_stop_outcome: WinnerTargetStopOutcome | WinnerTrainingOutcomeReplay
    inclusion_weight: Decimal = Decimal("1")
    eligibility_decision_id: int | None = None
    outcome_replay_id: int | None = None
    evidence_origin: str = EVIDENCE_ORIGIN_NATIVE

    @property
    def won(self) -> bool:
        return bool(self.target_stop_outcome.primary_winner)


@dataclass(frozen=True)
class EvidenceFunnelStage:
    predicate: str
    before_count: int
    after_count: int


@dataclass(frozen=True)
class EvidenceDiagnosticFunnel:
    stages: tuple[EvidenceFunnelStage, ...]
    evidence: tuple[EvidenceOutcome, ...]

    def counts(self) -> dict[str, int]:
        return {stage.predicate: stage.after_count for stage in self.stages}


class EvidenceService:
    def __init__(self, policy: TrainingEligibilityPolicy | None = None) -> None:
        self.policy = policy or TrainingEligibilityPolicy()
        self._global_funnel_cache: dict[tuple[object, ...], EvidenceDiagnosticFunnel] = {}

    def load_evidence(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        cohort_key: CohortKey,
        training_cutoff_at: datetime,
        config: WinnerProbabilityConfig,
    ) -> tuple[EvidenceOutcome, ...]:
        return self.diagnostic_funnel(
            db,
            prediction=prediction,
            outcome_definition=outcome_definition,
            cohort_key=cohort_key,
            training_cutoff_at=training_cutoff_at,
            config=config,
        ).evidence

    def filter_for_cohort(
        self,
        evidence: tuple[EvidenceOutcome, ...],
        cohort_key: CohortKey,
    ) -> tuple[EvidenceOutcome, ...]:
        return tuple(row for row in evidence if _matches(row.prediction, cohort_key))

    def diagnostic_funnel(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        cohort_key: CohortKey,
        training_cutoff_at: datetime,
        config: WinnerProbabilityConfig,
    ) -> EvidenceDiagnosticFunnel:
        cache_key = None
        if cohort_key.dimensions == {"global": "all"}:
            cache_key = (
                id(db),
                outcome_definition.id,
                training_cutoff_at,
                (None if prediction.source_data_cutoff_at == training_cutoff_at else prediction.id),
                prediction.feature_schema_version,
                prediction.calculation_version,
                prediction.config_hash,
                config.config_hash,
            )
            cached = self._global_funnel_cache.get(cache_key)
            if cached is not None:
                return cached
        native_rows = tuple(
            EvidenceOutcome(row[0], row[1], row[2])
            for row in db.execute(
                select(WinnerPredictionSnapshot, WinnerForwardOutcome, WinnerTargetStopOutcome)
                .join(
                    WinnerForwardOutcome,
                    WinnerForwardOutcome.prediction_id == WinnerPredictionSnapshot.id,
                )
                .join(
                    WinnerTargetStopOutcome,
                    WinnerTargetStopOutcome.forward_outcome_id == WinnerForwardOutcome.id,
                )
                .order_by(
                    WinnerPredictionSnapshot.prediction_as_of_date,
                    WinnerPredictionSnapshot.id,
                    WinnerForwardOutcome.revision,
                    WinnerTargetStopOutcome.revision,
                )
            )
        )
        replay_rows = self._load_compatibility_replays(
            db,
            training_cutoff_at=training_cutoff_at,
            outcome_definition=outcome_definition,
        )
        rows = native_rows + replay_rows
        completed_session = latest_completed_session(training_cutoff_at)
        rolling_start = _subtract_years(
            training_cutoff_at.date(), config.cohort.rolling_window_years
        )
        stages: list[EvidenceFunnelStage] = []

        def apply(name: str, predicate: Callable[[EvidenceOutcome], bool]) -> None:
            nonlocal rows
            before = len(rows)
            rows = tuple(row for row in rows if predicate(row))
            stages.append(EvidenceFunnelStage(name, before, len(rows)))

        apply(
            "historical_predictions_before_cutoff",
            lambda row: (
                row.prediction.id != prediction.id
                and row.prediction.source_data_cutoff_at < training_cutoff_at
                and _visible_at_cutoff(row.prediction.superseded_at, training_cutoff_at)
            ),
        )
        apply(
            "full_horizon_matured_before_cutoff",
            lambda row: _matured_before_cutoff(row, completed_session, training_cutoff_at),
        )
        apply(
            "current_forward_revision_at_cutoff",
            lambda row: _visible_at_cutoff(row.forward_outcome.superseded_at, training_cutoff_at),
        )
        apply(
            "compatible_target_stop_label",
            lambda row: (
                row.forward_outcome.entry_model == outcome_definition.entry_model
                and row.forward_outcome.horizon_sessions == outcome_definition.horizon_sessions
                and _target_definition_id(row) == outcome_definition.id
                and row.target_stop_outcome.entry_model == outcome_definition.entry_model
                and row.target_stop_outcome.horizon_sessions == outcome_definition.horizon_sessions
                and _decimal_equal(
                    row.target_stop_outcome.target_pct, outcome_definition.target_pct
                )
                and _decimal_equal(row.target_stop_outcome.stop_pct, outcome_definition.stop_pct)
                and row.target_stop_outcome.primary_winner is not None
            ),
        )
        apply(
            "current_target_stop_revision_at_cutoff",
            lambda row: (
                row.evidence_origin == EVIDENCE_ORIGIN_PRE11
                or _visible_at_cutoff(row.target_stop_outcome.superseded_at, training_cutoff_at)
            ),
        )
        apply(
            "prediction_eligible",
            lambda row: row.prediction.eligibility_status == PredictionEligibility.ELIGIBLE,
        )
        apply(
            "point_in_time_validated",
            lambda row: (row.prediction.lineage_json or {}).get("point_in_time_validated") is True,
        )
        apply("native_capture", lambda row: row.prediction.reconstruction_method is None)
        apply(
            "production_training_eligible",
            lambda row: (
                (
                    row.evidence_origin == EVIDENCE_ORIGIN_PRE11
                    and row.eligibility_decision_id is not None
                )
                or (
                    row.evidence_origin == EVIDENCE_ORIGIN_NATIVE
                    and self.policy.persisted_capture_decision(
                        row.prediction
                    ).capture_training_candidate
                )
            ),
        )
        apply(
            "feature_schema_compatible",
            lambda row: (
                (
                    row.evidence_origin == EVIDENCE_ORIGIN_PRE11
                    and row.prediction.feature_schema_version == config.feature_schema.version
                )
                or (
                    row.evidence_origin == EVIDENCE_ORIGIN_NATIVE
                    and row.prediction.feature_schema_version
                    == prediction.feature_schema_version
                    == config.feature_schema.version
                )
            ),
        )
        apply(
            "calculation_version_compatible",
            lambda row: (
                (
                    row.evidence_origin == EVIDENCE_ORIGIN_PRE11
                    and getattr(row.target_stop_outcome, "compatibility_bridge_version", None)
                    == BRIDGE_VERSION
                )
                or (
                    row.evidence_origin == EVIDENCE_ORIGIN_NATIVE
                    and row.prediction.calculation_version
                    == prediction.calculation_version
                    == outcome_definition.calculation_version
                    == config.engine.calculation_version
                )
            ),
        )
        apply(
            "config_compatible",
            lambda row: (
                (
                    row.evidence_origin == EVIDENCE_ORIGIN_PRE11
                    and getattr(row.target_stop_outcome, "target_outcome_definition_id", None)
                    == outcome_definition.id
                )
                or (
                    row.evidence_origin == EVIDENCE_ORIGIN_NATIVE
                    and row.prediction.config_hash
                    == prediction.config_hash
                    == outcome_definition.config_hash
                    == config.config_hash
                )
            ),
        )
        apply(
            "outcome_definition_compatible",
            lambda row: (
                outcome_definition.is_active
                and outcome_definition.definition_id == config.primary_outcome_definition.id
            ),
        )
        apply("quality_gates", _passes_quality_gates)
        apply(
            "rolling_window_eligible",
            lambda row: row.prediction.prediction_as_of_date >= rolling_start,
        )
        apply(
            "no_revised_after_cutoff_leakage",
            lambda row: (
                _source_revision_cutoff(row) is not None
                and _source_revision_cutoff(row) <= training_cutoff_at
            ),
        )
        apply("cohort_match", lambda row: _matches(row.prediction, cohort_key))
        apply("independent_episode", lambda row: not _is_dependent(row.prediction))

        before = len(rows)
        rows = _one_per_episode(rows)
        stages.append(EvidenceFunnelStage("one_representative_per_episode", before, len(rows)))
        result = EvidenceDiagnosticFunnel(tuple(stages), rows)
        if cache_key is not None:
            self._global_funnel_cache[cache_key] = result
        return result

    @staticmethod
    def _load_compatibility_replays(
        db: Session,
        *,
        training_cutoff_at: datetime,
        outcome_definition: WinnerOutcomeDefinition,
    ) -> tuple[EvidenceOutcome, ...]:
        if not isinstance(db, Session):
            # Small unit-test repositories provide only the legacy native-row
            # query contract.  Production always supplies a SQLAlchemy Session.
            return ()
        raw = list(
            db.execute(
                select(
                    WinnerPredictionSnapshot,
                    WinnerForwardOutcome,
                    WinnerTrainingOutcomeReplay,
                    WinnerTrainingEligibilityDecision,
                )
                .join(
                    WinnerTrainingEligibilityDecision,
                    WinnerTrainingEligibilityDecision.prediction_id == WinnerPredictionSnapshot.id,
                )
                .outerjoin(
                    WinnerTrainingOutcomeReplay,
                    WinnerTrainingOutcomeReplay.eligibility_decision_id
                    == WinnerTrainingEligibilityDecision.id,
                )
                .outerjoin(
                    WinnerForwardOutcome,
                    WinnerForwardOutcome.id
                    == WinnerTrainingOutcomeReplay.source_forward_outcome_id,
                )
                .where(WinnerTrainingEligibilityDecision.training_family == TRAINING_FAMILY)
                .where(
                    WinnerTrainingEligibilityDecision.target_outcome_definition_id
                    == outcome_definition.id
                )
                .where(WinnerTrainingEligibilityDecision.classified_at < training_cutoff_at)
                .order_by(
                    WinnerPredictionSnapshot.prediction_as_of_date,
                    WinnerPredictionSnapshot.id,
                    WinnerTrainingEligibilityDecision.revision.desc(),
                    WinnerTrainingOutcomeReplay.revision.desc(),
                )
            )
        )
        return _select_latest_compatibility_replays(raw, training_cutoff_at)


def _visible_at_cutoff(superseded_at: datetime | None, cutoff: datetime) -> bool:
    return superseded_at is None or superseded_at >= cutoff


def _select_latest_compatibility_replays(
    raw: list[tuple], training_cutoff_at: datetime
) -> tuple[EvidenceOutcome, ...]:
    """Select the latest visible decision first, including a later rejection."""
    selected: list[EvidenceOutcome] = []
    seen_predictions: set[int] = set()
    for prediction, forward, replay, decision in raw:
        if prediction.id in seen_predictions:
            continue
        seen_predictions.add(prediction.id)
        if (
            not decision.training_allowed
            or replay is None
            or forward is None
            or replay.status != OutcomeStatus.MATURED
            or replay.replayed_at >= training_cutoff_at
        ):
            continue
        selected.append(
            EvidenceOutcome(
                prediction=prediction,
                forward_outcome=forward,
                target_stop_outcome=replay,
                eligibility_decision_id=decision.id,
                outcome_replay_id=replay.id,
                evidence_origin=EVIDENCE_ORIGIN_PRE11,
            )
        )
    return tuple(selected)


def _target_definition_id(row: EvidenceOutcome) -> int:
    value = getattr(row.target_stop_outcome, "outcome_definition_id", None)
    if value is None:
        value = row.target_stop_outcome.target_outcome_definition_id
    return int(value)


def _source_revision_cutoff(row: EvidenceOutcome) -> datetime | None:
    if row.evidence_origin == EVIDENCE_ORIGIN_PRE11:
        return row.target_stop_outcome.source_revision_cutoff_at
    return row.forward_outcome.source_revision_cutoff_at


def _matured_before_cutoff(
    row: EvidenceOutcome,
    completed_session: date,
    training_cutoff_at: datetime,
) -> bool:
    if (
        row.forward_outcome.due_session is None
        or row.forward_outcome.due_session > completed_session
    ):
        return False
    if row.evidence_origin == EVIDENCE_ORIGIN_PRE11:
        return (
            row.target_stop_outcome.status == OutcomeStatus.MATURED
            and row.target_stop_outcome.due_session <= completed_session
            and row.target_stop_outcome.replayed_at < training_cutoff_at
        )
    return (
        row.forward_outcome.status == OutcomeStatus.MATURED
        and row.forward_outcome.matured_at is not None
        and row.forward_outcome.matured_at < training_cutoff_at
        and row.target_stop_outcome.status == OutcomeStatus.MATURED
        and row.target_stop_outcome.evaluated_at is not None
        and row.target_stop_outcome.evaluated_at < training_cutoff_at
    )


def _decimal_equal(left, right) -> bool:
    return left is not None and right is not None and Decimal(str(left)) == Decimal(str(right))


def _passes_quality_gates(row: EvidenceOutcome) -> bool:
    prediction = row.prediction
    lineage = prediction.lineage_json or {}
    blocking = {
        "quality_blocking",
        "invalid_source",
        "exclude_from_production_training",
    }
    source_flags = {str(value) for value in lineage.get("source_quality_flags", [])}
    warning_flags = {str(value) for value in prediction.warning_flags_json or []}
    return not bool((source_flags | warning_flags) & blocking)


def _subtract_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


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
