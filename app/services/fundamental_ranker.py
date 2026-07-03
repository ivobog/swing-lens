from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.services.column_mapper import MappedCsvRow
from app.services.numeric_parser import parse_financial_number


@dataclass(frozen=True)
class FundamentalScoreResult:
    ticker: str
    growth_score: float
    profitability_score: float
    fcf_score: float
    balance_sheet_score: float
    valuation_score: float
    momentum_score: float
    dilution_score: float
    risk_score: float
    missing_data_penalty: float
    fundamental_score: float
    fundamental_label: str
    trap_flags: list[str]
    explanation: str
    debug: dict[str, Any]


def score_rows(rows: list[MappedCsvRow]) -> list[FundamentalScoreResult]:
    return [score_row(row) for row in rows if row.ticker]


def score_row(row: MappedCsvRow) -> FundamentalScoreResult:
    values = row.canonical
    component_scores = {
        "growth_score": _growth_score(values),
        "profitability_score": _profitability_score(values),
        "fcf_score": _fcf_score(values),
        "balance_sheet_score": _balance_sheet_score(values),
        "valuation_score": _valuation_score(values),
        "momentum_score": _momentum_score(values),
        "dilution_score": _dilution_score(values),
        "risk_score": _risk_score(values),
    }
    missing_data_penalty = _missing_data_penalty(values)
    trap_flags = _trap_flags(values, component_scores, missing_data_penalty)
    fundamental_score = _weighted_score(component_scores) - missing_data_penalty
    fundamental_score = _clamp(fundamental_score)
    label = _label_for_score(fundamental_score, component_scores, trap_flags)
    explanation = _explain(label, component_scores, trap_flags, missing_data_penalty)

    return FundamentalScoreResult(
        ticker=row.ticker,
        growth_score=component_scores["growth_score"],
        profitability_score=component_scores["profitability_score"],
        fcf_score=component_scores["fcf_score"],
        balance_sheet_score=component_scores["balance_sheet_score"],
        valuation_score=component_scores["valuation_score"],
        momentum_score=component_scores["momentum_score"],
        dilution_score=component_scores["dilution_score"],
        risk_score=component_scores["risk_score"],
        missing_data_penalty=missing_data_penalty,
        fundamental_score=fundamental_score,
        fundamental_label=label,
        trap_flags=trap_flags,
        explanation=explanation,
        debug={
            "component_scores": component_scores,
            "missing_fields": sorted(_missing_core_fields(values)),
            "canonical_fields_present": sorted(values.keys()),
            "parse_diagnostics": _parse_diagnostics(values),
        },
    )


def to_decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def _weighted_score(component_scores: dict[str, float]) -> float:
    weights = _load_scoring_weights()
    total = 0.0
    for key, weight in weights.items():
        total += component_scores[key] * float(weight)
    return total


def _load_scoring_weights(path: Path = Path("config/scoring_weights.yaml")) -> dict[str, float]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    return dict(config.get("fundamental_components", {}))


def _growth_score(values: dict[str, Any]) -> float:
    scores = [
        _higher_better(values, "revenue_growth_quarterly_yoy", -10, 30),
        _higher_better(values, "revenue_growth_ttm_yoy", -10, 25),
        _higher_better(values, "revenue_growth_5y_cagr", 0, 20),
        _higher_better(values, "eps_growth_quarterly_yoy", -10, 30),
        _higher_better(values, "eps_growth_ttm_yoy", -10, 25),
        _higher_better(values, "ebitda_growth_ttm_yoy", -10, 25),
        _higher_better(values, "fcf_growth_ttm_yoy", -20, 30),
    ]
    return _average_present(scores)


def _profitability_score(values: dict[str, Any]) -> float:
    scores = [
        _higher_better(values, "gross_margin_ttm", 10, 60),
        _higher_better(values, "ebitda_margin_ttm", 5, 35),
        _higher_better(values, "operating_margin_ttm", 0, 25),
        _higher_better(values, "net_margin_ttm", 0, 20),
        _higher_better(values, "roe_ttm", 0, 25),
        _higher_better(values, "roic_ttm", 0, 20),
        _higher_better(values, "roce_ttm", 0, 20),
    ]
    return _average_present(scores)


