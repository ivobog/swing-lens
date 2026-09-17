"""Phase-5 mutation authority declarations, without changing existing writers.

Declaration validation is not persisted-evidence certification. Semantic writers
must resolve the declared exact references and apply their existing Phase-1/2/3
validators in the write transaction. No current/latest/default authority is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType

from sqlalchemy.orm import Session

from app.services.calculation_identity import (
    CalculationIdentity,
    ConfigurationCoverage,
    EffectiveConfigurationIdentity,
    IdentityState,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.domain_write_fence import (
    DomainWriteOwnership,
    assert_current_execution_ownership,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.producer_readiness import (
    ConsumerEligibilityDecision,
    ConsumerEligibilityStatus,
    ProducerReadinessEnvelope,
)


class MutationSemanticMode(StrEnum):
    CANONICAL_CALCULATION = "CANONICAL_CALCULATION"
    CURRENT_PROJECTION_ADVANCE = "CURRENT_PROJECTION_ADVANCE"
    CURRENT_STATE_REPAIR = "CURRENT_STATE_REPAIR"
    CURRENT_RULES_RETROSPECTIVE = "CURRENT_RULES_RETROSPECTIVE"
    BOOTSTRAP = "BOOTSTRAP"
    MAINTENANCE = "MAINTENANCE"
    PUBLICATION = "PUBLICATION"
    OUTCOME_MATURATION = "OUTCOME_MATURATION"
    LEGACY_UNCERTIFIED = "LEGACY_UNCERTIFIED"
    OPERATIONAL = "OPERATIONAL"
    # Reserved only. No repository path proves full original-context reconstruction.
    ORIGINAL_CONTEXT = "ORIGINAL_CONTEXT"


class MutationDomain(StrEnum):
    RAW = "RAW"
    PRICE = "PRICE"
    FUNDAMENTAL = "FUNDAMENTAL"
    TECHNICAL = "TECHNICAL"
    COMBINED = "COMBINED"
    RANKING = "RANKING"
    REGIME = "REGIME"
    SECTOR = "SECTOR"
    CERI_SOURCE = "CERI_SOURCE"
    CERI = "CERI"
    CERI_CHANGE = "CERI_CHANGE"
    CERI_REVIEW = "CERI_REVIEW"
    CERI_ALERT = "CERI_ALERT"
    IBMI_SOURCE = "IBMI_SOURCE"
    IBMI = "IBMI"
    SETUP = "SETUP"
    LIFECYCLE_EVALUATION = "LIFECYCLE_EVALUATION"
    LIFECYCLE_TRANSITION = "LIFECYCLE_TRANSITION"
    ALERT_DECISION = "ALERT_DECISION"
    ALERT_STATUS = "ALERT_STATUS"
    WINNER_PREDICTION = "WINNER_PREDICTION"
    WINNER_ESTIMATE = "WINNER_ESTIMATE"
    WINNER_OUTCOME = "WINNER_OUTCOME"
    WINNER_COHORT = "WINNER_COHORT"
    WINNER_GENERATION = "WINNER_GENERATION"
    WINNER_PUBLICATION = "WINNER_PUBLICATION"
    WINNER_MODEL = "WINNER_MODEL"
    WINNER_DIAGNOSTICS = "WINNER_DIAGNOSTICS"
    CONFIGURATION = "CONFIGURATION"
    PIPELINE = "PIPELINE"
    TRADE_JOURNAL = "TRADE_JOURNAL"
    CURRENT_PROJECTION = "CURRENT_PROJECTION"
    OPERATIONAL = "OPERATIONAL"


@dataclass(frozen=True)
class MutationEntryPointDescriptor:
    identity: str
    classification: str


@dataclass(frozen=True)
class MutationWriterDescriptor:
    identity: str
    version: str
    domain: MutationDomain


@dataclass(frozen=True)
class MutationEvidenceReference:
    """An exact retained address; fingerprint is verified by the domain writer.

    Not all authority is CoreCalculationEvidence: raw sources, frozen Winner
    contracts, generations and dedicated lifecycle/alert ledgers have their own
    validators. table + id disambiguates overlapping primary-key spaces.
    """

    role: str
    table: str
    artifact_id: int | str
    fingerprint: str


@dataclass(frozen=True)
class MutationEligibilityReference:
    role: str
    readiness: ProducerReadinessEnvelope
    decision: ConsumerEligibilityDecision
    included: bool

    def __post_init__(self):
        if (
            type(self.readiness) is not ProducerReadinessEnvelope
            or type(self.decision) is not ConsumerEligibilityDecision
            or type(self.included) is not bool
        ):
            raise TypeError("eligibility requires frozen native readiness and a typed decision")


@dataclass(frozen=True)
class MutationAuthorityRequirement:
    domain: MutationDomain
    modes: frozenset[MutationSemanticMode]
    identity: bool = False
    temporal: bool = False
    configuration_namespace: str | None = None
    evidence_roles: tuple[str, ...] = ()
    optional_evidence_roles: tuple[str, ...] = ()
    eligibility_roles: tuple[str, ...] = ()
    mandatory_consumption: tuple[str, ...] = ()
    run: bool = False
    pipeline_if_bound: bool = True
    fence_if_durable: bool = True
    eligibility_if_pinned: tuple[str, ...] = ()


_CALCULATION_MODES = frozenset(
    {
        MutationSemanticMode.CANONICAL_CALCULATION,
        MutationSemanticMode.CURRENT_STATE_REPAIR,
        MutationSemanticMode.CURRENT_RULES_RETROSPECTIVE,
    }
)


def _calculation(
    domain,
    namespace,
    roles=(),
    optional=(),
    eligibility=(),
    mandatory=(),
    run=True,
    eligibility_if_pinned=(),
):
    return MutationAuthorityRequirement(
        domain,
        _CALCULATION_MODES,
        True,
        True,
        namespace,
        roles,
        optional,
        eligibility,
        mandatory,
        run,
        eligibility_if_pinned=eligibility_if_pinned,
    )


# Optional inputs are required to carry exact authority only when included.
# CERI/IBMI have constituent manifests, not invented global financial dependencies.
_POLICIES = [
    MutationAuthorityRequirement(
        MutationDomain.CERI_ALERT,
        _CALCULATION_MODES,
        configuration_namespace="decision.alerts.ceri",
        evidence_roles=("change", "rule"),
    ),
    MutationAuthorityRequirement(
        MutationDomain.WINNER_MODEL,
        frozenset({MutationSemanticMode.MAINTENANCE, MutationSemanticMode.PUBLICATION}),
        evidence_roles=("model_version", "governance_action"),
        optional_evidence_roles=(
            "training",
            "calibration",
            "drift",
            "replacement_model",
            "promotion_report",
            "governance_configuration",
        ),
    ),
    MutationAuthorityRequirement(
        MutationDomain.WINNER_DIAGNOSTICS,
        _CALCULATION_MODES,
        evidence_roles=("diagnostic_subject", "input_manifest", "diagnostic_contract"),
    ),
    MutationAuthorityRequirement(
        MutationDomain.CERI_REVIEW,
        frozenset({MutationSemanticMode.MAINTENANCE}),
        evidence_roles=("review_target", "human_review"),
    ),
    _calculation(MutationDomain.FUNDAMENTAL, "core.fundamental", ("raw_source",)),
    _calculation(MutationDomain.TECHNICAL, "core.technical", ("price_manifest",)),
    _calculation(
        MutationDomain.COMBINED,
        "core.combined",
        ("fundamental", "technical"),
        eligibility=("fundamental", "technical"),
    ),
    _calculation(
        MutationDomain.RANKING,
        "core.ranking",
        ("fundamental", "technical"),
        ("ibmi_liquidity",),
        ("fundamental", "technical"),
        eligibility_if_pinned=("ibmi_liquidity",),
    ),
    _calculation(MutationDomain.REGIME, "contextual.regime", ("price_manifest",), run=False),
    _calculation(
        MutationDomain.SECTOR,
        "contextual.sector",
        ("universe_manifest",),
        ("regime", "prior_sector", "fundamental", "technical", "combined", "ranking"),
        run=False,
        eligibility_if_pinned=("regime", "prior_sector", "technical", "combined", "ranking"),
    ),
    _calculation(
        MutationDomain.CERI,
        "contextual.ceri",
        ("source_manifest",),
        ("ibmi_volatility", "ibmi_short_pressure"),
        run=False,
        eligibility_if_pinned=("ibmi_volatility", "ibmi_short_pressure"),
    ),
    _calculation(MutationDomain.IBMI, "contextual.ibmi", ("constituent_manifest",), run=False),
    _calculation(
        MutationDomain.SETUP,
        "decision.setup",
        ("price_manifest", "technical"),
        ("fundamental", "combined", "ranking_metadata", "regime", "sector"),
        ("technical",),
        eligibility_if_pinned=("fundamental", "combined", "regime", "sector"),
    ),
    _calculation(
        MutationDomain.LIFECYCLE_EVALUATION,
        "decision.lifecycle",
        ("setup",),
        ("previous_episode",),
    ),
    _calculation(
        MutationDomain.LIFECYCLE_TRANSITION,
        "decision.lifecycle",
        ("evaluation",),
        ("previous_episode",),
    ),
    _calculation(
        MutationDomain.ALERT_DECISION,
        "decision.alerts.setup",
        ("rule", "evaluation"),
        ("setup", "transition"),
    ),
    _calculation(MutationDomain.CERI_CHANGE, "decision.ceri.changes", ("ceri",), ("prior_ceri",)),
    _calculation(
        MutationDomain.WINNER_PREDICTION,
        "decision.winner.prediction",
        ("raw_source", "technical", "combined", "ranking"),
        ("fundamental", "regime", "sector"),
        ("technical", "combined", "ranking"),
        ("technical", "combined", "ranking"),
        eligibility_if_pinned=("fundamental", "regime", "sector"),
    ),
    _calculation(
        MutationDomain.WINNER_ESTIMATE,
        "decision.winner.cohort",
        ("prediction", "model", "evidence_manifest"),
    ),
    MutationAuthorityRequirement(
        MutationDomain.WINNER_OUTCOME,
        frozenset(
            {MutationSemanticMode.OUTCOME_MATURATION, MutationSemanticMode.CURRENT_STATE_REPAIR}
        ),
        temporal=True,
        configuration_namespace="decision.winner.outcome",
        evidence_roles=("prediction_contract", "outcome_price_manifest"),
    ),
    MutationAuthorityRequirement(
        MutationDomain.WINNER_COHORT,
        frozenset({MutationSemanticMode.CANONICAL_CALCULATION}),
        configuration_namespace="decision.winner.cohort",
        evidence_roles=("evidence_manifest",),
    ),
    MutationAuthorityRequirement(
        MutationDomain.WINNER_GENERATION,
        frozenset({MutationSemanticMode.CANONICAL_CALCULATION, MutationSemanticMode.BOOTSTRAP}),
        configuration_namespace="decision.winner.generation",
        evidence_roles=("generation_manifest",),
    ),
    MutationAuthorityRequirement(
        MutationDomain.WINNER_PUBLICATION,
        frozenset({MutationSemanticMode.PUBLICATION}),
        evidence_roles=("generation", "estimate"),
    ),
    MutationAuthorityRequirement(
        MutationDomain.CURRENT_PROJECTION,
        frozenset({MutationSemanticMode.CURRENT_PROJECTION_ADVANCE}),
        evidence_roles=("target_evidence", "projection_scope"),
    ),
]
for _domain, _roles in (
    (MutationDomain.RAW, ("source_file",)),
    (MutationDomain.PRICE, ("acquisition_plan",)),
    (MutationDomain.CERI_SOURCE, ("provider_source",)),
    (MutationDomain.IBMI_SOURCE, ("request_scope",)),
    (MutationDomain.CONFIGURATION, ("configuration_bundle",)),
    (MutationDomain.PIPELINE, ("pipeline_context",)),
    (MutationDomain.TRADE_JOURNAL, ("execution_source",)),
    (MutationDomain.ALERT_STATUS, ("alert",)),
):
    _POLICIES.append(
        MutationAuthorityRequirement(
            _domain,
            frozenset(
                {
                    MutationSemanticMode.CANONICAL_CALCULATION,
                    MutationSemanticMode.BOOTSTRAP,
                    MutationSemanticMode.MAINTENANCE,
                }
            ),
            evidence_roles=_roles,
        )
    )
_POLICIES.append(
    MutationAuthorityRequirement(
        MutationDomain.OPERATIONAL,
        frozenset({MutationSemanticMode.OPERATIONAL}),
        pipeline_if_bound=False,
    )
)
MUTATION_AUTHORITY_POLICIES = MappingProxyType({policy.domain: policy for policy in _POLICIES})


@dataclass(frozen=True)
class DomainMutationContext:
    domain: MutationDomain
    semantic_mode: MutationSemanticMode
    entrypoint: MutationEntryPointDescriptor
    writer: MutationWriterDescriptor
    reason: str
    calculation_identity: CalculationIdentity | None = None
    temporal: MarketCalculationCutoff | None = None
    configuration: EffectiveConfigurationIdentity | None = None
    evidence: tuple[MutationEvidenceReference, ...] = ()
    eligibility: tuple[MutationEligibilityReference, ...] = ()
    run_id: int | None = None
    pipeline_run_id: int | None = None
    durable: bool = False
    execution: DomainWriteOwnership | None = None

    def __post_init__(self):
        if (
            type(self.domain) is not MutationDomain
            or type(self.semantic_mode) is not MutationSemanticMode
        ):
            raise TypeError("domain and semantic mode must be typed")
        if (
            type(self.entrypoint) is not MutationEntryPointDescriptor
            or type(self.writer) is not MutationWriterDescriptor
            or (
                self.calculation_identity is not None
                and type(self.calculation_identity) is not CalculationIdentity
            )
            or (self.temporal is not None and type(self.temporal) is not MarketCalculationCutoff)
            or (
                self.configuration is not None
                and type(self.configuration) is not EffectiveConfigurationIdentity
            )
            or (self.execution is not None and type(self.execution) is not DomainWriteOwnership)
            or type(self.durable) is not bool
        ):
            raise TypeError("mutation authority must use typed immutable references")
        if any(type(p) is not MutationEvidenceReference for p in self.evidence) or any(
            type(p) is not MutationEligibilityReference for p in self.eligibility
        ):
            raise TypeError("mutation evidence/eligibility must be typed")
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "eligibility", tuple(self.eligibility))

    def canonical_payload(self):
        """Deterministic authority declaration, distinct from Calculation Identity.

        Execution attempt remains operational and is deliberately not rehashed
        into financial identity. This payload is not persisted proof or a permit.
        """
        return Canonical.canonicalize(
            {
                "contract": "domain-mutation-declaration-v1",
                "domain": self.domain.value,
                "semantic_mode": self.semantic_mode.value,
                "entrypoint": self.entrypoint.identity,
                "entrypoint_class": self.entrypoint.classification,
                "writer": self.writer.identity,
                "writer_version": self.writer.version,
                "reason": self.reason,
                "identity": self.calculation_identity.canonical_payload()
                if self.calculation_identity
                else None,
                "configuration": self.configuration.as_dict() if self.configuration else None,
                "temporal": (
                    {
                        "cutoff_at": self.temporal.cutoff_at,
                        "as_of_session": self.temporal.latest_completed_session,
                        "calendar_version": self.temporal.calendar_version,
                        "context_id": self.temporal.context_id,
                        "bar_readiness_version": self.temporal.bar_readiness_version,
                        "exchange_timezone": self.temporal.exchange_timezone,
                    }
                    if self.temporal
                    else None
                ),
                "evidence": sorted(
                    (
                        {
                            "role": p.role,
                            "table": p.table,
                            "id": p.artifact_id,
                            "fingerprint": p.fingerprint,
                        }
                        for p in self.evidence
                    ),
                    key=Canonical.dumps,
                ),
                "eligibility": sorted(
                    (
                        {
                            "role": p.role,
                            "readiness": p.readiness.canonical_payload(),
                            "decision": p.decision.to_dto(),
                            "included": p.included,
                        }
                        for p in self.eligibility
                    ),
                    key=Canonical.dumps,
                ),
                "run_id": self.run_id,
                "pipeline_run_id": self.pipeline_run_id,
                "durable": self.durable,
            }
        )


@dataclass(frozen=True)
class MutationValidationResult:
    """Valid means declaration-consistent, never independently certified storage."""

    issues: tuple[str, ...]

    @property
    def valid(self):
        return not self.issues

    def require_valid(self):
        if self.issues:
            raise ValueError("MUTATION_AUTHORITY_REJECTED: " + "; ".join(self.issues))


def validate_mutation_context(context: DomainMutationContext) -> MutationValidationResult:
    policy = MUTATION_AUTHORITY_POLICIES[context.domain]
    issues = []
    if context.semantic_mode not in policy.modes:
        issues.append("semantic_mode: unsupported or uncertified")
    if context.writer.domain is not context.domain:
        issues.append("writer: domain mismatch")
    if not all(
        (
            context.entrypoint.identity.strip(),
            context.entrypoint.classification.strip(),
            context.writer.identity.strip(),
            context.writer.version.strip(),
            context.reason.strip(),
        )
    ):
        issues.append("descriptor: explicit initiator, writer/version and reason required")
    identity = context.calculation_identity
    if policy.identity and identity is None:
        issues.append("calculation_identity: required")
    if identity is not None:
        issues.extend("calculation_identity: " + i.dimension for i in identity.validate().issues)
        # Only policy-relevant dimensions must be KNOWN. Unused dimensions may be N/A.
        required = (
            [identity.algorithm.calculation_version, identity.source_lineage]
            if policy.identity
            else []
        )
        if policy.run:
            required.append(identity.ownership.run_id)
        if policy.temporal:
            required.extend(
                (
                    identity.temporal.as_of_session,
                    identity.temporal.calculation_cutoff,
                    identity.temporal.calendar,
                )
            )
        if any(d.state is not IdentityState.KNOWN for d in required):
            issues.append("calculation_identity: missing required known dimensions")
        if context.run_id is not None and (
            identity.ownership.run_id.state is not IdentityState.KNOWN
            or identity.ownership.run_id.value != context.run_id
        ):
            issues.append("run: identity mismatch")
    if policy.run and (type(context.run_id) is not int or context.run_id <= 0):
        issues.append("run: required")
    if (
        policy.pipeline_if_bound
        and identity is not None
        and identity.ownership.pipeline_id.state is IdentityState.KNOWN
        and context.pipeline_run_id != identity.ownership.pipeline_id.value
    ):
        issues.append("pipeline: bound identity cannot lose its pipeline authority")
    if (
        policy.pipeline_if_bound
        and context.pipeline_run_id is not None
        and (
            identity is None
            or identity.ownership.pipeline_id.state is not IdentityState.KNOWN
            or identity.ownership.pipeline_id.value != context.pipeline_run_id
            or identity.calculation_context.market_calculation_context_id.state
            is not IdentityState.KNOWN
            or identity.calculation_context.context_fingerprint.state is not IdentityState.KNOWN
        )
    ):
        issues.append("pipeline: exact identity/context required")
    temporal = context.temporal
    if policy.temporal and temporal is None:
        issues.append("temporal: required")
    if temporal is not None and (
        type(temporal.cutoff_at) is not datetime
        or temporal.cutoff_at.tzinfo is None
        or temporal.cutoff_at.utcoffset() is None
        or type(temporal.latest_completed_session) is not date
        or not temporal.calendar_version.strip()
        or not temporal.bar_readiness_version.strip()
        or not temporal.exchange_timezone.strip()
        or (
            temporal.context_id is not None
            and (type(temporal.context_id) is not int or temporal.context_id <= 0)
        )
    ):
        issues.append("temporal: explicit session, aware cutoff and versioned calendar required")
    if temporal is not None and identity is not None and policy.identity:
        calendar = identity.temporal.calendar.value
        if (
            identity.temporal.as_of_session.value != temporal.latest_completed_session
            or identity.temporal.calculation_cutoff.value != temporal.cutoff_at
            or calendar is None
            or calendar.calendar_version != temporal.calendar_version
            or calendar.exchange_timezone != temporal.exchange_timezone
            or identity.calculation_context.market_calculation_context_id.value
            != temporal.context_id
        ):
            issues.append("temporal: identity/context mismatch")
    config = context.configuration
    if config is not None and (
        config.coverage is not ConfigurationCoverage.COMPLETE_EFFECTIVE_CONFIGURATION
        or config.fingerprint.algorithm != "sha256"
        or len(config.fingerprint.digest) != 64
        or any(c not in "0123456789abcdef" for c in config.fingerprint.digest)
        or not config.fingerprint.proof_boundary.strip()
        or not config.resolution_contract.namespace.strip()
        or not config.resolution_contract.version.strip()
    ):
        issues.append("configuration: incomplete authority")
    if policy.configuration_namespace:
        if config is None:
            issues.append("configuration: required")
        elif not (
            config.namespace == policy.configuration_namespace
            or (
                context.domain is MutationDomain.IBMI
                and config.namespace.startswith("contextual.ibmi.")
            )
        ):
            issues.append("configuration: family mismatch")
        if (
            config is not None
            and identity is not None
            and (
                identity.configuration.effective_configuration.state is not IdentityState.KNOWN
                or identity.configuration.effective_configuration.value != config
            )
        ):
            issues.append("configuration: identity mismatch")
    pins = {pin.role: pin for pin in context.evidence}
    allowed_roles = set(policy.evidence_roles + policy.optional_evidence_roles)
    if set(pins) - allowed_roles:
        issues.append("evidence: undeclared dependency roles")
    if len(pins) != len(context.evidence):
        issues.append("evidence: duplicate roles")
    for role in policy.evidence_roles:
        if role not in pins:
            issues.append("evidence: missing " + role)
    if (
        context.domain is MutationDomain.WINNER_MODEL
        and context.semantic_mode is MutationSemanticMode.PUBLICATION
    ):
        for role in ("promotion_report", "governance_configuration"):
            if role not in pins:
                issues.append("evidence: missing " + role)
    for pin in context.evidence:
        if (
            not pin.role.strip()
            or not pin.table.strip()
            or not pin.fingerprint.strip()
            or isinstance(pin.artifact_id, bool)
            or type(pin.artifact_id) not in {int, str}
            or (isinstance(pin.artifact_id, str) and not pin.artifact_id.strip())
            or not pin.artifact_id
            or (isinstance(pin.artifact_id, int) and pin.artifact_id <= 0)
        ):
            issues.append("evidence: invalid exact reference " + pin.role)
    permissions = {p.role: p for p in context.eligibility}
    if len(permissions) != len(context.eligibility):
        issues.append("readiness: duplicate roles")
    for role in policy.eligibility_roles:
        if role not in permissions:
            issues.append("readiness: missing " + role)
    for role in policy.eligibility_if_pinned:
        if role in pins and role not in permissions:
            issues.append("readiness: missing optional source decision " + role)
    for role, permission in permissions.items():
        pin = pins.get(role)
        readiness, decision = permission.readiness, permission.decision
        if (
            not decision.policy_version.strip()
            or not decision.producer_readiness_fingerprint
            or decision.producer_readiness_fingerprint != readiness.fingerprint()
            or decision.producer_evidence_id != readiness.evidence_id
            or pin is None
            or decision.producer_evidence_id != pin.artifact_id
        ):
            issues.append("readiness: exact evidence binding mismatch " + role)
        consumer = (
            "WINNER" if context.domain is MutationDomain.WINNER_PREDICTION else context.domain.value
        )
        if decision.consumer != consumer:
            issues.append("readiness: consumer mismatch " + role)
        if permission.included and decision.status is not ConsumerEligibilityStatus.ELIGIBLE:
            issues.append("readiness: included source lacks permission " + role)
        if role in policy.mandatory_consumption and not permission.included:
            issues.append("readiness: required consumption denied " + role)
    if (
        policy.fence_if_durable
        and (context.durable or context.execution is not None)
        and (
            context.execution is None
            or type(context.execution.job_id) is not int
            or context.execution.job_id <= 0
            or not context.execution.execution_token.strip()
        )
    ):
        issues.append("execution: durable ownership required")
    return MutationValidationResult(tuple(sorted(set(issues))))


def fence_mutation_transaction(db: Session, context: DomainMutationContext) -> None:
    """Validate declarations and reuse Phase-0 locking in the caller's transaction.

    Does not commit. Writer must additionally resolve evidence/configuration pins,
    validate domain compatibility/eligibility and projection order before writing.
    Invoke again after any intermediate commit; row locks do not survive commits.
    """
    if not isinstance(db, Session):
        raise TypeError("mutation transaction requires a real SQLAlchemy Session")
    validate_mutation_context(context).require_valid()
    if context.execution is not None:
        assert_current_execution_ownership(
            db,
            job_id=context.execution.job_id,
            execution_token=context.execution.execution_token,
        )
