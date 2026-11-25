from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from unittest.mock import patch

from app.services.ceri.enums import CeriDataset
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.providers.manual_provider import ManualCeriProvider

EXACT_TEN = ("BHE", "BLLN", "KLIC", "LSCC", "PDFS", "ACMR", "RDVT", "AVT", "DVN", "JNJ")
SCALE_TICKERS = EXACT_TEN + tuple(f"F{index:04d}" for index in range(90))
SCALE_SEC_TEST_ENV = "SWINGLENS_CERTIFICATION_SCALE_SEC_TEST"
SCALE_SEC_POSSESSED_AT = datetime.fromisoformat("2026-09-23T19:00:00+00:00")


@dataclass(frozen=True)
class ScaleSecFiling:
    ticker: str
    cik: str
    accession: str
    filename: str
    point: int
    body: str

    @property
    def locator(self) -> str:
        return f"{self.accession}/{self.filename}#paragraph-2"

    @property
    def record_key(self) -> str:
        return f"{self.accession}:{self.locator}"

    @property
    def body_sha256(self) -> str:
        return sha256(self.body.encode("utf-8")).hexdigest()


def scale_sec_test_size(nodeid: str) -> int | None:
    from single_run_certification.reporting import CARDINALITY_TEST

    match = re.fullmatch(
        re.escape(CARDINALITY_TEST) + r"\[(?:chromium|firefox)-(?:False|True)-(88|100)\]",
        nodeid,
    )
    return int(match[1]) if match else None


def scale_sec_filings(size: int) -> tuple[ScaleSecFiling, ...]:
    filings = []
    for index, ticker in enumerate(SCALE_TICKERS[:size], start=1):
        cik = str(900_000_000 + index).zfill(10)
        point = 100 + index
        body = (
            f"<!DOCTYPE html>\n<!-- Synthetic certification 8-K for {ticker}. -->\n"
            f"<SEC-HEADER>\nCENTRAL INDEX KEY: {cik}\n</SEC-HEADER>\n"
            "<html><body><p>The company raises its full year revenue guidance "
            f"to USD {point}.</p></body></html>"
        )
        filings.append(ScaleSecFiling(
            ticker, cik, f"0000000000-26-{index:06d}", f"{ticker.lower()}-scale-8k.htm",
            point, body,
        ))
    return tuple(filings)


def scale_offline_sec_provider(environment, database_url: str):
    from sqlalchemy.engine import make_url

    from app.services.ceri.sec.client import SecClientConfig
    from app.services.ceri.sec.provider import SecCeriProvider

    size = scale_sec_test_size(environment.get(SCALE_SEC_TEST_ENV, ""))
    target = make_url(database_url)
    required = {
        "RUNTIME_MODE": "CERTIFICATION",
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
        "SWINGLENS_CERTIFICATION_PROVIDER_MODE": "deterministic",
        "CERTIFICATION_FROZEN_CERI_PROVIDERS": "true",
        "SEC_DOCUMENT_INCREMENTAL_MODE": "SHADOW",
    }
    if (
        size is None
        or environment.get("CERTIFICATION_GOLDEN_VERSION")
        or any(environment.get(key) != value for key, value in required.items())
        or database_url != environment.get("DATABASE_URL")
        or target.get_backend_name() != "postgresql"
        or target.host not in {"localhost", "127.0.0.1", "::1"}
        or not (target.database or "").startswith("swinglens_pytest_cert_")
        or target.query
    ):
        raise RuntimeError("SEC_SCALE_REQUIRES_DETERMINISTIC_DISPOSABLE_SHADOW")

    by_cik = {filing.cik: filing for filing in scale_sec_filings(size)}

    class OfflineScaleSec:
        config = SecClientConfig()

        def submissions(self, cik):
            filing = by_cik[cik]
            return {"cik": cik, "filings": {"recent": {
                "form": ["8-K"],
                "primaryDocument": [filing.filename],
                "accessionNumber": [filing.accession],
                "filingDate": [SCALE_SEC_POSSESSED_AT.date().isoformat()],
                "acceptanceDateTime": [SCALE_SEC_POSSESSED_AT.isoformat()],
            }}}

        def archive_document(self, cik, accession, filename):
            filing = by_cik[cik]
            if (filing.accession, filing.filename) != (accession, filename):
                raise RuntimeError("SEC_SCALE_FILING_IDENTITY_MISMATCH")
            return filing.body

    class ScaleSecProvider(SecCeriProvider):
        def extract_guidance_document(
            self, document, *, text=None, require_financial_attribution=False, retrieved_at=None,
        ):
            filing = by_cik[document.cik]
            if (
                (document.ticker, document.accession_number, document.document_name)
                != (filing.ticker, filing.accession, filing.filename)
                or text != filing.body
                or not require_financial_attribution
                or retrieved_at != SCALE_SEC_POSSESSED_AT
            ):
                raise RuntimeError("SEC_SCALE_FILING_OR_RECEIPT_MISMATCH")
            records = super().extract_guidance_document(
                document, text=text, require_financial_attribution=True, retrieved_at=retrieved_at,
            )
            if len(records) != 1 or records[0].provider_record_id != filing.record_key:
                raise RuntimeError("SEC_SCALE_PROJECTED_SOURCE_MISMATCH")
            # Carry the native writer's synthetic acquisition receipt at the
            # same provider boundary used by the reviewed golden replay.
            return (replace(records[0], source_url=None, retrieved_at=retrieved_at),)

    return ScaleSecProvider(client=OfflineScaleSec())


