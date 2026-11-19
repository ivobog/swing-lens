import math
from decimal import Decimal
from typing import Any

from app.services.numeric_parser import CURRENCY_CODES, parse_financial_number

FORWARD_FORMULA_FIELDS_V21 = {
    "eps_estimate_annual",
    "eps_estimate_quarterly",
    "revenue_estimate_annual",
    "net_income_estimate_ntm",
    "ebit_estimate_ntm",
    "cash_short_term_investments_estimate_annual",
    "capex_estimate_ntm",
    "analyst_rating",
}
FORWARD_QUALITY_WEIGHTS = {
    "net_income_growth": 0.30,
    "revenue_comparison": 0.25,
    "profitability": 0.20,
    "cash_comparison": 0.10,
    "capex_sustainability": 0.10,
    "analyst_rating": 0.05,
}

COMPONENT_SCORE_KEYS = [
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
]

COMPONENT_FORMULA_FIELDS = {
    "growth_quality_score": {
        "revenue_growth_quarterly_yoy",
        "revenue_growth_ttm_yoy",
        "revenue_growth_5y_cagr",
        "eps_growth_quarterly_yoy",
        "eps_growth_ttm_yoy",
        "ebitda_growth_ttm_yoy",
        "fcf_growth_ttm_yoy",
        "gross_profit_growth_ttm_yoy",
        "net_income_growth_ttm_yoy",
        "total_revenue_ttm",
    },
    "profitability_quality_score": {
        "gross_margin_ttm",
        "gross_margin_annual",
        "ebitda_margin_ttm",
        "operating_margin_ttm",
        "operating_margin_annual",
        "net_margin_ttm",
        "net_margin_annual",
        "roe_ttm",
        "roa_ttm",
        "roa_annual",
        "roic_ttm",
        "roce_ttm",
        "return_on_total_capital_ttm",
    },
    "fcf_quality_score": {
        "fcf_ttm",
        "fcf_margin_ttm",
        "fcf_growth_ttm_yoy",
        "pfcf",
        "ev_fcf",
        "operating_cash_flow_per_share_ttm",
        "capex_ttm",
        "capex_growth_ttm_yoy",
    },
    "earnings_quality_score": {
        "sloan_ratio_ttm",
        "net_income_ttm",
        "fcf_ttm",
        "operating_cash_flow_per_share_ttm",
    },
    "capital_efficiency_score": {
        "roic_ttm",
        "roic_annual",
        "return_on_total_capital_ttm",
        "roa_ttm",
        "roa_annual",
        "asset_turnover_ttm",
        "asset_turnover_annual",
        "total_assets_growth_annual_yoy",
    },
    "balance_sheet_quality_score": {
        "total_assets_annual",
        "total_liabilities_annual",
        "working_capital_per_share_annual",
        "working_capital_per_share_quarterly",
        "quick_ratio_quarterly",
        "quick_ratio_annual",
        "cash_short_term_investments_annual",
        "current_ratio",
        "net_debt_to_ebitda",
        "debt_to_ebitda_annual",
        "debt_to_equity",
        "debt_to_assets",
        "total_debt_to_capital_annual",
        "ebitda_interest_coverage_ttm",
    },
    "valuation_quality_score": {
        "pe_ratio",
        "forward_pe",
        "earnings_yield_ttm",
        "ps_ratio",
        "ev_revenue",
        "ev_ebitda",
        "pfcf",
        "ev_fcf",
        "peg_ratio",
    },
    "forward_quality_score": {
        "net_income_ttm",
        "revenue_estimate_annual",
        "net_income_estimate_ntm",
        "total_revenue_ttm",
        "net_margin_ttm",
        "cash_short_term_investments_annual",
        "cash_short_term_investments_estimate_annual",
        "capex_ttm",
        "capex_estimate_ntm",
        "analyst_rating",
    },
    "shareholder_quality_score": {
        "buyback_yield",
        "dividend_yield_ttm",
        "dividend_payout_ratio_ttm",
        "total_common_shares_outstanding",
    },
    "liquidity_risk_score": {
        "dollar_volume_10d",
        "dollar_volume_30d",
        "dollar_volume_60d",
        "free_float",
        "relative_volume_1d",
        "volume_change_1d_pct",
        "price_change_1d_pct",
        "beta_1y",
        "beta_3y",
        "tradingview_atr_pct_14d",
    },
}


