from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.models.tables import WinnerPredictionSnapshot
from app.services.winner_probability.training_eligibility import (
    TrainingEligibilityPolicy,
    TrainingRejectionReason,
)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda row: setattr(row, "reconstruction_method", "AS_OF_REPLAY"),
            TrainingRejectionReason.RECONSTRUCTED_HISTORY,
        ),
        (
            lambda row: row.lineage_json.update(point_in_time_validated=False),
            TrainingRejectionReason.POINT_IN_TIME_NOT_VALIDATED,
        ),
        (
            lambda row: setattr(row, "eligibility_status", "EXCLUDED"),
            TrainingRejectionReason.PREDICTION_NOT_ELIGIBLE,
        ),
        (
            lambda row: row.lineage_json.update(dependent_episode=True),
            TrainingRejectionReason.DEPENDENT_EPISODE,
        ),
        (
            lambda row: row.lineage_json.update(source_quality_flags=["quality_blocking"]),
            TrainingRejectionReason.SOURCE_QUALITY_BLOCKED,
        ),
    ],
)
def test_capture_training_policy_has_structured_reason_codes(mutation, reason) -> None:
    prediction = _prediction()
    mutation(prediction)

    decision = TrainingEligibilityPolicy().persist_capture_decision(prediction)

    assert decision.evidence_training_eligible is False
    assert str(reason) in decision.rejection_reasons
    assert prediction.lineage_json["production_training_allowed"] is False


def test_capture_training_policy_positive_independent_native_control() -> None:
    prediction = _prediction()

    decision = TrainingEligibilityPolicy().persist_capture_decision(prediction)

    assert decision.capture_training_candidate is True
    assert decision.evidence_training_eligible is True
    assert decision.rejection_reasons == ()
    assert prediction.lineage_json["production_training_allowed"] is True


def test_legacy_prediction_is_not_silently_reclassified() -> None:
    prediction = _prediction()
    prediction.lineage_json.pop("capture_training_candidate", None)

    decision = TrainingEligibilityPolicy().persisted_capture_decision(prediction)

    assert decision.evidence_training_eligible is False
    assert decision.rejection_reasons == (
        str(TrainingRejectionReason.LEGACY_ELIGIBILITY_UNCLASSIFIED),
    )


def _prediction() -> WinnerPredictionSnapshot:
    return WinnerPredictionSnapshot(
        id=1,
        run_id=1,
        ticker="MSFT",
        prediction_as_of_date=date(2026, 8, 13),
        source_data_cutoff_at=datetime(2026, 8, 14, 2, 0, tzinfo=UTC),
        entry_schedule_status="RESOLVED",
        entry_data_status="NOT_DUE",
        eligibility_status="ELIGIBLE",
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash="hash",
        config_hash="config",
        calculation_version="calc",
        feature_json={},
        lineage_json={
            "point_in_time_validated": True,
            "dependent_episode": False,
            "source_quality_flags": [],
            "capture_training_candidate": True,
            "evidence_training_eligible": True,
        },
    )
