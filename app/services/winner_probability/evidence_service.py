from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from time import perf_counter
from types import MappingProxyType
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.models.tables import (
    OutcomeStatus,
    PredictionEligibility,
    PriceBar,
    PriceBarRevision,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
    WinnerTemporalValidityDecision,
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
    _hash,
)
from app.services.winner_probability.temporal_eligibility import (
    load_current_temporal_decisions,
    prediction_temporally_eligible,
)
from app.services.winner_probability.trading_session_service import latest_completed_session
from app.services.winner_probability.training_eligibility import TrainingEligibilityPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvidenceOutcome:
    prediction: WinnerPredictionSnapshot | FrozenORMRow
    forward_outcome: WinnerForwardOutcome | FrozenORMRow
    target_stop_outcome: WinnerTargetStopOutcome | WinnerTrainingOutcomeReplay | FrozenORMRow
    inclusion_weight: Decimal = Decimal("1")
    eligibility_decision_id: int | None = None
    temporal_validity_decision_id: int | None = None
    outcome_replay_id: int | None = None
    evidence_origin: str = EVIDENCE_ORIGIN_NATIVE

    @property
    def won(self) -> bool:
        return bool(self.target_stop_outcome.primary_winner)


@dataclass(frozen=True, slots=True)
class FrozenORMRow:
    """Fully materialized ORM column values safe beyond a Session lifecycle.

    The cache intentionally captures mapped columns only. Evidence evaluation
    never depends on ORM relationships, and missing attributes retain normal
    ``getattr(..., default)`` behavior.
    """

    model_name: str
    column_values: Mapping[str, Any]
    is_persisted_snapshot: bool = True

    def __getattr__(self, name: str) -> Any:
        try:
            return self.column_values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass(frozen=True)
class FrozenEvidencePrediction:
    id: int
    episode_id: int | None
    prediction_as_of_date: date
    feature_json: dict[str, Any]


@dataclass(frozen=True)
class FrozenEvidenceForwardOutcome:
    id: int
    revision: int
    close_return_pct: Decimal | None
    mfe_pct: Decimal | None
    mae_pct: Decimal | None


@dataclass(frozen=True)
class FrozenEvidenceTargetStopOutcome:
    id: int
    revision: int
    primary_winner: bool
    first_event: str | None


@dataclass(frozen=True)
class FrozenEvidenceMember:
    """Detached generation evidence safe across heartbeat commits."""

    prediction: FrozenEvidencePrediction
    forward_outcome: FrozenEvidenceForwardOutcome
    target_stop_outcome: FrozenEvidenceTargetStopOutcome
    inclusion_weight: Decimal = Decimal("1")
    eligibility_decision_id: int | None = None
    temporal_validity_decision_id: int | None = None
    outcome_replay_id: int | None = None
    evidence_origin: str = EVIDENCE_ORIGIN_NATIVE

    @property
    def won(self) -> bool:
        return self.target_stop_outcome.primary_winner


GenerationEvidenceMember = EvidenceOutcome | FrozenEvidenceMember


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


@dataclass(frozen=True)
class GenerationEvidenceUniverse:
    evidence: tuple[FrozenEvidenceMember, ...]
    stages: tuple[EvidenceFunnelStage, ...]

    def counts(self) -> dict[str, int]:
        return {stage.predicate: stage.after_count for stage in self.stages}


@dataclass
class RunEvidenceCandidateUniverse:
    """Run-scoped, point-in-time candidate superset.

    Candidate loading is frozen before the first current-run prediction is
    committed.  Every prediction still applies the original predicates using
    its exact cutoff; no cutoff is rounded or shared.
    """

    outcome_definition_id: int
    config_hash: str
    feature_schema_version: str
    calculation_version: str
    native_rows: tuple[EvidenceOutcome, ...]
    compatibility_candidates: tuple[tuple[FrozenORMRow | None, ...], ...]
    temporal_decisions: dict[int, FrozenORMRow]
    lineage_price_bars: dict[int, FrozenORMRow]
    lineage_price_bar_revisions: dict[int, FrozenORMRow]
    load_seconds: float
    native_base_queries: int = 1
    compatibility_base_queries: int = 1
    reuse_count: int = 0
    evaluation_count: int = 0
    filtered_evidence_rows: int = 0
    python_filter_seconds: float = 0.0

    def metrics(self) -> dict[str, int | float]:
        return {
            "native_candidate_rows": len(self.native_rows),
            "compatibility_candidate_rows": len(self.compatibility_candidates),
            "native_base_queries": self.native_base_queries,
            "compatibility_base_queries": self.compatibility_base_queries,
            "evidence_load_count": 1,
            "cache_reuse_count": self.reuse_count,
            "evidence_evaluation_count": self.evaluation_count,
            "filtered_evidence_rows": self.filtered_evidence_rows,
            "evidence_load_ms": round(self.load_seconds * 1000, 3),
            "python_filter_ms": round(self.python_filter_seconds * 1000, 3),
        }


