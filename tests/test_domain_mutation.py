from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime

import pytest
from test_calculation_identity import _identity

from app.services.calculation_identity import (
    CalculationIdentity,
    ConfigurationCoverage,
    IdentityDimension,
)
from app.services.domain_mutation import (
    MUTATION_AUTHORITY_POLICIES,
    DomainMutationContext,
    MutationDomain,
    MutationEligibilityReference,
    MutationEntryPointDescriptor,
    MutationEvidenceReference,
    MutationSemanticMode,
    MutationWriterDescriptor,
    validate_mutation_context,
)
from app.services.domain_write_fence import DomainWriteOwnership
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.producer_readiness import (
    ConsumerEligibilityDecision,
    ConsumerEligibilityStatus,
    ProducerReadinessEnvelope,
    ReadinessStatus,
)


def mutation_context(domain=MutationDomain.RANKING):
    policy = MUTATION_AUTHORITY_POLICIES[domain]
    base = _identity()
    config = replace(
        base.configuration.effective_configuration.value,
        namespace=policy.configuration_namespace or "test",
    )
    identity = replace(
        base,
        configuration=replace(
            base.configuration, effective_configuration=IdentityDimension.known(config)
        ),
    )
    temporal = MarketCalculationCutoff(
        cutoff_at=datetime(2026, 9, 10, 20, tzinfo=UTC),
        exchange_timezone="America/New_York",
        latest_completed_session=date(2026, 9, 10),
        daily_bar_ready_at=datetime(2026, 9, 10, 20, 15, tzinfo=UTC),
        calendar_version="swinglens-us-equities-v1",
        bar_readiness_version="daily-close-plus-15m-v1",
        cutoff_reason="TEST_DECLARATION",
        context_id=404,
    )
    evidence = tuple(
        MutationEvidenceReference(role, "core_calculation_evidence", i + 1, "a" * 64)
        for i, role in enumerate(policy.evidence_roles)
    )
    eligibility = []
    consumer = "WINNER" if domain is MutationDomain.WINNER_PREDICTION else domain.value
    for role in policy.eligibility_roles:
        pin = next(p for p in evidence if p.role == role)
        readiness = ProducerReadinessEnvelope(
            role.upper(), ReadinessStatus.READY, "b" * 64, evidence_id=pin.artifact_id
        )
        decision = ConsumerEligibilityDecision(
            consumer,
            ConsumerEligibilityStatus.ELIGIBLE,
            (),
            "native-policy-v1",
            readiness.fingerprint(),
            pin.artifact_id,
        )
        eligibility.append(MutationEligibilityReference(role, readiness, decision, True))
    return DomainMutationContext(
        domain,
        MutationSemanticMode.CANONICAL_CALCULATION,
        MutationEntryPointDescriptor("unit:initiator", "CANONICAL_PIPELINE"),
        MutationWriterDescriptor("unit:writer", "v1", domain),
        "test declaration",
        identity,
        temporal,
        config,
        evidence,
        tuple(eligibility),
        101,
        202,
    )


@pytest.mark.parametrize(
    "domain",
    [
        MutationDomain.FUNDAMENTAL,
        MutationDomain.RANKING,
        MutationDomain.CERI,
        MutationDomain.SETUP,
        MutationDomain.WINNER_PREDICTION,
    ],
)
def test_representative_contexts(domain):
    validate_mutation_context(mutation_context(domain)).require_valid()


def test_human_ceri_review_uses_exact_review_authority_without_score_dependencies():
    context = replace(
        mutation_context(MutationDomain.CERI_REVIEW),
        semantic_mode=MutationSemanticMode.MAINTENANCE,
        calculation_identity=None,
        temporal=None,
        configuration=None,
        run_id=None,
        pipeline_run_id=None,
        evidence=(
            MutationEvidenceReference(
                "review_target", "ceri_catalyst_event_revisions", 7, "a" * 64
            ),
            MutationEvidenceReference("human_review", "ceri_manual_reviews", 8, "b" * 64),
        ),
    )
    validate_mutation_context(context).require_valid()
    assert not validate_mutation_context(replace(context, evidence=())).valid
    assert not validate_mutation_context(
        replace(context, semantic_mode=MutationSemanticMode.CANONICAL_CALCULATION)
    ).valid


@pytest.mark.parametrize(
    "field,change,diagnostic",
    [
        ("calculation_identity", None, "calculation_identity: required"),
        ("configuration", None, "configuration: required"),
        ("temporal", None, "temporal: required"),
        ("evidence", (), "evidence: missing"),
        ("eligibility", (), "readiness: missing"),
        ("run_id", None, "run: required"),
        ("pipeline_run_id", 999, "pipeline: exact"),
    ],
)
def test_missing_or_mismatched_authority_fails_without_reconstruction(field, change, diagnostic):
    result = validate_mutation_context(replace(mutation_context(), **{field: change}))
    assert not result.valid
    assert any(diagnostic in issue for issue in result.issues)
    with pytest.raises(ValueError, match="MUTATION_AUTHORITY_REJECTED"):
        result.require_valid()


