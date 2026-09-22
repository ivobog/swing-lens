"""Machine-enforced archive and retention boundaries for historical authority.

This module never discovers missing history from current state.  It classifies
retained authority, evaluates purge safety, and provides a small content-addressed
archive envelope for already-immutable proof.  Financial calculations are outside
this boundary.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical

ARCHIVE_SCHEMA_VERSION = "historical-authority-archive-v1"


class HistoricalBoundaryClassification(StrEnum):
    LEGACY_AUTHORITY_UNAVAILABLE = "LEGACY_AUTHORITY_UNAVAILABLE"
    LEGACY_REVISION_UNAVAILABLE = "LEGACY_REVISION_UNAVAILABLE"
    LEGACY_RULE_UNAVAILABLE = "LEGACY_RULE_UNAVAILABLE"
    ARCHIVE_PURGED = "ARCHIVE_PURGED"
    EXTERNAL_PROVIDER_NOT_RETAINABLE = "EXTERNAL_PROVIDER_NOT_RETAINABLE"
    CONFIGURATION_AUTHORITY_UNAVAILABLE = "CONFIGURATION_AUTHORITY_UNAVAILABLE"
    GOVERNANCE_ONLY = "GOVERNANCE_ONLY"
    CURRENT_PATH_CERTIFIED = "CURRENT_PATH_CERTIFIED"


class HistoricalReconstructionAvailability(StrEnum):
    EXACT = "EXACT"
    CURRENT_RULES_ONLY = "CURRENT_RULES_ONLY"
    PERMANENTLY_UNAVAILABLE = "PERMANENTLY_UNAVAILABLE"
    EXTERNAL_AUTHORITY_UNAVAILABLE = "EXTERNAL_AUTHORITY_UNAVAILABLE"


class RetentionClass(StrEnum):
    IMMUTABLE_AUTHORITY = "IMMUTABLE_AUTHORITY"
    ARCHIVED_IMMUTABLE_AUTHORITY = "ARCHIVED_IMMUTABLE_AUTHORITY"
    OPERATIONAL_ONLY = "OPERATIONAL_ONLY"
    EXTERNAL_UNRETAINABLE = "EXTERNAL_UNRETAINABLE"


class PurgeDisposition(StrEnum):
    ALLOW_OPERATIONAL_PURGE = "ALLOW_OPERATIONAL_PURGE"
    ALLOW_WITH_EXPLICIT_DOWNGRADE = "ALLOW_WITH_EXPLICIT_DOWNGRADE"
    BLOCK_MATERIAL_AUTHORITY = "BLOCK_MATERIAL_AUTHORITY"


@dataclass(frozen=True)
class RetainedAuthority:
    authority_type: str
    semantic_id: str
    content_fingerprint: str | None
    material_to_reconstruction: bool
    retention_class: RetentionClass
    pinned_by: tuple[str, ...] = ()
    external_exception: str | None = None

    def __post_init__(self) -> None:
        if not self.authority_type.strip() or not self.semantic_id.strip():
            raise ValueError("retained authority requires type and semantic identity")
        if (
            self.retention_class
            in {
                RetentionClass.IMMUTABLE_AUTHORITY,
                RetentionClass.ARCHIVED_IMMUTABLE_AUTHORITY,
            }
            and not (self.content_fingerprint or "").strip()
        ):
            raise ValueError("immutable retained authority requires a content fingerprint")
        if (
            self.retention_class is RetentionClass.EXTERNAL_UNRETAINABLE
            and not (self.external_exception or "").strip()
        ):
            raise ValueError("external unretainable authority requires an explicit exception")
        if len(set(self.pinned_by)) != len(self.pinned_by):
            raise ValueError("retention pins must be unique")

    @property
    def retention_protected(self) -> bool:
        return self.material_to_reconstruction and bool(self.pinned_by)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "authority_type": self.authority_type,
            "semantic_id": self.semantic_id,
            "content_fingerprint": self.content_fingerprint,
            "material_to_reconstruction": self.material_to_reconstruction,
            "retention_class": self.retention_class.value,
            "pinned_by": sorted(self.pinned_by),
            "external_exception": self.external_exception,
        }


@dataclass(frozen=True)
class HistoricalAvailabilityResult:
    availability: HistoricalReconstructionAvailability
    classification: HistoricalBoundaryClassification
    required_authority: tuple[str, ...]
    retained_authority: tuple[str, ...]
    missing_authority: tuple[str, ...]
    reason: str
    current_state_consulted: bool = False

    @property
    def exact(self) -> bool:
        return self.availability is HistoricalReconstructionAvailability.EXACT

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "availability": self.availability.value,
            "classification": self.classification.value,
            "required_authority": sorted(self.required_authority),
            "retained_authority": sorted(self.retained_authority),
            "missing_authority": sorted(self.missing_authority),
            "reason": self.reason,
            "current_state_consulted": self.current_state_consulted,
        }


def classify_historical_availability(
    *,
    required_authority: Iterable[str],
    retained_authority: Iterable[str],
    missing_classification: HistoricalBoundaryClassification,
    external_unavailable: bool = False,
    retrospective_available: bool = False,
) -> HistoricalAvailabilityResult:
    """Classify only retained historical proof; current state is not an input."""

    required = tuple(sorted(set(required_authority)))
    retained = tuple(sorted(set(retained_authority)))
    missing = tuple(sorted(set(required) - set(retained)))
    if not required:
        raise ValueError("historical availability requires at least one authority dimension")
    if not missing:
        return HistoricalAvailabilityResult(
            HistoricalReconstructionAvailability.EXACT,
            HistoricalBoundaryClassification.CURRENT_PATH_CERTIFIED,
            required,
            retained,
            (),
            "all material historical authority is retained",
        )
    if external_unavailable:
        availability = HistoricalReconstructionAvailability.EXTERNAL_AUTHORITY_UNAVAILABLE
    elif retrospective_available:
        availability = HistoricalReconstructionAvailability.CURRENT_RULES_ONLY
    else:
        availability = HistoricalReconstructionAvailability.PERMANENTLY_UNAVAILABLE
    return HistoricalAvailabilityResult(
        availability,
        missing_classification,
        required,
        retained,
        missing,
        "missing historical authority cannot be supplied by current state",
    )


@dataclass(frozen=True)
class PurgeAssessment:
    disposition: PurgeDisposition
    authority_ids: tuple[str, ...]
    blocked_authority_ids: tuple[str, ...]
    downgraded_authority_ids: tuple[str, ...]
    resulting_availability: HistoricalReconstructionAvailability
    reason: str

    @property
    def allowed(self) -> bool:
        return self.disposition is not PurgeDisposition.BLOCK_MATERIAL_AUTHORITY


def assess_authority_purge(
    authorities: Iterable[RetainedAuthority],
    *,
    explicit_reconstruction_downgrade: bool = False,
) -> PurgeAssessment:
    """Apply INV-PURGE-001 to a preloaded, set-based authority collection."""

    frozen = tuple(authorities)
    if not frozen:
        raise ValueError("purge assessment requires an explicit authority set")
    ids = tuple(sorted(item.semantic_id for item in frozen))
    protected = tuple(sorted(item.semantic_id for item in frozen if item.retention_protected))
    material = tuple(sorted(item.semantic_id for item in frozen if item.material_to_reconstruction))
    if protected:
        return PurgeAssessment(
            PurgeDisposition.BLOCK_MATERIAL_AUTHORITY,
            ids,
            protected,
            (),
            HistoricalReconstructionAvailability.EXACT,
            "pinned material authority must remain retained while dependent evidence is certified",
        )
    if material:
        if not explicit_reconstruction_downgrade:
            return PurgeAssessment(
                PurgeDisposition.BLOCK_MATERIAL_AUTHORITY,
                ids,
                material,
                (),
                HistoricalReconstructionAvailability.EXACT,
                "material authority requires retention or an explicit availability downgrade",
            )
        return PurgeAssessment(
            PurgeDisposition.ALLOW_WITH_EXPLICIT_DOWNGRADE,
            ids,
            (),
            material,
            HistoricalReconstructionAvailability.PERMANENTLY_UNAVAILABLE,
            "material authority purge is visible as a permanent reconstruction downgrade",
        )
    return PurgeAssessment(
        PurgeDisposition.ALLOW_OPERATIONAL_PURGE,
        ids,
        (),
        (),
        HistoricalReconstructionAvailability.EXACT,
        "operational-only state is outside the semantic proof boundary",
    )


@dataclass(frozen=True)
class HistoricalAuthorityArchive:
    archive_id: str
    source_manifest_fingerprint: str
    authorities: tuple[RetainedAuthority, ...]
    payload: Mapping[str, Any]
    archive_fingerprint: str
    schema_version: str = ARCHIVE_SCHEMA_VERSION

    def canonical_payload(self) -> dict[str, Any]:
        return Canonical.canonicalize(
            {
                "schema_version": self.schema_version,
                "archive_id": self.archive_id,
                "source_manifest_fingerprint": self.source_manifest_fingerprint,
                "authorities": sorted(
                    (item.canonical_payload() for item in self.authorities),
                    key=Canonical.dumps,
                ),
                "payload": self.payload,
            }
        )


def create_authority_archive(
    *,
    archive_id: str,
    source_manifest_fingerprint: str,
    authorities: Iterable[RetainedAuthority],
    payload: Mapping[str, Any],
) -> HistoricalAuthorityArchive:
    frozen = tuple(authorities)
    if not archive_id.strip() or not source_manifest_fingerprint.strip() or not frozen:
        raise ValueError("archive identity, source manifest, and authority are required")
    if any(
        item.material_to_reconstruction
        and item.retention_class
        not in {
            RetentionClass.IMMUTABLE_AUTHORITY,
            RetentionClass.ARCHIVED_IMMUTABLE_AUTHORITY,
        }
        for item in frozen
    ):
        raise ValueError("archive cannot claim material authority it does not retain")
    draft = HistoricalAuthorityArchive(
        archive_id,
        source_manifest_fingerprint,
        frozen,
        Canonical.canonicalize(payload),
        "pending",
    )
    return HistoricalAuthorityArchive(
        archive_id,
        source_manifest_fingerprint,
        frozen,
        draft.payload,
        Canonical.fingerprint(draft.canonical_payload()),
    )


def restore_authority_archive(value: Mapping[str, Any]) -> HistoricalAuthorityArchive:
    if value.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
        raise ValueError("unsupported historical authority archive schema")
    authorities = tuple(
        RetainedAuthority(
            authority_type=str(item["authority_type"]),
            semantic_id=str(item["semantic_id"]),
            content_fingerprint=item.get("content_fingerprint"),
            material_to_reconstruction=bool(item["material_to_reconstruction"]),
            retention_class=RetentionClass(item["retention_class"]),
            pinned_by=tuple(item.get("pinned_by", ())),
            external_exception=item.get("external_exception"),
        )
        for item in value["authorities"]
    )
    restored = HistoricalAuthorityArchive(
        archive_id=str(value["archive_id"]),
        source_manifest_fingerprint=str(value["source_manifest_fingerprint"]),
        authorities=authorities,
        payload=Canonical.canonicalize(value["payload"]),
        archive_fingerprint=str(value["archive_fingerprint"]),
    )
    if Canonical.fingerprint(restored.canonical_payload()) != restored.archive_fingerprint:
        raise ValueError("historical authority archive fingerprint mismatch")
    return restored


def archived_payload(archive: HistoricalAuthorityArchive) -> dict[str, Any]:
    return {
        **archive.canonical_payload(),
        "archive_fingerprint": archive.archive_fingerprint,
    }


RETENTION_REQUIREMENTS = {
    "effective_configuration": ("YES", "FORBIDDEN_WHILE_REFERENCED"),
    "calculation_evidence": ("YES", "FORBIDDEN_WHILE_REFERENCED"),
    "source_identity": ("YES", "FORBIDDEN_WHILE_REFERENCED"),
    "source_revisions": ("YES", "FORBIDDEN_WHILE_REFERENCED"),
    "scope_manifests": ("YES", "FORBIDDEN_WHILE_REFERENCED"),
    "refresh_identities": ("YES", "FORBIDDEN_WHILE_REFERENCED"),
    "acquisition_plans": ("YES_WHERE_MATERIAL", "FORBIDDEN_WHILE_REFERENCED"),
    "readiness_decisions": ("YES", "FORBIDDEN_WITH_PARENT_EVIDENCE"),
    "historical_rules": ("YES", "FORBIDDEN_WHILE_REFERENCED"),
    "predecessor_links": ("YES", "FORBIDDEN_WHILE_REFERENCED"),
    "pricebar_revisions": ("YES_WHERE_CONSUMED", "FORBIDDEN_WHILE_REFERENCED"),
    "provider_payload_evidence": (
        "YES_WHERE_LICENSE_PERMITS",
        "BLOCK_IF_PINNED_ELSE_EXPLICIT_DOWNGRADE",
    ),
    "reconstruction_manifests": ("YES", "FORBIDDEN_WITH_CERTIFIED_RESULT"),
    "operational_cache": ("NO", "ALLOWED"),
}


FINDING_BOUNDARIES = {
    "CORE-005": {
        "status": "PARTIAL",
        "classification": HistoricalBoundaryClassification.LEGACY_AUTHORITY_UNAVAILABLE,
        "current_path_certified": True,
    },
    "CERI-010": {
        "status": "CLOSED",
        "classification": HistoricalBoundaryClassification.ARCHIVE_PURGED,
        "current_path_certified": True,
    },
    "RANK-007": {
        "status": "PARTIAL",
        "classification": HistoricalBoundaryClassification.EXTERNAL_PROVIDER_NOT_RETAINABLE,
        "current_path_certified": True,
    },
    "WIN-006": {
        "status": "PARTIAL",
        "classification": HistoricalBoundaryClassification.LEGACY_REVISION_UNAVAILABLE,
        "current_path_certified": True,
    },
    "XINT-010": {
        "status": "CLOSED",
        "classification": HistoricalBoundaryClassification.GOVERNANCE_ONLY,
        "current_path_certified": True,
    },
}
