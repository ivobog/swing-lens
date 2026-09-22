from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.services.canonical_evidence import CanonicalEvidenceSerializer

RECONSTRUCTION_MANIFEST_SCHEMA_VERSION = "original-context-reconstruction-manifest-v1"


class ReconstructionMode(StrEnum):
    ORIGINAL_CONTEXT = "ORIGINAL_CONTEXT"
    CURRENT_RULES_RETROSPECTIVE = "CURRENT_RULES_RETROSPECTIVE"
    CURRENT = "CURRENT"


class ReconstructionStatus(StrEnum):
    EXACT = "EXACT"
    BOUNDED = "BOUNDED"
    CURRENT_RULES_ONLY = "CURRENT_RULES_ONLY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    PERMANENTLY_UNAVAILABLE = "PERMANENTLY_UNAVAILABLE"
    CURRENT = "CURRENT"


class AuthorityDimension(StrEnum):
    CALCULATION_IDENTITY = "CALCULATION_IDENTITY"
    WORK_SCOPE_IDENTITY = "WORK_SCOPE_IDENTITY"
    REFRESH_CYCLE_IDENTITY = "REFRESH_CYCLE_IDENTITY"
    ACQUISITION_PLAN_IDENTITY = "ACQUISITION_PLAN_IDENTITY"
    BUSINESS_CUTOFF = "BUSINESS_CUTOFF"
    MARKET_SESSION_CALENDAR = "MARKET_SESSION_CALENDAR"
    EFFECTIVE_CONFIGURATION = "EFFECTIVE_CONFIGURATION"
    READINESS_POLICY = "READINESS_POLICY"
    SOURCE_EVIDENCE = "SOURCE_EVIDENCE"
    SOURCE_REVISION = "SOURCE_REVISION"
    PROVIDER_IDENTITY = "PROVIDER_IDENTITY"
    CURRENCY_AUTHORITY = "CURRENCY_AUTHORITY"
    TEMPORAL_POSSESSION_AUTHORITY = "TEMPORAL_POSSESSION_AUTHORITY"
    PREDECESSOR_STATE = "PREDECESSOR_STATE"
    PRIOR_DECISION_STATE = "PRIOR_DECISION_STATE"
    RULE_POLICY_VERSION = "RULE_POLICY_VERSION"
    DECISION_INPUTS = "DECISION_INPUTS"
    ALGORITHM_SCHEMA_VERSION = "ALGORITHM_SCHEMA_VERSION"
    CODE_DEPLOYMENT_IDENTITY = "CODE_DEPLOYMENT_IDENTITY"


class AuthorityAvailability(StrEnum):
    EXACT = "EXACT"
    BOUNDED = "BOUNDED"
    CURRENT_ONLY = "CURRENT_ONLY"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"
    REVISION_IDENTITY_UNAVAILABLE = "REVISION_IDENTITY_UNAVAILABLE"
    PURGED = "PURGED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AuthorityReferenceKind(StrEnum):
    RETAINED_HISTORICAL = "RETAINED_HISTORICAL"
    CONTENT_ADDRESSED_HISTORICAL = "CONTENT_ADDRESSED_HISTORICAL"
    EXTERNAL_ARCHIVE = "EXTERNAL_ARCHIVE"
    CURRENT_STATE = "CURRENT_STATE"
    CURRENT_REPLACEMENT = "CURRENT_REPLACEMENT"


class CodeIdentityStatus(StrEnum):
    EXACT_DEPLOYMENT_IDENTITY = "EXACT_DEPLOYMENT_IDENTITY"
    BOUNDED_CODE_IDENTITY = "BOUNDED_CODE_IDENTITY"
    UNKNOWN_CODE_IDENTITY = "UNKNOWN_CODE_IDENTITY"


class ReconstructionOutcome(StrEnum):
    READY = "READY"
    ORIGINAL_CONTEXT_UNAVAILABLE = "ORIGINAL_CONTEXT_UNAVAILABLE"
    CURRENT_RULES_RETROSPECTIVE_READY = "CURRENT_RULES_RETROSPECTIVE_READY"
    CURRENT_READY = "CURRENT_READY"


