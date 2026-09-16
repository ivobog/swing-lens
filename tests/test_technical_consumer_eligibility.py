from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest
from readiness_helpers import seal_technical
from setup_lifecycle.test_snapshot_builder import _ticker_context
from test_combined_decision import _config

from app.models.tables import TechnicalScore
from app.services.combined_decision import combine_row_decision
from app.services.producer_readiness import (
    ConsumerEligibilityStatus as Eligibility,
)
from app.services.producer_readiness import (
    NativeReadinessMetrics,
    ProducerReadinessEnvelope,
    ReadinessReason,
    ReadinessStatus,
    readiness_from_evidence,
)
from app.services.ranking_profile_config import get_ranking_profile
from app.services.ranking_profile_engine import rank_profile, rank_single_row
from app.services.setup_lifecycle.actionability_policy import SetupLifecycleActionabilityPolicy
from app.services.setup_lifecycle.enums import Actionability, LifecycleState
from app.services.setup_lifecycle.episode_service import normalized_snapshot_from_row
from app.services.setup_lifecycle.lifecycle_engine import (
    LifecycleEvaluationInput,
    SetupLifecycleEngine,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
from app.services.technical_consumer_eligibility import (
    TECHNICAL_ELIGIBILITY_KEY,
    TECHNICAL_TO_COMBINED,
    TECHNICAL_TO_RANKING,
    TECHNICAL_TO_SETUP,
    technical_decision_input,
)

POLICIES = (TECHNICAL_TO_COMBINED, TECHNICAL_TO_RANKING, TECHNICAL_TO_SETUP)


def _context(status: ReadinessStatus, *, ticker="MSFT", value="8.0"):
    context = _ticker_context()
    context.raw_row.ticker = ticker
    context.technical_score.ticker = ticker
    context.fundamental_score.ticker = ticker
    context.technical_score.dual_score = Decimal(value)
    context.technical_score.technical_confidence = "normal"
    context.technical_score.insufficient_data = status is ReadinessStatus.INSUFFICIENT_EVIDENCE
    if status is ReadinessStatus.ERROR:
        context.technical_score.technical_confidence = "error"
    elif status is ReadinessStatus.DEGRADED:
        context.technical_score.technical_confidence = "low"
    elif status is ReadinessStatus.UNKNOWN:
        context.technical_score.insufficient_data = None
    seal_technical(context.technical_score)
    if status is ReadinessStatus.LEGACY_UNKNOWN:
        context.technical_score.evidence_id = None
        context.technical_score.calculation_evidence = None
    return context


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.policy_version)
@pytest.mark.parametrize("status", list(ReadinessStatus))
def test_explicit_policy_matrix_and_deterministic_retry(policy, status):
    readiness = ProducerReadinessEnvelope("TECHNICAL", status, "identity", evidence_id=123)
    expected = {
        ReadinessStatus.READY: Eligibility.ELIGIBLE,
        ReadinessStatus.DEGRADED: Eligibility.POLICY_UNDECIDED,
        ReadinessStatus.UNKNOWN: Eligibility.POLICY_UNDECIDED,
        ReadinessStatus.LEGACY_UNKNOWN: Eligibility.POLICY_UNDECIDED,
    }.get(status, Eligibility.INELIGIBLE)
    decision = policy.evaluate(readiness)
    assert decision.status is expected
    assert decision == policy.evaluate(readiness)
    assert decision.producer_evidence_id == 123
    assert decision.producer_readiness_fingerprint == readiness.fingerprint()