def score_components_v2(
    values: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    model_version: str = "fundamentals_v2.2",
) -> dict[str, float]:
    return {
        "growth_quality_score": growth_quality_score(values),
        "profitability_quality_score": profitability_quality_score(values),
        "fcf_quality_score": fcf_quality_score(values, thresholds),
        "earnings_quality_score": earnings_quality_score(values, thresholds),
        "capital_efficiency_score": capital_efficiency_score(values, thresholds),
        "balance_sheet_quality_score": balance_sheet_quality_score(values, thresholds),
        "valuation_quality_score": valuation_quality_score(values),
        "forward_quality_score": forward_quality_score(values, model_version=model_version),
        "shareholder_quality_score": shareholder_quality_score(values, thresholds),
        "liquidity_risk_score": liquidity_risk_score(values, thresholds),
    }


def growth_quality_score(values: dict[str, Any]) -> float:
    base = _weighted_average_present(
        [
            (_higher_better(values, "revenue_growth_quarterly_yoy", -10, 25), 0.8),
            (_higher_better(values, "revenue_growth_ttm_yoy", -10, 25), 1.4),
            (_higher_better(values, "revenue_growth_5y_cagr", 0, 20), 1.0),
            (_higher_better(values, "eps_growth_quarterly_yoy", -10, 25), 0.7),
            (_higher_better(values, "eps_growth_ttm_yoy", -10, 25), 1.1),
            (_higher_better(values, "ebitda_growth_ttm_yoy", -10, 25), 0.8),
            (_higher_better(values, "fcf_growth_ttm_yoy", -20, 30), 1.1),
            (_higher_better(values, "gross_profit_growth_ttm_yoy", -10, 25), 0.8),
            (_higher_better(values, "net_income_growth_ttm_yoy", -10, 25), 1.2),
            (_positive_amount(values, "total_revenue_ttm"), 0.4),
        ]
    )
    revenue_growth = _number(values.get("revenue_growth_ttm_yoy"))
    net_income_growth = _number(values.get("net_income_growth_ttm_yoy"))
    eps_growth = _number(values.get("eps_growth_ttm_yoy"))
    fcf_growth = _number(values.get("fcf_growth_ttm_yoy"))

    penalty = 0.0
    if revenue_growth is not None and revenue_growth > 15 and _is_negative(net_income_growth):
        penalty += 1.0
    if eps_growth is not None and eps_growth > 15 and _is_negative(fcf_growth):
        penalty += 1.0
    return _clamp(base - penalty)


def profitability_quality_score(values: dict[str, Any]) -> float:
    base = _weighted_average_present(
        [
            (_higher_better(values, "gross_margin_ttm", 10, 60), 0.8),
            (_higher_better(values, "gross_margin_annual", 10, 60), 0.5),
            (_higher_better(values, "ebitda_margin_ttm", 5, 35), 0.8),
            (_higher_better(values, "operating_margin_ttm", 0, 25), 1.2),
            (_higher_better(values, "operating_margin_annual", 0, 25), 0.7),
            (_higher_better(values, "net_margin_ttm", 0, 20), 1.1),
            (_higher_better(values, "net_margin_annual", 0, 20), 0.7),
            (_higher_better(values, "roe_ttm", 0, 25), 0.7),
            (_higher_better(values, "roa_ttm", 0, 15), 1.1),
            (_higher_better(values, "roa_annual", 0, 15), 0.7),
            (_higher_better(values, "roic_ttm", 0, 20), 1.1),
            (_higher_better(values, "roce_ttm", 0, 20), 0.6),
            (_higher_better(values, "return_on_total_capital_ttm", 0, 20), 0.8),
        ]
    )
    return _clamp(base + _trend_bonus(values, "operating_margin_ttm", "operating_margin_annual"))


def fcf_quality_score(values: dict[str, Any], thresholds: dict[str, Any]) -> float:
    base = _weighted_average_present(
        [
            (_positive_amount(values, "fcf_ttm"), 1.5),
            (_higher_better(values, "fcf_margin_ttm", 0, 20), 1.3),
            (_higher_better(values, "fcf_growth_ttm_yoy", -20, 30), 1.0),
            (_lower_better(values, "pfcf", 35, 8), 0.7),
            (_lower_better(values, "ev_fcf", 35, 8), 0.7),
            (_positive_amount(values, "operating_cash_flow_per_share_ttm"), 0.9),
            (_capex_burden_score(values), 0.7),
        ]
    )
    fcf_growth = _number(values.get("fcf_growth_ttm_yoy"))
    capex_growth = _number(values.get("capex_growth_ttm_yoy"))
    if (
        capex_growth is not None
        and capex_growth > float(thresholds["capex_growth_high"])
        and (fcf_growth is None or fcf_growth <= 0)
    ):
        base -= 1.0
    return _clamp(base)


