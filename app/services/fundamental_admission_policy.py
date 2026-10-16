"""Bounded Fundamental admission policy with explicit evidence dependencies.

The v1 contract is intentionally tied to ``fundamentals_v2.1``.  A scoring
configuration change must produce a new policy version rather than silently
broadening the set of incomplete scores admitted to consumers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.eligibility_policy import (
    AnalyticalConfidence,
    CompletenessStatus,
    ConsumerEligibilityOutcome,
    ExecutionOutcome,
    IntegrityStatus,
    OrthogonalConsumerDecision,
)
from app.services.warning_taxonomy import classify_warnings

FUNDAMENTAL_ADMISSION_POLICY_VERSION = "fundamental-admission-v1"
FUNDAMENTAL_ADMISSION_SHADOW_POLICY_VERSION = "fundamental-admission-shadow-v1"
FUNDAMENTAL_SCORING_MODEL_VERSION = "fundamentals_v2.1"
FUNDAMENTAL_SCORING_FIELD_COUNT = 79
FUNDAMENTAL_COMPONENTS = frozenset(
    {
        "growth_quality_score",
        "profitability_quality_score",
        "fcf_quality_score",
        "earnings_quality_score",
        "capital_efficiency_score",
        "balance_sheet_quality_score",
        "valuation_quality_score",
        "forward_quality_score",
        "shareholder_quality_score",
        "liquidity_risk_score",
    }
)
CERTIFIED_LOW_PRIORITY_FIELDS = frozenset(
    {
        "capex_estimate_ntm",
        "quick_ratio_annual",
        "number_of_shareholders_annual",
        "analyst_rating",
    }
)
VALID_FUNDAMENTAL_LABELS = frozenset(
    {
        "Clean compounder",
        "High-quality quant",
        "Mixed but interesting",
        "Low priority",
        "Quality risk",
        "Value trap risk",
    }
)
SCORE_ONLY_EDGES = frozenset(
    {
        "FUNDAMENTAL_TO_COMBINED",
        "FUNDAMENTAL_TO_RANKING",
    }
)


@dataclass(frozen=True)
class FundamentalAdmissionInput:
    consumer_edge: str
    source_identity_fingerprint: str
    source_evidence_id: int | None
    decision_cutoff: datetime
    fundamental_score: Any
    fundamental_label: str | None
    scoring_model_version: str | None
    coverage: Mapping[str, Any]
    component_coverage: Mapping[str, Any]
    parse_diagnostics: Mapping[str, Any]
    warning_codes: tuple[str, ...] = ()


def evaluate_fundamental_admission(
    value: FundamentalAdmissionInput,
    *,
    authoritative: bool = False,
) -> OrthogonalConsumerDecision:
    reasons: list[str] = []
    blocking = False
    if value.consumer_edge not in SCORE_ONLY_EDGES:
        reasons.append("consumer_field_dependencies_not_certified")
        blocking = True
    if not _finite_number(value.fundamental_score):
        reasons.append("fundamental_score_missing_or_non_finite")
        blocking = True
    if value.fundamental_label not in VALID_FUNDAMENTAL_LABELS:
        reasons.append("fundamental_label_invalid")
        blocking = True
    if value.scoring_model_version != FUNDAMENTAL_SCORING_MODEL_VERSION:
        reasons.append("scoring_model_version_unsupported")
        blocking = True
    source_identity_valid = _valid_sha256(value.source_identity_fingerprint)
    source_evidence_valid = (
        isinstance(value.source_evidence_id, int)
        and not isinstance(value.source_evidence_id, bool)
        and value.source_evidence_id > 0
    )
    if not source_identity_valid or not source_evidence_valid:
        reasons.append("source_identity_invalid")
        blocking = True
    if value.decision_cutoff.tzinfo is None or value.decision_cutoff.utcoffset() is None:
        raise ValueError("Fundamental admission decision cutoff must be timezone-aware")
    if not str(value.fundamental_label or "").strip():
        reasons.append("fundamental_label_missing")
        blocking = True

    failed_count = _nonnegative_int(value.parse_diagnostics.get("failed_field_count"))
    failed_fields = tuple(value.parse_diagnostics.get("failed_fields") or ())
    parse_failed = failed_count is None or failed_count > 0 or bool(failed_fields)
    if parse_failed:
        reasons.append("fundamental_parse_failure")
        blocking = True

    missing = value.coverage.get("missing_fields_by_priority") or {}
    priority_contract_valid = isinstance(missing, Mapping) and set(missing) == {
        "critical",
        "high",
        "medium",
        "low",
    }
    missing_critical = _field_names(missing.get("critical"))
    missing_high = _field_names(missing.get("high"))
    missing_medium = _field_names(missing.get("medium"))
    missing_low = _field_names(missing.get("low"))
    if not priority_contract_valid or None in {
        missing_critical,
        missing_high,
        missing_medium,
        missing_low,
    }:
        reasons.append("field_priority_contract_invalid")
        blocking = True
    missing_critical = missing_critical or ()
    missing_high = missing_high or ()
    missing_medium = missing_medium or ()
    missing_low = missing_low or ()
    if missing_critical:
        reasons.append("critical_field_missing")
        blocking = True
    if missing_high:
        reasons.append("high_priority_field_missing")
        blocking = True
    if missing_medium:
        reasons.append("medium_priority_field_missing")
        blocking = True
    if set(missing_low) - CERTIFIED_LOW_PRIORITY_FIELDS:
        reasons.append("low_priority_field_not_certified")
        blocking = True

    available = _nonnegative_int(value.coverage.get("available_scoring_fields"))
    total = _nonnegative_int(value.coverage.get("total_scoring_fields"))
    ratio = _finite_float(value.coverage.get("coverage_ratio"))
    coverage_score = _finite_float(value.coverage.get("data_coverage_score"))
    expected_ratio = (
        round(available / total, 4)
        if available is not None and total not in {None, 0}
        else None
    )
    if (
        available is None
        or total != FUNDAMENTAL_SCORING_FIELD_COUNT
        or available > total
        or ratio is None
        or expected_ratio is None
        or abs(ratio - expected_ratio) > 0.0001
        or coverage_score is None
        or abs(coverage_score - round(expected_ratio * 10, 4)) > 0.0001
    ):
        reasons.append("field_coverage_contract_invalid")
        blocking = True

    if set(value.component_coverage) != FUNDAMENTAL_COMPONENTS:
        reasons.append("component_coverage_contract_invalid")
        blocking = True
    unsupported_components = sorted(
        name
        for name, coverage in value.component_coverage.items()
        if not _finite_number(coverage) or not 0.0 < float(coverage) <= 1.0
    )
    if unsupported_components:
        reasons.append("component_evidence_absent")
        blocking = True

    warnings = classify_warnings(value.warning_codes)
    warning_blocks = any(item.blocks_integrity_readiness for item in warnings)
    if warning_blocks:
        reasons.append("blocking_or_unclassified_warning")
        blocking = True
    if missing_low and not blocking:
        reasons.append("low_priority_missing_with_component_support")
    if warnings and not blocking:
        reasons.append("classified_investment_or_strategy_warning")
    if not reasons:
        reasons.append("complete_score_evidence")

    return OrthogonalConsumerDecision(
        producer="FUNDAMENTAL",
        consumer=value.consumer_edge.removeprefix("FUNDAMENTAL_TO_"),
        consumer_edge=value.consumer_edge,
        policy_version=(
            FUNDAMENTAL_ADMISSION_POLICY_VERSION
            if authoritative
            else FUNDAMENTAL_ADMISSION_SHADOW_POLICY_VERSION
        ),
        source_identity_fingerprint=value.source_identity_fingerprint,
        source_evidence_id=value.source_evidence_id,
        decision_cutoff=value.decision_cutoff,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        integrity_status=(
            IntegrityStatus.UNCERTIFIED
            if (
                parse_failed
                or warning_blocks
                or not source_identity_valid
                or not source_evidence_valid
            )
            else IntegrityStatus.CERTIFIED
        ),
        completeness_status=(
            CompletenessStatus.INSUFFICIENT
            if blocking
            else CompletenessStatus.PARTIAL
            if missing_low
            else CompletenessStatus.COMPLETE
        ),
        analytical_confidence=(
            AnalyticalConfidence.INSUFFICIENT
            if blocking
            else AnalyticalConfidence.LOW
            if missing_low
            else AnalyticalConfidence.NORMAL
        ),
        warnings=warnings,
        eligibility=(
            ConsumerEligibilityOutcome.UNDECIDED
            if blocking
            else ConsumerEligibilityOutcome.INCLUDED
        ),
        reason_codes=tuple(reasons),
        authoritative=authoritative,
    )


def fundamental_input_from_frozen_row(
    row: Mapping[str, Any], *, consumer_edge: str
) -> FundamentalAdmissionInput:
    flags = row.get("v2_warning_flags_json") or {}
    return FundamentalAdmissionInput(
        consumer_edge=consumer_edge,
        source_identity_fingerprint=str(row["calculation_identity_fingerprint"]),
        source_evidence_id=row.get("evidence_id"),
        decision_cutoff=datetime.fromisoformat(str(row["decision_cutoff"]).replace("Z", "+00:00")),
        fundamental_score=row.get("fundamental_score"),
        fundamental_label=row.get("fundamental_label"),
        scoring_model_version=row.get("scoring_model_version"),
        coverage=row.get("coverage") or {},
        component_coverage=row.get("component_coverage") or {},
        parse_diagnostics=row.get("parse_diagnostics") or {},
        warning_codes=tuple(flags.get("flags") or ()),
    )


def _finite_number(value: Any) -> bool:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return number.is_finite() and math.isfinite(float(number))


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _finite_float(value: Any) -> float | None:
    if not _finite_number(value):
        return None
    return float(value)


def _field_names(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    fields = tuple(value)
    if any(not isinstance(field, str) or not field.strip() for field in fields):
        return None
    if len(set(fields)) != len(fields):
        return None
    return fields
