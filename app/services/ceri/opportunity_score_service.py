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
    score: float | None
    rated: bool
    coverage_pct: float
    available_weight: float
    minimum_required_coverage_pct: float
    reweighted: bool
    unrated_reason: str | None
    components: tuple[ScoreComponent, ...]
    penalties: tuple[dict[str, float], ...]
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
        price_response_parent_event_id: int | None = None,
        conflict_penalty: float = 0.0,
    ) -> OpportunityResult:
        guidance_events = guidance_events or []
        catalyst_features = catalyst_features or []
        guidance_value, guidance_ids, guidance_warnings = _guidance_score(guidance_events)
        accepted_catalyst_ids = tuple(
            feature.catalyst_event_id
            for feature in catalyst_features
            if feature.catalyst_event_id is not None and getattr(feature, "selected", True)
        )
        price_response_unavailable_reason = None
        if (
            price_response_quality is not None
            and price_response_parent_event_id is not None
            and price_response_parent_event_id not in accepted_catalyst_ids
        ):
            price_response_quality = None
            price_response_unavailable_reason = "PARENT_EVENT_INELIGIBLE"
        components = (
            self._component(
                "revision_magnitude",
                _revision_magnitude(revision_features),
                evidence_ids=_feature_ids(revision_features, "pct_change"),
            ),
            self._component(
                "revision_breadth",
                _revision_breadth(revision_features),
                evidence_ids=_feature_ids(revision_features, "net_breadth"),
            ),
            self._component(
                "revision_acceleration",
                _revision_acceleration(revision_features),
                evidence_ids=_feature_ids(revision_features, "acceleration"),
            ),
            self._component("surprise_trend", _surprise_score(surprise_summary)),
            self._component(
                "guidance",
                guidance_value,
                evidence_ids=guidance_ids,
                extra_warnings=guidance_warnings,
            ),
            self._component(
                "catalysts",
                _catalyst_score(catalyst_features),
                evidence_ids=accepted_catalyst_ids,
            ),
            self._component(
                "price_response",
                price_response_quality,
                evidence_ids=(price_response_parent_event_id,)
                if price_response_parent_event_id is not None
                and price_response_quality is not None
                else (),
                unavailable_reason=price_response_unavailable_reason,
            ),
        )
        available_weight = sum(component.weight for component in components if component.available)
        coverage_pct = available_weight * 100.0
        minimum = float(self.config.revision.minimum_component_coverage_pct)
        raw_available_sum = sum(
            component.contribution
            if component.contribution is not None
            else 0.0
            for component in components
        )
        rated = coverage_pct + 1e-9 >= minimum
        score = (
            max(0.0, min(10.0, raw_available_sum / available_weight - conflict_penalty))
            if rated and available_weight > 0
            else None
        )
        warnings = tuple(
            warning for component in components for warning in component.warnings
        )
        unrated_reason = None
        if not rated:
            unrated_reason = "INSUFFICIENT_COMPONENT_COVERAGE"
            warnings = (*warnings, "opportunity_component_coverage_insufficient")
        reasons = tuple(
            component.name
            for component in components
            if component.contribution is not None and component.contribution > 0
        )
        if conflict_penalty:
            reasons = (*reasons, "conflict_penalty")
        return OpportunityResult(
            score=score,
            rated=rated,
            coverage_pct=coverage_pct,
            available_weight=available_weight,
            minimum_required_coverage_pct=minimum,
            reweighted=rated and available_weight < 1.0,
            unrated_reason=unrated_reason,
            components=components,
            penalties=(
                ({"name": "conflict_penalty", "value": float(conflict_penalty)},)
                if conflict_penalty
                else ()
            ),
            reasons=reasons,
            warnings=warnings,
        )

    def _component(
        self,
        name: str,
        value: float | None,
        *,
        evidence_ids: tuple[int, ...] = (),
        extra_warnings: tuple[str, ...] = (),
        unavailable_reason: str | None = None,
    ) -> ScoreComponent:
        weight = float(self.config.opportunity_weights[name])
        contribution = None if value is None else max(0.0, min(10.0, value)) * weight
        warnings = extra_warnings
        if value is None:
            warnings = (*warnings, f"{name}_unavailable")
        return ScoreComponent(
            name=name,
            value=value,
            weight=weight,
            contribution=contribution,
            available=value is not None,
            unavailable_reason=(
                None
                if value is not None
                else unavailable_reason or f"{name.upper()}_UNAVAILABLE"
            ),
            evidence_ids=evidence_ids,
            warnings=warnings,
        )


