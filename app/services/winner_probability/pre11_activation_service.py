"""Controlled L5 activation for the reviewed pre-1.1 compatibility scope."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    EstimateKind,
    EstimateSource,
    WinnerCohortStatistic,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerTrainingEligibilityDecision,
)
from app.services.winner_probability.cohort_definition import (
    COHORT_BASELINE_SOURCE_VERSION,
    CohortDefinitionService,
)
from app.services.winner_probability.cohort_statistics import CohortStatisticsService
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.services.winner_probability.estimate_lifecycle import published_lifecycle_fields
from app.services.winner_probability.evidence_manifest_service import EvidenceManifestService
from app.services.winner_probability.evidence_service import EvidenceOutcome, EvidenceService
from app.services.winner_probability.pre11_compatibility_service import (
    EVIDENCE_ORIGIN_PRE11,
    POLICY_VERSION,
    TRAINING_FAMILY,
    _hash,
)
from app.services.winner_probability.probability_estimator import (
    ProbabilityEstimator,
    _estimate_metadata,
    _evidence_composition,
)


@dataclass(frozen=True)
class Pre11L5ActivationResult:
    cohort_statistic: WinnerCohortStatistic
    estimate: WinnerProbabilityEstimate
    evidence: tuple[EvidenceOutcome, ...]
    reviewed_manifest_hash: str
    evidence_manifest_hash: str


class Pre11L5ActivationService:
    """Fail-closed activation of one exact reviewed L5 membership set."""

    def __init__(
        self,
        *,
        evidence_service: EvidenceService | None = None,
        definition_service: CohortDefinitionService | None = None,
        statistics_service: CohortStatisticsService | None = None,
        manifest_service: EvidenceManifestService | None = None,
    ) -> None:
        self.evidence_service = evidence_service or EvidenceService()
        self.definition_service = definition_service or CohortDefinitionService()
        self.statistics_service = statistics_service or CohortStatisticsService()
        self.manifest_service = manifest_service or EvidenceManifestService()

    def activate(
        self,
        db: Session,
        *,
        prediction: WinnerPredictionSnapshot,
        outcome_definition: WinnerOutcomeDefinition,
        training_cutoff_at: datetime,
        config: WinnerProbabilityConfig,
        request_key: str,
        expected_reviewed_manifest_hash: str,
        actor: str,
        approve_write: bool,
    ) -> Pre11L5ActivationResult:
        if not approve_write:
            raise PermissionError("explicit approve_write=True is required")
        if not actor.strip():
            raise ValueError("actor is required")
        if training_cutoff_at.tzinfo is None:
            raise ValueError("training_cutoff_at must be timezone-aware")
        if not outcome_definition.is_active:
            raise ValueError("outcome definition must be active")

        keys = self.definition_service.cohort_keys_for_prediction(prediction, config)
        l5_key = next((key for key in keys if key.level == "L5"), None)
        if l5_key is None or l5_key.dimensions != {"global": "all"}:
            raise ValueError("active configuration does not define a global L5 cohort")
        evidence = self.evidence_service.load_evidence(
            db,
            prediction=prediction,
            outcome_definition=outcome_definition,
            cohort_key=l5_key,
            training_cutoff_at=training_cutoff_at,
            config=config,
        )
        reviewed_hash = self.reviewed_manifest_hash(
            db, evidence=evidence, request_key=request_key
        )
        if reviewed_hash != expected_reviewed_manifest_hash:
            raise ValueError("persisted eligible-member hash differs from the reviewed set")
        statistics = self.statistics_service.calculate(evidence, config)
        if (
            statistics.sample_n != 390
            or statistics.effective_n != Decimal("390.000000")
            or statistics.wins != Decimal("145.000000")
        ):
            raise ValueError("persisted L5 statistics differ from the reviewed set")

        estimator = ProbabilityEstimator(
            cohort_definition_service=self.definition_service,
            evidence_service=self.evidence_service,
            statistics_service=self.statistics_service,
            manifest_service=self.manifest_service,
        )
        statistic = estimator._materialize_cohort_statistic(
            db,
            cohort_key=l5_key,
            outcome_definition=outcome_definition,
            training_cutoff_at=training_cutoff_at,
            evidence=evidence,
            statistics=statistics,
            config=config,
        )
        manifest = self.manifest_service.create_or_get_manifest(
            db,
            evidence=evidence,
            hash_algorithm=config.evidence_membership.manifest_hash_algorithm,
        )
        statistic.metadata_json = {
            **(statistic.metadata_json or {}),
            "training_family": TRAINING_FAMILY,
            "activation_actor": actor,
            "activation_request_key": request_key,
            "reviewed_manifest_hash": reviewed_hash,
        }
        existing = db.scalar(
            select(WinnerProbabilityEstimate)
            .where(WinnerProbabilityEstimate.prediction_id == prediction.id)
            .where(WinnerProbabilityEstimate.outcome_definition_id == outcome_definition.id)
            .where(WinnerProbabilityEstimate.estimate_kind == EstimateKind.LATEST_RESCORE)
            .where(WinnerProbabilityEstimate.source_version == COHORT_BASELINE_SOURCE_VERSION)
            .where(WinnerProbabilityEstimate.training_cutoff_at == training_cutoff_at)
        )
        if existing is None:
            estimate = WinnerProbabilityEstimate(
                **published_lifecycle_fields(),
                prediction_id=prediction.id,
                outcome_definition_id=outcome_definition.id,
                estimate_kind=EstimateKind.LATEST_RESCORE,
                source=EstimateSource.COHORT,
                source_version=COHORT_BASELINE_SOURCE_VERSION,
                cohort_definition_id=statistic.cohort_definition_id,
                model_version_id=None,
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
                metadata_json={
                    **_estimate_metadata(
                        prediction=prediction,
                        outcome_definition=outcome_definition,
                        cohort_key=l5_key,
                        statistics=statistics,
                        config=config,
                        reconstruction_method=None,
                        extra=_evidence_composition(evidence),
                    ),
                    "training_family": TRAINING_FAMILY,
                    "activation_actor": actor,
                    "activation_request_key": request_key,
                    "reviewed_manifest_hash": reviewed_hash,
                    "attempted_cohort_level": "L5",
                    "attempted_cohort_key": l5_key.key,
                },
            )
            db.add(estimate)
            db.flush()
        else:
            estimate = existing
            if (
                estimate.evidence_manifest_hash != manifest.manifest_hash
                or estimate.cohort_definition_id != statistic.cohort_definition_id
            ):
                raise ValueError("existing activation estimate has different evidence")
        self.manifest_service.persist_members(
            db,
            estimate=estimate,
            evidence=evidence,
            included_as_of=training_cutoff_at,
            inclusion_cutoff_at=training_cutoff_at,
        )
        return Pre11L5ActivationResult(
            cohort_statistic=statistic,
            estimate=estimate,
            evidence=evidence,
            reviewed_manifest_hash=reviewed_hash,
            evidence_manifest_hash=manifest.manifest_hash,
        )

    @staticmethod
    def reviewed_manifest_hash(
        db: Session,
        *,
        evidence: tuple[EvidenceOutcome, ...],
        request_key: str,
    ) -> str:
        ordered = sorted(
            evidence,
            key=lambda row: (
                row.prediction.prediction_as_of_date,
                row.prediction.source_data_cutoff_at,
                row.prediction.id,
            ),
        )
        eligible = []
        for row in ordered:
            if row.evidence_origin != EVIDENCE_ORIGIN_PRE11:
                raise ValueError("reviewed activation set contains a non-pre-1.1 member")
            decision = db.get(WinnerTrainingEligibilityDecision, row.eligibility_decision_id)
            if decision is None or not decision.training_allowed:
                raise ValueError("member eligibility decision is absent or rejected")
            eligible.append(
                {
                    "prediction_id": row.prediction.id,
                    "source_manifest_hash": decision.source_manifest_hash,
                    "bar_lineage_hash": row.target_stop_outcome.source_bar_lineage_hash,
                }
            )
        return _hash(
            {
                "scope_request_key": request_key,
                "policy_version": POLICY_VERSION,
                "eligible": eligible,
            }
        )