def _fcf_score(values: dict[str, Any]) -> float:
    scores = [
        _positive_amount(values, "fcf_ttm"),
        _higher_better(values, "fcf_margin_ttm", 0, 20),
        _higher_better(values, "fcf_growth_ttm_yoy", -20, 30),
        _lower_better(values, "pfcf", 35, 8),
        _lower_better(values, "ev_fcf", 35, 8),
        _positive_amount(values, "operating_cash_flow_per_share_ttm"),
    ]
    return _average_present(scores)


def _balance_sheet_score(values: dict[str, Any]) -> float:
    scores = [
        _lower_better(values, "net_debt_to_ebitda", 5, 0),
        _lower_better(values, "debt_to_equity", 250, 30),
        _lower_better(values, "debt_to_assets", 70, 20),
        _range_score(
            values,
            "current_ratio",
            low_bad=0.8,
            ideal_low=1.2,
            ideal_high=3.0,
            high_bad=6.0,
        ),
        _higher_better(values, "ebitda_interest_coverage_ttm", 1, 10),
        _lower_better(values, "total_debt_to_capital_annual", 80, 25),
    ]
    return _average_present(scores)


def _valuation_score(values: dict[str, Any]) -> float:
    scores = [
        _lower_better(values, "pe_ratio", 60, 12),
        _lower_better(values, "forward_pe", 45, 10),
        _lower_better(values, "ps_ratio", 12, 2),
        _lower_better(values, "ev_revenue", 12, 2),
        _lower_better(values, "ev_ebitda", 30, 8),
        _lower_better(values, "pfcf", 35, 8),
        _lower_better(values, "peg_ratio", 3, 0.8),
    ]
    return _average_present(scores)


def _momentum_score(values: dict[str, Any]) -> float:
    scores = [
        _higher_better(values, "performance_1w_pct", -8, 8),
        _higher_better(values, "performance_1m_pct", -15, 15),
        _higher_better(values, "performance_3m_pct", -25, 35),
        _higher_better(values, "performance_1y_pct", -40, 80),
        _higher_better(values, "tradingview_momentum_10d", -10, 10),
    ]
    return _average_present(scores)


def _dilution_score(values: dict[str, Any]) -> float:
    buyback_yield = _number(values.get("buyback_yield"))
    if buyback_yield is None:
        return 5.0
    return _clamp(5 + buyback_yield)


def _risk_score(values: dict[str, Any]) -> float:
    scores = [
        _lower_better(values, "beta_1y", 2.2, 0.8),
        _lower_better(values, "beta_3y", 2.2, 0.8),
        _lower_better(values, "tradingview_atr_pct_14d", 12, 2),
        _higher_better(values, "dollar_volume_30d", 2_000_000, 25_000_000),
        _higher_better(values, "current_ratio", 0.8, 2.5),
        _lower_better(values, "net_debt_to_ebitda", 5, 0),
    ]
    return _average_present(scores)


def _trap_flags(
    values: dict[str, Any],
    scores: dict[str, float],
    missing_data_penalty: float,
) -> list[str]:
    flags: list[str] = []

    fcf_ttm = _number(values.get("fcf_ttm"))
    net_debt_to_ebitda = _number(values.get("net_debt_to_ebitda"))
    current_ratio = _number(values.get("current_ratio"))
    buyback_yield = _number(values.get("buyback_yield"))

    if fcf_ttm is not None and fcf_ttm <= 0:
        flags.append("Negative free cash flow")
    if net_debt_to_ebitda is not None and net_debt_to_ebitda > 4:
        flags.append("High leverage")
    if current_ratio is not None and current_ratio < 1:
        flags.append("Weak liquidity")
    if scores["valuation_score"] <= 3:
        flags.append("Extreme valuation")
    if buyback_yield is not None and buyback_yield < -2:
        flags.append("Share dilution")
    if scores["growth_score"] >= 7 and scores["profitability_score"] <= 4:
        flags.append("Growth without profitability")
    if (
        scores["valuation_score"] >= 7
        and scores["growth_score"] <= 4
        and scores["profitability_score"] <= 4
    ):
        flags.append("Cheap but deteriorating fundamentals")
    if missing_data_penalty >= 1:
        flags.append("Missing critical data")

    return flags