class EvidenceService:
    def __init__(self, policy: TrainingEligibilityPolicy | None = None) -> None:
        self.policy = policy or TrainingEligibilityPolicy()
        self._global_funnel_cache: dict[tuple[object, ...], EvidenceDiagnosticFunnel] = {}
        self._run_candidate_universe: RunEvidenceCandidateUniverse | None = None

    def prepare_run_candidate_universe(
        self,
        db: Session,
        *,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
        lease_guard: Callable[[], None] | None = None,
    ) -> RunEvidenceCandidateUniverse | None:
        """Load the heavyweight evidence superset once for a capture run."""
        if not isinstance(db, Session):
            return None
        started = perf_counter()
        native_rows = tuple(
            _freeze_evidence_outcome(EvidenceOutcome(row[0], row[1], row[2]))
            for row in db.execute(
                _native_candidate_statement(
                    outcome_definition=outcome_definition,
                    config=config,
                )
            )
        )
        if lease_guard is not None:
            lease_guard()
        compatibility_candidates = tuple(
            tuple(_freeze_orm_row(item) if item is not None else None for item in row)
            for row in self._load_compatibility_replay_candidates(
                db,
                outcome_definition=outcome_definition,
            )
        )
        replay_lineage_candidates = _compatibility_lineage_candidates(compatibility_candidates)
        all_rows = native_rows + replay_lineage_candidates
        temporal_decisions = {
            prediction_id: _freeze_orm_row(decision)
            for prediction_id, decision in _temporal_decisions(db, all_rows).items()
        }
        loaded_price_bars, loaded_price_bar_revisions = _load_replay_lineage_rows(db, all_rows)
        lineage_price_bars = {
            row_id: _freeze_orm_row(row) for row_id, row in loaded_price_bars.items()
        }
        lineage_price_bar_revisions = {
            row_id: _freeze_orm_row(row) for row_id, row in loaded_price_bar_revisions.items()
        }
        if lease_guard is not None:
            lease_guard()
        universe = RunEvidenceCandidateUniverse(
            outcome_definition_id=int(outcome_definition.id),
            config_hash=config.config_hash,
            feature_schema_version=config.feature_schema.version,
            calculation_version=config.engine.calculation_version,
            native_rows=native_rows,
            compatibility_candidates=compatibility_candidates,
            temporal_decisions=temporal_decisions,
            lineage_price_bars=lineage_price_bars,
            lineage_price_bar_revisions=lineage_price_bar_revisions,
            load_seconds=max(0.0, perf_counter() - started),
        )
        self._run_candidate_universe = universe
        self._global_funnel_cache.clear()
        logger.info("winner.evidence_run_universe_prepared", extra=universe.metrics())
        return universe

    def run_candidate_metrics(self) -> dict[str, int | float]:
        universe = self._run_candidate_universe
        return universe.metrics() if universe is not None else {}

    def clear_run_candidate_universe(self) -> None:
        self._run_candidate_universe = None
        self._global_funnel_cache.clear()

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

    def load_generation_evidence(
        self,
        db: Session,
        *,
        outcome_definition: WinnerOutcomeDefinition,
        training_cutoff_at: datetime,
        config: WinnerProbabilityConfig,
        watermark: dict[str, int],
        progress_guard: Callable[[], None] | None = None,
    ) -> GenerationEvidenceUniverse:
        """Load and gate one frozen evidence universe for a cohort generation."""
        rows = tuple(
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
                .where(WinnerForwardOutcome.id <= int(watermark.get("forward_revision_id") or 0))
                .where(
                    WinnerTargetStopOutcome.id <= int(watermark.get("target_stop_revision_id") or 0)
                )
                .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition.id)
                .order_by(
                    WinnerPredictionSnapshot.prediction_as_of_date,
                    WinnerPredictionSnapshot.id,
                    WinnerForwardOutcome.revision,
                    WinnerTargetStopOutcome.revision,
                )
            )
        )
        replay_rows = tuple(
            row
            for row in self._load_compatibility_replays(
                db,
                training_cutoff_at=training_cutoff_at,
                outcome_definition=outcome_definition,
            )
            if int(row.eligibility_decision_id or 0)
            <= int(watermark.get("eligibility_decision_id") or 0)
            and int(row.outcome_replay_id or 0) <= int(watermark.get("training_replay_id") or 0)
        )
        candidates = rows + replay_rows
        temporal_decisions = _temporal_decisions(
            db,
            candidates,
            max_id=int(watermark.get("temporal_validity_decision_id") or 0),
        )
        lineage_price_bars, lineage_price_bar_revisions = _load_replay_lineage_rows(db, candidates)
        if progress_guard is not None:
            progress_guard()
        completed_session = latest_completed_session(training_cutoff_at)
        rolling_start = _subtract_years(
            training_cutoff_at.date(), config.cohort.rolling_window_years
        )
        stages: list[EvidenceFunnelStage] = []

        def apply(name: str, predicate: Callable[[EvidenceOutcome], bool]) -> None:
            nonlocal candidates
            before = len(candidates)
            candidates = tuple(row for row in candidates if predicate(row))
            stages.append(EvidenceFunnelStage(name, before, len(candidates)))
            if progress_guard is not None:
                progress_guard()

        apply(
            "historical_predictions_before_cutoff",
            lambda row: (
                row.prediction.source_data_cutoff_at < training_cutoff_at
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
        apply(
            "temporal_execution_and_lineage_eligible",
            lambda row: prediction_temporally_eligible(
                row.prediction, temporal_decisions.get(int(row.prediction.id))
            ),
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
            lambda row: row.prediction.feature_schema_version == config.feature_schema.version,
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
                    == outcome_definition.config_hash
                    == config.config_hash
                )
            ),
        )
        apply(
            "outcome_definition_compatible",
            lambda _row: (
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
            "replay_lineage_reproducible",
            lambda row: (
                row.evidence_origin != EVIDENCE_ORIGIN_PRE11
                or _replay_lineage_is_reproducible(
                    row.target_stop_outcome,
                    row.forward_outcome,
                    price_bars=lineage_price_bars,
                    price_bar_revisions=lineage_price_bar_revisions,
                )
            ),
        )
        apply(
            "no_revised_after_cutoff_leakage",
            lambda row: (
                _source_revision_cutoff(row) is not None
                and _source_revision_cutoff(row) <= training_cutoff_at
            ),
        )
        apply("independent_episode", lambda row: not _is_dependent(row.prediction))
        before = len(candidates)
        candidates = _one_per_episode(candidates)
        stages.append(
            EvidenceFunnelStage("one_representative_per_episode", before, len(candidates))
        )
        candidates = _attach_temporal_decision_ids(candidates, temporal_decisions)
        frozen = tuple(_freeze_generation_member(row) for row in candidates)
        # No ORM-backed evidence escapes this method. The domain session may
        # now commit heartbeats without expiring data used by later groups.
        candidates = ()
        if progress_guard is not None:
            progress_guard()
        return GenerationEvidenceUniverse(evidence=frozen, stages=tuple(stages))

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
        evaluation_started = perf_counter()
        universe = self._compatible_run_universe(outcome_definition, config)
        cache_key = None
        if universe is None and cohort_key.dimensions == {"global": "all"}:
            temporal_watermark = (
                db.scalar(select(func.max(WinnerTemporalValidityDecision.id)))
                if hasattr(db, "get_bind")
                else 0
            )
            cache_key = (
                id(db),
                outcome_definition.id,
                training_cutoff_at,
                (None if prediction.source_data_cutoff_at == training_cutoff_at else prediction.id),
                prediction.feature_schema_version,
                prediction.calculation_version,
                prediction.config_hash,
                config.config_hash,
                int(temporal_watermark or 0),
            )
            cached = self._global_funnel_cache.get(cache_key)
            if cached is not None:
                return cached
        if universe is None:
            native_rows = tuple(
                EvidenceOutcome(row[0], row[1], row[2])
                for row in db.execute(_native_candidate_statement())
            )
            replay_rows = self._load_compatibility_replays(
                db,
                training_cutoff_at=training_cutoff_at,
                outcome_definition=outcome_definition,
            )
        else:
            universe.reuse_count += 1
            native_rows = universe.native_rows
            replay_rows = _select_latest_compatibility_replays(
                list(universe.compatibility_candidates), training_cutoff_at
            )
        rows = native_rows + replay_rows
        temporal_decisions = (
            universe.temporal_decisions if universe is not None else _temporal_decisions(db, rows)
        )
        lineage_price_bars, lineage_price_bar_revisions = (
            (universe.lineage_price_bars, universe.lineage_price_bar_revisions)
            if universe is not None
            else _load_replay_lineage_rows(db, rows)
        )
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
        apply(
            "temporal_execution_and_lineage_eligible",
            lambda row: prediction_temporally_eligible(
                row.prediction, temporal_decisions.get(int(row.prediction.id))
            ),
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
            "replay_lineage_reproducible",
            lambda row: (
                row.evidence_origin != EVIDENCE_ORIGIN_PRE11
                or _replay_lineage_is_reproducible(
                    row.target_stop_outcome,
                    row.forward_outcome,
                    price_bars=lineage_price_bars,
                    price_bar_revisions=lineage_price_bar_revisions,
                )
            ),
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
        rows = _attach_temporal_decision_ids(rows, temporal_decisions)
        stages.append(EvidenceFunnelStage("one_representative_per_episode", before, len(rows)))
        result = EvidenceDiagnosticFunnel(tuple(stages), rows)
        if universe is not None:
            universe.evaluation_count += 1
            universe.filtered_evidence_rows += len(rows)
            universe.python_filter_seconds += max(0.0, perf_counter() - evaluation_started)
            logger.info(
                "winner.evidence_prediction_evaluated",
                extra={
                    "prediction_id": int(prediction.id),
                    "ticker": prediction.ticker,
                    "training_cutoff_at": training_cutoff_at.isoformat(),
                    "candidate_rows": len(native_rows) + len(replay_rows),
                    "filtered_evidence_rows": len(rows),
                    "elapsed_seconds": round(perf_counter() - evaluation_started, 6),
                    "cache_reuse_count": universe.reuse_count,
                },
            )
        if cache_key is not None:
            self._global_funnel_cache[cache_key] = result
        return result

    def _compatible_run_universe(
        self,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
    ) -> RunEvidenceCandidateUniverse | None:
        universe = self._run_candidate_universe
        if universe is None:
            return None
        if (
            universe.outcome_definition_id != int(outcome_definition.id)
            or universe.config_hash != config.config_hash
            or universe.feature_schema_version != config.feature_schema.version
            or universe.calculation_version != config.engine.calculation_version
        ):
            return None
        return universe

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
            EvidenceService._load_compatibility_replay_candidates(
                db,
                outcome_definition=outcome_definition,
                classified_before=training_cutoff_at,
            )
        )
        return _select_latest_compatibility_replays(raw, training_cutoff_at)

    @staticmethod
    def _load_compatibility_replay_candidates(
        db: Session,
        *,
        outcome_definition: WinnerOutcomeDefinition,
        classified_before: datetime | None = None,
    ) -> tuple[tuple[Any, Any, Any, Any], ...]:
        statement = (
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
                WinnerForwardOutcome.id == WinnerTrainingOutcomeReplay.source_forward_outcome_id,
            )
            .where(WinnerTrainingEligibilityDecision.training_family == TRAINING_FAMILY)
            .where(
                WinnerTrainingEligibilityDecision.target_outcome_definition_id
                == outcome_definition.id
            )
        )
        if classified_before is not None:
            statement = statement.where(
                WinnerTrainingEligibilityDecision.classified_at < classified_before
            )
        statement = statement.order_by(
            WinnerPredictionSnapshot.prediction_as_of_date,
            WinnerPredictionSnapshot.id,
            WinnerTrainingEligibilityDecision.revision.desc(),
            WinnerTrainingOutcomeReplay.revision.desc(),
        )
        return tuple(db.execute(statement))