def earnings_quality_score(values: dict[str, Any], thresholds: dict[str, Any]) -> float:
    net_income = _number(values.get("net_income_ttm"))
    fcf = _number(values.get("fcf_ttm"))
    conversion_score = None
    if net_income is not None and net_income > 0 and fcf is not None:
        conversion_score = _ratio_score(fcf / net_income, poor=0, excellent=1)
    elif net_income is not None and net_income <= 0 and fcf is not None and fcf > 0:
        conversion_score = 7.0
    elif net_income is not None and fcf is not None:
        conversion_score = 0.0

    return _weighted_average_present(
        [
            (
                _lower_better(
                    values,
                    "sloan_ratio_ttm",
                    float(thresholds["sloan_ratio_danger_min"]),
                    0,
                ),
                0.45,
            ),
            (conversion_score, 0.35),
            (_positive_amount(values, "operating_cash_flow_per_share_ttm"), 0.20),
        ]
    )


def capital_efficiency_score(values: dict[str, Any], thresholds: dict[str, Any]) -> float:
    base = _weighted_average_present(
        [
            (_higher_better(values, "roic_ttm", 0, float(thresholds["roic_excellent"])), 1.3),
            (_higher_better(values, "roic_annual", 0, float(thresholds["roic_excellent"])), 0.9),
            (
                _higher_better(
                    values,
                    "return_on_total_capital_ttm",
                    0,
                    float(thresholds["roic_excellent"]),
                ),
                0.9,
            ),
            (_higher_better(values, "roa_ttm", 0, float(thresholds["roa_excellent"])), 1.1),
            (_higher_better(values, "roa_annual", 0, float(thresholds["roa_excellent"])), 0.7),
            (_higher_better(values, "asset_turnover_ttm", 0.1, 1.5), 0.9),
            (_higher_better(values, "asset_turnover_annual", 0.1, 1.5), 0.6),
        ]
    )
    base += _trend_bonus(values, "roic_ttm", "roic_annual")
    base += _trend_bonus(values, "roa_ttm", "roa_annual")
    base += _trend_bonus(values, "asset_turnover_ttm", "asset_turnover_annual")

    asset_growth = _number(values.get("total_assets_growth_annual_yoy"))
    if asset_growth is not None and asset_growth > float(thresholds["total_assets_growth_high"]):
        roa_ttm = _number(values.get("roa_ttm"))
        roa_annual = _number(values.get("roa_annual"))
        turnover_ttm = _number(values.get("asset_turnover_ttm"))
        turnover_annual = _number(values.get("asset_turnover_annual"))
        if _declined(roa_ttm, roa_annual) or _declined(turnover_ttm, turnover_annual):
            base -= 1.2
    return _clamp(base)


def balance_sheet_quality_score(values: dict[str, Any], thresholds: dict[str, Any]) -> float:
    liabilities_to_assets = _liabilities_to_assets(values)
    working_capital_trend = _difference_score(
        values,
        "working_capital_per_share_quarterly",
        "working_capital_per_share_annual",
    )
    scores = [
        (
            _inverse_ratio_score(
                liabilities_to_assets,
                poor=float(thresholds["liabilities_to_assets_warning_min"]),
                excellent=float(thresholds["liabilities_to_assets_good_max"]),
            ),
            1.2,
        ),
        (_positive_amount(values, "working_capital_per_share_annual"), 0.7),
        (_positive_amount(values, "working_capital_per_share_quarterly"), 0.8),
        (working_capital_trend, 0.4),
        (_higher_better(values, "quick_ratio_quarterly", 0.5, 1.5), 0.9),
        (_higher_better(values, "quick_ratio_annual", 0.5, 1.5), 0.4),
        (_positive_amount(values, "cash_short_term_investments_annual"), 0.5),
        (_range_score(values, "current_ratio", 0.8, 1.2, 3.0, 6.0), 0.8),
        (_lower_better(values, "net_debt_to_ebitda", 5, 0), 0.9),
        (_lower_better(values, "debt_to_ebitda_annual", 5, 0), 0.7),
        (_lower_better(values, "debt_to_equity", 250, 30), 0.5),
        (_lower_better(values, "debt_to_assets", 70, 20), 0.6),
        (_lower_better(values, "total_debt_to_capital_annual", 80, 25), 0.5),
        (_higher_better(values, "ebitda_interest_coverage_ttm", 1, 10), 0.6),
    ]
    return _weighted_average_present(scores)


