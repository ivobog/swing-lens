from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.services.ceri.enums import CeriDataset, ExportPolicy
from app.services.ceri.sec.provider import SecCeriProvider, SecGuidanceDocument

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests/ceri/fixtures/sec_guidance_real_corpus_manifest_v1.json"


def _hash_text(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def _row_key(payload: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        payload[field]
        for field in (
            "metric",
            "period_type",
            "low_value",
            "high_value",
            "unit",
            "currency",
            "management_claim",
        )
    )


@pytest.mark.integration
def test_real_sec_documents_flow_through_the_production_provider_boundary() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    documents = {item["document_name"]: item for item in manifest["offline_production_documents"]}
    provider = SecCeriProvider()
    expected = {
        "q22026991.htm": {
            (
                "REVENUE",
                "CURRENT_QUARTER",
                Decimal("12.7"),
                Decimal("13.3"),
                "BILLION",
                "USD",
                None,
            )
        },
        "earningspresentationfy27.htm": {
            ("REVENUE", "CURRENT_QUARTER", Decimal("4"), Decimal("5"), "%", None, None),
            (
                "EPS_DILUTED",
                "CURRENT_QUARTER",
                Decimal("0.72"),
                Decimal("0.74"),
                "PER_SHARE",
                "USD",
                None,
            ),
            (
                "REVENUE",
                "CURRENT_FISCAL_YEAR",
                Decimal("3.5"),
                Decimal("4.5"),
                "%",
                None,
                "MAINTAINED",
            ),
            (
                "EPS_DILUTED",
                "CURRENT_FISCAL_YEAR",
                Decimal("2.75"),
                Decimal("2.85"),
                "PER_SHARE",
                "USD",
                "MAINTAINED",
            ),
        },
    }
    identities = {
        "q22026991.htm": ("AMD", "0000002488", "2026-08-04"),
        "earningspresentationfy27.htm": ("WMT", "0000104169", "2026-05-21"),
    }

    for document_name, expected_rows in expected.items():
        item = documents[document_name]
        path = ROOT / item["path"]
        ticker, cik, filing_date = identities[document_name]
        document = SecGuidanceDocument(
            ticker=ticker,
            cik=cik,
            accession_number=item["accession"],
            document_name=document_name,
            form="8-K",
            filing_date=filing_date,
        )

        assert _hash_text(path) == item["sha256"]
        records = provider.extract_guidance_document(
            document, text=path.read_text(encoding="utf-8")
        )
        accepted = {
            _row_key(record.payload)
            for record in records
            if record.payload["confidence"] == "HIGH"
        }

        assert expected_rows <= accepted
        assert all(record.provider == "sec" for record in records)
        assert all(record.dataset is CeriDataset.GUIDANCE for record in records)
        assert all(record.export_policy == ExportPolicy.RESTRICTED.value for record in records)
        assert all(record.payload["ticker"] == ticker for record in records)
        assert all(record.payload["cik"] == cik for record in records)
        assert all(record.payload["filing_accession"] == item["accession"] for record in records)
        assert all(
            record.payload["source_reference"].startswith(
                f"{item['accession']}/{document_name}#paragraph-"
            )
            for record in records
        )
