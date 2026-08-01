from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.tables import (
    WinnerEstimateEvidenceMember,
    WinnerForwardOutcome,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.evidence_manifest_service import (
    _hash_payload,
    _manifest_payload,
)
from app.services.winner_probability.evidence_service import EvidenceOutcome
from app.services.winner_probability.reproduction_service import ReproductionService


def test_reproduction_uses_exact_membership_not_current_query() -> None:
    config = load_winner_probability_config()
    evidence = tuple(_evidence(index, won=index % 2 == 0) for index in range(20))
    manifest_hash = _hash_payload(_manifest_payload(evidence))
    estimate = WinnerProbabilityEstimate(
        id=50,
        prediction_id=999,
        outcome_definition_id=1,
        estimate_kind="DECISION_TIME",
        source="COHORT",
        source_version="cohort_baseline_v1",
        training_cutoff_at=datetime(2026, 7, 1, tzinfo=UTC),
        point_probability=Decimal("0.500000"),
        sample_n=20,
        evidence_grade="Low",
        config_hash=config.config_hash,
        feature_schema_version=config.feature_schema.version,
        evidence_manifest_hash=manifest_hash,
    )
    db = ReproductionFakeDb(estimate, evidence)

    result = ReproductionService().reproduce_estimate(db, estimate_id=50, config=config)

    assert result.matches is True
    assert result.sample_n == 20
    assert result.evidence_manifest_hash == manifest_hash
    db.target_stop_outcomes[evidence[0].target_stop_outcome.id].primary_winner = False
    changed = ReproductionService().reproduce_estimate(db, estimate_id=50, config=config)
    assert changed.matches is False
    assert "evidence_manifest_hash" in changed.mismatches
    assert "point_probability" in changed.mismatches


class ReproductionFakeDb:
    def __init__(
        self,
        estimate: WinnerProbabilityEstimate,
        evidence: tuple[EvidenceOutcome, ...],
    ) -> None:
        self.estimate = estimate
        self.members = [
            WinnerEstimateEvidenceMember(
                id=index + 1,
                estimate_id=estimate.id,
                prediction_id=row.prediction.id,
                outcome_id=row.forward_outcome.id,
                outcome_revision=row.forward_outcome.revision,
                episode_id=row.prediction.episode_id,
                inclusion_weight=Decimal("1"),
                included_as_of=estimate.training_cutoff_at,
                inclusion_cutoff_at=estimate.training_cutoff_at,
                metadata_json={"target_stop_outcome_id": row.target_stop_outcome.id},
            )
            for index, row in enumerate(evidence)
        ]
        self.forward_outcomes = {row.forward_outcome.id: row.forward_outcome for row in evidence}
        self.target_stop_outcomes = {
            row.target_stop_outcome.id: row.target_stop_outcome for row in evidence
        }

    def get(self, model, row_id):
        if model is WinnerProbabilityEstimate:
            return self.estimate if row_id == self.estimate.id else None
        if model is WinnerForwardOutcome:
            return self.forward_outcomes.get(row_id)
        if model is WinnerTargetStopOutcome:
            return self.target_stop_outcomes.get(row_id)
        return None

    def scalars(self, _statement):
        return self.members


def _evidence(index: int, *, won: bool) -> EvidenceOutcome:
    prediction = WinnerPredictionSnapshot(
        id=index,
        run_id=index,
        ticker=f"T{index}",
        prediction_as_of_date=date(2026, 1, 1),
        source_data_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
        entry_schedule_status="RESOLVED",
        entry_data_status="AVAILABLE",
        eligibility_status="ELIGIBLE",
        episode_id=index,
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash=f"hash-{index}",
        config_hash="config",
        calculation_version="calc",
        feature_json={},
    )
    forward = WinnerForwardOutcome(
        id=index + 1000,
        prediction_id=index,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        close_return_pct=Decimal("2.0") if won else Decimal("-1.0"),
        mfe_pct=Decimal("3.0"),
        mae_pct=Decimal("-1.0"),
        matured_at=datetime(2026, 1, 10, tzinfo=UTC),
        prediction=prediction,
    )
    target = WinnerTargetStopOutcome(
        id=index + 2000,
        prediction_id=index,
        outcome_definition_id=1,
        forward_outcome_id=forward.id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
        first_event="TARGET_FIRST" if won else "STOP_FIRST",
        primary_winner=won,
        evaluated_at=datetime(2026, 1, 10, tzinfo=UTC),
    )
    return EvidenceOutcome(
        prediction=prediction,
        forward_outcome=forward,
        target_stop_outcome=target,
    )
