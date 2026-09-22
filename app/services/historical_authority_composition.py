"""Typed, read-only proof-boundary composition for historical authority.

An immutable producer can be internally valid and still be the wrong producer for a
consumer.  This module binds declared producer-consumer edges, the exact producer
authority expected by the consumer, and the resolved producer authority into one
content-addressed result.  Operational co-location (run, pipeline, or ticker alone)
is never accepted as compatibility proof.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import CoreCalculationEvidence, CoreCalculationEvidenceSource
from app.services.calculation_identity import CalculationIdentity, IdentityState
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.decision_effective_configuration import configuration_from_payload
from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY
from app.services.producer_readiness import READINESS_PAYLOAD_KEY, readiness_from_evidence

COMPOSITION_SCHEMA_VERSION = "historical-authority-composition-v1"


class CompositionDimension(StrEnum):
    PRODUCER_EVIDENCE = "PRODUCER_EVIDENCE"
    CALCULATION_CONTEXT = "CALCULATION_CONTEXT"
    TEMPORAL_CONTEXT = "TEMPORAL_CONTEXT"
    SESSION_CALENDAR = "SESSION_CALENDAR"
    PRODUCER_NOT_AFTER_CONSUMER = "PRODUCER_NOT_AFTER_CONSUMER"
    SUBJECT_SCOPE = "SUBJECT_SCOPE"
    DECLARED_POPULATION_SCOPE = "DECLARED_POPULATION_SCOPE"
    WORK_SCOPE = "WORK_SCOPE"
    REFRESH_CYCLE = "REFRESH_CYCLE"
    EFFECTIVE_CONFIGURATION = "EFFECTIVE_CONFIGURATION"
    READINESS = "READINESS"
    SOURCE_LINEAGE = "SOURCE_LINEAGE"
    SOURCE_REVISION = "SOURCE_REVISION"
    PREDECESSOR = "PREDECESSOR"
    ALGORITHM_SCHEMA = "ALGORITHM_SCHEMA"
    RULE_POLICY = "RULE_POLICY"
    PROVIDER_REVISION = "PROVIDER_REVISION"


class HistoricalCompatibilityStatus(StrEnum):
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE_CALCULATION_CONTEXT = "INCOMPATIBLE_CALCULATION_CONTEXT"
    INCOMPATIBLE_TEMPORAL_CONTEXT = "INCOMPATIBLE_TEMPORAL_CONTEXT"
    INCOMPATIBLE_CONFIGURATION = "INCOMPATIBLE_CONFIGURATION"
    INCOMPATIBLE_SCOPE = "INCOMPATIBLE_SCOPE"
    INCOMPATIBLE_SOURCE_LINEAGE = "INCOMPATIBLE_SOURCE_LINEAGE"
    INCOMPATIBLE_READINESS = "INCOMPATIBLE_READINESS"
    INCOMPATIBLE_PREDECESSOR = "INCOMPATIBLE_PREDECESSOR"
    INCOMPATIBLE_SCHEMA_ALGORITHM = "INCOMPATIBLE_SCHEMA_ALGORITHM"
    INCOMPATIBLE_RULE_POLICY = "INCOMPATIBLE_RULE_POLICY"
    INCOMPATIBLE_PROVIDER_REVISION = "INCOMPATIBLE_PROVIDER_REVISION"
    MISSING_AUTHORITY = "MISSING_AUTHORITY"
    UNDECLARED_DEPENDENCY = "UNDECLARED_DEPENDENCY"


@dataclass(frozen=True)
class HistoricalAuthorityArtifact:
    domain: str
    artifact_type: str
    artifact_id: str
    evidence_fingerprint: str
    calculation_identity: str | None
    calculation_context: str | None
    temporal_context: str | None
    business_session: str | None
    business_cutoff: str | None
    calendar_identity: str | None
    subject_scope: str | None
    work_scope_identity: str | None
    refresh_cycle_identity: str | None
    effective_configuration: str | None
    readiness: str | None
    source_lineage: str | None
    source_revision: str | None
    predecessor_identity: str | None
    algorithm_schema: str | None
    rule_policy: str | None
    provider_revision: str | None
    semantic_output: str | None = None
    historical_authority: bool = True

    def __post_init__(self) -> None:
        for value, name in (
            (self.domain, "domain"),
            (self.artifact_type, "artifact_type"),
            (self.artifact_id, "artifact_id"),
            (self.evidence_fingerprint, "evidence_fingerprint"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")

    def canonical_payload(self) -> dict[str, Any]:
        return Canonical.canonicalize(
            {
                "domain": self.domain,
                "artifact_type": self.artifact_type,
                "artifact_id": self.artifact_id,
                "evidence_fingerprint": self.evidence_fingerprint,
                "calculation_identity": self.calculation_identity,
                "calculation_context": self.calculation_context,
                "temporal_context": self.temporal_context,
                "business_session": self.business_session,
                "business_cutoff": self.business_cutoff,
                "calendar_identity": self.calendar_identity,
                "subject_scope": self.subject_scope,
                "work_scope_identity": self.work_scope_identity,
                "refresh_cycle_identity": self.refresh_cycle_identity,
                "effective_configuration": self.effective_configuration,
                "readiness": self.readiness,
                "source_lineage": self.source_lineage,
                "source_revision": self.source_revision,
                "predecessor_identity": self.predecessor_identity,
                "algorithm_schema": self.algorithm_schema,
                "rule_policy": self.rule_policy,
                "provider_revision": self.provider_revision,
                "semantic_output": self.semantic_output,
                "historical_authority": self.historical_authority,
            }
        )


@dataclass(frozen=True)
class HistoricalAuthorityCompatibilityContract:
    producer_domain: str
    consumer_domain: str
    dependency_type: str
    required_exact_dimensions: tuple[CompositionDimension, ...]
    required_compatible_dimensions: tuple[CompositionDimension, ...]
    allowed_differences: tuple[str, ...]

    @property
    def contract_id(self) -> str:
        return Canonical.fingerprint(self.canonical_payload())

    @property
    def edge_key(self) -> tuple[str, str, str]:
        return self.producer_domain, self.consumer_domain, self.dependency_type

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "producer_domain": self.producer_domain,
            "consumer_domain": self.consumer_domain,
            "dependency_type": self.dependency_type,
            "required_exact_dimensions": sorted(
                dimension.value for dimension in self.required_exact_dimensions
            ),
            "required_compatible_dimensions": sorted(
                dimension.value for dimension in self.required_compatible_dimensions
            ),
            "allowed_differences": sorted(self.allowed_differences),
        }


@dataclass(frozen=True)
class HistoricalAuthorityDependency:
    contract: HistoricalAuthorityCompatibilityContract
    source_role: str
    consumer: HistoricalAuthorityArtifact
    expected_producer: HistoricalAuthorityArtifact
    resolved_producer: HistoricalAuthorityArtifact | None

    def __post_init__(self) -> None:
        if not self.source_role.strip():
            raise ValueError("dependency source role must not be blank")
        if self.consumer.domain != self.contract.consumer_domain:
            raise ValueError("dependency consumer does not match its contract")
        if self.expected_producer.domain != self.contract.producer_domain:
            raise ValueError("dependency producer does not match its contract")

    @property
    def edge_id(self) -> str:
        return Canonical.fingerprint(
            {
                "contract_id": self.contract.contract_id,
                "source_role": self.source_role,
                "consumer": self.consumer.artifact_id,
                "expected_producer": self.expected_producer.artifact_id,
            }
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "contract_id": self.contract.contract_id,
            "contract": self.contract.canonical_payload(),
            "source_role": self.source_role,
            "consumer": self.consumer.canonical_payload(),
            "expected_producer": self.expected_producer.canonical_payload(),
            "resolved_producer": (
                self.resolved_producer.canonical_payload()
                if self.resolved_producer is not None
                else None
            ),
        }


@dataclass(frozen=True)
class HistoricalAuthorityEdgeResult:
    edge_id: str
    contract_id: str
    producer_domain: str
    consumer_domain: str
    dependency_type: str
    status: HistoricalCompatibilityStatus
    failure_dimension: CompositionDimension | None = None
    failure_reason: str | None = None
    missing_authority: tuple[CompositionDimension, ...] = ()

    @property
    def compatible(self) -> bool:
        return self.status is HistoricalCompatibilityStatus.COMPATIBLE

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "contract_id": self.contract_id,
            "producer_domain": self.producer_domain,
            "consumer_domain": self.consumer_domain,
            "dependency_type": self.dependency_type,
            "status": self.status.value,
            "failure_dimension": (
                self.failure_dimension.value if self.failure_dimension is not None else None
            ),
            "failure_reason": self.failure_reason,
            "missing_authority": [item.value for item in self.missing_authority],
        }


@dataclass(frozen=True)
class HistoricalAuthorityCompositionResult:
    dependencies: tuple[HistoricalAuthorityDependency, ...]
    edge_results: tuple[HistoricalAuthorityEdgeResult, ...]
    proof_boundary_fingerprint: str
    overall_exact: bool
    manifest_fingerprint: str | None = None
    schema_version: str = COMPOSITION_SCHEMA_VERSION

    @property
    def failed_edge(self) -> HistoricalAuthorityEdgeResult | None:
        return next((edge for edge in self.edge_results if not edge.compatible), None)

    @property
    def missing_authority(self) -> tuple[CompositionDimension, ...]:
        return tuple(
            sorted(
                {dimension for edge in self.edge_results for dimension in edge.missing_authority},
                key=lambda item: item.value,
            )
        )

    def bind_manifest(self, manifest_fingerprint: str) -> HistoricalAuthorityCompositionResult:
        if not manifest_fingerprint.strip():
            raise ValueError("manifest fingerprint must not be blank")
        return replace(self, manifest_fingerprint=manifest_fingerprint)

    def canonical_payload(self) -> dict[str, Any]:
        # manifest_fingerprint is a back-reference and is intentionally excluded
        # from the payload embedded by the manifest, preventing a hash cycle.
        return Canonical.canonicalize(
            {
                "schema_version": self.schema_version,
                "dependencies": sorted(
                    (dependency.canonical_payload() for dependency in self.dependencies),
                    key=Canonical.dumps,
                ),
                "edge_results": sorted(
                    (edge.canonical_payload() for edge in self.edge_results),
                    key=Canonical.dumps,
                ),
                "proof_boundary_fingerprint": self.proof_boundary_fingerprint,
                "overall_exact": self.overall_exact,
            }
        )


_FIELD_BY_EXACT_DIMENSION = {
    CompositionDimension.PRODUCER_EVIDENCE: ("artifact_id", "evidence_fingerprint"),
    CompositionDimension.EFFECTIVE_CONFIGURATION: ("effective_configuration",),
    CompositionDimension.READINESS: ("readiness",),
    CompositionDimension.SOURCE_LINEAGE: ("source_lineage",),
    CompositionDimension.SOURCE_REVISION: ("source_revision",),
    CompositionDimension.WORK_SCOPE: ("work_scope_identity",),
    CompositionDimension.REFRESH_CYCLE: ("refresh_cycle_identity",),
    CompositionDimension.PREDECESSOR: ("predecessor_identity",),
    CompositionDimension.ALGORITHM_SCHEMA: ("algorithm_schema",),
    CompositionDimension.RULE_POLICY: ("rule_policy",),
    CompositionDimension.PROVIDER_REVISION: ("provider_revision",),
}


def _failure_status(dimension: CompositionDimension) -> HistoricalCompatibilityStatus:
    if dimension in {
        CompositionDimension.TEMPORAL_CONTEXT,
        CompositionDimension.SESSION_CALENDAR,
        CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER,
    }:
        return HistoricalCompatibilityStatus.INCOMPATIBLE_TEMPORAL_CONTEXT
    if dimension is CompositionDimension.CALCULATION_CONTEXT:
        return HistoricalCompatibilityStatus.INCOMPATIBLE_CALCULATION_CONTEXT
    if dimension is CompositionDimension.EFFECTIVE_CONFIGURATION:
        return HistoricalCompatibilityStatus.INCOMPATIBLE_CONFIGURATION
    if dimension in {
        CompositionDimension.SUBJECT_SCOPE,
        CompositionDimension.DECLARED_POPULATION_SCOPE,
        CompositionDimension.WORK_SCOPE,
        CompositionDimension.REFRESH_CYCLE,
    }:
        return HistoricalCompatibilityStatus.INCOMPATIBLE_SCOPE
    if dimension is CompositionDimension.READINESS:
        return HistoricalCompatibilityStatus.INCOMPATIBLE_READINESS
    if dimension is CompositionDimension.PREDECESSOR:
        return HistoricalCompatibilityStatus.INCOMPATIBLE_PREDECESSOR
    if dimension is CompositionDimension.ALGORITHM_SCHEMA:
        return HistoricalCompatibilityStatus.INCOMPATIBLE_SCHEMA_ALGORITHM
    if dimension is CompositionDimension.RULE_POLICY:
        return HistoricalCompatibilityStatus.INCOMPATIBLE_RULE_POLICY
    if dimension is CompositionDimension.PROVIDER_REVISION:
        return HistoricalCompatibilityStatus.INCOMPATIBLE_PROVIDER_REVISION
    return HistoricalCompatibilityStatus.INCOMPATIBLE_SOURCE_LINEAGE


def _missing_exact_dimensions(
    contract: HistoricalAuthorityCompatibilityContract,
    expected: HistoricalAuthorityArtifact,
    actual: HistoricalAuthorityArtifact,
) -> tuple[CompositionDimension, ...]:
    missing: set[CompositionDimension] = set()
    if not actual.historical_authority:
        missing.add(CompositionDimension.PRODUCER_EVIDENCE)
    for dimension in contract.required_exact_dimensions:
        fields = _FIELD_BY_EXACT_DIMENSION.get(dimension, ())
        if any(
            getattr(expected, field) is None or getattr(actual, field) is None for field in fields
        ):
            missing.add(dimension)
    return tuple(sorted(missing, key=lambda item: item.value))


def _compare_exact_dimension(
    dimension: CompositionDimension,
    expected: HistoricalAuthorityArtifact,
    actual: HistoricalAuthorityArtifact,
) -> bool:
    return all(
        getattr(expected, field) == getattr(actual, field)
        for field in _FIELD_BY_EXACT_DIMENSION.get(dimension, ())
    )


def _parse_cutoff(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _compatible_dimension(
    dimension: CompositionDimension,
    consumer: HistoricalAuthorityArtifact,
    producer: HistoricalAuthorityArtifact,
) -> bool:
    if dimension is CompositionDimension.CALCULATION_CONTEXT:
        if consumer.calculation_context is None and producer.calculation_context is None:
            # Direct/synchronously built native evidence can legitimately mark
            # the persisted pipeline context N/A.  Exact retained temporal
            # identity is the semantic compatibility proof in that case; run
            # or pipeline co-location is deliberately not consulted.
            return consumer.temporal_context is not None and (
                consumer.temporal_context == producer.temporal_context
            )
        return consumer.calculation_context is not None and (
            consumer.calculation_context == producer.calculation_context
        )
    if dimension is CompositionDimension.TEMPORAL_CONTEXT:
        return consumer.temporal_context is not None and (
            consumer.temporal_context == producer.temporal_context
        )
    if dimension is CompositionDimension.SESSION_CALENDAR:
        return (
            consumer.business_session is not None
            and consumer.business_session == producer.business_session
            and consumer.calendar_identity is not None
            and consumer.calendar_identity == producer.calendar_identity
        )
    if dimension is CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER:
        producer_cutoff = _parse_cutoff(producer.business_cutoff)
        consumer_cutoff = _parse_cutoff(consumer.business_cutoff)
        return (
            producer.business_session is not None
            and consumer.business_session is not None
            and producer.business_session <= consumer.business_session
            and producer_cutoff is not None
            and consumer_cutoff is not None
            and producer_cutoff <= consumer_cutoff
        )
    if dimension is CompositionDimension.SUBJECT_SCOPE:
        return (
            producer.subject_scope is not None
            and consumer.subject_scope is not None
            and producer.subject_scope == consumer.subject_scope
        )
    if dimension is CompositionDimension.DECLARED_POPULATION_SCOPE:
        # Exact producer evidence is the membership proof. Global/population
        # producers deliberately need not share the consumer's scalar subject.
        return producer.historical_authority
    return False


def _edge_result(dependency: HistoricalAuthorityDependency) -> HistoricalAuthorityEdgeResult:
    contract = dependency.contract
    actual = dependency.resolved_producer
    base = {
        "edge_id": dependency.edge_id,
        "contract_id": contract.contract_id,
        "producer_domain": contract.producer_domain,
        "consumer_domain": contract.consumer_domain,
        "dependency_type": contract.dependency_type,
    }
    if actual is None:
        missing = tuple(
            sorted(set(contract.required_exact_dimensions), key=lambda item: item.value)
        )
        return HistoricalAuthorityEdgeResult(
            **base,
            status=HistoricalCompatibilityStatus.MISSING_AUTHORITY,
            failure_reason="declared historical producer could not be resolved",
            missing_authority=missing,
        )
    missing = _missing_exact_dimensions(contract, dependency.expected_producer, actual)
    if missing:
        return HistoricalAuthorityEdgeResult(
            **base,
            status=HistoricalCompatibilityStatus.MISSING_AUTHORITY,
            failure_dimension=missing[0],
            failure_reason="required historical producer authority is absent or current-only",
            missing_authority=missing,
        )
    for dimension in contract.required_compatible_dimensions:
        if not _compatible_dimension(dimension, dependency.consumer, actual):
            return HistoricalAuthorityEdgeResult(
                **base,
                status=_failure_status(dimension),
                failure_dimension=dimension,
                failure_reason="producer and consumer violate the declared compatibility rule",
            )
    # Compare semantic dimensions before the catch-all producer address so the
    # diagnostic identifies config/readiness/schema attacks precisely.
    ordered = tuple(
        dimension
        for dimension in contract.required_exact_dimensions
        if dimension is not CompositionDimension.PRODUCER_EVIDENCE
    ) + tuple(
        dimension
        for dimension in contract.required_exact_dimensions
        if dimension is CompositionDimension.PRODUCER_EVIDENCE
    )
    for dimension in ordered:
        if not _compare_exact_dimension(dimension, dependency.expected_producer, actual):
            return HistoricalAuthorityEdgeResult(
                **base,
                status=_failure_status(dimension),
                failure_dimension=dimension,
                failure_reason="resolved producer differs from the consumer's frozen authority",
            )
    return HistoricalAuthorityEdgeResult(
        **base,
        status=HistoricalCompatibilityStatus.COMPATIBLE,
    )


def validate_historical_authority_composition(
    dependencies: Iterable[HistoricalAuthorityDependency],
) -> HistoricalAuthorityCompositionResult:
    """Validate one immutable dependency set; never infer or query replacements."""

    frozen = tuple(dependencies)
    edge_ids = [dependency.edge_id for dependency in frozen]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("historical composition edges must be unique")
    edge_results = tuple(_edge_result(dependency) for dependency in frozen)
    payload = {
        "schema_version": COMPOSITION_SCHEMA_VERSION,
        "dependencies": sorted(
            (dependency.canonical_payload() for dependency in frozen), key=Canonical.dumps
        ),
        "edge_results": sorted(
            (edge.canonical_payload() for edge in edge_results), key=Canonical.dumps
        ),
    }
    fingerprint = Canonical.fingerprint(payload)
    return HistoricalAuthorityCompositionResult(
        dependencies=frozen,
        edge_results=edge_results,
        proof_boundary_fingerprint=fingerprint,
        overall_exact=bool(frozen) and all(edge.compatible for edge in edge_results),
    )


def reject_undeclared_dependency(producer_domain: str, consumer_domain: str) -> None:
    if (producer_domain, consumer_domain) in FORBIDDEN_DEPENDENCY_EDGES:
        raise ValueError(
            f"UNDECLARED_DEPENDENCY: {producer_domain} -> {consumer_domain} is forbidden"
        )


def _contract(
    producer: str,
    consumer: str,
    dependency_type: str,
    exact: tuple[CompositionDimension, ...],
    compatible: tuple[CompositionDimension, ...],
    *allowed: str,
) -> HistoricalAuthorityCompatibilityContract:
    return HistoricalAuthorityCompatibilityContract(
        producer,
        consumer,
        dependency_type,
        exact,
        compatible,
        tuple(allowed),
    )


_BEHAVIORAL_EXACT = (
    CompositionDimension.PRODUCER_EVIDENCE,
    CompositionDimension.EFFECTIVE_CONFIGURATION,
    CompositionDimension.READINESS,
    CompositionDimension.SOURCE_LINEAGE,
    CompositionDimension.ALGORITHM_SCHEMA,
)
_METADATA_EXACT = tuple(
    dimension for dimension in _BEHAVIORAL_EXACT if dimension is not CompositionDimension.READINESS
)
_STRICT_COMPATIBLE = (
    CompositionDimension.CALCULATION_CONTEXT,
    CompositionDimension.TEMPORAL_CONTEXT,
    CompositionDimension.SUBJECT_SCOPE,
)


EDGE_CONTRACTS = (
    _contract(
        "RAW",
        "FUNDAMENTAL",
        "raw_source",
        (
            CompositionDimension.PRODUCER_EVIDENCE,
            CompositionDimension.SOURCE_LINEAGE,
            CompositionDimension.SOURCE_REVISION,
        ),
        (CompositionDimension.SUBJECT_SCOPE,),
        "producer calculation/config identity not applicable",
    ),
    _contract(
        "PRICE",
        "TECHNICAL",
        "price_manifest",
        (
            CompositionDimension.PRODUCER_EVIDENCE,
            CompositionDimension.SOURCE_LINEAGE,
            CompositionDimension.SOURCE_REVISION,
            CompositionDimension.PROVIDER_REVISION,
        ),
        (CompositionDimension.TEMPORAL_CONTEXT,),
        "producer calculation/config identity not applicable",
    ),
    *(
        _contract(
            producer,
            consumer,
            dependency,
            exact,
            compatible,
            allowed,
        )
        for producer, consumer, dependency, exact, compatible, allowed in (
            (
                "FUNDAMENTAL",
                "COMBINED",
                "behavioral_input",
                _BEHAVIORAL_EXACT,
                _STRICT_COMPATIBLE,
                "calculation identity and configuration family",
            ),
            (
                "TECHNICAL",
                "COMBINED",
                "behavioral_input",
                _BEHAVIORAL_EXACT,
                _STRICT_COMPATIBLE,
                "calculation identity and configuration family",
            ),
            (
                "FUNDAMENTAL",
                "RANKING",
                "behavioral_input",
                _BEHAVIORAL_EXACT,
                _STRICT_COMPATIBLE,
                "calculation identity and configuration family",
            ),
            (
                "TECHNICAL",
                "RANKING",
                "behavioral_input",
                _BEHAVIORAL_EXACT,
                _STRICT_COMPATIBLE,
                "calculation identity and configuration family",
            ),
            (
                "IBMI",
                "RANKING",
                "liquidity_input",
                _BEHAVIORAL_EXACT,
                (
                    CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER,
                    CompositionDimension.SUBJECT_SCOPE,
                ),
                "independent contextual configuration family",
            ),
            (
                "RANKING",
                "SECTOR",
                "population_ranking_input",
                _BEHAVIORAL_EXACT,
                (
                    CompositionDimension.CALCULATION_CONTEXT,
                    CompositionDimension.TEMPORAL_CONTEXT,
                    CompositionDimension.DECLARED_POPULATION_SCOPE,
                ),
                "Ranking profile and Sector configuration families",
            ),
            (
                "TECHNICAL",
                "SETUP",
                "behavioral_input",
                _BEHAVIORAL_EXACT,
                _STRICT_COMPATIBLE,
                "calculation identity and configuration family",
            ),
            (
                "COMBINED",
                "SETUP",
                "earnings_risk_actionability",
                _BEHAVIORAL_EXACT,
                _STRICT_COMPATIBLE,
                "calculation identity and configuration family",
            ),
            (
                "COMBINED",
                "SETUP",
                "score_decision_metadata",
                _METADATA_EXACT,
                _STRICT_COMPATIBLE,
                "producer readiness eligibility is non-behavioral for this metadata edge",
            ),
            (
                "RANKING",
                "SETUP",
                "score_decision_profile_metadata",
                _METADATA_EXACT,
                _STRICT_COMPATIBLE,
                "Ranking profile/config family differs from Setup",
            ),
            (
                "REGIME",
                "SETUP",
                "market_context_input",
                _BEHAVIORAL_EXACT,
                (
                    CompositionDimension.SESSION_CALENDAR,
                    CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER,
                ),
                "global subject and independent contextual configuration",
            ),
            (
                "SECTOR",
                "SETUP",
                "market_context_input",
                _BEHAVIORAL_EXACT,
                (
                    CompositionDimension.CALCULATION_CONTEXT,
                    CompositionDimension.TEMPORAL_CONTEXT,
                    CompositionDimension.DECLARED_POPULATION_SCOPE,
                ),
                "global Sector subject and independent contextual configuration",
            ),
            (
                "SETUP",
                "LIFECYCLE",
                "subject_input",
                _BEHAVIORAL_EXACT,
                (CompositionDimension.TEMPORAL_CONTEXT, CompositionDimension.SUBJECT_SCOPE),
                "Lifecycle calculation identity/config family",
            ),
            (
                "LIFECYCLE_EVALUATION",
                "LIFECYCLE_TRANSITION",
                "evaluation_predecessor",
                (
                    CompositionDimension.PRODUCER_EVIDENCE,
                    CompositionDimension.PREDECESSOR,
                    CompositionDimension.ALGORITHM_SCHEMA,
                ),
                (CompositionDimension.TEMPORAL_CONTEXT, CompositionDimension.SUBJECT_SCOPE),
                "distinct evaluation/transition identities",
            ),
            (
                "LIFECYCLE_TRANSITION",
                "LIFECYCLE_EVALUATION",
                "transition_predecessor",
                (
                    CompositionDimension.PRODUCER_EVIDENCE,
                    CompositionDimension.PREDECESSOR,
                    CompositionDimension.ALGORITHM_SCHEMA,
                ),
                (
                    CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER,
                    CompositionDimension.SUBJECT_SCOPE,
                ),
                "distinct transition/evaluation identities",
            ),
            (
                "SETUP",
                "ALERT",
                "decision_source",
                (
                    CompositionDimension.PRODUCER_EVIDENCE,
                    CompositionDimension.READINESS,
                    CompositionDimension.SOURCE_LINEAGE,
                ),
                (
                    CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER,
                    CompositionDimension.SUBJECT_SCOPE,
                ),
                "Alert calculation identity/config family",
            ),
            (
                "LIFECYCLE_EVALUATION",
                "ALERT",
                "evaluation_source",
                (
                    CompositionDimension.PRODUCER_EVIDENCE,
                    CompositionDimension.PREDECESSOR,
                    CompositionDimension.READINESS,
                ),
                (
                    CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER,
                    CompositionDimension.SUBJECT_SCOPE,
                ),
                "Alert calculation identity/config family",
            ),
            (
                "LIFECYCLE_TRANSITION",
                "ALERT",
                "transition_source",
                (CompositionDimension.PRODUCER_EVIDENCE, CompositionDimension.PREDECESSOR),
                (
                    CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER,
                    CompositionDimension.SUBJECT_SCOPE,
                ),
                "Alert calculation identity/config family",
            ),
            (
                "ALERT_RULE",
                "ALERT",
                "rule_policy",
                (
                    CompositionDimension.PRODUCER_EVIDENCE,
                    CompositionDimension.RULE_POLICY,
                    CompositionDimension.EFFECTIVE_CONFIGURATION,
                    CompositionDimension.ALGORITHM_SCHEMA,
                ),
                (CompositionDimension.SUBJECT_SCOPE,),
                "rule evidence has no consumer calculation identity",
            ),
            (
                "ALERT_DECISION",
                "ALERT_DECISION",
                "cooldown_dedup_predecessor",
                (
                    CompositionDimension.PRODUCER_EVIDENCE,
                    CompositionDimension.PREDECESSOR,
                    CompositionDimension.RULE_POLICY,
                ),
                (
                    CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER,
                    CompositionDimension.SUBJECT_SCOPE,
                ),
                "distinct alert decision identities",
            ),
            (
                "CERI_CHANGE",
                "CERI_ALERT",
                "change_source",
                (
                    CompositionDimension.PRODUCER_EVIDENCE,
                    CompositionDimension.SOURCE_LINEAGE,
                    CompositionDimension.SOURCE_REVISION,
                ),
                (
                    CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER,
                    CompositionDimension.SUBJECT_SCOPE,
                ),
                "CERI change and alert calculation identities",
            ),
            (
                "CERI_RULE",
                "CERI_ALERT",
                "rule_policy",
                (
                    CompositionDimension.PRODUCER_EVIDENCE,
                    CompositionDimension.RULE_POLICY,
                    CompositionDimension.EFFECTIVE_CONFIGURATION,
                ),
                (CompositionDimension.SUBJECT_SCOPE,),
                "rule row may later mutate but retained proof may not",
            ),
            *(
                (
                    producer,
                    "WINNER",
                    "independent_prediction_input",
                    _BEHAVIORAL_EXACT,
                    _STRICT_COMPATIBLE,
                    (
                        "Winner and Ranking configuration/profile families"
                        if producer == "RANKING"
                        else f"Winner and {producer.title()} configuration families"
                    ),
                )
                for producer in ("FUNDAMENTAL", "TECHNICAL", "COMBINED", "RANKING")
            ),
            (
                "REGIME",
                "WINNER",
                "independent_prediction_input",
                _BEHAVIORAL_EXACT,
                (
                    CompositionDimension.SESSION_CALENDAR,
                    CompositionDimension.PRODUCER_NOT_AFTER_CONSUMER,
                ),
                "global subject and independent contextual configuration",
            ),
            (
                "SECTOR",
                "WINNER",
                "independent_prediction_input",
                _BEHAVIORAL_EXACT,
                (
                    CompositionDimension.CALCULATION_CONTEXT,
                    CompositionDimension.TEMPORAL_CONTEXT,
                    CompositionDimension.DECLARED_POPULATION_SCOPE,
                ),
                "global subject and independent contextual configuration",
            ),
        )
    ),
    _contract(
        "RAW",
        "WINNER",
        "independent_prediction_input",
        (
            CompositionDimension.PRODUCER_EVIDENCE,
            CompositionDimension.SOURCE_LINEAGE,
            CompositionDimension.SOURCE_REVISION,
        ),
        (CompositionDimension.SUBJECT_SCOPE,),
        "producer calculation/config identity not applicable",
    ),
)


FORBIDDEN_DEPENDENCY_EDGES = frozenset(
    {
        ("CERI", "RANKING"),
        ("CERI", "SETUP"),
        ("CERI", "WINNER"),
        ("SETUP", "WINNER"),
        ("LIFECYCLE", "WINNER"),
        ("IBMI", "WINNER"),
        ("SECTOR", "RANKING"),
    }
)

CONTRACT_BY_KEY = {contract.edge_key: contract for contract in EDGE_CONTRACTS}


def _dimension_value(payload: Mapping[str, Any], *path: str) -> Any:
    value: Any = payload
    for member in path:
        if not isinstance(value, Mapping) or member not in value:
            return None
        value = value[member]
    if not isinstance(value, Mapping) or value.get("state") != IdentityState.KNOWN.value:
        return None
    return value.get("value")


def artifact_from_core_evidence(row: CoreCalculationEvidence) -> HistoricalAuthorityArtifact:
    """Project one already-retained core artifact into the composition contract."""

    if Canonical.fingerprint(row.payload_json) != row.payload_fingerprint:
        raise ValueError("historical producer payload fingerprint mismatch")
    identity = CalculationIdentity.from_canonical_payload(row.calculation_identity_json)
    if str(identity.fingerprint()) != row.calculation_identity_fingerprint:
        raise ValueError("historical producer Calculation Identity mismatch")
    if row.evidence_key != Canonical.fingerprint(
        {
            "artifact_kind": row.artifact_kind,
            "calculation_identity_fingerprint": row.calculation_identity_fingerprint,
            "payload_fingerprint": row.payload_fingerprint,
            "source_evidence_ids": row.source_evidence_ids_json,
        }
    ):
        raise ValueError("historical producer evidence key mismatch")
    readiness_payload = row.payload_json.get(READINESS_PAYLOAD_KEY)
    if not isinstance(readiness_payload, dict):
        raise ValueError("historical producer readiness is missing")
    readiness = readiness_from_evidence(row)
    if readiness.calculation_identity_fingerprint != row.calculation_identity_fingerprint:
        raise ValueError("historical producer readiness identity mismatch")
    configuration_payload = row.payload_json.get(CONFIGURATION_PAYLOAD_KEY)
    if not isinstance(configuration_payload, dict):
        raise ValueError("historical producer effective configuration is missing")
    retained_configuration = configuration_from_payload(configuration_payload)
    if (
        identity.configuration.effective_configuration.value
        != retained_configuration.snapshot.identity
    ):
        raise ValueError("historical producer effective configuration identity mismatch")
    payload = identity.canonical_payload()
    temporal = payload["temporal"]
    subject = payload["subject"]
    calculation_context = payload["calculation_context"]
    configuration = payload["configuration"]["effective_configuration"]
    algorithm = payload["algorithm"]
    source_lineage = payload["source_lineage"]
    session = _dimension_value(temporal, "as_of_session")
    cutoff = _dimension_value(temporal, "calculation_cutoff")
    calendar = _dimension_value(temporal, "calendar")
    ticker = _dimension_value(subject, "ticker")
    company = _dimension_value(subject, "company_id")
    config_value = configuration.get("value") if configuration.get("state") == "KNOWN" else None
    lineage_value = source_lineage.get("value") if source_lineage.get("state") == "KNOWN" else None
    return HistoricalAuthorityArtifact(
        domain=row.artifact_kind,
        artifact_type="CoreCalculationEvidence",
        artifact_id=f"core:{row.id}",
        evidence_fingerprint=row.evidence_key,
        calculation_identity=row.calculation_identity_fingerprint,
        calculation_context=(
            Canonical.fingerprint(calculation_context)
            if all(
                calculation_context[key].get("state") == "KNOWN"
                for key in ("market_calculation_context_id", "context_fingerprint")
            )
            else None
        ),
        temporal_context=Canonical.fingerprint(temporal)
        if session and cutoff and calendar
        else None,
        business_session=str(session) if session is not None else None,
        business_cutoff=str(cutoff) if cutoff is not None else None,
        calendar_identity=Canonical.fingerprint(calendar) if calendar is not None else None,
        subject_scope=(
            Canonical.fingerprint({"ticker": ticker, "company_id": company})
            if ticker is not None or company is not None
            else None
        ),
        work_scope_identity=None,
        refresh_cycle_identity=None,
        effective_configuration=(
            Canonical.fingerprint(config_value) if config_value is not None else None
        ),
        readiness=readiness.fingerprint(),
        source_lineage=(
            Canonical.fingerprint(lineage_value) if lineage_value is not None else None
        ),
        source_revision=(
            Canonical.fingerprint(lineage_value.get("references", []))
            if isinstance(lineage_value, dict)
            else None
        ),
        predecessor_identity=None,
        algorithm_schema=Canonical.fingerprint(algorithm),
        rule_policy=None,
        provider_revision=(
            Canonical.fingerprint(
                [
                    reference
                    for reference in lineage_value.get("references", [])
                    if "provider" in str(reference.get("artifact_type", "")).lower()
                ]
            )
            if isinstance(lineage_value, dict)
            else None
        ),
        semantic_output=row.payload_fingerprint,
    )


def _core_contracts(consumer_kind: str, role: str):
    if consumer_kind == "COMBINED" and role in {"fundamental", "technical"}:
        return (CONTRACT_BY_KEY[(role.upper(), "COMBINED", "behavioral_input")],)
    if consumer_kind == "RANKING" and role in {"fundamental", "technical"}:
        return (CONTRACT_BY_KEY[(role.upper(), "RANKING", "behavioral_input")],)
    if consumer_kind == "RANKING" and role == "ibmi_liquidity":
        return (CONTRACT_BY_KEY[("IBMI", "RANKING", "liquidity_input")],)
    if consumer_kind == "SECTOR" and role.startswith("ranking:"):
        return (CONTRACT_BY_KEY[("RANKING", "SECTOR", "population_ranking_input")],)
    if consumer_kind == "SETUP" and role == "technical":
        return (CONTRACT_BY_KEY[("TECHNICAL", "SETUP", "behavioral_input")],)
    if consumer_kind == "SETUP" and role == "combined":
        return (
            CONTRACT_BY_KEY[("COMBINED", "SETUP", "earnings_risk_actionability")],
            CONTRACT_BY_KEY[("COMBINED", "SETUP", "score_decision_metadata")],
        )
    if consumer_kind == "SETUP" and role == "ranking_metadata":
        return (CONTRACT_BY_KEY[("RANKING", "SETUP", "score_decision_profile_metadata")],)
    if consumer_kind == "SETUP" and role in {"regime", "sector"}:
        return (CONTRACT_BY_KEY[(role.upper(), "SETUP", "market_context_input")],)
    return ()


def compose_core_evidence_graph(
    target_ids: Iterable[int],
    evidence_by_id: Mapping[int, CoreCalculationEvidence],
    source_edges: Mapping[int, Mapping[str, int]],
) -> HistoricalAuthorityCompositionResult:
    """Compose a preloaded Core graph without issuing queries or inferring edges."""

    dependencies: list[HistoricalAuthorityDependency] = []
    visited: set[int] = set()
    pending = list(dict.fromkeys(int(value) for value in target_ids))
    artifacts: dict[int, HistoricalAuthorityArtifact] = {}
    while pending:
        consumer_id = pending.pop()
        if consumer_id in visited:
            continue
        visited.add(consumer_id)
        consumer_row = evidence_by_id.get(consumer_id)
        if consumer_row is None:
            continue
        consumer = artifacts.setdefault(consumer_id, artifact_from_core_evidence(consumer_row))
        declared = {
            str(role): int(source_id)
            for role, source_id in consumer_row.source_evidence_ids_json.items()
        }
        if dict(source_edges.get(consumer_id, {})) != declared:
            raise ValueError("retained Core source-edge graph does not match the consumer manifest")
        for role, producer_id in sorted(declared.items()):
            pending.append(producer_id)
            contracts = _core_contracts(consumer_row.artifact_kind, role)
            if not contracts:
                continue
            producer_row = evidence_by_id.get(producer_id)
            producer = (
                artifacts.setdefault(producer_id, artifact_from_core_evidence(producer_row))
                if producer_row is not None
                else None
            )
            expected = producer or HistoricalAuthorityArtifact(
                domain=contracts[0].producer_domain,
                artifact_type="CoreCalculationEvidence",
                artifact_id=f"core:{producer_id}",
                evidence_fingerprint=f"missing:{producer_id}",
                calculation_identity=None,
                calculation_context=None,
                temporal_context=None,
                business_session=None,
                business_cutoff=None,
                calendar_identity=None,
                subject_scope=None,
                work_scope_identity=None,
                refresh_cycle_identity=None,
                effective_configuration=None,
                readiness=None,
                source_lineage=None,
                source_revision=None,
                predecessor_identity=None,
                algorithm_schema=None,
                rule_policy=None,
                provider_revision=None,
                historical_authority=False,
            )
            for contract in contracts:
                dependencies.append(
                    HistoricalAuthorityDependency(
                        contract,
                        role,
                        consumer,
                        expected,
                        producer,
                    )
                )
    return validate_historical_authority_composition(dependencies)


def resolve_core_evidence_composition(
    db: Session, target_ids: Iterable[int]
) -> HistoricalAuthorityCompositionResult:
    """Set-wise load an immutable Core dependency closure and validate it."""

    requested = tuple(dict.fromkeys(int(value) for value in target_ids))
    evidence: dict[int, CoreCalculationEvidence] = {}
    edges: dict[int, dict[str, int]] = {}
    pending = set(requested)
    with db.no_autoflush:
        while pending:
            rows = {
                row.id: row
                for row in db.scalars(
                    select(CoreCalculationEvidence).where(
                        CoreCalculationEvidence.id.in_(sorted(pending))
                    )
                )
            }
            evidence.update(rows)
            found_edges = db.scalars(
                select(CoreCalculationEvidenceSource).where(
                    CoreCalculationEvidenceSource.evidence_id.in_(sorted(rows))
                )
            )
            next_ids: set[int] = set()
            for edge in found_edges:
                edges.setdefault(edge.evidence_id, {})[edge.source_role] = edge.source_evidence_id
                if edge.source_evidence_id not in evidence:
                    next_ids.add(edge.source_evidence_id)
            for row_id in rows:
                edges.setdefault(row_id, {})
            pending = next_ids
    return compose_core_evidence_graph(requested, evidence, edges)