def test_numeric_survival_cannot_replace_frozen_eligibility():
    context = mutation_context()
    permission = context.eligibility[0]
    denied = replace(
        permission,
        decision=replace(permission.decision, status=ConsumerEligibilityStatus.POLICY_UNDECIDED),
    )
    assert not validate_mutation_context(
        replace(context, eligibility=(denied, context.eligibility[1]))
    ).valid
    # A denied optional input can be recorded in blocked/diagnostic evidence,
    # provided its numeric values are not included in a financial decision.
    excluded = replace(denied, included=False)
    assert validate_mutation_context(
        replace(context, eligibility=(excluded, context.eligibility[1]))
    ).valid


def test_winner_mandatory_source_cannot_be_excluded():
    context = mutation_context(MutationDomain.WINNER_PREDICTION)
    assert not validate_mutation_context(
        replace(
            context,
            eligibility=(replace(context.eligibility[0], included=False), *context.eligibility[1:]),
        )
    ).valid


@pytest.mark.parametrize(
    "mode", [MutationSemanticMode.LEGACY_UNCERTIFIED, MutationSemanticMode.ORIGINAL_CONTEXT]
)
def test_legacy_and_reserved_original_context_cannot_certify(mode):
    assert not validate_mutation_context(replace(mutation_context(), semantic_mode=mode)).valid
    context = mutation_context()
    legacy = CalculationIdentity.legacy_unknown(run_id=101)
    assert not validate_mutation_context(replace(context, calculation_identity=legacy)).valid


def test_current_rules_mode_is_explicit_and_immutable():
    context = replace(mutation_context(), semantic_mode=MutationSemanticMode.CURRENT_STATE_REPAIR)
    assert validate_mutation_context(context).valid
    assert context.semantic_mode != MutationSemanticMode.CANONICAL_CALCULATION
    with pytest.raises(FrozenInstanceError):
        context.semantic_mode = MutationSemanticMode.CANONICAL_CALCULATION


@pytest.mark.parametrize(
    "domain,forbidden",
    [
        (MutationDomain.RANKING, {"ceri", "sector"}),
        (MutationDomain.SETUP, {"ceri"}),
        (MutationDomain.WINNER_PREDICTION, {"ceri", "setup", "lifecycle", "ibmi"}),
    ],
)
def test_negative_dependencies(domain, forbidden):
    policy = MUTATION_AUTHORITY_POLICIES[domain]
    assert not forbidden.intersection(policy.evidence_roles + policy.optional_evidence_roles)
    context = mutation_context(domain)
    for role in forbidden:
        pin = MutationEvidenceReference(role, "core_calculation_evidence", 999, "c" * 64)
        assert not validate_mutation_context(
            replace(context, evidence=(*context.evidence, pin))
        ).valid


def test_config_identity_and_completeness_are_required():
    context = mutation_context()
    assert not validate_mutation_context(
        replace(
            context,
            configuration=replace(
                context.configuration, coverage=ConfigurationCoverage.PARTIAL_DEBUG
            ),
        )
    ).valid
    assert not validate_mutation_context(
        replace(context, configuration=replace(context.configuration, namespace="contextual.ceri"))
    ).valid


def test_durable_requires_ownership_but_token_does_not_replace_semantic_authority():
    context = replace(mutation_context(), durable=True)
    assert not validate_mutation_context(context).valid
    owned = replace(context, execution=DomainWriteOwnership(1, "test-attempt"))
    assert validate_mutation_context(owned).valid
    assert not validate_mutation_context(replace(owned, evidence=())).valid


def test_outcome_maturation_does_not_require_unrelated_financial_producers():
    policy = MUTATION_AUTHORITY_POLICIES[MutationDomain.WINNER_OUTCOME]
    assert not policy.identity
    assert policy.evidence_roles == ("prediction_contract", "outcome_price_manifest")
    assert not policy.eligibility_roles


def test_native_lifecycle_and_estimate_authority_are_not_invented():
    # Lifecycle retains Setup evidence, including its nested native permission,
    # then evaluates native actionability; it has no separate Setup->Lifecycle
    # ConsumerEligibilityDecision. Estimation resolves the cohort configuration.
    assert not MUTATION_AUTHORITY_POLICIES[MutationDomain.LIFECYCLE_EVALUATION].eligibility_roles
    assert MUTATION_AUTHORITY_POLICIES[MutationDomain.WINNER_ESTIMATE].configuration_namespace == (
        "decision.winner.cohort"
    )


