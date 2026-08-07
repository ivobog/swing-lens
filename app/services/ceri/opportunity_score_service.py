from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.ceri_tables import CeriGuidanceEvent, CeriRevisionFeature
from app.services.ceri.catalyst_feature_service import CatalystFeature
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.dtos import ScoreComponent
from app.services.ceri.enums import GuidanceAction
from app.services.ceri.surprise_feature_service import SurpriseSummary


@dataclass(frozen=True)
class OpportunityResult:
    score: float
    components: tuple[ScoreComponent, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


class CeriOpportunityScoreService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()

    def calculate(
        self,
        *,
        revision_features: list[CeriRevisionFeature],
        surprise_summary: SurpriseSummary | None = None,
        guidance_events: list[CeriGuidanceEvent] | None = None,
        catalyst_features: list[CatalystFeature] | None = None,
        price_response_quality: float | None = None,
        conflict_penalty: float = 0.0,
    ) -> OpportunityResult:
        guidance_events = guidance_events or []
        catalyst_features = catalyst_features or []
        components = (
            self._component("revision_magnitude", _revision_magnitude(revision_features)),
            self._component("revision_breadth", _revision_breadth(revision_features)),
            self._component("revision_acceleration", _revision_acceleration(revision_features)),
            self._component("surprise_trend", _surprise_score(surprise_summary)),
            self._component("guidance", _guidance_score(guidance_events)),
            self._component("catalysts", _catalyst_score(catalyst_features)),
            self._component("price_response", price_response_quality),
        )
        score = sum(
            component.contribution
            if component.contribution is not None
            else 0.0
            for component in components
        )
        score = max(0.0, min(10.0, score - conflict_penalty))
        warnings = tuple(
            warning for component in components for warning in component.warnings
        )
        reasons = tuple(
            component.name
            for component in components
            if component.contribution is not None and component.contribution > 0
        )
        if conflict_penalty:
            reasons = (*reasons, "conflict_penalty")
        return OpportunityResult(
            score=score,
            components=components,
            reasons=reasons,
            warnings=warnings,
        )

    def _component(self, name: str, value: float | None) -> ScoreComponent:
        weight = float(self.config.opportunity_weights[name])
        contribution = None if value is None else max(0.0, min(10.0, value)) * weight
        warnings = () if value is not None else (f"{name}_unavailable",)
        return ScoreComponent(
            name=name,
            value=value,
            weight=weight,
            contribution=contribution,
            warnings=warnings,
        )


def _revision_magnitude(features: list[CeriRevisionFeature]) -> float | None:
    values = [
        abs(float(feature.pct_change)) * 100
        for feature in features
        if feature.pct_change is not None
    ]
    if not values:
        return None
    return min(10.0, sum(values) / len(values))


def _revision_breadth(features: list[CeriRevisionFeature]) -> float | None:
    values = [float(feature.net_breadth) for feature in features if feature.net_breadth is not None]
    if not values:
        return None
    return max(0.0, min(10.0, (sum(values) / len(values) + 1.0) * 5.0))


def _revision_acceleration(features: list[CeriRevisionFeature]) -> float | None:
    values = [
        float(feature.acceleration)
        for feature in features
        if feature.acceleration is not None
    ]
    if not values:
        return None
    return max(0.0, min(10.0, 5.0 + sum(values) / len(values) * 10.0))


def _surprise_score(summary: SurpriseSummary | None) -> float | None:
    if summary is None or summary.average_surprise_pct is None:
        return None
    return max(0.0, min(10.0, 5.0 + float(summary.average_surprise_pct) * 20.0))


def _guidance_score(events: list[CeriGuidanceEvent]) -> float | None:
    if not events:
        return None
    values = {
        GuidanceAction.RAISED.value: Decimal("8"),
        GuidanceAction.INITIATED.value: Decimal("6"),
        GuidanceAction.MAINTAINED.value: Decimal("5"),
        GuidanceAction.NARROWED.value: Decimal("5"),
        GuidanceAction.WIDENED.value: Decimal("4"),
        GuidanceAction.LOWERED.value: Decimal("2"),
        GuidanceAction.WITHDRAWN.value: Decimal("1"),
        GuidanceAction.UNKNOWN.value: Decimal("3"),
    }
    total = sum((values.get(event.action, Decimal("3")) for event in events), Decimal("0"))
    return float(total / len(events))


def _catalyst_score(features: list[CatalystFeature]) -> float | None:
    if not features:
        return None
    return max(0.0, min(10.0, sum(feature.opportunity_component for feature in features)))
