from app.services.column_mapper import MappedCsvRow
from app.services.fundamental_ranker import score_row
from app.services.numeric_parser import parse_financial_number


def test_parse_financial_number_handles_screener_formats() -> None:
    examples = {
        "24.5%": 24.5,
        "$1.2B": 1_200_000_000.0,
        "CHF 500M": 500_000_000.0,
        "(35.4)": -35.4,
        "1,234.56": 1234.56,
    }

    for raw, expected in examples.items():
        result = parse_financial_number(raw)

        assert result.parsed
        assert result.value == expected
        assert result.reason is None


def test_parse_financial_number_treats_placeholders_as_missing() -> None:
    for raw in ["", "-", "N/A", "NA", "None", "nan", "null"]:
        result = parse_financial_number(raw)

        assert not result.parsed
        assert result.value is None
        assert result.reason == "missing"


def test_fundamental_debug_includes_parse_diagnostics() -> None:
    row = MappedCsvRow(
        row_number=1,
        ticker="BAD",
        company_name="Bad Format Co",
        sector="Technology",
        canonical={
            "market_cap": "not-a-number",
            "fcf_ttm": "$1.2B",
            "gross_margin_ttm": "24.5%",
        },
        raw={},
    )

    score = score_row(row)
    diagnostics = score.debug["parse_diagnostics"]

    assert diagnostics["failed_field_count"] == 1
    assert diagnostics["failed_fields"][0]["field"] == "market_cap"
    assert diagnostics["failed_fields"][0]["reason"] == "invalid numeric format"
