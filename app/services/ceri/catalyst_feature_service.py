from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models.ceri_tables import CeriCatalystEvent, CeriCatalystEventRevision
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import (
    CatalystCategory,
    CatalystDirection,
    CatalystStatus,
    DateConfidence,
)


@dataclass(frozen=True)
class CatalystFeature:
    catalyst_event_id: int | None
    catalyst_revision_id: int | None
    category: str
    status: str
    direction: str
    materiality_score: float
    opportunity_component: float
    binary_risk_score: float
    conflict_penalty: float
    date_confidence: str | None
    selected: bool = True
    rejection_reason: str | None = None
    issuer_relevance: bool | None = None
    binary_eligible: bool = False
    opportunity_available: bool = True
    risk_component: str | None = None
    dedup_key: str | None = None
    warnings: tuple[str, ...] = ()


class CeriCatalystFeatureService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()

    def calculate(
        self,
        *,
        event: CeriCatalystEvent,
        revision: CeriCatalystEventRevision,
        as_of_session: date,
    ) -> CatalystFeature:
        materiality = (
            float(revision.materiality)
            if revision.materiality is not None
            else 0.0
        )
        direction_multiplier = _direction_multiplier(revision.direction)
        operational = revision.operational_values_json or {}
        issuer_relevance = (
            revision.issuer_relevance
            if revision.issuer_relevance is not None
            else operational.get("issuer_relevance")
        )
        selected = issuer_relevance is True and revision.review_state != "REJECTED"
        rejection_reason = None
        if not selected:
            rejection_reason = (
                revision.relevance_reason
                or operational.get("relevance_reason")
                or ("MANUAL_REVIEW_REJECTED" if revision.review_state == "REJECTED" else None)
                or "ISSUER_RELEVANCE_UNVERIFIED"
            )
        conflicts = tuple(revision.conflict_flags_json or ())
        conflict_penalty = min(3.0, 0.75 * len(conflicts)) if selected else 0.0
        category = CatalystCategory(event.category)
        binary_eligible = selected and self._binary_eligible(category, revision, as_of_session)
        binary_risk = (
            self._binary_risk(category, revision, as_of_session) if binary_eligible else 0.0
        )
        date_penalty = (
            min(
                _date_confidence_penalty(revision.date_confidence),
                float(self.config.event_risk["unknown_date_penalty"]),
            )
            if binary_eligible
            else 0.0
        )
        warnings = []
        if conflicts:
            warnings.append("catalyst_conflicts_present")
        if revision.materiality is None:
            warnings.append("catalyst_materiality_unavailable")
        low_confidence_dates = {
            DateConfidence.UNKNOWN.value,
            DateConfidence.DATE_RANGE.value,
        }
        if revision.date_confidence in low_confidence_dates:
            warnings.append("catalyst_date_confidence_low")
        if not selected:
            warnings.append(f"catalyst_rejected:{rejection_reason}")
        return CatalystFeature(
            catalyst_event_id=event.id,
            catalyst_revision_id=revision.id,
            category=event.category,
            status=revision.status,
            direction=revision.direction,
            materiality_score=materiality,
            opportunity_component=(
                max(0.0, materiality * direction_multiplier - conflict_penalty)
                if selected
                else 0.0
            ),
            binary_risk_score=max(0.0, binary_risk + date_penalty + conflict_penalty),
            conflict_penalty=conflict_penalty,
            date_confidence=revision.date_confidence,
            selected=selected,
            rejection_reason=rejection_reason,
            issuer_relevance=issuer_relevance,
            binary_eligible=binary_eligible,
            opportunity_available=selected and revision.materiality is not None,
            risk_component=_risk_component(category) if binary_eligible else None,
            dedup_key=f"{event.company_id}:{event.category}:{event.subject_key}",
            warnings=tuple(warnings),
        )

    def _binary_eligible(
        self,
        category: CatalystCategory,
        revision: CeriCatalystEventRevision,
        as_of_session: date,
    ) -> bool:
        if category not in {
            CatalystCategory.REGULATORY,
            CatalystCategory.LEGAL,
            CatalystCategory.FINANCING,
            CatalystCategory.CORPORATE_ACTION,
        }:
            return False
        if revision.status in {
            CatalystStatus.COMPLETED.value,
            CatalystStatus.CANCELLED.value,
            CatalystStatus.OUTCOME_KNOWN.value,
            "RESOLVED",
        }:
            return False
        if revision.expected_date is not None and revision.expected_date < as_of_session:
            return False
        if revision.status in {CatalystStatus.SCHEDULED.value, CatalystStatus.DELAYED.value}:
            return True
        return bool(
            revision.binary_eligible
            if revision.binary_eligible is not None
            else (revision.operational_values_json or {}).get("binary_eligible")
        )

    def _binary_risk(
        self,
        category: CatalystCategory,
        revision: CeriCatalystEventRevision,
        as_of_session: date,
    ) -> float:
        base_by_category = {
            CatalystCategory.REGULATORY: float(self.config.event_risk["regulatory_binary_base"]),
            CatalystCategory.LEGAL: float(self.config.event_risk["legal_binary_base"]),
            CatalystCategory.FINANCING: float(self.config.event_risk["financing_gap_base"]),
            CatalystCategory.CORPORATE_ACTION: float(
                self.config.event_risk["corporate_action_base"]
            ),
        }
        base = base_by_category.get(category, 0.0)
        if base == 0.0:
            return 0.0
        if revision.expected_date is None:
            return base
        days_until = (revision.expected_date - as_of_session).days
        if days_until < 0:
            return 0.0
        return base if days_until <= 30 else base * 0.5


def _direction_multiplier(direction: str) -> float:
    values = {
        CatalystDirection.STRONG_POSITIVE.value: 1.0,
        CatalystDirection.POSITIVE.value: 0.7,
        CatalystDirection.NEUTRAL.value: 0.2,
        CatalystDirection.NEGATIVE.value: -0.7,
        CatalystDirection.STRONG_NEGATIVE.value: -1.0,
        CatalystDirection.UNKNOWN.value: 0.0,
    }
    return values.get(direction, 0.0)


def _date_confidence_penalty(value: str | None) -> float:
    penalties = {
        DateConfidence.EXACT_TIMESTAMP.value: 0.0,
        DateConfidence.EXACT_DATE.value: 0.25,
        DateConfidence.DATE_RANGE.value: 0.75,
        DateConfidence.ESTIMATED_PERIOD.value: 1.0,
        DateConfidence.UNKNOWN.value: 1.5,
    }
    return penalties.get(value or DateConfidence.UNKNOWN.value, 1.5)


def _risk_component(category: CatalystCategory) -> str:
    return {
        CatalystCategory.REGULATORY: "regulatory_binary_risk",
        CatalystCategory.LEGAL: "legal_binary_risk",
        CatalystCategory.FINANCING: "financing_gap_risk",
        CatalystCategory.CORPORATE_ACTION: "corporate_action_risk",
    }.get(category, "other_event_risk")
