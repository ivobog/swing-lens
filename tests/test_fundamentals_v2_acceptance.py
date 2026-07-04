from datetime import UTC, datetime
from decimal import Decimal

from app.models.tables import CombinedResult, UploadRun
from app.services.column_mapper import map_csv_rows
from app.services.export_service import export_run_csv
from app.services.fundamental_ranker_v2 import score_row_v2
from app.services.upload_service import _fundamental_score_from_v2


def test_fundamentals_v2_acceptance_flow_from_csv_mapping_to_exports() -> None:
    mapped = map_csv_rows([_tradingview_v2_row()])[0]

    assert mapped.ticker == "QUAL"
    assert mapped.canonical["sloan_ratio_ttm"] == "2"
    assert mapped.canonical["total_assets_annual"] == "10000000000"
    assert mapped.canonical["quick_ratio_quarterly"] == "1.3"
    assert mapped.canonical["earnings_yield_ttm"] == "5"

    score = score_row_v2(mapped)
    stored_score = _fundamental_score_from_v2(run_id=7, score=score)

    assert score.debug["model_version"] == "fundamentals_v2.0"
    assert score.data_coverage_score >= 9
    assert stored_score.scoring_model_version == "fundamentals_v2.0"
    assert stored_score.earnings_quality_score is not None
    assert stored_score.v2_warning_flags_json == {"flags": score.warning_flags}

    run = _run_with_results(stored_score)
    fundamentals_csv = export_run_csv(run, "fundamentals")
    combined_csv = export_run_csv(run, "combined")

    assert "model_version,growth_quality_score" in fundamentals_csv
    assert "fundamentals_v2.0" in fundamentals_csv
    assert "earnings_quality_score" in fundamentals_csv
    assert "fundamental_model_version" in combined_csv
    assert "fundamental_data_coverage_score" in combined_csv
    assert "forward_quality_score" in combined_csv


def _run_with_results(stored_score) -> UploadRun:
    run = UploadRun(
        id=7,
        filename="acceptance.csv",
        uploaded_at=datetime(2026, 7, 4, tzinfo=UTC),
        processed_at=datetime(2026, 7, 4, tzinfo=UTC),
        row_count=1,
        status="COMPLETED",
    )
    run.fundamental_scores = [stored_score]
    run.combined_results = [
        CombinedResult(
            run_id=7,
            ticker="QUAL",
            company_name="Quality Co",
            sector="Technology",
            final_rank=1,
            final_score=Decimal("7.50"),
            fundamental_score=stored_score.fundamental_score,
            fundamental_label=stored_score.fundamental_label,
            combined_decision="Candidate",
            position_size_hint="Half starter",
            notes="aligned",
        )
    ]
    return run


def _tradingview_v2_row() -> dict[str, str]:
    return {
        "Symbol": "QUAL",
        "Description": "Quality Co",
        "Sector": "Technology",
        "Price change %, 1 day": "1",
        "Volume change %, 1 day": "10",
        "Relative volume, 1 day": "1.2",
        "Market capitalization": "100000000000",
        "Revenue growth %, Quarterly YoY": "18",
        "Revenue growth %, TTM YoY": "20",
        "Revenue growth %, 5 year CAGR": "15",
        "Earnings per share diluted growth %, Quarterly YoY": "18",
        "Earnings per share diluted growth %, TTM YoY": "22",
        "EBITDA growth %, TTM YoY": "18",
        "Free cash flow growth %, TTM YoY": "20",
        "Gross profit growth %, TTM YoY": "17",
        "Net income growth %, TTM YoY": "19",
        "Total revenue, Trailing 12 months": "5000000000",
        "Gross margin %, Trailing 12 months": "62",
        "Gross margin %, Annual": "60",
        "EBITDA margin %, Trailing 12 months": "34",
        "Operating margin %, Trailing 12 months": "28",
        "Operating margin %, Annual": "26",
        "Net margin %, Trailing 12 months": "23",
        "Net margin %, Annual": "21",
        "Return on equity %, Trailing 12 months": "28",
        "Return on assets %, Trailing 12 months": "15",
        "Return on assets %, Annual": "13",
        "Return on invested capital %, Trailing 12 months": "24",
        "Return on invested capital %, Annual": "21",
        "Return on capital employed %, Trailing 12 months": "22",
        "Return on total capital %, Trailing 12 months": "21",
        "Free cash flow, Trailing 12 months": "900000000",
        "Free cash flow margin %, Trailing 12 months": "22",
        "Price to free cash flow ratio": "20",
        "Enterprise value to free cash flow, Trailing 12 months": "21",
        "Operating cash flow per share, Trailing 12 months": "5",
        "Capital expenditures, Trailing 12 months": "-100000000",
        "Capital expenditures per share, Trailing 12 months": "-0.8",
        "Capital expenditures growth %, TTM YoY": "8",
        "Capital expenditures growth %, Quarterly YoY": "10",
        "Sloan ratio %, Trailing 12 months": "2",
        "Net income, Trailing 12 months": "800000000",
        "Asset turnover, Trailing 12 months": "1.3",
        "Asset turnover, Annual": "1.1",
        "Total assets, Annual": "10000000000",
        "Total liabilities, Annual": "3500000000",
        "Total assets growth %, Annual YoY": "12",
        "Working capital per share, Annual": "5",
        "Working capital per share, Quarterly": "5.5",
        "Quick ratio, Quarterly": "1.3",
        "Quick ratio, Annual": "1.2",
        "Cash and short-term investments, Annual": "1000000000",
        "Current ratio, Quarterly": "2",
        "Net debt to EBITDA ratio, Trailing 12 months": "0.4",
        "Debt to EBITDA ratio, Annual": "0.6",
        "Debt to equity ratio, Quarterly": "40",
        "Debt to assets ratio, Annual": "25",
        "Total debt to capital, Annual": "30",
        "EBITDA interest coverage, Trailing 12 months": "12",
        "Price to earnings ratio": "24",
        "Forward non-GAAP price to earnings, Annual": "20",
        "Earnings yield %, Trailing 12 months": "5",
        "Price to sales ratio": "6",
        "Enterprise value to revenue ratio, Trailing 12 months": "5",
        "Enterprise value to EBITDA ratio, Trailing 12 months": "15",
        "Price to earning to growth, Trailing 12 months": "1.2",
        "Earnings per share estimate, Annual": "7",
        "Earnings per share estimate, Quarterly": "1.5",
        "Revenue estimate, Annual": "5500000000",
        "Net income estimate, Next 12 months": "900000000",
        "EBIT estimate, Next 12 months": "1200000000",
        "Cash and short term investments estimate, Annual": "1100000000",
        "Capital expenditures estimate, Next 12 months": "-120000000",
        "Analyst rating": "Buy",
        "Buyback yield %": "1.5",
        "Dividend yield %, Trailing 12 months": "1.2",
        "Dividend payout ratio %, Trailing 12 months": "35",
        "Total common shares outstanding": "1000000000",
        "Price x average volume, 10 days": "40000000",
        "Price x average volume, 30 days": "45000000",
        "Price x average volume, 60 days": "50000000",
        "Free float": "900000000",
        "Beta, 1 year": "1.0",
        "Beta, 3 years": "1.1",
        "Average true range %, 14, 1 day": "3",
    }
