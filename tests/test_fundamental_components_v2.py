from app.services.fundamental_components_v2 import (
    capital_efficiency_score,
    earnings_quality_score,
)
from app.services.fundamental_ranker_v2 import load_fundamentals_v2_config


def test_earnings_quality_rewards_low_sloan_and_positive_fcf_conversion() -> None:
    thresholds = load_fundamentals_v2_config()["thresholds"]

    strong = earnings_quality_score(
        {
            "sloan_ratio_ttm": "2",
            "net_income_ttm": "100000000",
            "fcf_ttm": "95000000",
            "operating_cash_flow_per_share_ttm": "4.5",
        },
        thresholds,
    )
    weak = earnings_quality_score(
        {
            "sloan_ratio_ttm": "24",
            "net_income_ttm": "100000000",
            "fcf_ttm": "-5000000",
            "operating_cash_flow_per_share_ttm": "-0.2",
        },
        thresholds,
    )

    assert strong >= 8
    assert weak <= 2


def test_capital_efficiency_penalizes_asset_growth_without_returns() -> None:
    thresholds = load_fundamentals_v2_config()["thresholds"]

    disciplined = capital_efficiency_score(
        {
            "roic_ttm": "22",
            "roic_annual": "18",
            "return_on_total_capital_ttm": "18",
            "roa_ttm": "16",
            "roa_annual": "14",
            "asset_turnover_ttm": "1.4",
            "asset_turnover_annual": "1.2",
            "total_assets_growth_annual_yoy": "10",
        },
        thresholds,
    )
    deteriorating = capital_efficiency_score(
        {
            "roic_ttm": "8",
            "roic_annual": "12",
            "return_on_total_capital_ttm": "8",
            "roa_ttm": "4",
            "roa_annual": "9",
            "asset_turnover_ttm": "0.4",
            "asset_turnover_annual": "0.8",
            "total_assets_growth_annual_yoy": "35",
        },
        thresholds,
    )

    assert disciplined > deteriorating
    assert deteriorating < 5