def _native_candidate_statement(
    *,
    outcome_definition: WinnerOutcomeDefinition | None = None,
    config: WinnerProbabilityConfig | None = None,
):
    statement = (
        select(WinnerPredictionSnapshot, WinnerForwardOutcome, WinnerTargetStopOutcome)
        .join(
            WinnerForwardOutcome,
            WinnerForwardOutcome.prediction_id == WinnerPredictionSnapshot.id,
        )
        .join(
            WinnerTargetStopOutcome,
            WinnerTargetStopOutcome.forward_outcome_id == WinnerForwardOutcome.id,
        )
    )
    if outcome_definition is not None and config is not None:
        # These predicates are invariant across every exact decision cutoff.
        # Revision visibility, maturation timestamps, temporal ledgers, rolling
        # windows, lineage and episode selection deliberately remain in the
        # per-prediction evaluator below.
        statement = (
            statement.where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition.id)
            .where(WinnerForwardOutcome.entry_model == outcome_definition.entry_model)
            .where(WinnerForwardOutcome.horizon_sessions == outcome_definition.horizon_sessions)
            .where(WinnerTargetStopOutcome.entry_model == outcome_definition.entry_model)
            .where(WinnerTargetStopOutcome.horizon_sessions == outcome_definition.horizon_sessions)
            .where(WinnerTargetStopOutcome.target_pct == outcome_definition.target_pct)
            .where(WinnerTargetStopOutcome.stop_pct == outcome_definition.stop_pct)
            .where(WinnerTargetStopOutcome.primary_winner.is_not(None))
            .where(WinnerForwardOutcome.status == OutcomeStatus.MATURED)
            .where(WinnerTargetStopOutcome.status == OutcomeStatus.MATURED)
            .where(WinnerPredictionSnapshot.eligibility_status == PredictionEligibility.ELIGIBLE)
            .where(WinnerPredictionSnapshot.reconstruction_method.is_(None))
            .where(WinnerPredictionSnapshot.feature_schema_version == config.feature_schema.version)
            .where(
                WinnerPredictionSnapshot.calculation_version == config.engine.calculation_version
            )
            .where(WinnerPredictionSnapshot.config_hash == config.config_hash)
        )
    return statement.order_by(
        WinnerPredictionSnapshot.prediction_as_of_date,
        WinnerPredictionSnapshot.id,
        WinnerForwardOutcome.revision,
        WinnerTargetStopOutcome.revision,
    )


