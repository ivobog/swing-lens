"""Consumer-specific CERI research visibility and actionability policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

CERI_ACTIONABILITY_POLICY_VERSION = "ceri-actionability-v1"


class CeriConsumer(StrEnum):
    RESEARCH_DISPLAY = "RESEARCH_DISPLAY"
    CHANGE_DETECTION = "CHANGE_DETECTION"
    ALERT = "ALERT"
    RECOMMENDATION = "RECOMMENDATION"


@dataclass(frozen=True)
class CeriActionabilityDecision:
    consumer: CeriConsumer
    allowed: bool
    reason_codes: tuple[str, ...]
    confidence: str
    policy_version: str = CERI_ACTIONABILITY_POLICY_VERSION

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "consumer": self.consumer.value,
            "allowed": self.allowed,
            "reason_codes": list(self.reason_codes),
            "confidence": self.confidence,
            "policy_version": self.policy_version,
        }


def evaluate_ceri_actionability(
    snapshot: Any,
    *,
    consumer: CeriConsumer,
    evidence_eligible: bool,
    require_opportunity: bool = False,
    require_risk_evidence: bool = False,
    risk_evidence_accepted: bool = False,
) -> CeriActionabilityDecision:
    confidence = str(getattr(snapshot, "data_confidence", None) or "Unknown")
    reasons: list[str] = []
    if not evidence_eligible:
        reasons.append("evidence_ineligible")

    if consumer in {CeriConsumer.ALERT, CeriConsumer.RECOMMENDATION}:
        if confidence not in {"High", "Normal"}:
            reasons.append("confidence_not_actionable")
        if _critical_provenance_caps(snapshot):
            reasons.append("critical_provenance_cap")
        if require_opportunity and (
            getattr(snapshot, "opportunity_score", None) is None
            or getattr(snapshot, "opportunity_unrated_reason", None) is not None
            or getattr(snapshot, "opportunity_coverage_pct", None) is None
        ):
            reasons.append("opportunity_not_rated")
        if require_risk_evidence and not risk_evidence_accepted:
            reasons.append("risk_evidence_not_accepted")
    elif consumer is CeriConsumer.CHANGE_DETECTION:
        if confidence == "Insufficient":
            reasons.append("confidence_insufficient_for_change_detection")

    if not reasons:
        reasons.append(
            "research_visibility_only"
            if consumer is CeriConsumer.RESEARCH_DISPLAY and confidence == "Low"
            else "consumer_requirements_satisfied"
        )
    blocking = {
        "evidence_ineligible",
        "confidence_not_actionable",
        "critical_provenance_cap",
        "opportunity_not_rated",
        "risk_evidence_not_accepted",
        "confidence_insufficient_for_change_detection",
    }
    return CeriActionabilityDecision(
        consumer=consumer,
        allowed=not any(reason in blocking for reason in reasons),
        reason_codes=tuple(sorted(reasons)),
        confidence=confidence,
    )


def _critical_provenance_caps(snapshot: Any) -> tuple[str, ...]:
    ledger = getattr(snapshot, "confidence_ledger_json", None) or {}
    return tuple(
        sorted(
            str(value)
            for value in ledger.get("caps", ())
            if str(value).startswith("CRITICAL_PROVENANCE_CAP_")
        )
    )
