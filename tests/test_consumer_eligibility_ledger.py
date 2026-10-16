from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.consumer_eligibility_ledger import (
    EligibilityShadowEvaluation,
    WinnerAcquisitionDecision,
    WinnerAcquisitionOutcome,
)
from app.services.eligibility_policy import (
    AnalyticalConfidence,
    CompletenessStatus,
    ConsumerEligibilityOutcome,
    ExecutionOutcome,
    IntegrityStatus,
    OrthogonalConsumerDecision,
)
from app.services.eligibility_shadow import FrozenEligibilityInput, evaluate_frozen_input
from app.services.warning_taxonomy import classify_warnings

NOW = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)


def _proposed(*, authoritative: bool = False) -> OrthogonalConsumerDecision:
    return OrthogonalConsumerDecision(
        producer="COMBINED",
        consumer="WINNER",
        consumer_edge="COMBINED_TO_WINNER",
        policy_version="test-v1",
        source_identity_fingerprint="a" * 64,
        source_evidence_id=42,
        decision_cutoff=NOW,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        integrity_status=IntegrityStatus.CERTIFIED,
        completeness_status=CompletenessStatus.COMPLETE,
        analytical_confidence=AnalyticalConfidence.LOW,
        warnings=classify_warnings(["high_leverage"]),
        eligibility=ConsumerEligibilityOutcome.INCLUDED,
        reason_codes=("classified_risk_warning",),
        authoritative=authoritative,
    )


def test_winner_acquisition_fingerprint_is_stable_and_retains_source_evidence() -> None:
    decision = WinnerAcquisitionDecision(
        pipeline_run_id=33,
        upload_run_id=18,
        raw_row_id=7,
        prediction_id=99,
        ticker="abc",
        decision_cutoff=NOW,
        outcome=WinnerAcquisitionOutcome.INSERTED,
        source_identities={"combined": {"evidence_id": 42, "fingerprint": "a" * 64}},
        source_decisions=({"edge": "COMBINED_TO_WINNER", "outcome": "INCLUDED"},),
        warning_classifications=classify_warnings(["high_leverage"]),
    )

    assert decision.fingerprint() == decision.fingerprint()
    assert decision.canonical_payload()["ticker"] == "ABC"
    assert decision.canonical_payload()["source_identities"]["combined"]["evidence_id"] == 42


def test_inserted_acquisition_requires_prediction_identity() -> None:
    with pytest.raises(ValueError, match="prediction id"):
        WinnerAcquisitionDecision(
            pipeline_run_id=33,
            upload_run_id=18,
            ticker="ABC",
            decision_cutoff=NOW,
            outcome=WinnerAcquisitionOutcome.INSERTED,
            source_identities={},
            source_decisions=(),
        )


def test_shadow_evaluation_records_a_difference_without_authority() -> None:
    evaluation = EligibilityShadowEvaluation(
        pipeline_run_id=33,
        upload_run_id=18,
        ticker="ABC",
        consumer_edge="COMBINED_TO_WINNER",
        policy_version="test-v1",
        source_identity_fingerprint="a" * 64,
        decision_cutoff=NOW,
        legacy_outcome=ConsumerEligibilityOutcome.UNDECIDED,
        proposed_decision=_proposed(),
        frozen_input={"warning_flags": ["high_leverage"]},
    )

    assert evaluation.differs is True
    assert evaluation.proposed_outcome is ConsumerEligibilityOutcome.INCLUDED
    assert evaluation.fingerprint() == evaluation.fingerprint()
    assert evaluation.canonical_payload()["proposed_decision"]["authoritative"] is False


def test_shadow_evaluator_rejects_authoritative_policy_output() -> None:
    frozen = FrozenEligibilityInput(
        pipeline_run_id=33,
        upload_run_id=18,
        ticker="ABC",
        consumer_edge="COMBINED_TO_WINNER",
        policy_version="test-v1",
        source_identity_fingerprint="a" * 64,
        decision_cutoff=NOW,
        legacy_outcome=ConsumerEligibilityOutcome.UNDECIDED,
        payload={"warning_flags": ["high_leverage"]},
    )

    with pytest.raises(ValueError, match="rejects authoritative"):
        evaluate_frozen_input(frozen, proposed_policy=lambda _: _proposed(authoritative=True))


def test_shadow_evaluation_requires_matching_source_identity() -> None:
    with pytest.raises(ValueError, match="source identity"):
        EligibilityShadowEvaluation(
            pipeline_run_id=33,
            upload_run_id=18,
            ticker="ABC",
            consumer_edge="COMBINED_TO_WINNER",
            policy_version="test-v1",
            source_identity_fingerprint="b" * 64,
            decision_cutoff=NOW,
            legacy_outcome=ConsumerEligibilityOutcome.UNDECIDED,
            proposed_decision=_proposed(),
            frozen_input={},
        )