class ReconstructionComparisonStatus(StrEnum):
    MATCHED_ORIGINAL = "MATCHED_ORIGINAL"
    DIFFERS_FROM_ORIGINAL = "DIFFERS_FROM_ORIGINAL"
    ORIGINAL_VALUE_UNAVAILABLE = "ORIGINAL_VALUE_UNAVAILABLE"
    INSUFFICIENT_AUTHORITY = "INSUFFICIENT_AUTHORITY"
    CURRENT_RULES_RETROSPECTIVE_ONLY = "CURRENT_RULES_RETROSPECTIVE_ONLY"


@dataclass(frozen=True)
class AuthorityReference:
    reference_type: str
    reference_id: str
    kind: AuthorityReferenceKind
    independently_proven_historical_identity: bool = False
    historical_identity_proof_id: str | None = None

    def __post_init__(self) -> None:
        if not self.reference_type.strip() or not self.reference_id.strip():
            raise ValueError("authority references require non-empty type and identity")
        current_like = self.kind in {
            AuthorityReferenceKind.CURRENT_STATE,
            AuthorityReferenceKind.CURRENT_REPLACEMENT,
        }
        if (
            current_like
            and self.independently_proven_historical_identity
            and not (self.historical_identity_proof_id or "").strip()
        ):
            raise ValueError("current authority needs an independent historical proof identity")
        if self.historical_identity_proof_id is not None and not (
            self.historical_identity_proof_id.strip()
        ):
            raise ValueError("historical proof identity must not be blank")

    @property
    def proves_original_context(self) -> bool:
        if self.kind in {
            AuthorityReferenceKind.RETAINED_HISTORICAL,
            AuthorityReferenceKind.CONTENT_ADDRESSED_HISTORICAL,
            AuthorityReferenceKind.EXTERNAL_ARCHIVE,
        }:
            return True
        return bool(
            self.independently_proven_historical_identity and self.historical_identity_proof_id
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference_type": self.reference_type,
            "reference_id": self.reference_id,
            "kind": self.kind.value,
            "independently_proven_historical_identity": (
                self.independently_proven_historical_identity
            ),
            "historical_identity_proof_id": self.historical_identity_proof_id,
        }


@dataclass(frozen=True)
class AuthorityResolution:
    dimension: AuthorityDimension
    availability: AuthorityAvailability
    material: bool
    references: tuple[AuthorityReference, ...] = ()
    reason: str | None = None
    permanently_unavailable: bool = False

    def __post_init__(self) -> None:
        if self.availability is AuthorityAvailability.EXACT and not self.references:
            raise ValueError(f"exact {self.dimension.value} authority requires a reference")
        if self.availability is AuthorityAvailability.NOT_APPLICABLE and self.material:
            raise ValueError("a material authority dimension cannot be NOT_APPLICABLE")
        if self.permanently_unavailable and self.availability in {
            AuthorityAvailability.EXACT,
            AuthorityAvailability.BOUNDED,
            AuthorityAvailability.NOT_APPLICABLE,
        }:
            raise ValueError("available authority cannot be permanently unavailable")
        identities = {
            (reference.reference_type, reference.reference_id) for reference in self.references
        }
        if len(identities) != len(self.references):
            raise ValueError(f"duplicate references for {self.dimension.value}")

    @property
    def exact_for_original_context(self) -> bool:
        return (
            self.availability is AuthorityAvailability.EXACT
            and bool(self.references)
            and all(reference.proves_original_context for reference in self.references)
        )

    def as_dict(self) -> dict[str, Any]:
        references = sorted(
            (reference.as_dict() for reference in self.references),
            key=CanonicalEvidenceSerializer.dumps,
        )
        return {
            "dimension": self.dimension.value,
            "availability": self.availability.value,
            "material": self.material,
            "references": references,
            "reason": self.reason,
            "permanently_unavailable": self.permanently_unavailable,
        }


@dataclass(frozen=True)
class AuthorityCompleteness:
    required_dimensions: int
    exact: int
    bounded: int
    unavailable: int
    permanently_unavailable: int
    exact_for_original_context: bool

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "required_dimensions": self.required_dimensions,
            "exact": self.exact,
            "bounded": self.bounded,
            "unavailable": self.unavailable,
            "permanently_unavailable": self.permanently_unavailable,
            "exact_for_original_context": self.exact_for_original_context,
        }


