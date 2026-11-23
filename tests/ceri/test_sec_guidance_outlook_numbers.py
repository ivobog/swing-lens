from decimal import Decimal

import pytest

from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService
from app.services.ceri.sec.provider import SecCeriProvider, SecGuidanceDocument


def test_amzn_sales_range_takes_precedence_over_fx_assumption():
    # AMZN 10-Q 0001018724-26-000040, paragraph 3723. The following FX figure
    # reproduced the incorrect 60-unit production revenue point.
    document = SecGuidanceDocument(
        "AMZN",
        "0001018724",
        "0001018724-26-000040",
        "amzn-20260930.htm",
        "10-Q",
        "2026-10-30",
    )
    original = (
        "Net sales are expected to be between $240.0 billion and $247.0 billion, "
        "compared with fourth quarter 2025. "
        "This guidance anticipates an unfavorable impact of approximately 60 basis points."
    )
    (record,) = SecCeriProvider().extract_guidance_document(document, text=original)
    payload = record.payload
    assert (payload["metric"], payload["period_type"]) == ("REVENUE", "CURRENT_QUARTER")
    assert (payload["low_value"], payload["high_value"]) == (Decimal("240"), Decimal("247"))
    assert (payload["unit"], payload["currency"], payload["point_value"]) == (
        "BILLION",
        "USD",
        None,
    )
    assert payload["filing_accession"] == document.accession_number
    assert payload["action"] == "UNKNOWN" and payload["comparison_confidence"] == "INSUFFICIENT"


def test_emr_revenue_outlook_retains_explicit_percent_unit():
    # EMR 10-K 0000032604-26-000054, paragraph 1860.
    text = (
        "For fiscal year 2027, consolidated net sales from continuing operations "
        "are expected to be up approximately 5 percent."
    )
    (guidance,) = GuidanceExtractionService().extract(text)
    assert (guidance.metric, guidance.period_label) == ("REVENUE", "CURRENT_FISCAL_YEAR")
    assert (guidance.point_value, guidance.unit, guidance.currency) == (Decimal("5"), "%", None)
    assert guidance.action == "UNKNOWN"


def test_ccep_split_reaffirmed_sales_growth_preserves_manual_review():
    # CCEP 6-K 0001650107-26-000139, paragraphs 163-165.
    document = SecGuidanceDocument(
        "CCEP",
        "0001650107",
        "0001650107-26-000139",
        "ccepq32026tradingupdate-1s.htm",
        "6-K",
        "2026-11-05",
    )
    original = (
        "Outlook for FY26 unchanged. FX represents a full-year headwind of ~10 basis points "
        "to revenue & tailwind of ~30 basis points to operating profit."
        "\n\nRevenue\n\n: growth of 3% to 4%"
    )
    (record,) = SecCeriProvider().extract_guidance_document(document, text=original)
    payload = record.payload
    assert (payload["metric"], payload["period_type"]) == ("REVENUE", "CURRENT_FISCAL_YEAR")
    assert (payload["low_value"], payload["high_value"], payload["unit"]) == (
        Decimal("3"),
        Decimal("4"),
        "%",
    )
    assert payload["point_value"] is None and payload["currency"] is None
    assert payload["evidence_locator"].endswith("#paragraph-1-3")
    assert payload["action"] == "UNKNOWN"
    assert payload["management_claim"] == "MAINTAINED" and payload["manual_review_required"] is True

    missing_reaffirmation = original.replace("Outlook for FY26 unchanged", "Quotes for FY26")
    assert all(
        row.low_value is None for row in GuidanceExtractionService().extract(missing_reaffirmation)
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "Net sales are expected to be between $240 billion and EUR 247 billion",
        "Net sales are expected to be between $247 billion and $240 billion",
    ],
)
def test_ambiguous_ranges_have_no_numeric_outlook(sentence):
    text = (
        f"{sentence} for fourth quarter 2026. This outlook mentions approximately 60 basis points."
    )
    assert all(
        row.low_value is row.high_value is row.point_value is None
        for row in GuidanceExtractionService().extract(text)
    )


def test_historical_sales_range_is_not_an_outlook():
    original = "Net sales were between $240 billion and $247 billion in fourth quarter 2025."
    assert GuidanceExtractionService().extract(original) == []


def test_abbv_eps_ranges_use_their_locally_stated_fiscal_period():
    # ABBV 8-K 0001551152-26-000030, paragraph 73.
    original = (
        "Adjusted diluted earnings per share guidance for 2026 previously announced on July 31, "
        "2026 excluded the impact of acquired IPR&D expense beyond the second quarter of 2026. "
        "AbbVie's full-year 2026 adjusted diluted earnings per share guidance range, including "
        "the impact of third-quarter 2026 expense, is $13.76 - $13.96. "
        "AbbVie's third-quarter 2026 adjusted diluted earnings per share guidance range, "
        "including the impact of third-quarter 2026 expense, is $3.73 - $3.77."
    )
    rows = GuidanceExtractionService().extract(original)
    assert {(row.period_label, row.low_value, row.high_value) for row in rows} == {
        ("CURRENT_FISCAL_YEAR", Decimal("13.76"), Decimal("13.96")),
        ("CURRENT_QUARTER", Decimal("3.73"), Decimal("3.77")),
    }
    assert all(
        (row.metric, row.point_value, row.unit, row.currency, row.action)
        == ("EPS_DILUTED", None, "PER_SHARE", "USD", "UNKNOWN")
        for row in rows
    )
