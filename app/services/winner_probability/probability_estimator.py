from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    EstimateKind,
    EstimateSource,
    EvidenceGrade,
    WinnerCohortStatistic,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
)
from app.services.winner_probability.cohort_definition import (
    COHORT_BASELINE_SOURCE_VERSION,
    CohortDefinitionService,
    CohortKey,
)
from app.services.winner_probability.cohort_statistics import (
    CohortStatisticsResult,
    CohortStatisticsService,
)
from app.services.winner_probability.config import (
    ESTIMATE_KIND_DECISION_TIME,
    ESTIMATE_KIND_LATEST_RESCORE,
    WinnerProbabilityConfig,
    load_winner_probability_config,
)
from app.services.winner_probability.evidence_manifest_service import EvidenceManifestService
from app.services.winner_probability.evidence_service import EvidenceOutcome, EvidenceService
from app.services.winner_probability.model_registry import ModelRegistry
from app.services.winner_probability.pre11_compatibility_service import (
    EVIDENCE_ORIGIN_NATIVE,
    EVIDENCE_ORIGIN_PRE11,
    POLICY_VERSION,
)


@dataclass(frozen=True)
class ProbabilityEstimateResult:
    estimate: WinnerProbabilityEstimate
    status: str
    evidence: tuple[EvidenceOutcome, ...]
    selected_cohort: CohortKey | None
    statistics: CohortStatisticsResult | None


