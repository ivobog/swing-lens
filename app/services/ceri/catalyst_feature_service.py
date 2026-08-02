from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models.ceri_tables import CeriCatalystEvent, CeriCatalystEventRevision
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import CatalystCategory, CatalystDirection, DateConfidence


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
        materiality = float(revision.materiality or 0.0)
        direction_multiplier = _direction_multiplier(revision.direction)
        conflicts = tuple(revision.conflict_flags_json or ())
        conflict_penalty = min(3.0, 0.75 * len(conflicts))
        category = CatalystCategory(event.category)
        binary_risk = self._binary_risk(category, revision, as_of_session)
        date_penalty = _date_confidence_penalty(revision.date_confidence)
        warnings = []
        if conflicts:
            warnings.append("catalyst_conflicts_present")
        low_confidence_dates = {
            DateConfidence.UNKNOWN.value,
            DateConfidence.DATE_RANGE.value,
        }
        if revision.date_confidence in low_confidence_dates:
            warnings.append("catalyst_date_confidence_low")
        return CatalystFeature(
            catalyst_event_id=event.id,
            catalyst_revision_id=revision.id,
            category=event.category,
            status=revision.status,
            direction=revision.direction,
            materiality_score=materiality,
            opportunity_component=max(0.0, materiality * direction_multiplier - conflict_penalty),
            binary_risk_score=max(0.0, binary_risk + date_penalty + conflict_penalty),
            conflict_penalty=conflict_penalty,
            date_confidence=revision.date_confidence,
            warnings=tuple(warnings),
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
            return base + float(self.config.event_risk["unknown_date_penalty"])
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
