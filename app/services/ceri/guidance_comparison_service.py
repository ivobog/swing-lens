from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.models.ceri_tables import CeriGuidanceEvent


@dataclass(frozen=True)
class GuidanceComparison:
    action: str
    confidence: str
    warnings: tuple[str, ...] = ()


def compare_guidance(
    current: CeriGuidanceEvent,
    prior: CeriGuidanceEvent | None,
) -> GuidanceComparison:
    if prior is None:
        return GuidanceComparison("UNKNOWN", "INSUFFICIENT", ("guidance_comparison_insufficient",))
    if (
        current.metric != prior.metric
        or current.period_type != prior.period_type
        or current.currency != prior.currency
        or current.unit != prior.unit
    ):
        return GuidanceComparison("UNKNOWN", "INSUFFICIENT", ("guidance_not_comparable",))

    current_values = _values(current)
    prior_values = _values(prior)
    if current_values is None or prior_values is None:
        return GuidanceComparison("UNKNOWN", "INSUFFICIENT", ("guidance_values_unavailable",))

    current_low, current_high = current_values
    prior_low, prior_high = prior_values
    if current_low == prior_low and current_high == prior_high:
        return GuidanceComparison("MAINTAINED", "HIGH")
    if current_low >= prior_low and current_high >= prior_high:
        return GuidanceComparison("RAISED", "HIGH")
    if current_low <= prior_low and current_high <= prior_high:
        return GuidanceComparison("LOWERED", "HIGH")
    current_width = current_high - current_low
    prior_width = prior_high - prior_low
    if current_width < prior_width:
        return GuidanceComparison("NARROWED", "HIGH")
    if current_width > prior_width:
        return GuidanceComparison("WIDENED", "HIGH")
    return GuidanceComparison("UNKNOWN", "INSUFFICIENT", ("guidance_not_comparable",))


def _values(guidance: CeriGuidanceEvent) -> tuple[Decimal, Decimal] | None:
    point = _decimal(guidance.point_value)
    low = _decimal(guidance.low_value)
    high = _decimal(guidance.high_value)
    if point is not None:
        low = high = point
    if low is None or high is None:
        return None
    if high < low:
        return None
    return low, high


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))