def _compatibility_lineage_candidates(
    raw: tuple[tuple[Any, Any, Any, Any], ...],
) -> tuple[EvidenceOutcome, ...]:
    return tuple(
        EvidenceOutcome(
            prediction=prediction,
            forward_outcome=forward,
            target_stop_outcome=replay,
            eligibility_decision_id=decision.id,
            outcome_replay_id=replay.id,
            evidence_origin=EVIDENCE_ORIGIN_PRE11,
        )
        for prediction, forward, replay, decision in raw
        if replay is not None and forward is not None
    )


def _freeze_evidence_outcome(row: EvidenceOutcome) -> EvidenceOutcome:
    return replace(
        row,
        prediction=_freeze_orm_row(row.prediction),
        forward_outcome=_freeze_orm_row(row.forward_outcome),
        target_stop_outcome=_freeze_orm_row(row.target_stop_outcome),
    )


def _freeze_orm_row(row: Any) -> FrozenORMRow:
    """Copy every mapped column while the ORM instance is still session-bound."""
    if isinstance(row, FrozenORMRow):
        return row
    mapper = sa_inspect(type(row))
    return FrozenORMRow(
        model_name=type(row).__name__,
        column_values=MappingProxyType(
            {
                attribute.key: deepcopy(getattr(row, attribute.key))
                for attribute in mapper.column_attrs
            }
        ),
    )


