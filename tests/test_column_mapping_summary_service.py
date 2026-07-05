from app.models.tables import RawCompanyRow, UploadRun
from app.services.column_mapping_summary_service import summarize_run_column_mapping


def test_mapping_summary_classifies_raw_headers_against_scoring_config() -> None:
    run = UploadRun(id=1, filename="sample.csv", status="COMPLETED")
    run.raw_company_rows = [
        RawCompanyRow(
            run_id=1,
            row_number=1,
            ticker="MSFT",
            company_name="Microsoft",
            sector="Technology",
            raw_json={
                "Symbol": "MSFT",
                "Description": "Microsoft",
                "Market capitalization": "3000000000000",
                "Free cash flow, Trailing 12 months": "65000000000",
                "Net income, Trailing 12 months": "73000000000",
                "Mystery Column": "ignored",
            },
        ),
        RawCompanyRow(
            run_id=1,
            row_number=2,
            ticker="AAPL",
            company_name="Apple",
            sector="Technology",
            raw_json={
                "Symbol": "AAPL",
                "Description": "Apple",
                "Market capitalization": "2800000000000",
            },
        ),
    ]

    summary = summarize_run_column_mapping(run)
    by_header = {item.raw_header: item for item in summary.items}

    assert summary.raw_column_count == 6
    assert summary.recognized_count == 5
    assert summary.unrecognized_count == 1
    assert summary.scoring_count == 2
    assert summary.stored_only_count == 3
    assert summary.unrecognized_columns == ["Mystery Column"]
    assert by_header["Market capitalization"].canonical_field == "market_cap"
    assert by_header["Market capitalization"].priority == "critical"
    assert by_header["Free cash flow, Trailing 12 months"].component == "fcf_quality_score"
    assert by_header["Net income, Trailing 12 months"].component == "earnings_quality_score"
    assert by_header["Mystery Column"].canonical_field is None
    assert "sloan_ratio_ttm" in summary.missing_critical_fields