def valuation_quality_score(values: dict[str, Any]) -> float:
    return _weighted_average_present(
        [
            (_lower_better(values, "pe_ratio", 60, 12), 1.0),
            (_lower_better(values, "forward_pe", 45, 10), 1.0),
            (_higher_better(values, "earnings_yield_ttm", 0, 8), 1.1),
            (_lower_better(values, "ps_ratio", 12, 2), 0.8),
            (_lower_better(values, "ev_revenue", 12, 2), 0.8),
            (_lower_better(values, "ev_ebitda", 30, 8), 0.9),
            (_lower_better(values, "pfcf", 35, 8), 0.9),
            (_lower_better(values, "ev_fcf", 35, 8), 0.9),
            (_lower_better(values, "peg_ratio", 3, 0.8), 0.7),
        ]
    )


def forward_quality_score(
    values: dict[str, Any], *, model_version: str = "fundamentals_v2.2"
) -> float:
    if model_version == "fundamentals_v2.1":
        return _forward_quality_score_v21(values)
    if model_version != "fundamentals_v2.2":
        raise ValueError(f"Unsupported fundamental scoring model: {model_version}")
    scores = forward_quality_subscores(values)
    return _weighted_average_present(
        [(scores[key], weight) for key, weight in FORWARD_QUALITY_WEIGHTS.items()]
    )


def forward_quality_subscores(values: dict[str, Any]) -> dict[str, float | None]:
    """v2.2 mathematical availability, distinct from field-presence coverage."""
    income = _forward_amount_pair(values, "net_income_ttm", "net_income_estimate_ntm")
    income_growth = None
    income_score = None
    if income is not None:
        historical, estimated = income
        if historical <= 0:
            income_score = 7.0 if estimated > 0 else 0.0
        elif estimated <= 0:
            income_score = 0.0
        else:
            income_growth = _forward_growth(historical, estimated)
            income_score = _forward_growth_score(income_growth, positive=30, negative=25)

    revenue = _forward_amount_pair(values, "total_revenue_ttm", "revenue_estimate_annual")
    revenue_score = None
    if revenue is not None and revenue[0] > 0 and revenue[1] >= 0:
        revenue_score = _forward_growth_score(_forward_growth(*revenue), positive=20, negative=20)

    cash = _forward_amount_pair(
        values,
        "cash_short_term_investments_annual",
        "cash_short_term_investments_estimate_annual",
    )
    cash_score = None
    if cash is not None and cash[0] > 0 and cash[1] >= 0:
        cash_score = _forward_growth_score(_forward_growth(*cash), positive=30, negative=30)

    capex = _forward_amount_pair(values, "capex_ttm", "capex_estimate_ntm")
    capex_score = None
    if capex is not None and capex[0] != 0:
        expected_income = _forward_number(values.get("net_income_estimate_ntm"))
        if expected_income is not None and expected_income <= 0:
            capex_score = 0.0
        elif income_growth is not None:
            capex_growth = _forward_growth(abs(capex[0]), abs(capex[1]))
            pressure = max(Decimal(0), capex_growth - income_growth)
            capex_score = _clamp(float(Decimal(10) - pressure / 5))

    return {
        "net_income_growth": income_score,
        "revenue_comparison": revenue_score,
        # NTM income and annual revenue lack demonstrably identical forecast periods.
        "profitability": None,
        "cash_comparison": cash_score,
        "capex_sustainability": capex_score,
        "analyst_rating": _forward_analyst_rating_score(values.get("analyst_rating")),
    }