@pytest.mark.parametrize(
    "status",
    [
        ReadinessStatus.INSUFFICIENT_EVIDENCE,
        ReadinessStatus.ERROR,
        ReadinessStatus.UNKNOWN,
        ReadinessStatus.LEGACY_UNKNOWN,
        ReadinessStatus.DEGRADED,
    ],
)
def test_positive_numerics_are_excluded_across_all_three_consumers(status):
    context = _context(status)
    row, fundamental, technical = (
        context.raw_row,
        context.fundamental_score,
        context.technical_score,
    )
    combined = combine_row_decision(row, fundamental, technical, config=_config())
    missing = combine_row_decision(row, fundamental, None, config=_config())
    assert combined.final_score == missing.final_score
    assert combined.dual_score is None
    assert combined.is_complete is combined.has_technical is False
    assert (
        combined.debug_evidence["penalty_breakdown"] == missing.debug_evidence["penalty_breakdown"]
    )
    ranking = rank_single_row(
        profile=get_ranking_profile("momentum_swing"),
        row=row,
        fundamental=fundamental,
        technical=technical,
        config=_config(),
    )
    assert ranking.base_technical_score is ranking.technical_profile_score is None
    assert ranking.component_scores == {}
    assert ranking.profile_rank == 0
    assert ranking.decision_label not in {"Candidate", "Strong candidate"}
    assert ranking.position_size_hint == "No new entry"
    assert ranking.has_technical is ranking.is_complete is False
    built = SetupLifecycleSnapshotBuilder().build(context)
    assert built.dto.promoted_fields["dual_score"] is None
    assert built.dto.signals["setup_score"]["value"] is None
    assert built.dto.signals["volume_dry_up"]["value"] is None
    assert built.dto.data_quality_label == "INSUFFICIENT"
    frozen = built.dto.source_lineage[TECHNICAL_ELIGIBILITY_KEY]
    assert frozen["decision"]["status"] != "ELIGIBLE"
    assert technical.dual_score == Decimal("8.0")  # diagnostics survive
    for consumer in (combined.debug_evidence, ranking.debug, built.dto.source_lineage):
        assert consumer[TECHNICAL_ELIGIBILITY_KEY]["decision"]["status"] != "ELIGIBLE"


def _normalized(context):
    from app.models.tables import SetupSignalSnapshot

    snapshot = SetupSignalSnapshot(id=77)
    SetupLifecycleRepository()._apply_snapshot_fields(
        snapshot,
        SetupLifecycleSnapshotBuilder().build(context).dto,
    )
    return normalized_snapshot_from_row(snapshot)


@pytest.mark.parametrize(
    "previous",
    [
        None,
        LifecycleState.DEVELOPING,
        LifecycleState.READY,
        LifecycleState.TRIGGERED,
        LifecycleState.CONFIRMED,
    ],
)
def test_lifecycle_cannot_advance_or_recover_actionability_from_residual_signals(previous):
    blocked = _normalized(_context(ReadinessStatus.INSUFFICIENT_EVIDENCE))
    # Even if a later projection restores all formerly qualifying numeric signals,
    # the frozen Setup permission is authoritative before family evaluation.
    ready = _normalized(_context(ReadinessStatus.READY))
    blocked = replace(blocked, signals=ready.signals)
    decision = SetupLifecycleEngine().evaluate(
        LifecycleEvaluationInput(
            snapshot=blocked,
            previous_state=previous,
            previous_phase="PIVOT_READY",
        )
    )
    assert decision.proposed_state is (previous or LifecycleState.DISCOVERED)
    assert decision.immediate_transition is False
    assert decision.actionability_candidate is Actionability.BLOCKED
    actionability = SetupLifecycleActionabilityPolicy().evaluate(decision, blocked)
    assert actionability.actionability is Actionability.BLOCKED
    assert "TECHNICAL_CONSUMER_INELIGIBLE" in decision.reason_codes


