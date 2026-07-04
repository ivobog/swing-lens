from typing import Any

from app.services.fundamental_components_v2 import liabilities_to_assets, number
from app.services.fundamental_coverage_service import FundamentalCoverageResult

EARNINGS_QUALITY_RISK = "earnings_quality_risk"
POOR_CASH_CONVERSION = "poor_cash_conversion"
HIGH_ACCRUAL_RISK = "high_accrual_risk"
CAPITAL_EFFICIENCY_DETERIORATION = "capital_efficiency_deterioration"
ASSET_GROWTH_WITHOUT_RETURNS = "asset_growth_without_returns"
BALANCE_SHEET_STRESS = "balance_sheet_stress"
LIQUIDITY_BUFFER_WEAK = "liquidity_buffer_weak"
FORWARD_QUALITY_WEAK = "forward_quality_weak"
DIVIDEND_PAYOUT_RISK = "dividend_payout_risk"
SPARSE_FUNDAMENTAL_DATA = "sparse_fundamental_data"


def build_warning_flags_v2(
    values: dict[str, Any],
    component_scores: dict[str, float],
    coverage: FundamentalCoverageResult,
    thresholds: dict[str, Any],
    sparse_data_coverage_threshold: float,
) -> list[str]:
    flags: list[str] = []

    _add_earnings_quality_flags(flags, values, thresholds)
    _add_capital_efficiency_flags(flags, values, thresholds)
    _add_balance_sheet_flags(flags, values, thresholds)
    _add_forward_flags(flags, values, component_scores)
    _add_shareholder_flags(flags, values, thresholds)

    if coverage.coverage_ratio < sparse_data_coverage_threshold:
        flags.append(SPARSE_FUNDAMENTAL_DATA)

    return _dedupe(flags)


def _add_earnings_quality_flags(
    flags: list[str],
    values: dict[str, Any],
    thresholds: dict[str, Any],
) -> None:
    sloan = number(values.get("sloan_ratio_ttm"))
    net_income = number(values.get("net_income_ttm"))
    fcf = number(values.get("fcf_ttm"))
    net_income_growth = number(values.get("net_income_growth_ttm_yoy"))

    if sloan is not None and sloan >= float(thresholds["sloan_ratio_danger_min"]):
        flags.extend([HIGH_ACCRUAL_RISK, EARNINGS_QUALITY_RISK])
    elif sloan is not None and sloan >= float(thresholds["sloan_ratio_warning_min"]):
        flags.append(EARNINGS_QUALITY_RISK)

    if net_income is not None and net_income > 0 and fcf is not None:
        if fcf <= 0:
            flags.extend([POOR_CASH_CONVERSION, EARNINGS_QUALITY_RISK])
        elif fcf / net_income < float(thresholds["fcf_to_net_income_good"]):
            flags.append(POOR_CASH_CONVERSION)

    if net_income_growth is not None and net_income_growth > 10 and fcf is not None and fcf <= 0:
        flags.extend([POOR_CASH_CONVERSION, EARNINGS_QUALITY_RISK])


def _add_capital_efficiency_flags(
    flags: list[str],
    values: dict[str, Any],
    thresholds: dict[str, Any],
) -> None:
    asset_growth = number(values.get("total_assets_growth_annual_yoy"))
    roa_ttm = number(values.get("roa_ttm"))
    roa_annual = number(values.get("roa_annual"))
    turnover_ttm = number(values.get("asset_turnover_ttm"))
    turnover_annual = number(values.get("asset_turnover_annual"))

    if (
        asset_growth is not None
        and asset_growth > float(thresholds["total_assets_growth_high"])
        and (
            _declined(roa_ttm, roa_annual)
            or _declined(turnover_ttm, turnover_annual)
            or (roa_ttm is not None and roa_ttm < float(thresholds["roa_good"]))
        )
    ):
        flags.extend([ASSET_GROWTH_WITHOUT_RETURNS, CAPITAL_EFFICIENCY_DETERIORATION])


def _add_balance_sheet_flags(
    flags: list[str],
    values: dict[str, Any],
    thresholds: dict[str, Any],
) -> None:
    liability_ratio = liabilities_to_assets(values)
    quick_ratio = number(values.get("quick_ratio_quarterly"))
    current_ratio = number(values.get("current_ratio"))
    net_debt_to_ebitda = number(values.get("net_debt_to_ebitda"))
    debt_to_ebitda = number(values.get("debt_to_ebitda_annual"))

    if liability_ratio is not None and liability_ratio > float(
        thresholds["liabilities_to_assets_warning_min"]
    ):
        flags.append(BALANCE_SHEET_STRESS)
    if net_debt_to_ebitda is not None and net_debt_to_ebitda > float(
        thresholds["net_debt_to_ebitda_warning"]
    ):
        flags.append(BALANCE_SHEET_STRESS)
    if debt_to_ebitda is not None and debt_to_ebitda > float(thresholds["debt_to_ebitda_warning"]):
        flags.append(BALANCE_SHEET_STRESS)
    if (
        quick_ratio is not None
        and quick_ratio < float(thresholds["quick_ratio_weak"])
        and (current_ratio is None or current_ratio < float(thresholds["current_ratio_weak"]))
    ):
        flags.append(LIQUIDITY_BUFFER_WEAK)


def _add_forward_flags(
    flags: list[str],
    values: dict[str, Any],
    component_scores: dict[str, float],
) -> None:
    eps_estimate = number(values.get("eps_estimate_annual"))
    net_income_estimate = number(values.get("net_income_estimate_ntm"))
    ebit_estimate = number(values.get("ebit_estimate_ntm"))
    if component_scores["forward_quality_score"] < 4:
        flags.append(FORWARD_QUALITY_WEAK)
    if any(
        value is not None and value <= 0
        for value in (eps_estimate, net_income_estimate, ebit_estimate)
    ):
        flags.append(FORWARD_QUALITY_WEAK)


def _add_shareholder_flags(
    flags: list[str],
    values: dict[str, Any],
    thresholds: dict[str, Any],
) -> None:
    payout = number(values.get("dividend_payout_ratio_ttm"))
    if payout is not None and payout > float(thresholds["dividend_payout_warning_min"]):
        flags.append(DIVIDEND_PAYOUT_RISK)


def _declined(current: float | None, baseline: float | None) -> bool:
    return current is not None and baseline is not None and current < baseline


def _dedupe(flags: list[str]) -> list[str]:
    seen: set[str] = set()
    unique = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            unique.append(flag)
    return unique