def _forward_number(value: Any) -> float | None:
    try:
        parsed = _number(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _forward_amount_pair(
    values: dict[str, Any], historical_field: str, estimated_field: str
) -> tuple[float, float] | None:
    historical = _forward_number(values.get(historical_field))
    estimated = _forward_number(values.get(estimated_field))
    historical_currency = str(values.get(f"{historical_field}_currency", "")).strip().upper()
    estimated_currency = str(values.get(f"{estimated_field}_currency", "")).strip().upper()
    if (
        historical is None
        or estimated is None
        or historical_currency not in CURRENCY_CODES
        or estimated_currency != historical_currency
    ):
        return None
    return historical, estimated


def _forward_growth(historical: float, estimated: float) -> Decimal:
    # Decimal keeps finite inputs finite even for subnormal denominators; it also
    # avoids inf - inf when comparing extreme CAPEX and earnings growth rates.
    return (Decimal(str(estimated)) / Decimal(str(historical)) - 1) * 100


def _forward_growth_score(growth: Decimal, *, positive: int, negative: int) -> float:
    threshold = positive if growth >= 0 else negative
    return _clamp(float(Decimal(5) + 5 * growth / threshold))


def _forward_analyst_rating_score(value: Any) -> float | None:
    return {
        "strong buy": 10.0,
        "buy": 7.5,
        "outperform": 7.5,
        "hold": 5.0,
        "neutral": 5.0,
        "sell": 2.5,
        "underperform": 2.5,
        "strong sell": 0.0,
    }.get(" ".join(str(value).strip().casefold().split()))


def _forward_quality_score_v21(values: dict[str, Any]) -> float:
    base = _weighted_average_present(
        [
            (_positive_amount(values, "eps_estimate_annual"), 1.0),
            (_positive_amount(values, "eps_estimate_quarterly"), 0.7),
            (_positive_amount(values, "revenue_estimate_annual"), 0.8),
            (_positive_amount(values, "net_income_estimate_ntm"), 1.0),
            (_positive_amount(values, "ebit_estimate_ntm"), 1.0),
            (_positive_amount(values, "cash_short_term_investments_estimate_annual"), 0.4),
            (_capex_estimate_score(values), 0.3),
        ]
    )
    return _clamp(base + _analyst_rating_bonus(values.get("analyst_rating")))


def shareholder_quality_score(values: dict[str, Any], thresholds: dict[str, Any]) -> float:
    payout = _number(values.get("dividend_payout_ratio_ttm"))
    payout_score = None
    if payout is not None:
        payout_score = _lower_better_value(
            payout,
            poor=float(thresholds["dividend_payout_warning_min"]),
            excellent=float(thresholds["dividend_payout_safe_max"]),
        )
    return _weighted_average_present(
        [
            (_buyback_score(values), 1.0),
            (_dividend_yield_score(values), 0.5),
            (payout_score, 1.0),
            (_positive_amount(values, "total_common_shares_outstanding"), 0.2),
        ]
    )


def liquidity_risk_score(values: dict[str, Any], thresholds: dict[str, Any]) -> float:
    dollar_volume_weak = float(thresholds["dollar_volume_weak"])
    dollar_volume_good = float(thresholds["dollar_volume_good"])
    return _weighted_average_present(
        [
            (
                _higher_better(values, "dollar_volume_10d", dollar_volume_weak, dollar_volume_good),
                0.9,
            ),
            (
                _higher_better(values, "dollar_volume_30d", dollar_volume_weak, dollar_volume_good),
                1.0,
            ),
            (
                _higher_better(values, "dollar_volume_60d", dollar_volume_weak, dollar_volume_good),
                1.0,
            ),
            (_positive_amount(values, "free_float"), 0.6),
            (_range_score(values, "relative_volume_1d", 0.2, 0.8, 2.5, 6.0), 0.4),
            (_range_score(values, "volume_change_1d_pct", -80, -20, 80, 300), 0.3),
            (_range_score(values, "price_change_1d_pct", -15, -5, 5, 20), 0.3),
            (_lower_better(values, "beta_1y", float(thresholds["beta_high"]), 0.8), 0.8),
            (_lower_better(values, "beta_3y", float(thresholds["beta_high"]), 0.8), 0.7),
            (
                _lower_better(
                    values,
                    "tradingview_atr_pct_14d",
                    float(thresholds["atr_pct_high"]),
                    2,
                ),
                0.9,
            ),
        ]
    )


def number(value: Any) -> float | None:
    return _number(value)


def liabilities_to_assets(values: dict[str, Any]) -> float | None:
    return _liabilities_to_assets(values)


def _number(value: Any) -> float | None:
    return parse_financial_number(value).value


def _higher_better(
    values: dict[str, Any],
    field: str,
    poor: float,
    excellent: float,
) -> float | None:
    return _higher_better_value(_number(values.get(field)), poor, excellent)


def _higher_better_value(value: float | None, poor: float, excellent: float) -> float | None:
    if value is None:
        return None
    if excellent == poor:
        return 5.0
    return _clamp((value - poor) / (excellent - poor) * 10)


def _lower_better(
    values: dict[str, Any],
    field: str,
    poor: float,
    excellent: float,
) -> float | None:
    return _lower_better_value(_number(values.get(field)), poor, excellent)


def _lower_better_value(value: float | None, poor: float, excellent: float) -> float | None:
    if value is None:
        return None
    if poor == excellent:
        return 5.0
    return _clamp((poor - value) / (poor - excellent) * 10)


def _positive_amount(values: dict[str, Any], field: str) -> float | None:
    value = _number(values.get(field))
    if value is None:
        return None
    return 10.0 if value > 0 else 0.0


def _ratio_score(value: float | None, poor: float, excellent: float) -> float | None:
    return _higher_better_value(value, poor, excellent)


def _inverse_ratio_score(value: float | None, poor: float, excellent: float) -> float | None:
    return _lower_better_value(value, poor, excellent)


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


def _weighted_average_present(scores: list[tuple[float | None, float]]) -> float:
    present = [(score, weight) for score, weight in scores if score is not None]
    if not present:
        return 5.0
    total_weight = sum(weight for _, weight in present)
    return _clamp(sum(score * weight for score, weight in present) / total_weight)


def _trend_bonus(values: dict[str, Any], current_field: str, baseline_field: str) -> float:
    current = _number(values.get(current_field))
    baseline = _number(values.get(baseline_field))
    if current is None or baseline is None:
        return 0.0
    if current >= baseline:
        return 0.3
    if current < baseline - 2:
        return -0.4
    return 0.0


def _declined(current: float | None, baseline: float | None) -> bool:
    return current is not None and baseline is not None and current < baseline


def _is_negative(value: float | None) -> bool:
    return value is not None and value < 0


def _capex_burden_score(values: dict[str, Any]) -> float | None:
    capex = _number(values.get("capex_ttm"))
    fcf = _number(values.get("fcf_ttm"))
    if capex is None:
        return None
    if capex >= 0:
        return 8.0
    if fcf is None or fcf <= 0:
        return 3.0
    return _clamp(10 - min(7, abs(capex) / fcf * 3))


def _capex_estimate_score(values: dict[str, Any]) -> float | None:
    value = _number(values.get("capex_estimate_ntm"))
    if value is None:
        return None
    return 7.0 if value < 0 else 5.0


def _buyback_score(values: dict[str, Any]) -> float | None:
    buyback_yield = _number(values.get("buyback_yield"))
    if buyback_yield is None:
        return None
    return _clamp(5 + buyback_yield)


def _dividend_yield_score(values: dict[str, Any]) -> float | None:
    dividend_yield = _number(values.get("dividend_yield_ttm"))
    if dividend_yield is None:
        return None
    if dividend_yield <= 0:
        return 5.0
    if dividend_yield <= 6:
        return _clamp(5 + dividend_yield * 0.6)
    return 6.0


def _difference_score(
    values: dict[str, Any],
    current_field: str,
    baseline_field: str,
) -> float | None:
    current = _number(values.get(current_field))
    baseline = _number(values.get(baseline_field))
    if current is None or baseline is None:
        return None
    return 8.0 if current >= baseline else 4.0


def _liabilities_to_assets(values: dict[str, Any]) -> float | None:
    liabilities = _number(values.get("total_liabilities_annual"))
    assets = _number(values.get("total_assets_annual"))
    if liabilities is None or assets is None or assets <= 0:
        return None
    return liabilities / assets


def _analyst_rating_bonus(value: Any) -> float:
    if value is None:
        return 0.0
    numeric = _number(value)
    if numeric is not None:
        return _clamp((3 - numeric) * 0.2, -0.3, 0.3)
    text = str(value).strip().lower()
    if any(token in text for token in ("strong buy", "buy", "outperform")):
        return 0.3
    if any(token in text for token in ("sell", "underperform")):
        return -0.3
    return 0.0


def _clamp(value: float, lower: float = 0.0, upper: float = 10.0) -> float:
    return round(max(lower, min(upper, value)), 4)
