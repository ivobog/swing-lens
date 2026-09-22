"""Read-only reconstruction of native Setup/Lifecycle/Alert decision context.

The resolver intentionally reads only immutable, content-addressed evidence.  Mutable
projections (including current setup selections, episodes, alert rules and PriceBars)
are not inputs and therefore cannot silently replace missing historical authority.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleTransitionEvidence,
    SignalAlertDecisionEvidence,
    SignalAlertRuleEvidence,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.decision_mutation_authority import validate_retained_decision
from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY
from app.services.original_context_reconstruction import (
    AuthorityAvailability,
    AuthorityDimension,
    AuthorityReference,
    AuthorityReferenceKind,
    AuthorityResolution,
    CodeIdentityStatus,
    OriginalContextReconstructionManifest,
    ReconstructionAuthorization,
    ReconstructionComparison,
    ReconstructionComparisonStatus,
    ReconstructionMode,
    ReconstructionResult,
    authorize_reconstruction,
)
from app.services.producer_readiness import (
    READINESS_PAYLOAD_KEY,
    readiness_from_evidence,
    readiness_from_lifecycle_evaluation,
)


class ReconstructionTargetKind(StrEnum):
    SETUP = "SETUP"
    LIFECYCLE_EVALUATION = "LIFECYCLE_EVALUATION"
    LIFECYCLE_TRANSITION = "LIFECYCLE_TRANSITION"
    ALERT_DECISION = "ALERT_DECISION"


class SetupOperationMode(StrEnum):
    ORIGINAL_CONTEXT_RECONSTRUCTION = "ORIGINAL_CONTEXT_RECONSTRUCTION"
    CURRENT_RULES_RETROSPECTIVE = "CURRENT_RULES_RETROSPECTIVE"
    CURRENT_CALCULATION = "CURRENT_CALCULATION"
    CURRENT_STATE_REPAIR = "CURRENT_STATE_REPAIR"


@dataclass(frozen=True, order=True)
class ReconstructionTarget:
    kind: ReconstructionTargetKind
    evidence_id: int

    def __post_init__(self) -> None:
        if self.evidence_id <= 0:
            raise ValueError("reconstruction evidence identity must be positive")


@dataclass(frozen=True)
class SetupOriginalContextResolution:
    target: ReconstructionTarget
    manifest: OriginalContextReconstructionManifest
    authorization: ReconstructionAuthorization
    reconstructed_output: dict[str, Any] | None
    result: ReconstructionResult


@dataclass(frozen=True)
class _LoadedEvidence:
    setups: dict[int, CoreCalculationEvidence]
    source_edges: dict[int, dict[str, int]]
    sources: dict[int, CoreCalculationEvidence]
    evaluations: dict[int, SetupLifecycleEvaluationEvidence]
    transitions: dict[int, SetupLifecycleTransitionEvidence]
    alerts: dict[int, SignalAlertDecisionEvidence]
    rules: dict[int, SignalAlertRuleEvidence]
    validated_core_ids: set[int]
    setup_authority_cache: dict[int, tuple[AuthorityResolution, ...]]


_SETUP_REQUIRED_ROLES = frozenset({"technical", "combined", "ranking_metadata", "regime", "sector"})
_SETUP_FORBIDDEN_ROLES = frozenset({"ceri", "winner", "winner_probability"})


def classify_operation_mode(
    value: str | ReconstructionMode | SetupOperationMode,
) -> SetupOperationMode:
    """Keep historical reconstruction distinct from replay, current work and repair."""

    if isinstance(value, SetupOperationMode):
        return value
    if isinstance(value, ReconstructionMode):
        return {
            ReconstructionMode.ORIGINAL_CONTEXT: SetupOperationMode.ORIGINAL_CONTEXT_RECONSTRUCTION,
            ReconstructionMode.CURRENT_RULES_RETROSPECTIVE: (
                SetupOperationMode.CURRENT_RULES_RETROSPECTIVE
            ),
            ReconstructionMode.CURRENT: SetupOperationMode.CURRENT_CALCULATION,
        }[value]
    normalized = value.strip().upper()
    aliases = {
        "ORIGINAL_CONTEXT": SetupOperationMode.ORIGINAL_CONTEXT_RECONSTRUCTION,
        "RECONSTRUCTION": SetupOperationMode.ORIGINAL_CONTEXT_RECONSTRUCTION,
        "REPLAY": SetupOperationMode.CURRENT_RULES_RETROSPECTIVE,
        "CURRENT_RULES_RETROSPECTIVE": SetupOperationMode.CURRENT_RULES_RETROSPECTIVE,
        "CURRENT": SetupOperationMode.CURRENT_CALCULATION,
        "DIRECT": SetupOperationMode.CURRENT_CALCULATION,
        "MAINTENANCE": SetupOperationMode.CURRENT_STATE_REPAIR,
        "CURRENT_STATE_REPAIR": SetupOperationMode.CURRENT_STATE_REPAIR,
    }
    if normalized not in aliases:
        raise ValueError(f"unknown setup operation mode: {value!r}")
    return aliases[normalized]


def _reference(reference_type: str, row: Any, fingerprint: str) -> AuthorityReference:
    return AuthorityReference(
        reference_type=reference_type,
        reference_id=f"{row.id}:{fingerprint}",
        kind=AuthorityReferenceKind.CONTENT_ADDRESSED_HISTORICAL,
    )


def _root_reference(reference_type: str, row: Any, member: str) -> AuthorityReference:
    return AuthorityReference(
        reference_type=reference_type,
        reference_id=f"{row.id}:{row.payload_fingerprint}:{member}=ROOT",
        kind=AuthorityReferenceKind.CONTENT_ADDRESSED_HISTORICAL,
    )


def _exact(
    dimension: AuthorityDimension,
    references: Iterable[AuthorityReference],
    *,
    material: bool = True,
    reason: str | None = None,
) -> AuthorityResolution:
    return AuthorityResolution(
        dimension=dimension,
        availability=AuthorityAvailability.EXACT,
        material=material,
        references=tuple(references),
        reason=reason,
    )


def _unavailable(
    dimension: AuthorityDimension,
    reason: str,
    *,
    permanent: bool = False,
    material: bool = True,
) -> AuthorityResolution:
    return AuthorityResolution(
        dimension=dimension,
        availability=(
            AuthorityAvailability.LEGACY_UNKNOWN if permanent else AuthorityAvailability.UNAVAILABLE
        ),
        material=material,
        reason=reason,
        permanently_unavailable=permanent,
    )


def _not_applicable(dimension: AuthorityDimension, reason: str) -> AuthorityResolution:
    return AuthorityResolution(
        dimension=dimension,
        availability=AuthorityAvailability.NOT_APPLICABLE,
        material=False,
        reason=reason,
    )


def _bounded_code(reason: str) -> AuthorityResolution:
    return AuthorityResolution(
        dimension=AuthorityDimension.CODE_DEPLOYMENT_IDENTITY,
        availability=AuthorityAvailability.BOUNDED,
        material=False,
        reason=reason,
    )


def _verify_payload_row(row: Any, *, contract: str, payload_key: str = "payload") -> None:
    validate_retained_decision(None, row, contract=contract, payload_key=payload_key)


def _verify_configuration_payload(payload: dict[str, Any]) -> str:
    frozen = payload.get(CONFIGURATION_PAYLOAD_KEY)
    if not isinstance(frozen, dict):
        raise ValueError("frozen effective configuration is unavailable")
    from app.services.decision_effective_configuration import configuration_from_payload

    return configuration_from_payload(frozen).snapshot.semantic_hash


def _verify_calculation_identity(row: Any) -> str:
    payload = row.payload_json
    encoded = payload.get("calculation_identity")
    claimed = payload.get("calculation_identity_fingerprint")
    if not isinstance(encoded, dict) or not isinstance(claimed, str):
        raise ValueError("frozen calculation identity is unavailable")
    from app.services.calculation_identity import CalculationIdentity

    actual = str(CalculationIdentity.from_canonical_payload(encoded).fingerprint())
    if actual != claimed:
        raise ValueError("frozen calculation identity fingerprint mismatch")
    if hasattr(row, "calculation_identity_fingerprint") and (
        row.calculation_identity_fingerprint != claimed
    ):
        raise ValueError("calculation identity column mismatch")
    return claimed


def _require_known_identity_dimensions(identity_payload: dict[str, Any]) -> None:
    """Reject a validly encoded identity whose material history is still unknown."""

    required_paths = (
        ("subject", "ticker"),
        ("temporal", "as_of_session"),
        ("temporal", "calculation_cutoff"),
        ("temporal", "calendar"),
        ("configuration", "effective_configuration"),
        ("algorithm", "calculation_version"),
        ("algorithm", "engine_version"),
        ("source_lineage",),
    )
    for path in required_paths:
        node: Any = identity_payload
        for member in path:
            if not isinstance(node, dict) or member not in node:
                raise ValueError(f"calculation identity dimension is missing: {'.'.join(path)}")
            node = node[member]
        if not isinstance(node, dict) or node.get("state") != "KNOWN":
            raise ValueError(f"calculation identity dimension is not exact: {'.'.join(path)}")


def _assert_explicit_member(row: Any, member: str) -> Any:
    if member not in row.payload_json:
        raise ValueError(f"historical {member} authority is unavailable")
    value = row.payload_json[member]
    if Canonical.dumps({member: getattr(row, member)}) != Canonical.dumps({member: value}):
        raise ValueError(f"historical {member} column mismatch")
    return value


def _row_ref(row: Any, reference_type: str) -> AuthorityReference:
    return _reference(reference_type, row, row.payload_fingerprint)


def _validate_core_once(row: CoreCalculationEvidence, loaded: _LoadedEvidence) -> None:
    if row.id in loaded.validated_core_ids:
        return
    validate_retained_decision(None, row, contract="unused")
    loaded.validated_core_ids.add(row.id)


def _integrity_failure_authority(
    kind: ReconstructionTargetKind, reason: str
) -> tuple[AuthorityResolution, ...]:
    common = (
        AuthorityDimension.CALCULATION_IDENTITY,
        AuthorityDimension.WORK_SCOPE_IDENTITY,
        AuthorityDimension.BUSINESS_CUTOFF,
        AuthorityDimension.MARKET_SESSION_CALENDAR,
        AuthorityDimension.EFFECTIVE_CONFIGURATION,
        AuthorityDimension.READINESS_POLICY,
        AuthorityDimension.SOURCE_EVIDENCE,
        AuthorityDimension.DECISION_INPUTS,
        AuthorityDimension.ALGORITHM_SCHEMA_VERSION,
    )
    domain = {
        ReconstructionTargetKind.SETUP: (),
        ReconstructionTargetKind.LIFECYCLE_EVALUATION: (
            AuthorityDimension.PREDECESSOR_STATE,
            AuthorityDimension.PRIOR_DECISION_STATE,
        ),
        ReconstructionTargetKind.LIFECYCLE_TRANSITION: (
            AuthorityDimension.PREDECESSOR_STATE,
            AuthorityDimension.PRIOR_DECISION_STATE,
        ),
        ReconstructionTargetKind.ALERT_DECISION: (
            AuthorityDimension.PREDECESSOR_STATE,
            AuthorityDimension.PRIOR_DECISION_STATE,
            AuthorityDimension.RULE_POLICY_VERSION,
        ),
    }[kind]
    dimensions = (*common, *domain)
    return tuple(_unavailable(dimension, reason, permanent=True) for dimension in dimensions) + (
        _bounded_code("deployment commit was not retained; semantic evidence is content-addressed"),
    )


def _common_non_material() -> tuple[AuthorityResolution, ...]:
    return (
        _not_applicable(
            AuthorityDimension.REFRESH_CYCLE_IDENTITY,
            "native Setup/Lifecycle decisions are addressed by decision session and cutoff",
        ),
        _not_applicable(
            AuthorityDimension.ACQUISITION_PLAN_IDENTITY,
            "no acquisition-plan replay is performed by this resolver",
        ),
        _not_applicable(
            AuthorityDimension.SOURCE_REVISION,
            "content-addressed native evidence replaces mutable source revisions",
        ),
        _not_applicable(
            AuthorityDimension.PROVIDER_IDENTITY,
            "provider identity is encapsulated by retained upstream evidence",
        ),
        _not_applicable(
            AuthorityDimension.CURRENCY_AUTHORITY,
            "no currency conversion is executed during semantic reconstruction",
        ),
        _not_applicable(
            AuthorityDimension.TEMPORAL_POSSESSION_AUTHORITY,
            "the resolver consumes already-certified decision evidence",
        ),
    )


def _setup_authority(
    row: CoreCalculationEvidence, loaded: _LoadedEvidence
) -> tuple[AuthorityResolution, ...]:
    cached = loaded.setup_authority_cache.get(row.id)
    if cached is not None:
        return cached
    _validate_core_once(row, loaded)
    if row.artifact_kind != "SETUP":
        raise ValueError("target evidence is not native Setup evidence")
    _require_known_identity_dimensions(row.calculation_identity_json)
    identity = row.calculation_identity_fingerprint
    if READINESS_PAYLOAD_KEY not in row.payload_json:
        raise ValueError("frozen Setup readiness authority is unavailable")
    readiness = readiness_from_evidence(row)
    if readiness.calculation_identity_fingerprint != identity:
        raise ValueError("Setup readiness identity mismatch")
    edges = loaded.source_edges.get(row.id, {})
    if edges != {str(key): int(value) for key, value in row.source_evidence_ids_json.items()}:
        raise ValueError("Setup source-edge graph does not match the retained source manifest")
    missing = sorted(_SETUP_REQUIRED_ROLES - set(edges))
    forbidden = sorted(_SETUP_FORBIDDEN_ROLES & set(edges))
    if missing or forbidden:
        raise ValueError(
            f"Setup source graph mismatch: missing={missing!r}, forbidden={forbidden!r}"
        )
    expected_kinds = {
        "technical": "TECHNICAL",
        "combined": "COMBINED",
        "ranking_metadata": "RANKING",
        "regime": "REGIME",
        "sector": "SECTOR",
        "fundamental": "FUNDAMENTAL",
    }
    source_refs: list[AuthorityReference] = []
    for role, source_id in sorted(edges.items()):
        source = loaded.sources.get(source_id)
        if source is None:
            raise ValueError(f"retained Setup source is missing: {role}")
        _validate_core_once(source, loaded)
        if READINESS_PAYLOAD_KEY not in source.payload_json:
            raise ValueError(f"retained Setup source readiness is missing: {role}")
        source_readiness = readiness_from_evidence(source)
        if source_readiness.calculation_identity_fingerprint != (
            source.calculation_identity_fingerprint
        ):
            raise ValueError(f"retained Setup source readiness mismatch: {role}")
        if role in expected_kinds and source.artifact_kind != expected_kinds[role]:
            raise ValueError(f"retained Setup source kind mismatch: {role}")
        source_refs.append(_reference(f"setup_source:{role}", source, source.payload_fingerprint))
    row_reference = _reference("core_setup_evidence", row, row.payload_fingerprint)
    identity_reference = _reference(
        "setup_calculation_identity", row, row.calculation_identity_fingerprint
    )
    authority = (
        _exact(AuthorityDimension.CALCULATION_IDENTITY, (identity_reference,)),
        _exact(AuthorityDimension.WORK_SCOPE_IDENTITY, (identity_reference,)),
        _exact(AuthorityDimension.BUSINESS_CUTOFF, (identity_reference,)),
        _exact(AuthorityDimension.MARKET_SESSION_CALENDAR, (identity_reference,)),
        _exact(AuthorityDimension.EFFECTIVE_CONFIGURATION, (identity_reference,)),
        _exact(
            AuthorityDimension.READINESS_POLICY,
            (row_reference,),
            reason="frozen Setup and upstream Technical readiness; no current PriceBar query",
        ),
        _exact(AuthorityDimension.SOURCE_EVIDENCE, source_refs),
        _not_applicable(
            AuthorityDimension.PREDECESSOR_STATE,
            "a Setup calculation is stateless; lifecycle predecessor authority is separate",
        ),
        _not_applicable(
            AuthorityDimension.PRIOR_DECISION_STATE,
            "a Setup calculation is stateless; lifecycle predecessor authority is separate",
        ),
        _not_applicable(
            AuthorityDimension.RULE_POLICY_VERSION,
            "alert rule policy does not participate in Setup calculation",
        ),
        _exact(AuthorityDimension.DECISION_INPUTS, (row_reference, *source_refs)),
        _exact(AuthorityDimension.ALGORITHM_SCHEMA_VERSION, (identity_reference,)),
        _bounded_code("engine/schema identity retained; deployment commit was not retained"),
        *_common_non_material(),
    )
    loaded.setup_authority_cache[row.id] = authority
    return authority


def _predecessor_references(
    row: SetupLifecycleEvaluationEvidence,
    loaded: _LoadedEvidence,
) -> tuple[AuthorityReference, ...]:
    references: list[AuthorityReference] = []
    resolved: dict[str, Any] = {}
    for member, rows, label in (
        ("prior_evaluation_evidence_id", loaded.evaluations, "prior_lifecycle_evaluation"),
        ("prior_transition_evidence_id", loaded.transitions, "prior_lifecycle_transition"),
    ):
        value = _assert_explicit_member(row, member)
        if value is None:
            references.append(_root_reference(label, row, member))
        else:
            prior = rows.get(value)
            if prior is None:
                raise ValueError(f"historical {member} row is missing")
            contract = (
                "setup-lifecycle-evaluation-evidence-v1"
                if member.startswith("prior_evaluation")
                else "setup-lifecycle-transition-evidence-v1"
            )
            _verify_payload_row(
                prior,
                contract=contract,
                payload_key="payload_fingerprint"
                if member.startswith("prior_evaluation")
                else "payload",
            )
            if (
                prior.ticker != row.ticker
                or prior.timeframe != row.timeframe
                or prior.setup_family != row.setup_family
            ):
                raise ValueError(f"historical {member} scope mismatch")
            predecessor_session = (
                prior.decision_session
                if member.startswith("prior_evaluation")
                else prior.effective_session
            )
            if predecessor_session > row.decision_session:
                raise ValueError(f"future {member} cannot be a predecessor")
            resolved[member] = prior
            references.append(_row_ref(prior, label))
    prior_evaluation = resolved.get("prior_evaluation_evidence_id")
    prior_transition = resolved.get("prior_transition_evidence_id")
    if prior_evaluation is not None and prior_transition is not None:
        if _assert_explicit_member(prior_transition, "evaluation_evidence_id") != (
            prior_evaluation.id
        ):
            raise ValueError("historical lifecycle predecessor cross-link mismatch")
    return tuple(references)


def _verify_evaluation_chain(
    row: SetupLifecycleEvaluationEvidence, loaded: _LoadedEvidence
) -> tuple[AuthorityReference, ...]:
    references: list[AuthorityReference] = []
    current = row
    seen: set[int] = set()
    while True:
        if current.id in seen:
            raise ValueError("lifecycle evaluation predecessor cycle")
        seen.add(current.id)
        predecessor_id = _assert_explicit_member(current, "prior_evaluation_evidence_id")
        if predecessor_id is None:
            references.append(
                _root_reference(
                    "prior_lifecycle_evaluation", current, "prior_evaluation_evidence_id"
                )
            )
            break
        predecessor = loaded.evaluations.get(predecessor_id)
        if predecessor is None:
            raise ValueError("historical lifecycle evaluation predecessor is missing")
        _verify_payload_row(
            predecessor,
            contract="setup-lifecycle-evaluation-evidence-v1",
            payload_key="payload_fingerprint",
        )
        if (
            predecessor.ticker,
            predecessor.timeframe,
            predecessor.setup_family,
        ) != (row.ticker, row.timeframe, row.setup_family):
            raise ValueError("historical lifecycle evaluation predecessor scope mismatch")
        if predecessor.decision_session > current.decision_session:
            raise ValueError("future lifecycle evaluation cannot be a predecessor")
        references.append(_row_ref(predecessor, "prior_lifecycle_evaluation"))
        current = predecessor
    return tuple(references)


def _verify_transition_chain(
    row: SetupLifecycleTransitionEvidence, loaded: _LoadedEvidence
) -> tuple[AuthorityReference, ...]:
    references: list[AuthorityReference] = []
    current = row
    seen: set[int] = set()
    while True:
        if current.id in seen:
            raise ValueError("lifecycle transition predecessor cycle")
        seen.add(current.id)
        predecessor_id = _assert_explicit_member(current, "prior_transition_evidence_id")
        if predecessor_id is None:
            references.append(
                _root_reference(
                    "prior_lifecycle_transition", current, "prior_transition_evidence_id"
                )
            )
            break
        predecessor = loaded.transitions.get(predecessor_id)
        if predecessor is None:
            raise ValueError("historical lifecycle transition predecessor is missing")
        _verify_payload_row(predecessor, contract="setup-lifecycle-transition-evidence-v1")
        if (
            predecessor.ticker,
            predecessor.timeframe,
            predecessor.setup_family,
        ) != (row.ticker, row.timeframe, row.setup_family):
            raise ValueError("historical lifecycle transition predecessor scope mismatch")
        if predecessor.effective_session > current.effective_session:
            raise ValueError("future lifecycle transition cannot be a predecessor")
        references.append(_row_ref(predecessor, "prior_lifecycle_transition"))
        current = predecessor
    return tuple(references)


def _evaluation_authority(
    row: SetupLifecycleEvaluationEvidence, loaded: _LoadedEvidence
) -> tuple[AuthorityResolution, ...]:
    _verify_payload_row(
        row,
        contract="setup-lifecycle-evaluation-evidence-v1",
        payload_key="payload_fingerprint",
    )
    identity = _verify_calculation_identity(row)
    _require_known_identity_dimensions(row.payload_json["calculation_identity"])
    semantic_hash = _verify_configuration_payload(row.payload_json)
    if row.config_hash != semantic_hash:
        raise ValueError("lifecycle configuration hash mismatch")
    readiness_from_lifecycle_evaluation(row)
    setup = loaded.setups.get(row.setup_evidence_id)
    if setup is None:
        raise ValueError("lifecycle Setup evidence is missing")
    setup_authority = _setup_authority(setup, loaded)
    source_resolution = next(
        item for item in setup_authority if item.dimension is AuthorityDimension.SOURCE_EVIDENCE
    )
    for member in ("calculation_cutoff_at", "calendar_version", "setup_evidence_id"):
        if _assert_explicit_member(row, member) is None:
            raise ValueError(f"historical lifecycle {member} authority is unavailable")
    predecessor_refs = tuple(
        dict.fromkeys(
            (*_predecessor_references(row, loaded), *_verify_evaluation_chain(row, loaded))
        )
    )
    row_ref = _row_ref(row, "lifecycle_evaluation")
    identity_ref = _reference("lifecycle_calculation_identity", row, identity)
    config_ref = _reference("lifecycle_effective_configuration", row, semantic_hash)
    return (
        _exact(AuthorityDimension.CALCULATION_IDENTITY, (identity_ref,)),
        _exact(AuthorityDimension.WORK_SCOPE_IDENTITY, (row_ref,)),
        _exact(AuthorityDimension.BUSINESS_CUTOFF, (row_ref,)),
        _exact(AuthorityDimension.MARKET_SESSION_CALENDAR, (row_ref,)),
        _exact(AuthorityDimension.EFFECTIVE_CONFIGURATION, (config_ref,)),
        _exact(AuthorityDimension.READINESS_POLICY, (row_ref,)),
        _exact(AuthorityDimension.SOURCE_EVIDENCE, source_resolution.references),
        _exact(AuthorityDimension.PREDECESSOR_STATE, predecessor_refs),
        _exact(AuthorityDimension.PRIOR_DECISION_STATE, predecessor_refs),
        _not_applicable(
            AuthorityDimension.RULE_POLICY_VERSION,
            "alert rules do not participate in lifecycle evaluation",
        ),
        _exact(
            AuthorityDimension.DECISION_INPUTS, (row_ref, _row_ref(setup, "core_setup_evidence"))
        ),
        _exact(AuthorityDimension.ALGORITHM_SCHEMA_VERSION, (identity_ref,)),
        _bounded_code("engine identity retained; deployment commit was not retained"),
        *_common_non_material(),
    )


def _transition_authority(
    row: SetupLifecycleTransitionEvidence, loaded: _LoadedEvidence
) -> tuple[AuthorityResolution, ...]:
    _verify_payload_row(row, contract="setup-lifecycle-transition-evidence-v1")
    evaluation = loaded.evaluations.get(row.evaluation_evidence_id)
    if evaluation is None:
        raise ValueError("transition evaluation evidence is missing")
    authority = list(_evaluation_authority(evaluation, loaded))
    if row.setup_evidence_id != evaluation.setup_evidence_id:
        raise ValueError("transition Setup evidence mismatch")
    if evaluation.decision_session > row.effective_session:
        raise ValueError("future lifecycle evaluation cannot produce a transition")
    for member in ("evaluation_evidence_id", "setup_evidence_id", "effective_session"):
        if _assert_explicit_member(row, member) is None:
            raise ValueError(f"historical transition {member} authority is unavailable")
    prior_refs = _verify_transition_chain(row, loaded)
    transition_ref = _row_ref(row, "lifecycle_transition")
    replacements = {
        AuthorityDimension.PREDECESSOR_STATE: _exact(
            AuthorityDimension.PREDECESSOR_STATE, prior_refs
        ),
        AuthorityDimension.PRIOR_DECISION_STATE: _exact(
            AuthorityDimension.PRIOR_DECISION_STATE, prior_refs
        ),
        AuthorityDimension.DECISION_INPUTS: _exact(
            AuthorityDimension.DECISION_INPUTS,
            (transition_ref, _row_ref(evaluation, "lifecycle_evaluation")),
        ),
    }
    return tuple(replacements.get(item.dimension, item) for item in authority)


def _alert_predecessor_references(
    row: SignalAlertDecisionEvidence, loaded: _LoadedEvidence
) -> tuple[AuthorityReference, ...]:
    references: list[AuthorityReference] = []
    for member in ("cooldown_predecessor_evidence_id", "dedup_predecessor_evidence_id"):
        current = row
        seen: set[int] = set()
        while True:
            if current.id in seen:
                raise ValueError(f"historical {member} cycle")
            seen.add(current.id)
            value = _assert_explicit_member(current, member)
            if value is None:
                references.append(_root_reference("alert_predecessor", current, member))
                break
            prior = loaded.alerts.get(value)
            if prior is None:
                raise ValueError(f"historical {member} row is missing")
            _verify_payload_row(prior, contract="signal-alert-decision-evidence-v1")
            if prior.effective_session > current.effective_session:
                raise ValueError("future alert cannot be a historical predecessor")
            current_rule = loaded.rules.get(current.rule_evidence_id)
            prior_rule = loaded.rules.get(prior.rule_evidence_id)
            if current_rule is None or prior_rule is None:
                raise ValueError("historical predecessor rule evidence is missing")
            _verify_payload_row(current_rule, contract="signal-alert-rule-evidence-v1")
            _verify_payload_row(prior_rule, contract="signal-alert-rule-evidence-v1")
            if (
                prior.ticker != current.ticker
                or prior.timeframe != current.timeframe
                or prior_rule.rule_id != current_rule.rule_id
                or prior.decision != "GENERATED"
            ):
                raise ValueError(f"historical {member} scope mismatch")
            if member.startswith("cooldown") and prior.semantic_key != current.semantic_key:
                raise ValueError("historical cooldown predecessor semantic-key mismatch")
            if member.startswith("dedup") and prior.source_event_key != current.source_event_key:
                raise ValueError("historical dedup predecessor source-key mismatch")
            references.append(_row_ref(prior, member.removesuffix("_id")))
            current = prior
    return tuple(references)


def _alert_authority(
    row: SignalAlertDecisionEvidence, loaded: _LoadedEvidence
) -> tuple[AuthorityResolution, ...]:
    _verify_payload_row(row, contract="signal-alert-decision-evidence-v1")
    identity = _verify_calculation_identity(row)
    _require_known_identity_dimensions(row.payload_json["calculation_identity"])
    semantic_hash = _verify_configuration_payload(row.payload_json)
    for member in (
        "rule_evidence_id",
        "setup_evidence_id",
        "lifecycle_evaluation_evidence_id",
        "lifecycle_transition_evidence_id",
        "calculation_cutoff_at",
        "calendar_version",
    ):
        if member not in row.payload_json or Canonical.dumps(
            {member: getattr(row, member)}
        ) != Canonical.dumps({member: row.payload_json[member]}):
            raise ValueError(f"historical alert {member} authority mismatch")
    if row.calculation_cutoff_at is None or row.calendar_version is None:
        raise ValueError("historical alert cutoff/calendar authority is unavailable")
    rule = loaded.rules.get(row.rule_evidence_id)
    if rule is None:
        raise ValueError("historical alert rule evidence is missing")
    _verify_payload_row(rule, contract="signal-alert-rule-evidence-v1")
    if rule.config_version != rule.payload_json.get("config_version"):
        raise ValueError("historical alert rule configuration mismatch")
    source_refs: list[AuthorityReference] = []
    if row.setup_evidence_id is not None:
        setup = loaded.setups.get(row.setup_evidence_id)
        if setup is None:
            raise ValueError("historical alert Setup evidence is missing")
        _setup_authority(setup, loaded)
        source_refs.append(_row_ref(setup, "core_setup_evidence"))
    if row.lifecycle_evaluation_evidence_id is not None:
        evaluation = loaded.evaluations.get(row.lifecycle_evaluation_evidence_id)
        if evaluation is None:
            raise ValueError("historical alert lifecycle evaluation is missing")
        _evaluation_authority(evaluation, loaded)
        if row.setup_evidence_id not in {None, evaluation.setup_evidence_id}:
            raise ValueError("historical alert Setup/evaluation binding mismatch")
        source_refs.append(_row_ref(evaluation, "lifecycle_evaluation"))
    if row.lifecycle_transition_evidence_id is not None:
        transition = loaded.transitions.get(row.lifecycle_transition_evidence_id)
        if transition is None:
            raise ValueError("historical alert lifecycle transition is missing")
        _transition_authority(transition, loaded)
        if row.lifecycle_evaluation_evidence_id != transition.evaluation_evidence_id:
            raise ValueError("historical alert transition/evaluation binding mismatch")
        source_refs.append(_row_ref(transition, "lifecycle_transition"))
    if not source_refs:
        raise ValueError("historical alert source evidence is unavailable")
    predecessor_refs = _alert_predecessor_references(row, loaded)
    row_ref = _row_ref(row, "alert_decision")
    identity_ref = _reference("alert_calculation_identity", row, identity)
    rule_ref = _row_ref(rule, "alert_rule")
    config_ref = _reference("alert_effective_configuration", row, semantic_hash)
    return (
        _exact(AuthorityDimension.CALCULATION_IDENTITY, (identity_ref,)),
        _exact(AuthorityDimension.WORK_SCOPE_IDENTITY, (row_ref,)),
        _exact(AuthorityDimension.BUSINESS_CUTOFF, (row_ref,)),
        _exact(AuthorityDimension.MARKET_SESSION_CALENDAR, (row_ref,)),
        _exact(AuthorityDimension.EFFECTIVE_CONFIGURATION, (config_ref, rule_ref)),
        _exact(
            AuthorityDimension.READINESS_POLICY,
            tuple(source_refs),
            reason="readiness is inherited from the exact native Setup/Lifecycle source",
        ),
        _exact(AuthorityDimension.SOURCE_EVIDENCE, tuple(source_refs)),
        _exact(AuthorityDimension.PREDECESSOR_STATE, predecessor_refs),
        _exact(AuthorityDimension.PRIOR_DECISION_STATE, predecessor_refs),
        _exact(AuthorityDimension.RULE_POLICY_VERSION, (rule_ref,)),
        _exact(AuthorityDimension.DECISION_INPUTS, (row_ref, rule_ref, *source_refs)),
        _exact(AuthorityDimension.ALGORITHM_SCHEMA_VERSION, (identity_ref,)),
        _bounded_code("alert semantic identity retained; deployment commit was not retained"),
        *_common_non_material(),
    )


def _select_map(db: Session, model: Any, ids: set[int]) -> dict[int, Any]:
    if not ids:
        return {}
    return {row.id: row for row in db.scalars(select(model).where(model.id.in_(sorted(ids))))}


def _expand_predecessors(
    db: Session,
    rows: dict[int, Any],
    model: Any,
    members: tuple[str, ...],
) -> None:
    while True:
        wanted = {
            int(value)
            for row in rows.values()
            for member in members
            if (value := getattr(row, member)) is not None and int(value) not in rows
        }
        if not wanted:
            return
        found = _select_map(db, model, wanted)
        rows.update(found)
        if set(found) != wanted:
            return


def _load(db: Session, targets: tuple[ReconstructionTarget, ...]) -> _LoadedEvidence:
    setup_ids = {
        target.evidence_id for target in targets if target.kind is ReconstructionTargetKind.SETUP
    }
    evaluation_ids = {
        target.evidence_id
        for target in targets
        if target.kind is ReconstructionTargetKind.LIFECYCLE_EVALUATION
    }
    transition_ids = {
        target.evidence_id
        for target in targets
        if target.kind is ReconstructionTargetKind.LIFECYCLE_TRANSITION
    }
    alert_ids = {
        target.evidence_id
        for target in targets
        if target.kind is ReconstructionTargetKind.ALERT_DECISION
    }
    alerts = _select_map(db, SignalAlertDecisionEvidence, alert_ids)
    _expand_predecessors(
        db,
        alerts,
        SignalAlertDecisionEvidence,
        ("cooldown_predecessor_evidence_id", "dedup_predecessor_evidence_id"),
    )
    evaluation_ids.update(
        row.lifecycle_evaluation_evidence_id
        for row in alerts.values()
        if row.lifecycle_evaluation_evidence_id is not None
    )
    transition_ids.update(
        row.lifecycle_transition_evidence_id
        for row in alerts.values()
        if row.lifecycle_transition_evidence_id is not None
    )
    transitions = _select_map(db, SetupLifecycleTransitionEvidence, transition_ids)
    _expand_predecessors(
        db, transitions, SetupLifecycleTransitionEvidence, ("prior_transition_evidence_id",)
    )
    evaluation_ids.update(row.evaluation_evidence_id for row in transitions.values())
    evaluations = _select_map(db, SetupLifecycleEvaluationEvidence, evaluation_ids)
    _expand_predecessors(
        db,
        evaluations,
        SetupLifecycleEvaluationEvidence,
        ("prior_evaluation_evidence_id",),
    )
    transition_ids.update(
        row.prior_transition_evidence_id
        for row in evaluations.values()
        if row.prior_transition_evidence_id is not None
    )
    missing_transition_ids = transition_ids - set(transitions)
    transitions.update(_select_map(db, SetupLifecycleTransitionEvidence, missing_transition_ids))
    _expand_predecessors(
        db, transitions, SetupLifecycleTransitionEvidence, ("prior_transition_evidence_id",)
    )
    setup_ids.update(row.setup_evidence_id for row in evaluations.values())
    setup_ids.update(row.setup_evidence_id for row in transitions.values())
    setup_ids.update(
        row.setup_evidence_id for row in alerts.values() if row.setup_evidence_id is not None
    )
    setups = _select_map(db, CoreCalculationEvidence, setup_ids)
    rule_ids = {row.rule_evidence_id for row in alerts.values()}
    rules = _select_map(db, SignalAlertRuleEvidence, rule_ids)
    source_edges: dict[int, dict[str, int]] = {setup_id: {} for setup_id in setups}
    if setups:
        edges = db.scalars(
            select(CoreCalculationEvidenceSource).where(
                CoreCalculationEvidenceSource.evidence_id.in_(sorted(setups))
            )
        )
        for edge in edges:
            source_edges[edge.evidence_id][edge.source_role] = edge.source_evidence_id
    source_ids = {source_id for mapping in source_edges.values() for source_id in mapping.values()}
    sources = _select_map(db, CoreCalculationEvidence, source_ids)
    return _LoadedEvidence(
        setups,
        source_edges,
        sources,
        evaluations,
        transitions,
        alerts,
        rules,
        set(),
        {},
    )


def _row_and_authority(
    target: ReconstructionTarget, loaded: _LoadedEvidence
) -> tuple[Any | None, tuple[AuthorityResolution, ...], str | None, str | None]:
    try:
        if target.kind is ReconstructionTargetKind.SETUP:
            row = loaded.setups.get(target.evidence_id)
            if row is None:
                raise ValueError("native Setup evidence is missing")
            return (
                row,
                _setup_authority(row, loaded),
                row.calculation_identity_fingerprint,
                row.ticker,
            )
        if target.kind is ReconstructionTargetKind.LIFECYCLE_EVALUATION:
            row = loaded.evaluations.get(target.evidence_id)
            if row is None:
                raise ValueError("native lifecycle evaluation evidence is missing")
            return (
                row,
                _evaluation_authority(row, loaded),
                row.calculation_identity_fingerprint,
                (
                    f"{row.ticker}:{row.timeframe}:{row.setup_family}:{row.decision_session.isoformat()}"
                ),
            )
        if target.kind is ReconstructionTargetKind.LIFECYCLE_TRANSITION:
            row = loaded.transitions.get(target.evidence_id)
            if row is None:
                raise ValueError("native lifecycle transition evidence is missing")
            evaluation = loaded.evaluations.get(row.evaluation_evidence_id)
            calculation_identity = (
                evaluation.calculation_identity_fingerprint if evaluation is not None else None
            )
            return (
                row,
                _transition_authority(row, loaded),
                calculation_identity,
                (
                    f"{row.ticker}:{row.timeframe}:{row.setup_family}:{row.effective_session.isoformat()}"
                ),
            )
        row = loaded.alerts.get(target.evidence_id)
        if row is None:
            raise ValueError("native alert decision evidence is missing")
        return (
            row,
            _alert_authority(row, loaded),
            row.payload_json.get("calculation_identity_fingerprint"),
            f"{row.ticker}:{row.timeframe}:{row.semantic_key}:{row.effective_session.isoformat()}",
        )
    except (KeyError, TypeError, ValueError) as exc:
        return None, _integrity_failure_authority(target.kind, str(exc)), None, None


def resolve_original_contexts(
    db: Session,
    targets: Iterable[ReconstructionTarget],
) -> tuple[SetupOriginalContextResolution, ...]:
    """Resolve a batch without flushing or consulting mutable/current tables."""

    requested = tuple(targets)
    if len(set(requested)) != len(requested):
        raise ValueError("reconstruction targets must be unique")
    before = (frozenset(db.new), frozenset(db.dirty), frozenset(db.deleted))
    with db.no_autoflush:
        loaded = _load(db, requested)
        resolutions: list[SetupOriginalContextResolution] = []
        for target in requested:
            row, authority, calculation_identity, scope_identity = _row_and_authority(
                target, loaded
            )
            artifact_id = str(target.evidence_id)
            manifest = OriginalContextReconstructionManifest(
                target_artifact_type=target.kind.value,
                target_artifact_id=artifact_id,
                target_historical_decision_id=f"{target.kind.value}:{artifact_id}",
                requested_mode=ReconstructionMode.ORIGINAL_CONTEXT,
                authority=authority,
                calculation_identity=calculation_identity,
                scope_identity=scope_identity,
                code_identity_status=CodeIdentityStatus.BOUNDED_CODE_IDENTITY,
            )
            authorization = authorize_reconstruction(manifest)
            output = (
                Canonical.canonicalize(
                    {
                        "target_kind": target.kind.value,
                        "target_evidence_id": target.evidence_id,
                        "semantic_output": row.payload_json,
                    }
                )
                if authorization.authorized and row is not None
                else None
            )
            result_fingerprint = Canonical.fingerprint(output) if output is not None else None
            comparison = ReconstructionComparison(
                manifest_fingerprint=manifest.fingerprint(),
                mode=ReconstructionMode.ORIGINAL_CONTEXT,
                comparison_target_id=(
                    f"{target.kind.value}:{target.evidence_id}" if row is not None else None
                ),
                status=(
                    ReconstructionComparisonStatus.MATCHED_ORIGINAL
                    if output is not None
                    else ReconstructionComparisonStatus.INSUFFICIENT_AUTHORITY
                ),
            )
            result = ReconstructionResult(
                manifest_fingerprint=manifest.fingerprint(),
                mode=ReconstructionMode.ORIGINAL_CONTEXT,
                reconstruction_status=manifest.status,
                authority_completeness=manifest.completeness,
                result_fingerprint=result_fingerprint,
                comparison=comparison,
            )
            resolutions.append(
                SetupOriginalContextResolution(target, manifest, authorization, output, result)
            )
    after = (frozenset(db.new), frozenset(db.dirty), frozenset(db.deleted))
    if after != before:
        raise RuntimeError("original-context reconstruction attempted to mutate the session")
    return tuple(resolutions)


def resolve_setup_original_context(
    db: Session, setup_evidence_id: int
) -> SetupOriginalContextResolution:
    return resolve_original_contexts(
        db, (ReconstructionTarget(ReconstructionTargetKind.SETUP, setup_evidence_id),)
    )[0]


def resolve_lifecycle_evaluation_original_context(
    db: Session, evaluation_evidence_id: int
) -> SetupOriginalContextResolution:
    return resolve_original_contexts(
        db,
        (
            ReconstructionTarget(
                ReconstructionTargetKind.LIFECYCLE_EVALUATION, evaluation_evidence_id
            ),
        ),
    )[0]


def resolve_lifecycle_transition_original_context(
    db: Session, transition_evidence_id: int
) -> SetupOriginalContextResolution:
    return resolve_original_contexts(
        db,
        (
            ReconstructionTarget(
                ReconstructionTargetKind.LIFECYCLE_TRANSITION, transition_evidence_id
            ),
        ),
    )[0]


def resolve_alert_original_context(
    db: Session, alert_decision_evidence_id: int
) -> SetupOriginalContextResolution:
    return resolve_original_contexts(
        db,
        (
            ReconstructionTarget(
                ReconstructionTargetKind.ALERT_DECISION, alert_decision_evidence_id
            ),
        ),
    )[0]