class ProbabilityEstimator:
    def __init__(
        self,
        *,
        cohort_definition_service: CohortDefinitionService | None = None,
        evidence_service: EvidenceService | None = None,
        statistics_service: CohortStatisticsService | None = None,
        manifest_service: EvidenceManifestService | None = None,
        model_registry: ModelRegistry | None = None,
    ) -> None:
        self.cohort_definition_service = cohort_definition_service or CohortDefinitionService()
        self.evidence_service = evidence_service or EvidenceService()
        self.statistics_service = statistics_service or CohortStatisticsService()
        self.manifest_service = manifest_service or EvidenceManifestService()
        self.model_registry = model_registry or ModelRegistry()

    def create_decision_time_estimate(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig | None = None,
    ) -> ProbabilityEstimateResult:
        config = config or load_winner_probability_config()
        estimate_kind = (
            EstimateKind.AS_OF_REPLAY
            if prediction.reconstruction_method
            else ESTIMATE_KIND_DECISION_TIME
        )
        return self._create_estimate(
            db,
            prediction=prediction,
            outcome_definition=outcome_definition,
            estimate_kind=estimate_kind,
            training_cutoff_at=prediction.source_data_cutoff_at,
            config=config,
            reconstruction_method=prediction.reconstruction_method,
        )

    def create_latest_rescore(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        as_of: datetime,
        config: WinnerProbabilityConfig | None = None,
        model_version_id: int | None = None,
    ) -> ProbabilityEstimateResult:
        config = config or load_winner_probability_config()
        self.model_registry.ensure_can_serve_latest_rescore(
            db,
            model_version_id=model_version_id,
        )
        return self._create_estimate(
            db,
            prediction=prediction,
            outcome_definition=outcome_definition,
            estimate_kind=ESTIMATE_KIND_LATEST_RESCORE,
            training_cutoff_at=as_of,
            config=config,
            reconstruction_method=None,
            model_version_id=model_version_id,
        )

    def _create_estimate(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        estimate_kind: str,
        training_cutoff_at: datetime,
        config: WinnerProbabilityConfig,
        reconstruction_method: str | None,
        model_version_id: int | None = None,
    ) -> ProbabilityEstimateResult:
        existing = _existing_estimate(
            db,
            prediction=prediction,
            outcome_definition=outcome_definition,
            estimate_kind=estimate_kind,
            training_cutoff_at=training_cutoff_at,
        )
        if existing is not None:
            return ProbabilityEstimateResult(
                estimate=existing,
                status="duplicate",
                evidence=(),
                selected_cohort=None,
                statistics=None,
            )

        selected = self._select_cohort(
            db,
            prediction=prediction,
            outcome_definition=outcome_definition,
            training_cutoff_at=training_cutoff_at,
            config=config,
        )
        if selected is None:
            return self._persist_insufficient(
                db,
                prediction=prediction,
                outcome_definition=outcome_definition,
                estimate_kind=estimate_kind,
                training_cutoff_at=training_cutoff_at,
                config=config,
                reconstruction_method=reconstruction_method,
                model_version_id=model_version_id,
                insufficient_reasons=("no_eligible_cohort",),
            )

        cohort_key, evidence, statistics = selected
        cohort_definition = self.cohort_definition_service.ensure_definition(
            db, cohort_key=cohort_key, outcome_definition=outcome_definition, config=config
        )
        manifest = self.manifest_service.create_or_get_manifest(
            db,
            evidence=evidence,
            hash_algorithm=config.evidence_membership.manifest_hash_algorithm,
        )
        statistic = _existing_cohort_statistic(
            db,
            cohort_definition_id=cohort_definition.id,
            outcome_definition_id=outcome_definition.id,
            training_cutoff_at=training_cutoff_at,
        )
        if statistic is None:
            statistic = WinnerCohortStatistic(
                cohort_definition_id=cohort_definition.id,
                outcome_definition_id=outcome_definition.id,
                statistic_as_of=_utcnow(),
                training_cutoff_at=training_cutoff_at,
                sample_n=statistics.sample_n,
                effective_n=statistics.effective_n,
                wins=statistics.wins,
                raw_rate=statistics.raw_rate,
                posterior_probability=statistics.posterior_probability,
                lower_bound=statistics.lower_bound,
                upper_bound=statistics.upper_bound,
                median_return_pct=statistics.median_return_pct,
                median_mfe_pct=statistics.median_mfe_pct,
                median_mae_pct=statistics.median_mae_pct,
                evidence_grade=statistics.evidence_grade,
                config_hash=config.config_hash,
                evidence_manifest_hash=manifest.manifest_hash,
                metadata_json={
                    "mean_return_pct": _str_or_none(statistics.mean_return_pct),
                    "target_first_rate": _str_or_none(statistics.target_first_rate),
                    "interval_width": str(statistics.interval_width),
                    **_evidence_composition(evidence),
                },
            )
            db.add(statistic)
            db.flush()
        elif (
            statistic.evidence_manifest_hash != manifest.manifest_hash
            or statistic.config_hash != config.config_hash
        ):
            raise ValueError(
                "cohort statistic key already exists with different evidence or configuration"
            )
        estimate = WinnerProbabilityEstimate(
            prediction_id=prediction.id,
            outcome_definition_id=outcome_definition.id,
            estimate_kind=estimate_kind,
            source=EstimateSource.COHORT,
            source_version=COHORT_BASELINE_SOURCE_VERSION,
            cohort_definition_id=cohort_definition.id,
            model_version_id=model_version_id,
            evidence_manifest_id=manifest.manifest.id,
            training_cutoff_at=training_cutoff_at,
            point_probability=statistics.posterior_probability,
            lower_bound=statistics.lower_bound,
            upper_bound=statistics.upper_bound,
            interval_width=statistics.interval_width,
            sample_n=statistics.sample_n,
            effective_n=statistics.effective_n,
            evidence_grade=statistics.evidence_grade,
            insufficient_reasons_json=[],
            expected_return_pct=statistics.mean_return_pct,
            median_return_pct=statistics.median_return_pct,
            median_mfe_pct=statistics.median_mfe_pct,
            median_mae_pct=statistics.median_mae_pct,
            target_first_rate=statistics.target_first_rate,
            config_hash=config.config_hash,
            feature_schema_version=config.feature_schema.version,
            evidence_manifest_hash=manifest.manifest_hash,
            metadata_json=_estimate_metadata(
                prediction=prediction,
                outcome_definition=outcome_definition,
                cohort_key=cohort_key,
                statistics=statistics,
                config=config,
                reconstruction_method=reconstruction_method,
                extra=_evidence_composition(evidence),
            ),
        )
        db.add(estimate)
        db.flush()
        self.manifest_service.persist_members(
            db,
            estimate=estimate,
            evidence=evidence,
            included_as_of=_utcnow(),
            inclusion_cutoff_at=training_cutoff_at,
        )
        return ProbabilityEstimateResult(
            estimate=estimate,
            status="estimated",
            evidence=evidence,
            selected_cohort=cohort_key,
            statistics=statistics,
        )

    def _select_cohort(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        training_cutoff_at: datetime,
        config: WinnerProbabilityConfig,
    ) -> tuple[CohortKey, tuple[EvidenceOutcome, ...], CohortStatisticsResult] | None:
        keys = self.cohort_definition_service.cohort_keys_for_prediction(prediction, config)
        if not keys:
            return None
        broadest_key = keys[-1]
        broadest_evidence = self.evidence_service.load_evidence(
            db,
            prediction=prediction,
            outcome_definition=outcome_definition,
            cohort_key=broadest_key,
            training_cutoff_at=training_cutoff_at,
            config=config,
        )
        calculated: dict[
            str, tuple[CohortKey, tuple[EvidenceOutcome, ...], CohortStatisticsResult]
        ] = {}
        # Materialize the global baseline first, then work toward specificity.
        for _level_config, cohort_key in reversed(
            tuple(zip(config.cohort.hierarchy, keys, strict=True))
        ):
            filter_for_cohort = getattr(self.evidence_service, "filter_for_cohort", None)
            evidence = (
                filter_for_cohort(broadest_evidence, cohort_key)
                if callable(filter_for_cohort)
                else self.evidence_service.load_evidence(
                    db,
                    prediction=prediction,
                    outcome_definition=outcome_definition,
                    cohort_key=cohort_key,
                    training_cutoff_at=training_cutoff_at,
                    config=config,
                )
            )
            statistics = self.statistics_service.calculate(evidence, config)
            self._materialize_cohort_statistic(
                db,
                cohort_key=cohort_key,
                outcome_definition=outcome_definition,
                training_cutoff_at=training_cutoff_at,
                evidence=evidence,
                statistics=statistics,
                config=config,
            )
            calculated[cohort_key.level] = (cohort_key, evidence, statistics)

        for level_config in config.cohort.hierarchy:
            cohort_key, evidence, statistics = calculated[level_config.level]
            if (
                statistics.effective_n >= Decimal(level_config.min_effective_n)
                and statistics.interval_width <= Decimal(str(config.cohort.max_interval_width))
                and statistics.evidence_grade != EvidenceGrade.INSUFFICIENT
            ):
                return cohort_key, evidence, statistics
        return None

    def _materialize_cohort_statistic(
        self,
        db: Session,
        *,
        cohort_key: CohortKey,
        outcome_definition: WinnerOutcomeDefinition,
        training_cutoff_at: datetime,
        evidence: tuple[EvidenceOutcome, ...],
        statistics: CohortStatisticsResult,
        config: WinnerProbabilityConfig,
    ) -> WinnerCohortStatistic:
        definition = self.cohort_definition_service.ensure_definition(
            db, cohort_key=cohort_key, outcome_definition=outcome_definition, config=config
        )
        manifest = self.manifest_service.create_or_get_manifest(
            db,
            evidence=evidence,
            hash_algorithm=config.evidence_membership.manifest_hash_algorithm,
        )
        existing = _existing_cohort_statistic(
            db,
            cohort_definition_id=definition.id,
            outcome_definition_id=outcome_definition.id,
            training_cutoff_at=training_cutoff_at,
        )
        if existing is not None:
            if (
                existing.evidence_manifest_hash != manifest.manifest_hash
                or existing.config_hash != config.config_hash
            ):
                raise ValueError(
                    "cohort statistic key already exists with different evidence or configuration"
                )
            return existing
        row = WinnerCohortStatistic(
            cohort_definition_id=definition.id,
            outcome_definition_id=outcome_definition.id,
            statistic_as_of=_utcnow(),
            training_cutoff_at=training_cutoff_at,
            sample_n=statistics.sample_n,
            effective_n=statistics.effective_n,
            wins=statistics.wins,
            raw_rate=statistics.raw_rate,
            posterior_probability=statistics.posterior_probability,
            lower_bound=statistics.lower_bound,
            upper_bound=statistics.upper_bound,
            median_return_pct=statistics.median_return_pct,
            median_mfe_pct=statistics.median_mfe_pct,
            median_mae_pct=statistics.median_mae_pct,
            evidence_grade=statistics.evidence_grade,
            config_hash=config.config_hash,
            evidence_manifest_hash=manifest.manifest_hash,
            metadata_json={
                "mean_return_pct": _str_or_none(statistics.mean_return_pct),
                "target_first_rate": _str_or_none(statistics.target_first_rate),
                "interval_width": str(statistics.interval_width),
                "materialization_order": "L5_TO_L0",
                **_evidence_composition(evidence),
            },
        )
        db.add(row)
        db.flush()
        return row

    def _persist_insufficient(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        estimate_kind: str,
        training_cutoff_at: datetime,
        config: WinnerProbabilityConfig,
        reconstruction_method: str | None,
        model_version_id: int | None = None,
        insufficient_reasons: tuple[str, ...],
    ) -> ProbabilityEstimateResult:
        keys = self.cohort_definition_service.cohort_keys_for_prediction(prediction, config)
        broadest_key = keys[-1] if keys else None
        funnel = (
            self.evidence_service.diagnostic_funnel(
                db,
                prediction=prediction,
                outcome_definition=outcome_definition,
                cohort_key=broadest_key,
                training_cutoff_at=training_cutoff_at,
                config=config,
            )
            if broadest_key is not None
            else None
        )
        evidence = funnel.evidence if funnel is not None else ()
        statistics = self.statistics_service.calculate(evidence, config)
        manifest = self.manifest_service.create_or_get_manifest(
            db,
            evidence=evidence,
            hash_algorithm=config.evidence_membership.manifest_hash_algorithm,
        )
        estimate = WinnerProbabilityEstimate(
            prediction_id=prediction.id,
            outcome_definition_id=outcome_definition.id,
            estimate_kind=estimate_kind,
            source=EstimateSource.INSUFFICIENT,
            source_version=COHORT_BASELINE_SOURCE_VERSION,
            model_version_id=model_version_id,
            evidence_manifest_id=manifest.manifest.id,
            training_cutoff_at=training_cutoff_at,
            point_probability=None,
            lower_bound=None,
            upper_bound=None,
            interval_width=None,
            sample_n=statistics.sample_n,
            effective_n=statistics.effective_n,
            evidence_grade=EvidenceGrade.INSUFFICIENT,
            insufficient_reasons_json=list(insufficient_reasons),
            expected_return_pct=statistics.mean_return_pct,
            median_return_pct=statistics.median_return_pct,
            median_mfe_pct=statistics.median_mfe_pct,
            median_mae_pct=statistics.median_mae_pct,
            target_first_rate=statistics.target_first_rate,
            config_hash=config.config_hash,
            feature_schema_version=config.feature_schema.version,
            evidence_manifest_hash=manifest.manifest_hash,
            metadata_json=_estimate_metadata(
                prediction=prediction,
                outcome_definition=outcome_definition,
                cohort_key=None,
                statistics=statistics,
                config=config,
                reconstruction_method=reconstruction_method,
                extra={
                    "attempted_cohort_level": (
                        broadest_key.level if broadest_key is not None else None
                    ),
                    "attempted_cohort_key": broadest_key.key if broadest_key is not None else None,
                    "cold_start_raw_counts_visible": True,
                    "evidence_funnel": funnel.counts() if funnel is not None else {},
                    "first_zero_stage": _first_zero_stage(funnel),
                    **_evidence_composition(evidence),
                },
            ),
        )
        db.add(estimate)
        db.flush()
        self.manifest_service.persist_members(
            db,
            estimate=estimate,
            evidence=evidence,
            included_as_of=_utcnow(),
            inclusion_cutoff_at=training_cutoff_at,
        )
        return ProbabilityEstimateResult(
            estimate=estimate,
            status="insufficient",
            evidence=evidence,
            selected_cohort=None,
            statistics=statistics,
        )