@dataclass(frozen=True)
class OriginalContextReconstructionManifest:
    target_artifact_type: str
    target_artifact_id: str
    target_historical_decision_id: str
    requested_mode: ReconstructionMode
    authority: tuple[AuthorityResolution, ...]
    calculation_identity: str | None = None
    scope_identity: str | None = None
    refresh_identity: str | None = None
    acquisition_plan_identity: str | None = None
    code_identity_status: CodeIdentityStatus = CodeIdentityStatus.UNKNOWN_CODE_IDENTITY
    schema_version: str = RECONSTRUCTION_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value, name in (
            (self.target_artifact_type, "target_artifact_type"),
            (self.target_artifact_id, "target_artifact_id"),
            (self.target_historical_decision_id, "target_historical_decision_id"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.schema_version != RECONSTRUCTION_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported reconstruction manifest schema")
        if not self.authority:
            raise ValueError("a reconstruction manifest requires authority dimensions")
        dimensions = [resolution.dimension for resolution in self.authority]
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("authority dimensions must be unique")
        if not any(resolution.material for resolution in self.authority):
            raise ValueError("a reconstruction manifest requires material authority")

    @property
    def completeness(self) -> AuthorityCompleteness:
        material = tuple(resolution for resolution in self.authority if resolution.material)
        exact = sum(resolution.exact_for_original_context for resolution in material)
        bounded = sum(
            resolution.availability is AuthorityAvailability.BOUNDED for resolution in material
        )
        permanently_unavailable = sum(resolution.permanently_unavailable for resolution in material)
        unavailable = len(material) - exact - bounded
        return AuthorityCompleteness(
            required_dimensions=len(material),
            exact=exact,
            bounded=bounded,
            unavailable=unavailable,
            permanently_unavailable=permanently_unavailable,
            exact_for_original_context=exact == len(material),
        )

    @property
    def status(self) -> ReconstructionStatus:
        if self.requested_mode is ReconstructionMode.CURRENT_RULES_RETROSPECTIVE:
            return ReconstructionStatus.CURRENT_RULES_ONLY
        if self.requested_mode is ReconstructionMode.CURRENT:
            return ReconstructionStatus.CURRENT
        completeness = self.completeness
        if completeness.exact_for_original_context:
            return ReconstructionStatus.EXACT
        if completeness.permanently_unavailable:
            return ReconstructionStatus.PERMANENTLY_UNAVAILABLE
        if completeness.bounded and not completeness.unavailable:
            return ReconstructionStatus.BOUNDED
        return ReconstructionStatus.INSUFFICIENT_EVIDENCE

    @property
    def unavailable_dimensions(self) -> tuple[AuthorityDimension, ...]:
        return tuple(
            sorted(
                (
                    resolution.dimension
                    for resolution in self.authority
                    if resolution.material and not resolution.exact_for_original_context
                ),
                key=lambda dimension: dimension.value,
            )
        )

    def canonical_payload(self) -> dict[str, Any]:
        authority = sorted(
            (resolution.as_dict() for resolution in self.authority),
            key=lambda item: str(item["dimension"]),
        )
        return CanonicalEvidenceSerializer.canonicalize(
            {
                "schema_version": self.schema_version,
                "target_artifact": {
                    "type": self.target_artifact_type,
                    "id": self.target_artifact_id,
                },
                "target_historical_decision_id": self.target_historical_decision_id,
                "requested_mode": self.requested_mode.value,
                "authority": authority,
                "calculation_identity": self.calculation_identity,
                "scope_identity": self.scope_identity,
                "refresh_identity": self.refresh_identity,
                "acquisition_plan_identity": self.acquisition_plan_identity,
                "code_identity_status": self.code_identity_status.value,
                "reconstruction_status": self.status.value,
                "authority_completeness": self.completeness.as_dict(),
            }
        )

    def canonical_json(self) -> str:
        return CanonicalEvidenceSerializer.dumps(self.canonical_payload())

    def fingerprint(self) -> str:
        return CanonicalEvidenceSerializer.fingerprint(self.canonical_payload())


@dataclass(frozen=True)
class ReconstructionAuthorization:
    manifest_fingerprint: str
    requested_mode: ReconstructionMode
    reconstruction_status: ReconstructionStatus
    outcome: ReconstructionOutcome
    authorized: bool
    unavailable_dimensions: tuple[AuthorityDimension, ...]


def authorize_reconstruction(
    manifest: OriginalContextReconstructionManifest,
) -> ReconstructionAuthorization:
    """Authorize exactly the requested mode; never downgrade an original-context request."""

    if manifest.requested_mode is ReconstructionMode.ORIGINAL_CONTEXT:
        authorized = manifest.status is ReconstructionStatus.EXACT
        outcome = (
            ReconstructionOutcome.READY
            if authorized
            else ReconstructionOutcome.ORIGINAL_CONTEXT_UNAVAILABLE
        )
    elif manifest.requested_mode is ReconstructionMode.CURRENT_RULES_RETROSPECTIVE:
        authorized = True
        outcome = ReconstructionOutcome.CURRENT_RULES_RETROSPECTIVE_READY
    else:
        authorized = True
        outcome = ReconstructionOutcome.CURRENT_READY
    return ReconstructionAuthorization(
        manifest_fingerprint=manifest.fingerprint(),
        requested_mode=manifest.requested_mode,
        reconstruction_status=manifest.status,
        outcome=outcome,
        authorized=authorized,
        unavailable_dimensions=manifest.unavailable_dimensions,
    )


@dataclass(frozen=True)
class ReconstructionComparison:
    manifest_fingerprint: str
    mode: ReconstructionMode
    comparison_target_id: str | None
    status: ReconstructionComparisonStatus
    differences: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode is ReconstructionMode.CURRENT_RULES_RETROSPECTIVE and (
            self.status is not ReconstructionComparisonStatus.CURRENT_RULES_RETROSPECTIVE_ONLY
        ):
            raise ValueError("current-rules output cannot claim an original-context comparison")
        if self.status is ReconstructionComparisonStatus.DIFFERS_FROM_ORIGINAL and (
            not self.differences
        ):
            raise ValueError("a differing comparison requires explicit differences")


@dataclass(frozen=True)
class ReconstructionResult:
    """Immutable semantic result envelope; execution-attempt metadata lives elsewhere."""

    manifest_fingerprint: str
    mode: ReconstructionMode
    reconstruction_status: ReconstructionStatus
    authority_completeness: AuthorityCompleteness
    result_fingerprint: str | None
    comparison: ReconstructionComparison

    def __post_init__(self) -> None:
        if not self.manifest_fingerprint.strip():
            raise ValueError("a reconstruction result requires a manifest identity")
        if self.comparison.manifest_fingerprint != self.manifest_fingerprint:
            raise ValueError("comparison must reference the same immutable manifest")
        if self.comparison.mode is not self.mode:
            raise ValueError("comparison mode must equal reconstruction result mode")
        if self.mode is ReconstructionMode.ORIGINAL_CONTEXT and (
            self.reconstruction_status is not ReconstructionStatus.EXACT
            or not self.authority_completeness.exact_for_original_context
        ):
            if self.result_fingerprint is not None:
                raise ValueError("incomplete authority cannot produce an original-context result")
        if self.mode is ReconstructionMode.CURRENT_RULES_RETROSPECTIVE and (
            self.reconstruction_status is not ReconstructionStatus.CURRENT_RULES_ONLY
        ):
            raise ValueError("retrospective results must retain CURRENT_RULES_ONLY status")


def require_batch_resolution(
    requested_reference_ids: tuple[str, ...],
    resolved_references: tuple[AuthorityReference, ...],
) -> tuple[AuthorityReference, ...]:
    """Validate one set-based authority lookup without filling gaps from current state."""

    if len(set(requested_reference_ids)) != len(requested_reference_ids):
        raise ValueError("requested reference identities must be unique")
    by_id = {reference.reference_id: reference for reference in resolved_references}
    if len(by_id) != len(resolved_references):
        raise ValueError("resolved authority identities must be unique")
    missing = sorted(set(requested_reference_ids) - set(by_id))
    unexpected = sorted(set(by_id) - set(requested_reference_ids))
    if missing or unexpected:
        raise ValueError(
            f"authority batch mismatch: missing={missing!r}, unexpected={unexpected!r}"
        )
    return tuple(by_id[reference_id] for reference_id in requested_reference_ids)
