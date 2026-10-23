"""Orthogonal, deterministic consumer eligibility decision contract."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.producer_readiness import (
    ConsumerEligibilityDecision,
    ConsumerEligibilityStatus,
    ProducerReadinessEnvelope,
)
from app.services.warning_taxonomy import ClassifiedWarning, classify_warnings

ORTHOGONAL_POLICY_VERSION = "orthogonal-consumer-eligibility-v1"


class ExecutionOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_WITH_GAPS = "SUCCEEDED_WITH_GAPS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class IntegrityStatus(StrEnum):
    CERTIFIED = "CERTIFIED"
    UNCERTIFIED = "UNCERTIFIED"
    CONTAMINATED = "CONTAMINATED"
    QUARANTINED = "QUARANTINED"
    UNKNOWN = "UNKNOWN"


class CompletenessStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


class AnalyticalConfidence(StrEnum):
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


class ConsumerEligibilityOutcome(StrEnum):
    INCLUDED = "INCLUDED"
    OMITTED_OPTIONAL = "OMITTED_OPTIONAL"
    REJECTED_REQUIRED = "REJECTED_REQUIRED"
    UNDECIDED = "UNDECIDED"


@dataclass(frozen=True)
class OrthogonalConsumerDecision:
    producer: str
    consumer: str
    consumer_edge: str
    policy_version: str
    source_identity_fingerprint: str
    source_evidence_id: int | None
    decision_cutoff: datetime
    execution_outcome: ExecutionOutcome
    integrity_status: IntegrityStatus
    completeness_status: CompletenessStatus
    analytical_confidence: AnalyticalConfidence
    warnings: tuple[ClassifiedWarning, ...]
    eligibility: ConsumerEligibilityOutcome
    reason_codes: tuple[str, ...]
    authoritative: bool = False

    def __post_init__(self) -> None:
        if not self.producer or not self.consumer or not self.consumer_edge:
            raise ValueError("consumer decision requires producer, consumer, and edge")
        if not self.policy_version or not self.source_identity_fingerprint:
            raise ValueError("consumer decision requires policy and source identity")
        if self.decision_cutoff.tzinfo is None or self.decision_cutoff.utcoffset() is None:
            raise ValueError("consumer decision cutoff must be timezone-aware")
        object.__setattr__(self, "decision_cutoff", self.decision_cutoff.astimezone(UTC))
        object.__setattr__(self, "warnings", tuple(sorted(self.warnings, key=_warning_sort_key)))
        object.__setattr__(self, "reason_codes", tuple(sorted(set(self.reason_codes))))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "producer": self.producer,
            "consumer": self.consumer,
            "consumer_edge": self.consumer_edge,
            "policy_version": self.policy_version,
            "source_identity_fingerprint": self.source_identity_fingerprint,
            "source_evidence_id": self.source_evidence_id,
            "decision_cutoff": self.decision_cutoff,
            "execution_outcome": self.execution_outcome.value,
            "integrity_status": self.integrity_status.value,
            "completeness_status": self.completeness_status.value,
            "analytical_confidence": self.analytical_confidence.value,
            "warnings": [item.canonical_payload() for item in self.warnings],
            "eligibility": self.eligibility.value,
            "reason_codes": list(self.reason_codes),
            "authoritative": self.authoritative,
            "contract_version": ORTHOGONAL_POLICY_VERSION,
        }

    def fingerprint(self) -> str:
        return Canonical.fingerprint(self.canonical_payload())


def from_legacy_decision(
    readiness: ProducerReadinessEnvelope,
    decision: ConsumerEligibilityDecision,
    *,
    consumer_edge: str,
    decision_cutoff: datetime,
    warning_codes: Iterable[object] = (),
    execution_outcome: ExecutionOutcome = ExecutionOutcome.UNKNOWN,
    integrity_status: IntegrityStatus = IntegrityStatus.UNKNOWN,
    completeness_status: CompletenessStatus = CompletenessStatus.UNKNOWN,
    analytical_confidence: AnalyticalConfidence = AnalyticalConfidence.UNKNOWN,
    optional_source: bool = False,
    authoritative: bool = False,
) -> OrthogonalConsumerDecision:
    eligibility = {
        ConsumerEligibilityStatus.ELIGIBLE: ConsumerEligibilityOutcome.INCLUDED,
        ConsumerEligibilityStatus.INELIGIBLE: (
            ConsumerEligibilityOutcome.OMITTED_OPTIONAL
            if optional_source
            else ConsumerEligibilityOutcome.REJECTED_REQUIRED
        ),
        ConsumerEligibilityStatus.POLICY_UNDECIDED: ConsumerEligibilityOutcome.UNDECIDED,
    }[decision.status]
    identity = readiness.calculation_identity_fingerprint
    if not identity:
        raise ValueError("orthogonal decisions require an exact source identity")
    return OrthogonalConsumerDecision(
        producer=readiness.producer,
        consumer=decision.consumer,
        consumer_edge=consumer_edge,
        policy_version=decision.policy_version,
        source_identity_fingerprint=identity,
        source_evidence_id=readiness.evidence_id,
        decision_cutoff=decision_cutoff,
        execution_outcome=execution_outcome,
        integrity_status=integrity_status,
        completeness_status=completeness_status,
        analytical_confidence=analytical_confidence,
        warnings=classify_warnings(warning_codes),
        eligibility=eligibility,
        reason_codes=tuple(reason.value for reason in decision.reasons),
        authoritative=authoritative,
    )


def _warning_sort_key(value: ClassifiedWarning) -> tuple[str, str, str]:
    return (value.normalized_code, value.category.value, value.original_code)