def _existing_estimate(
    db: Session,
    *,
    prediction: WinnerPredictionSnapshot,
    outcome_definition: WinnerOutcomeDefinition,
    estimate_kind: str,
    training_cutoff_at: datetime,
) -> WinnerProbabilityEstimate | None:
    getter = getattr(db, "get_existing_probability_estimate", None)
    if callable(getter):
        return getter(
            prediction_id=prediction.id,
            outcome_definition_id=outcome_definition.id,
            estimate_kind=estimate_kind,
            source_version=COHORT_BASELINE_SOURCE_VERSION,
            training_cutoff_at=training_cutoff_at,
        )
    return db.scalar(
        select(WinnerProbabilityEstimate)
        .where(WinnerProbabilityEstimate.prediction_id == prediction.id)
        .where(WinnerProbabilityEstimate.outcome_definition_id == outcome_definition.id)
        .where(WinnerProbabilityEstimate.estimate_kind == estimate_kind)
        .where(WinnerProbabilityEstimate.source_version == COHORT_BASELINE_SOURCE_VERSION)
        .where(WinnerProbabilityEstimate.training_cutoff_at == training_cutoff_at)
    )


def _existing_cohort_statistic(
    db: Session,
    *,
    cohort_definition_id: int,
    outcome_definition_id: int,
    training_cutoff_at: datetime,
) -> WinnerCohortStatistic | None:
    getter = getattr(db, "get_existing_cohort_statistic", None)
    if callable(getter):
        return getter(
            cohort_definition_id=cohort_definition_id,
            outcome_definition_id=outcome_definition_id,
            training_cutoff_at=training_cutoff_at,
        )
    return db.scalar(
        select(WinnerCohortStatistic)
        .where(WinnerCohortStatistic.cohort_definition_id == cohort_definition_id)
        .where(WinnerCohortStatistic.outcome_definition_id == outcome_definition_id)
        .where(WinnerCohortStatistic.training_cutoff_at == training_cutoff_at)
    )


