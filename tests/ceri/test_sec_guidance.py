from __future__ import annotations

from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService


def test_guidance_extractor_preserves_locator_and_marks_ambiguous_claims() -> None:
    text = (
        "The company raised full year revenue guidance to $100 to $110 million.\n\n"
        "Management discussed its outlook without a comparable numeric range."
    )

    rows = GuidanceExtractionService().extract(text, locator="acc-1/exhibit-99.htm")

    assert rows[0].action == "UNKNOWN"
    assert rows[0].management_claim == "RAISED"
    assert "guidance_comparison_requires_prior" in rows[0].warnings
    assert rows[0].metric == "REVENUE"
    assert rows[0].low_value == 100
    assert rows[0].high_value == 110
    assert rows[0].evidence_locator.endswith("#paragraph-1")
    assert rows[1].action == "UNKNOWN"
    assert "guidance_comparison_insufficient" in rows[1].warnings
