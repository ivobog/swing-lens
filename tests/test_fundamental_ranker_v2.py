from app.services.column_mapper import MappedCsvRow
from app.services.fundamental_ranker_v2 import score_row_v2, score_rows_v2
from app.services.fundamental_warning_service import (
    ASSET_GROWTH_WITHOUT_RETURNS,
    HIGH_ACCRUAL_RISK,
    POOR_CASH_CONVERSION,
    SPARSE_FUNDAMENTAL_DATA,
)


def test_score_row_v2_returns_debug_contract_and_is_deterministic() -> None:
    row = _row("QUAL", _quality_values())

    first = score_row_v2(row)
    second = score_rows_v2([row])[0]

    assert first == second
    assert first.debug["model_version"] == "fundamentals_v2.0"
    assert set(first.debug["component_scores"]) == {
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
    assert first.fundamental_score > 6
    assert first.data_coverage_score > 7
    assert "data coverage" in first.explanation


def test_score_row_v2_flags_earnings_quality_trap() -> None:
    values = _quality_values()
    values.update(
        {
            "sloan_ratio_ttm": "25",
            "net_income_ttm": "100000000",
            "fcf_ttm": "-10000000",
            "net_income_growth_ttm_yoy": "30",
        }
    )

    score = score_row_v2(_row("TRAP", values))

    assert HIGH_ACCRUAL_RISK in score.warning_flags
    assert POOR_CASH_CONVERSION in score.warning_flags
    assert score.fundamental_label == "Value trap risk"


def test_score_row_v2_flags_asset_growth_without_returns() -> None:
    values = _quality_values()
    values.update(
        {
            "total_assets_growth_annual_yoy": "38",
            "roa_ttm": "4",
            "roa_annual": "9",
            "asset_turnover_ttm": "0.4",
            "asset_turnover_annual": "0.9",
        }
    )

    score = score_row_v2(_row("ASSET", values))

    assert ASSET_GROWTH_WITHOUT_RETURNS in score.warning_flags


def test_score_row_v2_degrades_gracefully_for_sparse_older_csv() -> None:
    row = _row(
        "OLD",
        {
            "market_cap": "1000000000",
            "revenue_growth_ttm_yoy": "8",
            "operating_margin_ttm": "12",
            "fcf_ttm": "10000000",
            "pe_ratio": "18",
        },
    )

    score = score_row_v2(row)

    assert 0 <= score.fundamental_score <= 10
    assert SPARSE_FUNDAMENTAL_DATA in score.warning_flags
    assert score.debug["coverage"]["missing_core_fields"]


def _row(ticker: str, values: dict[str, str]) -> MappedCsvRow:
    return MappedCsvRow(
        row_number=1,
        ticker=ticker,
        company_name=None,
        sector=None,
        canonical=values,
        raw={},
    )


def _quality_values() -> dict[str, str]:
    return {
        "market_cap": "100000000000",
        "price_change_1d_pct": "1",
        "volume_change_1d_pct": "10",
        "relative_volume_1d": "1.2",
        "revenue_growth_quarterly_yoy": "18",
        "revenue_growth_ttm_yoy": "20",
        "revenue_growth_5y_cagr": "15",
        "eps_growth_quarterly_yoy": "18",
        "eps_growth_ttm_yoy": "22",
        "ebitda_growth_ttm_yoy": "18",
        "fcf_growth_ttm_yoy": "20",
        "gross_profit_growth_ttm_yoy": "17",
        "net_income_growth_ttm_yoy": "19",
        "total_revenue_ttm": "5000000000",
        "gross_margin_ttm": "62",
        "gross_margin_annual": "60",
        "ebitda_margin_ttm": "34",
        "operating_margin_ttm": "28",
        "operating_margin_annual": "26",
        "net_margin_ttm": "23",
        "net_margin_annual": "21",
        "roe_ttm": "28",
        "roa_ttm": "15",
        "roa_annual": "13",
        "roic_ttm": "24",
        "roic_annual": "21",
        "roce_ttm": "22",
        "return_on_total_capital_ttm": "21",
        "fcf_ttm": "900000000",
        "fcf_margin_ttm": "22",
        "pfcf": "20",
        "ev_fcf": "21",
        "operating_cash_flow_per_share_ttm": "5",
        "capex_ttm": "-100000000",
        "capex_per_share_ttm": "-0.8",
        "capex_growth_ttm_yoy": "8",
        "capex_growth_quarterly_yoy": "10",
        "sloan_ratio_ttm": "2",
        "net_income_ttm": "800000000",
        "asset_turnover_ttm": "1.3",
        "asset_turnover_annual": "1.1",
        "total_assets_annual": "10000000000",
        "total_liabilities_annual": "3500000000",
        "total_assets_growth_annual_yoy": "12",
        "working_capital_per_share_annual": "5",
        "working_capital_per_share_quarterly": "5.5",
        "quick_ratio_quarterly": "1.3",
        "quick_ratio_annual": "1.2",
        "cash_short_term_investments_annual": "1000000000",
        "current_ratio": "2",
        "net_debt_to_ebitda": "0.4",
        "debt_to_ebitda_annual": "0.6",
        "debt_to_equity": "40",
        "debt_to_assets": "25",
        "total_debt_to_capital_annual": "30",
        "ebitda_interest_coverage_ttm": "12",
        "pe_ratio": "24",
        "forward_pe": "20",
        "earnings_yield_ttm": "5",
        "ps_ratio": "6",
        "ev_revenue": "5",
        "ev_ebitda": "15",
        "peg_ratio": "1.2",
        "eps_estimate_annual": "7",
        "eps_estimate_quarterly": "1.5",
        "revenue_estimate_annual": "5500000000",
        "net_income_estimate_ntm": "900000000",
        "ebit_estimate_ntm": "1200000000",
        "cash_short_term_investments_estimate_annual": "1100000000",
        "capex_estimate_ntm": "-120000000",
        "analyst_rating": "Buy",
        "buyback_yield": "1.5",
        "dividend_yield_ttm": "1.2",
        "dividend_payout_ratio_ttm": "35",
        "total_common_shares_outstanding": "1000000000",
        "dollar_volume_10d": "40000000",
        "dollar_volume_30d": "45000000",
        "dollar_volume_60d": "50000000",
        "free_float": "900000000",
        "beta_1y": "1.0",
        "beta_3y": "1.1",
        "tradingview_atr_pct_14d": "3",
    }