def _visible_at_cutoff(superseded_at: datetime | None, cutoff: datetime) -> bool:
    return superseded_at is None or superseded_at >= cutoff


def _select_latest_compatibility_replays(
    raw: list[tuple], training_cutoff_at: datetime
) -> tuple[EvidenceOutcome, ...]:
    """Select the latest visible decision first, including a later rejection."""
    selected: list[EvidenceOutcome] = []
    seen_predictions: set[int] = set()
    for prediction, forward, replay, decision in raw:
        classified_at = getattr(decision, "classified_at", None)
        if classified_at is not None and classified_at >= training_cutoff_at:
            continue
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


def _load_replay_lineage_rows(
    db: Session,
    rows: tuple[EvidenceOutcome, ...],
) -> tuple[dict[int, PriceBar], dict[int, PriceBarRevision]]:
    """Load every replay lineage identity in two bounded set queries."""
    if not isinstance(db, Session):
        return {}, {}
    bar_ids: set[int] = set()
    revision_ids: set[int] = set()
    for row in rows:
        if row.evidence_origin != EVIDENCE_ORIGIN_PRE11:
            continue
        lineage = row.target_stop_outcome.bar_lineage_json or {}
        for item in lineage.get("bars") or ():
            if not isinstance(item, dict):
                continue
            if item.get("price_bar_id") is not None:
                bar_ids.add(int(item["price_bar_id"]))
            if item.get("price_bar_revision_id") is not None:
                revision_ids.add(int(item["price_bar_revision_id"]))
    price_bars = (
        {row.id: row for row in db.scalars(select(PriceBar).where(PriceBar.id.in_(bar_ids)))}
        if bar_ids
        else {}
    )
    revisions = (
        {
            row.id: row
            for row in db.scalars(
                select(PriceBarRevision).where(PriceBarRevision.id.in_(revision_ids))
            )
        }
        if revision_ids
        else {}
    )
    return price_bars, revisions