def _estimate_metadata(
    *,
    prediction: WinnerPredictionSnapshot,
    outcome_definition: WinnerOutcomeDefinition,
    cohort_key: CohortKey | None,
    statistics: CohortStatisticsResult,
    config: WinnerProbabilityConfig,
    reconstruction_method: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "feature_vector_hash": prediction.feature_vector_hash,
        "outcome_definition": outcome_definition.definition_id,
        "outcome_definition_id": outcome_definition.id,
        "cohort_level": cohort_key.level if cohort_key is not None else None,
        "cohort_key": cohort_key.key if cohort_key is not None else None,
        "selected_cohort_level": cohort_key.level if cohort_key is not None else None,
        "selected_cohort_key": cohort_key.key if cohort_key is not None else None,
        "prior_strength": config.cohort.prior_strength,
        "prior_probability": config.cohort.prior_probability,
        "calculation_version": config.engine.calculation_version,
        "source_version": COHORT_BASELINE_SOURCE_VERSION,
        "raw_rate": _str_or_none(statistics.raw_rate),
        "wins": str(statistics.wins),
        "prior_alpha": str(
            Decimal(str(config.cohort.prior_strength))
            * Decimal(str(config.cohort.prior_probability))
        ),
        "prior_beta": str(
            Decimal(str(config.cohort.prior_strength))
            * (Decimal("1") - Decimal(str(config.cohort.prior_probability)))
        ),
        "posterior_alpha": str(
            statistics.wins
            + Decimal(str(config.cohort.prior_strength))
            * Decimal(str(config.cohort.prior_probability))
        ),
        "posterior_beta": str(
            statistics.effective_n
            - statistics.wins
            + Decimal(str(config.cohort.prior_strength))
            * (Decimal("1") - Decimal(str(config.cohort.prior_probability)))
        ),
        "reconstruction_method": reconstruction_method,
        **(extra or {}),
    }


def _str_or_none(value) -> str | None:
    return str(value) if value is not None else None


def _evidence_composition(evidence: tuple[EvidenceOutcome, ...]) -> dict[str, Any]:
    dates = [row.prediction.prediction_as_of_date for row in evidence]
    native_n = sum(row.evidence_origin == EVIDENCE_ORIGIN_NATIVE for row in evidence)
    pre11_n = sum(row.evidence_origin == EVIDENCE_ORIGIN_PRE11 for row in evidence)
    return {
        "native_1_1_n": native_n,
        "pre11_compatible_n": pre11_n,
        "reconstructed_label_n": pre11_n,
        "compatibility_policy_version": POLICY_VERSION if pre11_n else None,
        "oldest_evidence_date": min(dates).isoformat() if dates else None,
        "newest_evidence_date": max(dates).isoformat() if dates else None,
    }


def _first_zero_stage(funnel) -> str | None:
    if funnel is None:
        return None
    return next((stage.predicate for stage in funnel.stages if stage.after_count == 0), None)


def _utcnow() -> datetime:
    return datetime.now(UTC)
