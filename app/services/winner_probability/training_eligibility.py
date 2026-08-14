from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.tables import PredictionEligibility, WinnerPredictionSnapshot


class TrainingRejectionReason(StrEnum):
    RECONSTRUCTED_HISTORY = "RECONSTRUCTED_HISTORY"
    POINT_IN_TIME_NOT_VALIDATED = "POINT_IN_TIME_NOT_VALIDATED"
    PREDICTION_NOT_ELIGIBLE = "PREDICTION_NOT_ELIGIBLE"
    DEPENDENT_EPISODE = "DEPENDENT_EPISODE"
    SOURCE_QUALITY_BLOCKED = "SOURCE_QUALITY_BLOCKED"
    LEGACY_ELIGIBILITY_UNCLASSIFIED = "LEGACY_ELIGIBILITY_UNCLASSIFIED"
    FEATURE_SCHEMA_MISMATCH = "FEATURE_SCHEMA_MISMATCH"
    CALCULATION_VERSION_MISMATCH = "CALCULATION_VERSION_MISMATCH"
    CONFIG_MISMATCH = "CONFIG_MISMATCH"
    OUTCOME_DEFINITION_MISMATCH = "OUTCOME_DEFINITION_MISMATCH"
    OUTCOME_NOT_CURRENT_AT_CUTOFF = "OUTCOME_NOT_CURRENT_AT_CUTOFF"
    OUTCOME_REVISED_AFTER_CUTOFF = "OUTCOME_REVISED_AFTER_CUTOFF"
    QUALITY_GATE_BLOCKED = "QUALITY_GATE_BLOCKED"
    OUTSIDE_ROLLING_WINDOW = "OUTSIDE_ROLLING_WINDOW"


BLOCKING_SOURCE_QUALITY_FLAGS = frozenset(
    {
        "exclude_from_production_training",
        "untrusted_point_in_time_source",
        "reconstructed_history",
        "point_in_time_invalid",
        "quality_blocking",
    }
)


@dataclass(frozen=True)
class TrainingEligibilityDecision:
    capture_training_candidate: bool
    evidence_training_eligible: bool
    rejection_reasons: tuple[str, ...]


class TrainingEligibilityPolicy:
    """Single authority for persisted capture and production-evidence eligibility."""

    def evaluate_capture(
        self,
        prediction: WinnerPredictionSnapshot,
        *,
        explicit_legacy_override: bool | None = None,
    ) -> TrainingEligibilityDecision:
        reasons: list[str] = []
        lineage = prediction.lineage_json or {}
        source_flags = {str(value) for value in lineage.get("source_quality_flags", [])}
        if prediction.reconstruction_method is not None:
            reasons.append(TrainingRejectionReason.RECONSTRUCTED_HISTORY)
        if lineage.get("point_in_time_validated") is not True:
            reasons.append(TrainingRejectionReason.POINT_IN_TIME_NOT_VALIDATED)
        if prediction.eligibility_status != PredictionEligibility.ELIGIBLE:
            reasons.append(TrainingRejectionReason.PREDICTION_NOT_ELIGIBLE)
        dependent = bool(lineage.get("dependent_episode"))
        if source_flags & BLOCKING_SOURCE_QUALITY_FLAGS:
            reasons.append(TrainingRejectionReason.SOURCE_QUALITY_BLOCKED)
        if explicit_legacy_override is False:
            reasons.append(TrainingRejectionReason.SOURCE_QUALITY_BLOCKED)
        capture_reasons = tuple(dict.fromkeys(str(reason) for reason in reasons))
        evidence_reasons = list(capture_reasons)
        if dependent:
            evidence_reasons.append(str(TrainingRejectionReason.DEPENDENT_EPISODE))
        unique = tuple(dict.fromkeys(evidence_reasons))
        capture_candidate = not capture_reasons
        return TrainingEligibilityDecision(
            capture_training_candidate=capture_candidate,
            evidence_training_eligible=capture_candidate and not dependent,
            rejection_reasons=unique,
        )

    def persist_capture_decision(
        self,
        prediction: WinnerPredictionSnapshot,
        *,
        explicit_legacy_override: bool | None = None,
    ) -> TrainingEligibilityDecision:
        decision = self.evaluate_capture(
            prediction,
            explicit_legacy_override=explicit_legacy_override,
        )
        prediction.lineage_json = {
            **(prediction.lineage_json or {}),
            "training_eligibility_policy_version": "owpe-training-eligibility-1.0.0",
            "capture_training_candidate": decision.capture_training_candidate,
            "evidence_training_eligible": decision.evidence_training_eligible,
            "production_training_allowed": decision.evidence_training_eligible,
            "training_rejection_reasons": list(decision.rejection_reasons),
        }
        return decision

    def persisted_capture_decision(
        self,
        prediction: WinnerPredictionSnapshot,
    ) -> TrainingEligibilityDecision:
        lineage = prediction.lineage_json or {}
        if "capture_training_candidate" not in lineage:
            return TrainingEligibilityDecision(
                capture_training_candidate=False,
                evidence_training_eligible=False,
                rejection_reasons=(
                    str(TrainingRejectionReason.LEGACY_ELIGIBILITY_UNCLASSIFIED),
                ),
            )
        reasons = tuple(str(value) for value in lineage.get("training_rejection_reasons", []))
        capture_candidate = lineage.get("capture_training_candidate") is True
        evidence_eligible = lineage.get("evidence_training_eligible") is True
        return TrainingEligibilityDecision(capture_candidate, evidence_eligible, reasons)
