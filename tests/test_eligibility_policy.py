from datetime import UTC, datetime

from app.services.eligibility_policy import (
    AnalyticalConfidence,
    CompletenessStatus,
    ConsumerEligibilityOutcome,
    ExecutionOutcome,
    IntegrityStatus,
    from_legacy_decision,
)
from app.services.producer_readiness import (
    ConsumerEligibilityDecision,
    ConsumerEligibilityStatus,
    EligibilityReason,
    ProducerReadinessEnvelope,
    ReadinessStatus,
)


def _readiness() -> ProducerReadinessEnvelope:
    return ProducerReadinessEnvelope(
        producer="COMBINED",
        status=ReadinessStatus.READY,
        calculation_identity_fingerprint="identity-1",
        evidence_id=42,
        readiness_policy_version="combined-readiness-v2",
    )


def _decision() -> ConsumerEligibilityDecision:
    readiness = _readiness()
    return ConsumerEligibilityDecision(
        consumer="WINNER",
        status=ConsumerEligibilityStatus.ELIGIBLE,
        reasons=(),
        policy_version="combined-to-winner-v2",
        producer_readiness_fingerprint=readiness.fingerprint(),
        producer_evidence_id=42,
    )


def test_orthogonal_decision_is_deterministic_and_binds_all_dimensions() -> None:
    values = {
        "readiness": _readiness(),
        "decision": _decision(),
        "consumer_edge": "COMBINED_TO_WINNER",
        "decision_cutoff": datetime(2026, 10, 12, 20, 47, tzinfo=UTC),
        "warning_codes": ["value_trap_risk", "stage_gate_blocked"],
        "execution_outcome": ExecutionOutcome.SUCCEEDED_WITH_GAPS,
        "integrity_status": IntegrityStatus.CERTIFIED,
        "completeness_status": CompletenessStatus.COMPLETE,
        "analytical_confidence": AnalyticalConfidence.NORMAL,
    }
    left = from_legacy_decision(**values)
    right = from_legacy_decision(
        **{**values, "warning_codes": list(reversed(values["warning_codes"]))}
    )

    assert left.eligibility is ConsumerEligibilityOutcome.INCLUDED
    assert left.fingerprint() == right.fingerprint()
    assert left.canonical_payload()["source_identity_fingerprint"] == "identity-1"
    assert left.canonical_payload()["decision_cutoff"] == datetime(
        2026, 10, 12, 20, 47, tzinfo=UTC
    )


def test_legacy_undecided_remains_undecided_in_inactive_foundation() -> None:
    readiness = _readiness()
    decision = ConsumerEligibilityDecision(
        consumer="WINNER",
        status=ConsumerEligibilityStatus.POLICY_UNDECIDED,
        reasons=(EligibilityReason.DEGRADED_POLICY_UNDECIDED,),
        policy_version="combined-to-winner-v2",
        producer_readiness_fingerprint=readiness.fingerprint(),
        producer_evidence_id=42,
    )

    value = from_legacy_decision(
        readiness,
        decision,
        consumer_edge="COMBINED_TO_WINNER",
        decision_cutoff=datetime(2026, 10, 12, tzinfo=UTC),
        warning_codes=["new_unreviewed_warning"],
    )

    assert value.eligibility is ConsumerEligibilityOutcome.UNDECIDED
    assert value.reason_codes == ("DEGRADED_POLICY_UNDECIDED",)
    assert value.warnings[0].blocks_integrity_readiness is True