def _revision_magnitude(features: list[CeriRevisionFeature]) -> float | None:
    values = [
        max(0.0, float(feature.pct_change))
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
    return max(0.0, min(10.0, 5.0 + float(summary.average_surprise_pct) * 0.2))


def _guidance_score(
    events: list[CeriGuidanceEvent],
) -> tuple[float | None, tuple[int, ...], tuple[str, ...]]:
    if not events:
        return None, (), ()
    values = {
        GuidanceAction.RAISED.value: Decimal("8"),
        GuidanceAction.INITIATED.value: Decimal("6"),
        GuidanceAction.MAINTAINED.value: Decimal("5"),
        GuidanceAction.NARROWED.value: Decimal("5"),
        GuidanceAction.WIDENED.value: Decimal("4"),
        GuidanceAction.LOWERED.value: Decimal("2"),
        GuidanceAction.WITHDRAWN.value: Decimal("1"),
    }
    superseded_ids = {event.supersedes_id for event in events if event.supersedes_id is not None}
    rejected = 0
    eligible: list[CeriGuidanceEvent] = []
    for event in events:
        if event.id in superseded_ids:
            rejected += 1
            continue
        if event.action not in values or str(event.confidence).upper() not in {"HIGH", "NORMAL"}:
            rejected += 1
            continue
        if event.accepted_for_scoring is False:
            rejected += 1
            continue
        if event.metric is None or event.period_type is None:
            rejected += 1
            continue
        warnings = set(event.quality_warnings_json or ())
        if "requires_review" in warnings or "extraction_insufficient" in warnings:
            rejected += 1
            continue
        eligible.append(event)
    latest_by_key: dict[tuple[str, str], CeriGuidanceEvent] = {}
    for event in eligible:
        key = (str(event.metric), str(event.period_type))
        current = latest_by_key.get(key)
        event_sort = _guidance_sort_key(event)
        current_sort = _guidance_sort_key(current) if current is not None else None
        if current is None or event_sort > current_sort:
            latest_by_key[key] = event
    selected = tuple(latest_by_key.values())
    if not selected:
        warnings = ("guidance_rows_rejected",) if rejected else ()
        return None, (), warnings
    total = sum((values[event.action] for event in selected), Decimal("0"))
    warnings = (f"guidance_rejected_count:{rejected}",) if rejected else ()
    return (
        float(total / len(selected)),
        tuple(event.id for event in selected if event.id is not None),
        warnings,
    )


def _catalyst_score(features: list[CatalystFeature]) -> float | None:
    selected = [
        feature
        for feature in features
        if getattr(feature, "selected", True)
        and getattr(feature, "opportunity_available", True)
    ]
    if not selected:
        return None
    return max(0.0, min(10.0, sum(feature.opportunity_component for feature in selected)))


def _feature_ids(features: list[CeriRevisionFeature], attribute: str) -> tuple[int, ...]:
    return tuple(
        feature.id
        for feature in features
        if feature.id is not None and getattr(feature, attribute) is not None
    )


def _guidance_sort_key(event: CeriGuidanceEvent) -> tuple[str, int]:
    effective = event.effective_at or event.effective_session
    return (effective.isoformat() if effective is not None else "", event.id or 0)
