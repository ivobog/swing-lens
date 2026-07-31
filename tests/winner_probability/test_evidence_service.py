from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.models.tables import (
    WinnerForwardOutcome,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.cohort_definition import CohortKey
from app.services.winner_probability.evidence_service import EvidenceService


def test_evidence_excludes_future_current_dependent_and_reconstructed_rows() -> None:
    cutoff = datetime(2026, 7, 1, tzinfo=UTC)
    current = _prediction(99, cutoff=cutoff)
    valid = _row(1, cutoff=cutoff - timedelta(days=30))
    future = _row(2, cutoff=cutoff + timedelta(days=1))
    dependent = _row(3, cutoff=cutoff - timedelta(days=30), dependent=True)
    reconstructed = _row(4, cutoff=cutoff - timedelta(days=30), reconstructed=True)
    db = EvidenceFakeDb([valid, future, dependent, reconstructed])

    result = EvidenceService().load_evidence(
        db,
        prediction=current,
        outcome_definition=_definition(),
        cohort_key=CohortKey(level="L5", dimensions={"global": "all"}, key="L5:test"),
        training_cutoff_at=cutoff,
    )

    assert [row.prediction.id for row in result] == [1]


def test_evidence_uses_revision_visible_at_training_cutoff() -> None:
    cutoff = datetime(2026, 7, 1, tzinfo=UTC)
    current = _prediction(99, cutoff=cutoff)
    visible_old_revision = _row(
        1,
        cutoff=cutoff - timedelta(days=30),
        superseded_at=cutoff + timedelta(days=1),
    )
    already_superseded = _row(
        2,
        cutoff=cutoff - timedelta(days=30),
        superseded_at=cutoff - timedelta(seconds=1),
    )
    db = EvidenceFakeDb([visible_old_revision, already_superseded])

    result = EvidenceService().load_evidence(
        db,
        prediction=current,
        outcome_definition=_definition(),
        cohort_key=CohortKey(level="L5", dimensions={"global": "all"}, key="L5:test"),
        training_cutoff_at=cutoff,
    )

    assert [row.prediction.id for row in result] == [1]


class EvidenceFakeDb:
    def __init__(self, rows) -> None:
        self.rows = rows

    def execute(self, _statement):
        return self.rows


def _row(
    id: int,
    *,
    cutoff: datetime,
    dependent: bool = False,
    reconstructed: bool = False,
    superseded_at: datetime | None = None,
):
    prediction = _prediction(id, cutoff=cutoff)
    prediction.lineage_json = {"dependent_episode": dependent}
    prediction.reconstruction_method = "AS_OF_REPLAY" if reconstructed else None
    forward = WinnerForwardOutcome(
        id=id + 100,
        prediction_id=id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        matured_at=cutoff + timedelta(days=1),
        superseded_at=superseded_at,
    )
    target = WinnerTargetStopOutcome(
        id=id + 200,
        prediction_id=id,
        outcome_definition_id=1,
        forward_outcome_id=forward.id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
        primary_winner=True,
        evaluated_at=cutoff + timedelta(days=1),
        superseded_at=superseded_at,
    )
    return prediction, forward, target


def _prediction(id: int, *, cutoff: datetime) -> WinnerPredictionSnapshot:
    return WinnerPredictionSnapshot(
        id=id,
        run_id=id,
        ticker=f"T{id}",
        prediction_as_of_date=date(2026, 1, 1),
        source_data_cutoff_at=cutoff,
        entry_schedule_status="RESOLVED",
        entry_data_status="AVAILABLE",
        eligibility_status="ELIGIBLE",
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash=f"hash-{id}",
        config_hash="config",
        calculation_version="calc",
        feature_json={},
    )


def _definition():
    return type("Definition", (), {"id": 1, "entry_model": "NEXT_OPEN", "horizon_sessions": 5})()
