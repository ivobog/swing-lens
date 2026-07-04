from app.services.fundamental_coverage_service import calculate_coverage_v2
from app.services.fundamental_ranker_v2 import load_fundamentals_v2_config


def test_coverage_tracks_missing_priorities_and_parse_failures() -> None:
    config = load_fundamentals_v2_config()

    coverage = calculate_coverage_v2(
        {
            "market_cap": "100B",
            "fcf_ttm": "not-a-number",
            "net_income_ttm": "10B",
            "sloan_ratio_ttm": "3",
            "roic_ttm": "20",
            "roa_ttm": "12",
            "total_assets_annual": "50B",
            "total_liabilities_annual": "20B",
            "revenue_growth_ttm_yoy": "15",
            "operating_margin_ttm": "25",
        },
        config,
    )

    assert "fcf_ttm" in coverage.missing_core_fields
    assert coverage.parse_diagnostics["failed_field_count"] == 1
    assert coverage.missing_data_penalty > 0
    assert 0 < coverage.coverage_ratio < 1
