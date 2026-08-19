from __future__ import annotations

from app.services.ceri.dtos import GuidanceRequest
from app.services.ceri.sec import processor_signature
from app.services.ceri.sec.client import SecClientConfig
from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService
from app.services.ceri.sec.provider import SecCeriProvider


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


def test_refactored_document_boundary_preserves_cold_provider_output() -> None:
    class Client:
        config = SecClientConfig()
        requests = 0
        failures = 0
        last_success_at = None

        def company_tickers(self):
            return {"0": {"ticker": "TEST", "cik_str": 123456}}

        def submissions(self, _cik):
            return {
                "filings": {
                    "recent": {
                        "form": ["8-K"],
                        "accessionNumber": ["0000123456-26-000001"],
                        "primaryDocument": ["test.htm"],
                        "filingDate": ["2026-08-01"],
                    }
                }
            }

        def archive_document(self, *_args):
            return "The company expects revenue guidance of $100 to $110 million."

    request = GuidanceRequest(company_id=None, ticker="TEST")
    legacy_shape = list(SecCeriProvider(client=Client()).fetch_guidance(request))
    refactored_provider = SecCeriProvider(client=Client())
    documents = refactored_provider.discover_guidance_documents(request)
    incremental_shape = list(refactored_provider.extract_guidance_document(documents[0]))

    assert legacy_shape == incremental_shape


def test_extractor_version_change_changes_processor_signature(monkeypatch) -> None:
    before = processor_signature.sec_guidance_processor_signature()

    monkeypatch.setattr(
        processor_signature,
        "GUIDANCE_EXTRACTOR_VERSION",
        "guidance-regex-visible-text-v3",
    )

    assert processor_signature.sec_guidance_processor_signature() != before
