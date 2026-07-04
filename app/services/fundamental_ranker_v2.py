from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.services.column_mapper import MappedCsvRow
from app.services.fundamental_components_v2 import score_components_v2
from app.services.fundamental_coverage_service import calculate_coverage_v2
from app.services.fundamental_warning_service import (
    BALANCE_SHEET_STRESS,
    EARNINGS_QUALITY_RISK,
    FORWARD_QUALITY_WEAK,
    POOR_CASH_CONVERSION,
    build_warning_flags_v2,
)


@dataclass(frozen=True)
class FundamentalScoreV2Result:
    ticker: str
    growth_quality_score: float
    profitability_quality_score: float
    fcf_quality_score: float
    earnings_quality_score: float
    capital_efficiency_score: float
    balance_sheet_quality_score: float
    valuation_quality_score: float
    forward_quality_score: float
    shareholder_quality_score: float
    liquidity_risk_score: float
    data_coverage_score: float
    missing_data_penalty: float
    fundamental_score: float
    fundamental_label: str
    warning_flags: list[str]
    explanation: str
    debug: dict[str, Any]


def score_rows_v2(rows: list[MappedCsvRow]) -> list[FundamentalScoreV2Result]:
    config = load_fundamentals_v2_config()
    return [score_row_v2(row, config=config) for row in rows if row.ticker]


def score_row_v2(
    row: MappedCsvRow,
    config: dict[str, Any] | None = None,
) -> FundamentalScoreV2Result:
    config = config or load_fundamentals_v2_config()
    values = row.canonical
    component_scores = score_components_v2(values, config["thresholds"])
    coverage = calculate_coverage_v2(values, config)
    warning_flags = build_warning_flags_v2(
        values=values,
        component_scores=component_scores,
        coverage=coverage,
        thresholds=config["thresholds"],
        sparse_data_coverage_threshold=float(config["missing_data"]["sparse_data_coverage_threshold"]),
    )
    score = _weighted_score(component_scores, config["weights"]) - coverage.missing_data_penalty
    score = _clamp(score)
    label = _label_for_score(score, component_scores, warning_flags)
    explanation = _explain(label, component_scores, warning_flags, coverage.data_coverage_score)

    return FundamentalScoreV2Result(
        ticker=row.ticker,
        growth_quality_score=component_scores["growth_quality_score"],
        profitability_quality_score=component_scores["profitability_quality_score"],
        fcf_quality_score=component_scores["fcf_quality_score"],
        earnings_quality_score=component_scores["earnings_quality_score"],
        capital_efficiency_score=component_scores["capital_efficiency_score"],
        balance_sheet_quality_score=component_scores["balance_sheet_quality_score"],
        valuation_quality_score=component_scores["valuation_quality_score"],
        forward_quality_score=component_scores["forward_quality_score"],
        shareholder_quality_score=component_scores["shareholder_quality_score"],
        liquidity_risk_score=component_scores["liquidity_risk_score"],
        data_coverage_score=coverage.data_coverage_score,
        missing_data_penalty=coverage.missing_data_penalty,
        fundamental_score=score,
        fundamental_label=label,
        warning_flags=warning_flags,
        explanation=explanation,
        debug={
            "model_version": config["model_version"],
            "component_scores": component_scores,
            "component_coverage": coverage.component_coverage,
            "coverage": {
                "available_scoring_fields": coverage.available_scoring_fields,
                "total_scoring_fields": coverage.total_scoring_fields,
                "coverage_ratio": coverage.coverage_ratio,
                "data_coverage_score": coverage.data_coverage_score,
                "missing_core_fields": coverage.missing_core_fields,
                "missing_high_fields": coverage.missing_high_fields,
                "missing_fields_by_priority": coverage.missing_fields_by_priority,
            },
            "warnings": warning_flags,
            "parse_diagnostics": coverage.parse_diagnostics,
            "canonical_fields_present": sorted(values.keys()),
        },
    )


def load_fundamentals_v2_config(
    path: Path = Path("config/fundamentals_v2.yaml"),
) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def to_decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def _weighted_score(component_scores: dict[str, float], weights: dict[str, Any]) -> float:
    return round(
        sum(component_scores[key] * float(weight) for key, weight in weights.items()),
        4,
    )


def _label_for_score(
    score: float,
    components: dict[str, float],
    warning_flags: list[str],
) -> str:
    flag_set = set(warning_flags)
    if BALANCE_SHEET_STRESS in flag_set or POOR_CASH_CONVERSION in flag_set:
        return "Value trap risk"
    if EARNINGS_QUALITY_RISK in flag_set or FORWARD_QUALITY_WEAK in flag_set:
        return "Quality risk"
    if (
        score >= 7.6
        and components["profitability_quality_score"] >= 7
        and components["fcf_quality_score"] >= 6.5
        and components["earnings_quality_score"] >= 6.5
    ):
        return "Clean compounder"
    if score >= 6.7:
        return "High-quality quant"
    if score >= 5.0:
        return "Mixed but interesting"
    return "Low priority"


def _explain(
    label: str,
    components: dict[str, float],
    warning_flags: list[str],
    data_coverage_score: float,
) -> str:
    strongest = max(components, key=components.get)
    weakest = min(components, key=components.get)
    parts = [
        f"{label}: strongest area is {_display_component(strongest)}",
        f"weakest area is {_display_component(weakest)}",
        f"data coverage {data_coverage_score:.1f}/10",
    ]
    if warning_flags:
        parts.append(f"warnings: {', '.join(warning_flags[:3])}")
    return "; ".join(parts) + "."


def _display_component(component: str) -> str:
    return component.replace("_score", "").replace("_", " ")


def _clamp(value: float, lower: float = 0.0, upper: float = 10.0) -> float:
    return round(max(lower, min(upper, value)), 4)