def frozen_ceri_registry(*, sec_provider=None) -> CeriProviderRegistry:
    """Return deterministic external-provider responses for the retained-clone proof."""

    from single_run_certification.candidate_validation import artifact
    from single_run_certification.golden_runtime import current_manifest

    if os.environ.get("CERTIFICATION_GOLDEN_VERSION") or current_manifest():
        replay = artifact("provider-replay.json")

        class GoldenProvider(ManualCeriProvider):
            def _records_for_request(self, dataset, ticker):
                for record in super()._records_for_request(dataset, ticker):
                    yield replace(record, retrieved_at=datetime.fromisoformat(
                        record.payload["retrieved_at"].replace("Z", "+00:00"),
                    ))

        providers = {}
        for name in ("eodhd", "sec"):
            if name == "sec" and sec_provider is not None:
                providers[name] = sec_provider
                continue
            if name == "sec" and "offline_filings" in replay[name]:
                # The parent can seed EODHD history before the SEC worker starts.
                continue
            provider = GoldenProvider(
                replay[name], provider_terms_version=replay["terms_version"],
            )
            provider.name = name
            providers[name] = provider
        return CeriProviderRegistry(providers=providers)

    tickers = SCALE_TICKERS
    observed = "2026-09-23T19:00:00Z"
    records = {dataset: [] for dataset in CeriDataset}
    for index, ticker in enumerate(tickers, start=1):
        base = 1.0 + index / 100.0
        # Cover each configured revision window with observations attributable
        # to a completed exchange session.
        for suffix, consensus, published in (
            ("90d", base, "2026-06-26T19:00:00Z"),
            ("30d", base * 1.04, "2026-08-25T19:00:00Z"),
            ("7d", base * 1.08, "2026-09-17T19:00:00Z"),
            ("current", base * 1.12, "2026-09-24T19:00:00Z"),
        ):
            records[CeriDataset.ESTIMATES].append(
                {
                    "provider_record_id": f"{ticker}-eps-{suffix}",
                    "provider_company_id": f"{ticker}.US",
                    "ticker": ticker,
                    "exchange": "US",
                    "metric": "EPS_DILUTED",
                    "period_type": "CURRENT_FISCAL_YEAR",
                    "fiscal_period_end": "2026-12-31",
                    "consensus": str(round(consensus, 4)),
                    "high": str(round(consensus * 1.1, 4)),
                    "low": str(round(consensus * 0.9, 4)),
                    "analyst_count": 10 + index,
                    "upward_count": 8,
                    "downward_count": 1,
                    "currency": "USD",
                    "published_at": published,
                    "observed_at": published,
                }
            )
        records[CeriDataset.GUIDANCE].append(
            {
                "provider_record_id": f"{ticker}-sec-guidance",
                "ticker": ticker,
                "action": "RAISED",
                "metric": "REVENUE",
                "period_type": "CURRENT_FISCAL_YEAR",
                "point": str(100 + index),
                "currency": "USD",
                "confidence": "high",
                "announced_at": observed,
                "published_at": observed,
                "filing_accession": f"0000000000-26-{index:06d}",
                "source_reference": f"frozen-sec:{ticker}",
            }
        )
        records[CeriDataset.CATALYSTS].append(
            {
                "provider_record_id": f"{ticker}-catalyst",
                "ticker": ticker,
                "category": "PRODUCT",
                "subtype": "launch",
                "subject_key": f"{ticker.lower()}-launch",
                "canonical_text": "Frozen provider product launch",
                "status": "ANNOUNCED",
                "direction": "POSITIVE",
                "materiality": 0.7,
                "announced_at": observed,
                "expected_date": "2026-10-15",
                "date_confidence": "EXACT_DATE",
                "source_confidence": "HIGH",
                "published_at": observed,
            }
        )

    class FrozenEodhdProvider(ManualCeriProvider):
        name = "eodhd"

    class FrozenSecProvider(ManualCeriProvider):
        name = "sec"

    return CeriProviderRegistry(
        providers={
            "eodhd": FrozenEodhdProvider(
                {
                    CeriDataset.ESTIMATES: records[CeriDataset.ESTIMATES],
                    CeriDataset.EARNINGS: records[CeriDataset.EARNINGS],
                    CeriDataset.CATALYSTS: records[CeriDataset.CATALYSTS],
                }
            ),
            "sec": (sec_provider if sec_provider is not None
                    else FrozenSecProvider({CeriDataset.GUIDANCE: records[CeriDataset.GUIDANCE]})),
        }
    )