def test_operational_context_has_its_own_requirements():
    domain = MutationDomain.OPERATIONAL
    context = DomainMutationContext(
        domain,
        MutationSemanticMode.OPERATIONAL,
        MutationEntryPointDescriptor("heartbeat", "OPERATIONAL_ONLY"),
        MutationWriterDescriptor("worker_registry", "v1", domain),
        "heartbeat",
    )
    assert validate_mutation_context(context).valid


def test_every_declared_domain_has_an_explicit_policy():
    assert set(MUTATION_AUTHORITY_POLICIES) == set(MutationDomain)


@pytest.mark.parametrize(
    "domain",
    [MutationDomain.CERI_ALERT, MutationDomain.WINNER_MODEL, MutationDomain.WINNER_DIAGNOSTICS],
)
def test_native_auxiliary_policies_require_exact_relevant_references(domain):
    context = mutation_context(domain)
    context = replace(
        context,
        semantic_mode=next(
            iter(MUTATION_AUTHORITY_POLICIES[domain].modes - {MutationSemanticMode.PUBLICATION})
        ),
    )
    assert validate_mutation_context(context).valid
    assert not validate_mutation_context(replace(context, evidence=())).valid
    irrelevant = MutationEvidenceReference("setup", "setup_signal_snapshots", 99, "f" * 64)
    assert not validate_mutation_context(
        replace(context, evidence=(*context.evidence, irrelevant))
    ).valid


def test_model_retirement_does_not_require_training_but_promotion_requires_gate_pins():
    context = replace(
        mutation_context(MutationDomain.WINNER_MODEL),
        semantic_mode=MutationSemanticMode.MAINTENANCE,
        calculation_identity=None,
        temporal=None,
        configuration=None,
        run_id=None,
        pipeline_run_id=None,
    )
    assert validate_mutation_context(context).valid
    promotion = replace(context, semantic_mode=MutationSemanticMode.PUBLICATION)
    assert not validate_mutation_context(promotion).valid
    pins = tuple(
        MutationEvidenceReference(role, "winner_model_lifecycle_events", role, "a" * 64)
        for role in ("promotion_report", "governance_configuration")
    )
    assert validate_mutation_context(
        replace(promotion, evidence=(*promotion.evidence, *pins))
    ).valid


def test_duplicate_pins_and_readiness_addresses_fail():
    context = mutation_context()
    assert not validate_mutation_context(
        replace(context, evidence=(*context.evidence, context.evidence[0]))
    ).valid
    permission = context.eligibility[0]
    mismatch = replace(permission, decision=replace(permission.decision, producer_evidence_id=999))
    assert not validate_mutation_context(
        replace(context, eligibility=(mismatch, context.eligibility[1]))
    ).valid


def test_bound_pipeline_cannot_be_omitted():
    assert not validate_mutation_context(replace(mutation_context(), pipeline_run_id=None)).valid


def test_optional_contributor_requires_permission_when_pinned():
    context = mutation_context(MutationDomain.WINNER_PREDICTION)
    pin = MutationEvidenceReference("sector", "core_calculation_evidence", 55, "c" * 64)
    assert not validate_mutation_context(replace(context, evidence=(*context.evidence, pin))).valid
    readiness = ProducerReadinessEnvelope("SECTOR", ReadinessStatus.READY, "d" * 64, evidence_id=55)
    decision = ConsumerEligibilityDecision(
        "WINNER",
        ConsumerEligibilityStatus.ELIGIBLE,
        (),
        "native-policy-v1",
        readiness.fingerprint(),
        55,
    )
    permission = MutationEligibilityReference("sector", readiness, decision, True)
    assert validate_mutation_context(
        replace(
            context,
            evidence=(*context.evidence, pin),
            eligibility=(*context.eligibility, permission),
        )
    ).valid


def test_authority_serialization_is_stable_under_reference_order():
    context = mutation_context()
    reordered = replace(
        context,
        evidence=tuple(reversed(context.evidence)),
        eligibility=tuple(reversed(context.eligibility)),
    )
    assert context.canonical_payload() == reordered.canonical_payload()
    changed = replace(context, semantic_mode=MutationSemanticMode.CURRENT_STATE_REPAIR)
    assert context.canonical_payload() != changed.canonical_payload()
    assert context.calculation_identity == changed.calculation_identity


def test_serialization_does_not_turn_execution_attempt_into_financial_identity():
    context = replace(mutation_context(), durable=True, execution=DomainWriteOwnership(1, "one"))
    retry = replace(context, execution=DomainWriteOwnership(1, "two"))
    assert context.canonical_payload() == retry.canonical_payload()
    assert context.execution != retry.execution
