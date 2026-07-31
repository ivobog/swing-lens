from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.tables import (
    WinnerEstimateEvidenceMember,
    WinnerEvidenceManifest,
    WinnerForwardOutcome,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.evidence_manifest_service import EvidenceManifestService
from app.services.winner_probability.evidence_service import EvidenceOutcome


def test_manifest_hash_is_stable_for_same_exact_membership() -> None:
    evidence = (_evidence(1), _evidence(2))
    db = ManifestFakeDb()
    service = EvidenceManifestService()

    first = service.create_or_get_manifest(db, evidence=evidence)
    second = service.create_or_get_manifest(db, evidence=evidence)

    assert first.manifest_hash == second.manifest_hash
    assert first.payload == second.payload


def test_persist_members_writes_exact_outcome_revision_membership() -> None:
    evidence = (_evidence(1),)
    db = ManifestFakeDb()
    estimate = WinnerProbabilityEstimate(
        id=10,
        prediction_id=99,
        outcome_definition_id=1,
        estimate_kind="DECISION_TIME",
        source="COHORT",
        source_version="cohort_baseline_v1",
        training_cutoff_at=datetime(2026, 7, 1, tzinfo=UTC),
        evidence_grade="Low",
        config_hash="config",
        feature_schema_version="owpe-features-1.0.0",
    )

    EvidenceManifestService().persist_members(
        db,
        estimate=estimate,
        evidence=evidence,
        included_as_of=datetime(2026, 7, 1, tzinfo=UTC),
        inclusion_cutoff_at=datetime(2026, 7, 1, tzinfo=UTC),
    )

    member = db.rows[WinnerEstimateEvidenceMember][0]
    assert member.estimate_id == 10
    assert member.outcome_id == evidence[0].forward_outcome.id
    assert member.outcome_revision == evidence[0].forward_outcome.revision
    assert member.metadata_json["target_stop_outcome_id"] == evidence[0].target_stop_outcome.id


class ManifestFakeDb:
    def __init__(self) -> None:
        self.rows: dict[type, list] = {
            WinnerEvidenceManifest: [],
            WinnerEstimateEvidenceMember: [],
        }
        self._next_id = 1

    def scalar(self, _statement):
        return None

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        self.rows.setdefault(type(row), []).append(row)

    def flush(self) -> None:
        return None


def _evidence(index: int) -> EvidenceOutcome:
    prediction = WinnerPredictionSnapshot(
        id=index,
        run_id=index,
        ticker=f"T{index}",
        prediction_as_of_date=date(2026, 1, 1),
        source_data_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
        entry_schedule_status="RESOLVED",
        entry_data_status="AVAILABLE",
        eligibility_status="ELIGIBLE",
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash=f"hash-{index}",
        config_hash="config",
        calculation_version="calc",
        feature_json={},
    )
    forward = WinnerForwardOutcome(
        id=index + 100,
        prediction_id=index,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
    )
    target = WinnerTargetStopOutcome(
        id=index + 200,
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
        primary_winner=True,
    )
    return EvidenceOutcome(
        prediction=prediction,
        forward_outcome=forward,
        target_stop_outcome=target,
    )