def require_c06_offline_runtime(environment, database_url: str) -> None:
    from sqlalchemy.engine import make_url

    from scripts.qa.golden_provenance import C06_VERSIONS

    target = make_url(database_url)
    required = {
        "RUNTIME_MODE": "CERTIFICATION",
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
        "SWINGLENS_CERTIFICATION_CANDIDATE_VALIDATION": "1",
        "SWINGLENS_CERTIFICATION_PROVIDER_MODE": "deterministic",
        "CERTIFICATION_FROZEN_CERI_PROVIDERS": "true",
        "CERTIFICATION_GOLDEN_VERSION": C06_VERSIONS[0],
        "SEC_DOCUMENT_INCREMENTAL_MODE": "SHADOW",
    }
    if (
        any(environment.get(key) != value for key, value in required.items())
        or database_url != environment.get("DATABASE_URL")
        or target.get_backend_name() != "postgresql"
        or target.host not in {"localhost", "127.0.0.1", "::1"}
        or not (target.database or "").startswith("swinglens_pytest_cert_")
        or target.query
    ):
        raise RuntimeError("C06_GOLDEN_REQUIRES_OFFLINE_DISPOSABLE_SHADOW")


def c06_offline_sec_provider(environment, database_url, *, bootstrap=False):
    from app.services.ceri.sec.client import SecClientConfig
    from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
    from app.services.ceri.sec.provider import SecCeriProvider
    from app.services.ceri.source_record_service import source_record_content_hash
    from scripts.qa.golden_provenance import C06_PROJECTED_HASHES
    from single_run_certification.candidate_validation import c06_offline_filings

    require_c06_offline_runtime(environment, database_url)
    specimen = c06_offline_filings()
    # Bind the replay to the reviewed extractor while checking every sealed projection below.
    if specimen is None or sec_guidance_processor_signature() != "sec-guidance:755df092d79e7a53":
        raise RuntimeError("C06_GOLDEN_PROCESSOR_OR_FIXTURE_MISMATCH")
    by_cik = {row["cik"]: row for row in specimen["filings"]}

    class OfflineSec:
        config = SecClientConfig()

        def submissions(self, cik):
            row = None if bootstrap else by_cik.get(cik)
            return {"cik": cik, "filings": {"recent": {
                key: [row[field]] if row is not None else []
                for key, field in (
                    ("form", "form"), ("primaryDocument", "filename"),
                    ("accessionNumber", "accession"), ("filingDate", "filing_date"),
                    ("acceptanceDateTime", "acceptance_utc"),
                )
            }}}

        def archive_document(self, cik, accession, filename):
            row = by_cik.get(cik)
            if (
                bootstrap or row is None
                or (accession, filename) != (row["accession"], row["filename"])
            ):
                raise RuntimeError("C06_GOLDEN_FILING_NOT_IN_OFFLINE_FIXTURE")
            return row["body"]

    class ReviewedSec(SecCeriProvider):
        def extract_guidance_document(
            self, document, *, text=None, require_financial_attribution=False, retrieved_at=None,
        ):
            row = by_cik.get(document.cik)
            body = text.encode("utf-8") if text is not None else b""
            if (
                bootstrap or row is None or not require_financial_attribution
                or (document.ticker, document.accession_number, document.document_name)
                != (row["ticker"], row["accession"], row["filename"])
                or retrieved_at != datetime.fromisoformat(row["body_possession_utc"])
                or len(body) != row["body_bytes"]
                or sha256(body).hexdigest() != row["body_sha256"]
            ):
                raise RuntimeError("C06_GOLDEN_FILING_IDENTITY_OR_ATTRIBUTION_MISMATCH")
            records = super().extract_guidance_document(
                document, text=text, require_financial_attribution=require_financial_attribution,
                retrieved_at=retrieved_at,
            )
            expected = row["independent_expected"]
            if (
                len(records) != 1
                or records[0].provider_record_id != expected["record_key"]
                or records[0].payload["source_reference"] != expected["locator"]
                or source_record_content_hash(records[0].payload)
                != C06_PROJECTED_HASHES[row["ticker"]]
            ):
                raise RuntimeError("C06_GOLDEN_PROJECTED_SOURCE_MISMATCH")
            # Supply the verified fixture receipt explicitly at the same seam
            # as the retained provider replay. The stored body was received at
            # this time by the native writer.
            return (replace(records[0], retrieved_at=retrieved_at),)

    return ReviewedSec(client=OfflineSec())


@contextmanager
def c06_offline_sec_clock(at):
    from app.services.ceri.sec import incremental_ingestion, provider
    from single_run_certification.golden_runtime import fixed_datetime

    clock = fixed_datetime(at)
    with (
        patch.object(incremental_ingestion, "datetime", clock),
        patch.object(provider, "datetime", clock),
    ):
        yield
