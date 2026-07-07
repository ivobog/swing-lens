from datetime import date
from decimal import Decimal

from app.services.column_mapper import map_csv_rows
from app.services.fundamental_ranker_v2 import FundamentalScoreV2Result
from app.services.upload_service import _fundamental_score_from_v2, _raw_company_row_from_mapped


def test_fundamental_score_from_v2_populates_legacy_and_explicit_columns() -> None:
    model = _fundamental_score_from_v2(run_id=7, score=_v2_score())

    assert model.run_id == 7
    assert model.ticker == "MSFT"
    assert model.growth_score == Decimal("8.1")
    assert model.profitability_score == Decimal("8.2")
    assert model.fcf_score == Decimal("7.4")
    assert model.balance_sheet_score == Decimal("7.1")
    assert model.valuation_score == Decimal("5.9")
    assert model.momentum_score is None
    assert model.dilution_score == Decimal("5.8")
    assert model.risk_score == Decimal("7.7")
    assert model.earnings_quality_score == Decimal("7.8")
    assert model.capital_efficiency_score == Decimal("8.0")
    assert model.forward_quality_score == Decimal("6.5")
    assert model.data_coverage_score == Decimal("8.7")
    assert model.scoring_model_version == "fundamentals_v2.0"
    assert model.v2_warning_flags_json == {"flags": ["high_accrual_risk"]}
    assert model.trap_flags_json == {"flags": ["high_accrual_risk"]}
    assert model.debug_json["model_version"] == "fundamentals_v2.0"


def test_raw_company_row_from_mapped_parses_upcoming_earnings_date() -> None:
    mapped = map_csv_rows(
        [
            {
                "Symbol": "AAPL",
                "Description": "Apple Inc.",
                "Sector": "Technology",
                "Upcoming earnings date": "2026-07-14",
            }
        ]
    )[0]

    model = _raw_company_row_from_mapped(run_id=7, row=mapped)

    assert model.run_id == 7
    assert model.ticker == "AAPL"
    assert model.company_name == "Apple Inc."
    assert model.upcoming_earnings_date == date(2026, 7, 14)
    assert model.raw_json["Upcoming earnings date"] == "2026-07-14"
    assert model.raw_json["upcoming_earnings_date"] == "2026-07-14"


def test_raw_company_row_from_mapped_keeps_unparseable_earnings_value() -> None:
    mapped = map_csv_rows(
        [
            {
                "Symbol": "AAPL",
                "Upcoming earnings date": "not a date",
            }
        ]
    )[0]

    model = _raw_company_row_from_mapped(run_id=7, row=mapped)

    assert model.upcoming_earnings_date is None
    assert model.raw_json["upcoming_earnings_date"] == "not a date"


def test_raw_company_row_from_mapped_allows_missing_earnings_column() -> None:
    mapped = map_csv_rows([{"Symbol": "AAPL", "Description": "Apple Inc."}])[0]

    model = _raw_company_row_from_mapped(run_id=7, row=mapped)

    assert model.upcoming_earnings_date is None
    assert "upcoming_earnings_date" not in model.raw_json


def _v2_score() -> FundamentalScoreV2Result:
    return FundamentalScoreV2Result(
        ticker="MSFT",
        growth_quality_score=8.1,
        profitability_quality_score=8.2,
        fcf_quality_score=7.4,
        earnings_quality_score=7.8,
        capital_efficiency_score=8.0,
        balance_sheet_quality_score=7.1,
        valuation_quality_score=5.9,
        forward_quality_score=6.5,
        shareholder_quality_score=5.8,
        liquidity_risk_score=7.7,
        data_coverage_score=8.7,
        missing_data_penalty=0.2,
        fundamental_score=7.4,
        fundamental_label="High-quality quant",
        warning_flags=["high_accrual_risk"],
        explanation="High-quality quant.",
        debug={"model_version": "fundamentals_v2.0"},
    )