def _label_for_score(score: float, components: dict[str, float], trap_flags: list[str]) -> str:
    trap_flag_set = set(trap_flags)
    if {
        "Negative free cash flow",
        "High leverage",
        "Weak liquidity",
        "Cheap but deteriorating fundamentals",
    } & trap_flag_set:
        return "Value trap risk"
    if "Growth without profitability" in trap_flag_set or "Extreme valuation" in trap_flag_set:
        return "Growth trap risk"
    if score >= 7.6 and components["profitability_score"] >= 7 and components["fcf_score"] >= 6.5:
        return "Clean compounder"
    if score >= 6.7:
        return "High-quality quant"
    if score >= 5.0:
        return "Mixed but interesting"
    return "Low priority"


def _explain(
    label: str,
    components: dict[str, float],
    trap_flags: list[str],
    missing_data_penalty: float,
) -> str:
    strongest = max(components, key=components.get)
    weakest = min(components, key=components.get)
    parts = [
        f"{label}: strongest area is {strongest.replace('_score', '').replace('_', ' ')}",
        f"weakest area is {weakest.replace('_score', '').replace('_', ' ')}",
    ]
    if trap_flags:
        parts.append(f"flags: {', '.join(trap_flags[:3])}")
    if missing_data_penalty:
        parts.append(f"missing-data penalty {missing_data_penalty:.1f}")
    return "; ".join(parts) + "."


def _missing_data_penalty(values: dict[str, Any]) -> float:
    missing = _missing_core_fields(values)
    return min(2.0, len(missing) * 0.25)


def _missing_core_fields(values: dict[str, Any]) -> set[str]:
    core_fields = {
        "revenue_growth_ttm_yoy",
        "eps_growth_ttm_yoy",
        "gross_margin_ttm",
        "operating_margin_ttm",
        "fcf_ttm",
        "fcf_margin_ttm",
        "net_debt_to_ebitda",
        "current_ratio",
        "pe_ratio",
        "market_cap",
    }
    return {field for field in core_fields if _number(values.get(field)) is None}


def _higher_better(
    values: dict[str, Any],
    field: str,
    poor: float,
    excellent: float,
) -> float | None:
    value = _number(values.get(field))
    if value is None:
        return None
    return _clamp((value - poor) / (excellent - poor) * 10)


def _lower_better(
    values: dict[str, Any],
    field: str,
    poor: float,
    excellent: float,
) -> float | None:
    value = _number(values.get(field))
    if value is None:
        return None
    return _clamp((poor - value) / (poor - excellent) * 10)


def _positive_amount(values: dict[str, Any], field: str) -> float | None:
    value = _number(values.get(field))
    if value is None:
        return None
    if value <= 0:
        return 0.0
    return 10.0


def _range_score(
    values: dict[str, Any],
    field: str,
    low_bad: float,
    ideal_low: float,
    ideal_high: float,
    high_bad: float,
) -> float | None:
    value = _number(values.get(field))
    if value is None:
        return None
    if ideal_low <= value <= ideal_high:
        return 10.0
    if value < ideal_low:
        return _clamp((value - low_bad) / (ideal_low - low_bad) * 10)
    return _clamp((high_bad - value) / (high_bad - ideal_high) * 10)


def _average_present(scores: list[float | None]) -> float:
    present = [score for score in scores if score is not None]
    if not present:
        return 5.0
    return round(sum(present) / len(present), 4)


def _number(value: Any) -> float | None:
    return parse_financial_number(value).value


def _parse_diagnostics(values: dict[str, Any]) -> dict[str, Any]:
    failed_fields = []
    for field, raw_value in values.items():
        result = parse_financial_number(raw_value)
        if not result.parsed and result.reason != "missing":
            failed_fields.append(
                {
                    "field": field,
                    "raw": raw_value,
                    "normalized": result.normalized,
                    "reason": result.reason,
                }
            )
    return {
        "failed_fields": failed_fields,
        "failed_field_count": len(failed_fields),
    }


def _clamp(value: float, lower: float = 0.0, upper: float = 10.0) -> float:
    return round(max(lower, min(upper, value)), 4)
