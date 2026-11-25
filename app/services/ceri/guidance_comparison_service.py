from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.models.ceri_tables import CeriGuidanceEvent, CeriSourceRecord


@dataclass(frozen=True)
class GuidanceComparison:
    action: str
    confidence: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuidanceIdentity:
    period: str
    financial_basis: str


def guidance_identity(
    guidance: CeriGuidanceEvent, source: CeriSourceRecord | None,
) -> GuidanceIdentity | None:
    if (
        source is None or source.id != guidance.source_record_id
        or source.provider != "sec" or source.dataset != "guidance"
        or source.quarantine_reason is not None
    ):
        return None
    payload = source.restricted_normalized_json or {}
    label = guidance.period_label
    basis = payload.get("guidance_financial_basis")
    period_shape = {
        "CURRENT_FISCAL_YEAR": r"FY20\d{2}",
        "CURRENT_QUARTER": r"FY20\d{2}-Q[1-4]",
    }.get(guidance.period_type)
    supported = {
        "REVENUE": {
            "REVENUE_REPORTED", "REVENUE_UNDERLYING", "REVENUE_CONSTANT_CURRENCY",
            "REVENUE_CONTINUING_OPERATIONS",
        },
        "EPS_DILUTED": {"EPS_REPORTED", "EPS_ADJUSTED"},
    }.get(guidance.metric, ())
    if (
        label is None or label != payload.get("period_label")
        or period_shape is None or re.fullmatch(period_shape, label) is None
        or not isinstance(basis, str) or basis not in supported
        or not guidance.unit or (guidance.unit != "%" and not guidance.currency)
    ):
        return None
    return GuidanceIdentity(label, basis)


def compare_guidance(
    current: CeriGuidanceEvent,
    prior: CeriGuidanceEvent | None,
    *,
    current_source: CeriSourceRecord | None = None,
    prior_source: CeriSourceRecord | None = None,
) -> GuidanceComparison:
    if prior is None:
        return GuidanceComparison("UNKNOWN", "INSUFFICIENT", ("guidance_comparison_insufficient",))
    identity = guidance_identity(current, current_source)
    if (
        identity is None
        or identity != guidance_identity(prior, prior_source)
        or current.company_id != prior.company_id
        or current.metric != prior.metric
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