def _replay_lineage_is_reproducible(
    replay: WinnerTrainingOutcomeReplay,
    forward_outcome: WinnerForwardOutcome,
    *,
    price_bars: dict[int, PriceBar],
    price_bar_revisions: dict[int, PriceBarRevision],
) -> bool:
    lineage = replay.bar_lineage_json or {}
    bars = lineage.get("bars")
    if not isinstance(bars, list) or len(bars) != replay.horizon_sessions:
        return False
    if _hash({"bars": tuple(bars)}) != replay.source_bar_lineage_hash:
        return False
    if replay.source_forward_outcome_id != forward_outcome.id or int(
        lineage.get("source_forward_outcome_revision", -1)
    ) != int(forward_outcome.revision):
        return False
    for item in bars:
        if not isinstance(item, dict):
            return False
        bar_id = item.get("price_bar_id")
        expected_hash = item.get("data_hash")
        if not bar_id or not expected_hash:
            return False
        revision_id = item.get("price_bar_revision_id")
        if revision_id is not None:
            revision = price_bar_revisions.get(int(revision_id))
            if (
                revision is None
                or revision.price_bar_id != bar_id
                or revision.new_data_hash != expected_hash
                or revision.revision_number != item.get("revision_count")
                or revision.observed_at > replay.source_revision_cutoff_at
            ):
                return False
            continue
        bar = price_bars.get(int(bar_id))
        if (
            bar is None
            or bar.data_hash != expected_hash
            or bar.revision_count != item.get("revision_count")
        ):
            return False
    return True


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


def _freeze_generation_member(row: EvidenceOutcome) -> FrozenEvidenceMember:
    return FrozenEvidenceMember(
        prediction=FrozenEvidencePrediction(
            id=int(row.prediction.id),
            episode_id=(
                int(row.prediction.episode_id) if row.prediction.episode_id is not None else None
            ),
            prediction_as_of_date=row.prediction.prediction_as_of_date,
            feature_json=dict(row.prediction.feature_json or {}),
        ),
        forward_outcome=FrozenEvidenceForwardOutcome(
            id=int(row.forward_outcome.id),
            revision=int(row.forward_outcome.revision),
            close_return_pct=row.forward_outcome.close_return_pct,
            mfe_pct=row.forward_outcome.mfe_pct,
            mae_pct=row.forward_outcome.mae_pct,
        ),
        target_stop_outcome=FrozenEvidenceTargetStopOutcome(
            id=int(row.target_stop_outcome.id),
            revision=int(row.target_stop_outcome.revision),
            primary_winner=bool(row.target_stop_outcome.primary_winner),
            first_event=row.target_stop_outcome.first_event,
        ),
        inclusion_weight=Decimal(str(row.inclusion_weight)),
        eligibility_decision_id=row.eligibility_decision_id,
        temporal_validity_decision_id=row.temporal_validity_decision_id,
        outcome_replay_id=row.outcome_replay_id,
        evidence_origin=row.evidence_origin,
    )


def _temporal_decisions(
    db: Session,
    rows: tuple[EvidenceOutcome, ...],
    *,
    max_id: int | None = None,
) -> dict[int, Any]:
    # Lightweight unit fakes intentionally do not emulate the temporal ledger;
    # PostgreSQL and real SQLAlchemy sessions always take the batched path.
    if not hasattr(db, "get_bind"):
        return {}
    return load_current_temporal_decisions(
        db,
        {int(row.prediction.id) for row in rows},
        max_id=max_id,
    )


def _attach_temporal_decision_ids(
    rows: tuple[EvidenceOutcome, ...],
    decisions: dict[int, Any],
) -> tuple[EvidenceOutcome, ...]:
    return tuple(
        replace(
            row,
            temporal_validity_decision_id=getattr(
                decisions.get(int(row.prediction.id)), "id", None
            ),
        )
        for row in rows
    )