def test_same_score_different_readiness_and_peer_population_isolation():
    ready = _context(ReadinessStatus.READY, ticker="A", value="8.0")
    bad = _context(ReadinessStatus.INSUFFICIENT_EVIDENCE, ticker="B", value="8.0")
    profile = get_ranking_profile("momentum_swing")
    kwargs = dict(
        profile=profile,
        config=_config(),
        fundamentals={"A": ready.fundamental_score, "B": bad.fundamental_score},
        technicals={"A": ready.technical_score, "B": bad.technical_score},
    )
    alone = rank_profile(rows=[ready.raw_row], **kwargs)[0]
    together = rank_profile(rows=[bad.raw_row, ready.raw_row], **kwargs)
    assert together[0] == alone
    assert together[1].ticker == "B" and together[1].profile_rank == 0
    # An arbitrarily large bad numeric cannot alter A's score or rank.
    bad.technical_score.dual_score = Decimal("9999")
    bad.technical_score.trend_score = Decimal("9999")
    seal_technical(bad.technical_score)
    changed = rank_profile(rows=[bad.raw_row, ready.raw_row], **kwargs)
    assert changed[0] == alone
    assert changed[1].profile_rank == 0
    for policy in POLICIES:
        good, good_dto = technical_decision_input(ready.technical_score, policy)
        excluded, bad_dto = technical_decision_input(bad.technical_score, policy)
        assert good.dual_score == Decimal("8.0")
        assert excluded is None
        assert good_dto["decision"]["status"] == "ELIGIBLE"
        assert bad_dto["decision"]["status"] == "INELIGIBLE"


def test_mutable_ready_numerics_cannot_replace_frozen_values():
    context = _context(ReadinessStatus.READY)
    row = context.technical_score
    row.dual_score = Decimal("0")
    row.insufficient_data = True
    row.technical_confidence = "error"
    for policy in POLICIES:
        projected, provenance = technical_decision_input(row, policy)
        assert projected.dual_score == Decimal("8.0")
        assert provenance["producer_readiness"]["status"] == "READY"


def test_missing_referenced_evidence_and_wrong_scope_fail_without_fallback():
    unresolved = TechnicalScore(run_id=7, ticker="MSFT", evidence_id=999, dual_score=Decimal("8"))
    with pytest.raises(LookupError, match="detached"):
        technical_decision_input(unresolved, TECHNICAL_TO_COMBINED)
    context = _context(ReadinessStatus.READY)
    context.technical_score.calculation_evidence.ticker = "OTHER"
    with pytest.raises(ValueError, match="scope mismatch"):
        technical_decision_input(context.technical_score, TECHNICAL_TO_RANKING)


def test_existing_blocking_reason_is_never_eligible_and_config_cannot_override():
    readiness = ProducerReadinessEnvelope(
        "TECHNICAL",
        ReadinessStatus.UNKNOWN,
        "identity",
        blocking_reasons=(ReadinessReason.TECH_INSUFFICIENT_HISTORY,),
    )
    for policy in POLICIES:
        assert policy.evaluate(readiness).status is Eligibility.INELIGIBLE
        with pytest.raises(ValueError, match="no configurable overrides"):
            policy.evaluate(
                readiness, config=NativeReadinessMetrics.freeze({"allow_degraded": True}),
            )


def test_ineligible_is_absent_and_never_a_valid_zero_substitution():
    zero = _context(ReadinessStatus.READY, value="0")
    blocked = _context(ReadinessStatus.INSUFFICIENT_EVIDENCE, value="8.0")
    for policy in POLICIES:
        allowed, _ = technical_decision_input(zero.technical_score, policy)
        absent, _ = technical_decision_input(blocked.technical_score, policy)
        assert allowed.dual_score == Decimal("0")
        assert absent is None
    ready_combined = combine_row_decision(
        zero.raw_row, zero.fundamental_score, zero.technical_score, config=_config(),
    )
    assert ready_combined.has_technical is True
    assert ready_combined.dual_score == 0.0
    assert SetupLifecycleSnapshotBuilder().build(
        zero,
    ).dto.promoted_fields["dual_score"] == Decimal("0")


def test_readiness_payload_views_and_stored_decisions_are_not_reinterpreted():
    context = _context(ReadinessStatus.INSUFFICIENT_EVIDENCE)
    _, frozen = technical_decision_input(context.technical_score, TECHNICAL_TO_SETUP)
    stored = deepcopy(frozen)
    context.technical_score.insufficient_data = False
    seal_technical(context.technical_score)
    _, new = technical_decision_input(context.technical_score, TECHNICAL_TO_SETUP)
    assert stored == frozen
    assert stored["decision"]["status"] == "INELIGIBLE"
    assert new["decision"]["status"] == "ELIGIBLE"
    assert (
        readiness_from_evidence(context.technical_score.calculation_evidence).status
        is ReadinessStatus.READY
    )
