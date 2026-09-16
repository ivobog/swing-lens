import pytest

from app.services.contextual_consumer_eligibility import (
    IBMI_LIQUIDITY_TO_RANKING,
    IBMI_SHORT_PRESSURE_TO_CERI,
    IBMI_VOLATILITY_TO_CERI,
    REGIME_TO_SETUP,
    SECTOR_TO_SETUP,
)
from app.services.producer_readiness import (
    ConsumerEligibilityStatus,
    ProducerReadinessEnvelope,
    ReadinessStatus,
)
from app.services.technical_consumer_eligibility import (
    TECHNICAL_TO_COMBINED,
    TECHNICAL_TO_RANKING,
    TECHNICAL_TO_SETUP,
)
from app.services.winner_probability.consumer_eligibility import (
    RANKING_TO_WINNER,
    REGIME_TO_WINNER,
    SECTOR_TO_WINNER,
    TECHNICAL_TO_WINNER,
)

POLICIES = (
    TECHNICAL_TO_COMBINED,
    TECHNICAL_TO_RANKING,
    TECHNICAL_TO_SETUP,
    IBMI_LIQUIDITY_TO_RANKING,
    IBMI_VOLATILITY_TO_CERI,
    IBMI_SHORT_PRESSURE_TO_CERI,
    REGIME_TO_SETUP,
    SECTOR_TO_SETUP,
    TECHNICAL_TO_WINNER,
    RANKING_TO_WINNER,
    REGIME_TO_WINNER,
    SECTOR_TO_WINNER,
)


@pytest.mark.parametrize("policy", POLICIES, ids=lambda item: item.policy_version)
@pytest.mark.parametrize("status", list(ReadinessStatus))
def test_known_phase3_graph_has_explicit_consistent_permission(policy, status):
    readiness = ProducerReadinessEnvelope(
        getattr(policy, "producer", "TECHNICAL"),
        status,
        "exact-source",
        evidence_id=20,
    )
    expected = (
        ConsumerEligibilityStatus.ELIGIBLE
        if status is ReadinessStatus.READY
        else ConsumerEligibilityStatus.INELIGIBLE
        if status
        in {
            ReadinessStatus.ERROR,
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
            ReadinessStatus.STALE,
        }
        else ConsumerEligibilityStatus.POLICY_UNDECIDED
    )
    decision = policy.evaluate(readiness)
    assert decision.status is expected
    assert decision.producer_evidence_id == 20
    assert decision.producer_readiness_fingerprint == readiness.fingerprint()
