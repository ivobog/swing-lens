from __future__ import annotations

import gzip
import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.ceri_tables import (
    CeriCompany,
    CeriGuidanceEvent,
    CeriScoreSnapshot,
    CeriSecFilingDocument,
    CeriSourceRecord,
)
from app.services.ceri import source_record_service
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.guidance_comparison_service import compare_guidance
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.processing_run_service import CeriProcessingRunService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.query_service import accepted_guidance_at_cutoff
from app.services.ceri.sec import incremental_ingestion
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.provider import SecCeriProvider
from app.services.ceri.sec.scope_evidence import generation_ready, source_attribution_proved
from app.settings import SecDocumentIncrementalMode, Settings

FIXTURES = Path(__file__).resolve().parents[1] / "ceri/fixtures/sec_guidance_financial_v1"


class RetainedSec:
    def __init__(self, issuer: dict, filing: dict, submissions: dict, body: bytes):
        self.issuer = issuer
        self.filing = filing
        self.roster = submissions
        self.body = body

    def submissions(self, cik):
        assert cik == self.issuer["cik"]
        return self.roster

    def archive_document(self, cik, accession, filename):
        assert (cik, accession, filename) == (
            self.issuer["cik"],
            self.filing["accession_number"],
            self.filing["document_name"],
        )
        return self.body.decode("utf-8")


class RetainedFilingProvider(SecCeriProvider):
    def select_guidance_documents(self, request, *, cik):
        selection = super().select_guidance_documents(request, cik=cik)
        exact = tuple(
            doc
            for doc in selection.documents
            if doc.accession_number == self.client.filing["accession_number"]
        )
        assert len(exact) == 1
        return replace(
            selection,
            documents=exact,
            discovery=dict(
                selection.discovery,
                observed_at=self.client.issuer["submissions_possession"],
            ),
        )


@contextmanager
def _financial_replay(url, tmp_path):
    run_guarded_alembic_upgrade(Config("alembic.ini"), url)
    engine = create_engine(url)
    settings = Settings(
        _env_file=None,
        database_url=url,
        cache_dir=tmp_path,
        sec_document_incremental_mode=SecDocumentIncrementalMode.SHADOW,
    )
    try:
        with Session(engine) as db:
            yield db, settings
    finally:
        engine.dispose()


def _run_filing(db, settings, issuer, filing, roster, body):
    received = datetime.fromisoformat(filing["registry_possession_utc"])
    accepted = datetime.fromisoformat(filing["sec_filing_accepted_at_utc"])
    assert len(body) == filing["cache_utf8_bytes"]
    assert hashlib.sha256(body).hexdigest() == filing["cache_sha256"]

    class OriginalReceiptClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return received.astimezone(tz) if tz is not None else received.replace(tzinfo=None)

    provider = RetainedFilingProvider(client=RetainedSec(issuer, filing, roster, body))
    ingestion = CeriIngestionService(
        registry=CeriProviderRegistry(providers={"sec": provider}, config=load_ceri_config()),
        settings=settings,
    )
    filing_day = date.fromisoformat(filing["filing_date"])
    key = filing["accession_number"]
    with (
        patch.object(incremental_ingestion, "datetime", OriginalReceiptClock),
        patch.object(source_record_service, "_utcnow", lambda: received),
    ):
        result = ingestion.ingest(
            db,
            CeriIngestionRequest(
                provider="sec",
                dataset=CeriDataset.GUIDANCE,
                ticker=issuer["ticker"],
                start=filing_day,
                end=filing_day,
                request_key=key,
            ),
        )
    db.commit()
    assert result.status == "COMPLETED", result.as_dict()
    assert generation_ready(db, issuer["cik"], sec_guidance_processor_signature())
    stored = db.scalars(
        select(CeriSecFilingDocument).where(
            CeriSecFilingDocument.accession_number == key,
        )
    ).one()
    assert (stored.last_content_hash, stored.last_downloaded_at) == (
        filing["cache_sha256"],
        received,
    )
    run, created = CeriProcessingRunService().create_or_get(
        db,
        job_type="CERI_NORMALIZE_BATCH",
        request_key=key + ":normalize",
    )
    assert created
    normalized = CeriNormalizationService().normalize(
        db,
        processing_run=run,
        ingestion_run_id=result.ingestion_run_id,
    )
    db.commit()
    assert normalized.failed == 0
    events = db.execute(
        select(CeriGuidanceEvent, CeriSourceRecord)
        .join(CeriSourceRecord)
        .where(CeriSourceRecord.ingestion_run_id == result.ingestion_run_id)
        .order_by(CeriGuidanceEvent.id)
    ).all()
    for event, source in events:
        assert source.retrieved_at == received
        assert source.source_timestamp == accepted
        assert source.published_at is None
        assert source_attribution_proved(db, source, issuer["cik"])
        assert (event.evidence_locator or "").startswith(key + "/" + filing["document_name"])
    return [event for event, _ in events]


@pytest.mark.integration
def test_retained_agio_emr_guidance_is_ineligible(disposable_postgres_database, tmp_path):
    with _financial_replay(disposable_postgres_database, tmp_path) as (db, settings):
        issuers = json.loads((FIXTURES / "manifest.json").read_bytes())
        eligible_count = 0
        for issuer in issuers:
            raw = gzip.decompress((FIXTURES / issuer["submissions"]).read_bytes())
            assert hashlib.sha256(raw).hexdigest() == issuer["submissions_sha256"]
            company = CeriCompany(ticker=issuer["ticker"], exchange="US", cik=issuer["cik"])
            db.add(company)
            db.commit()
            outcomes = {}
            for filing in issuer["documents"]:
                body = gzip.decompress((FIXTURES / filing["body_file"]).read_bytes())
                events = _run_filing(db, settings, issuer, filing, json.loads(raw), body)
                outcomes[filing["accession_number"]] = events
                eligible_count += sum(event.accepted_for_scoring for event in events)
            if issuer["ticker"] == "AGIO":
                assert all(
                    event.action != "MAINTAINED" for rows in outcomes.values() for event in rows
                )
                assert all(
                    event.metric != "REVENUE" or event.point_value != Decimal("140")
                    for rows in outcomes.values()
                    for event in rows
                )
            else:
                # The May report was possessed after the August document, although
                # it is normalized first here. The real SQL boundary must hold.
                earlier, later, cross_year = outcomes.values()
                assert any(r.period_label == "FY2026" and r.low_value is not None for r in earlier)
                assert any(
                    r.period_label == "FY2026" and r.point_value == Decimal("6.55") for r in later
                )
                assert all(not r.accepted_for_scoring for r in later)
                # The following ", with underlying sales ..." clause
                # is a separate measure and cannot label the consolidated point.
                for rows, amount, basis in (
                    (earlier, "4.5", "REVENUE_REPORTED"),
                    (later, "5", "REVENUE_REPORTED"),
                    (cross_year, "5", "REVENUE_CONTINUING_OPERATIONS"),
                ):
                    forecast = next(
                        r for r in rows
                        if r.metric == "REVENUE" and r.point_value == Decimal(amount)
                    )
                    source = db.get(CeriSourceRecord, forecast.source_record_id)
                    assert source.restricted_normalized_json["guidance_financial_basis"] == basis
                assert any(
                    r.period_label == "FY2027" and r.point_value == Decimal("7.25")
                    for r in cross_year
                )
            cutoff = datetime.fromisoformat(issuer["submissions_possession"]) + timedelta(days=1)
            assert accepted_guidance_at_cutoff(db, company.id, cutoff) is None
        assert eligible_count == 0
        assert db.scalar(select(func.count()).select_from(CeriScoreSnapshot)) == 0


def _synthetic_filing(db, settings, issuer, sequence, received, sentence):
    accession = f"{issuer['cik']}-26-{sequence:06}"
    body = (
        f"<SEC-HEADER>CENTRAL INDEX KEY: {issuer['cik']}</SEC-HEADER>"
        f"<html><body><p>{sentence}</p></body></html>"
    ).encode()
    posting = received - timedelta(hours=1)
    filing = {
        "accession_number": accession,
        "document_name": f"control-{sequence}.htm",
        "form": "8-K",
        "filing_date": posting.date().isoformat(),
        "sec_filing_accepted_at_utc": posting.isoformat(),
        "registry_possession_utc": received.isoformat(),
        "cache_sha256": hashlib.sha256(body).hexdigest(),
        "cache_utf8_bytes": len(body),
    }
    roster = {
        "cik": issuer["cik"],
        "filings": {
            "recent": {
                "form": ["8-K"],
                "accessionNumber": [accession],
                "primaryDocument": [filing["document_name"]],
                "filingDate": [filing["filing_date"]],
                "acceptanceDateTime": [posting.isoformat()],
            }
        },
    }
    return _run_filing(
        db,
        settings,
        dict(issuer, submissions_possession=received.isoformat()),
        filing,
        roster,
        body,
    )


@pytest.mark.integration
def test_native_prior_uses_exact_fiscal_period_and_financial_basis(
    disposable_postgres_database,
    tmp_path,
):
    with _financial_replay(disposable_postgres_database, tmp_path) as (db, settings):
        received = datetime(2026, 7, 21, 16, tzinfo=UTC)
        scenarios = (
            (
                "Our consolidated revenue for fiscal year 2027 is expected to be between "
                "USD 100 million and USD 110 million.",
                "For fiscal year 2028, our consolidated revenue is expected to be between "
                "USD 150 million and USD 160 million.",
                "For fiscal year 2027, our consolidated revenue is expected to be between "
                "USD 110 million and USD 120 million.",
                "FY2027",
                "RAISED",
            ),
            (
                "For second quarter FY2027, our adjusted EPS is expected to be "
                "USD 2.20 to USD 2.40.",
                "For second quarter FY2027, our GAAP EPS is expected to be USD 1.40 to USD 1.70.",
                "For second quarter FY2027, our adjusted EPS is expected to be "
                "USD 2.10 to USD 2.30.",
                "FY2027-Q2",
                "LOWERED",
            ),
            (
                "For second quarter FY2027, our adjusted EPS is expected to be "
                "USD 2.20 to USD 2.40.",
                "For third quarter FY2027, our adjusted EPS is expected to be "
                "USD 3.20 to USD 3.40.",
                "For second quarter FY2027, our adjusted EPS is expected to be "
                "USD 2.20 to USD 2.40.",
                "FY2027-Q2",
                "MAINTAINED",
            ),
        )
        for index, (original, incompatible, current, period, expected) in enumerate(scenarios, 1):
            issuer = {"ticker": f"FINANCIAL{index}", "cik": f"{990000000 + index:010}"}
            company = CeriCompany(ticker=issuer["ticker"], cik=issuer["cik"])
            db.add(company)
            db.commit()
            (first,) = _synthetic_filing(db, settings, issuer, 1, received, original)
            (intervening,) = _synthetic_filing(
                db,
                settings,
                issuer,
                2,
                received + timedelta(days=1),
                incompatible,
            )
            (event,) = _synthetic_filing(
                db,
                settings,
                issuer,
                3,
                received + timedelta(days=2),
                current,
            )
            assert first.period_label == event.period_label == period
            assert not first.accepted_for_scoring and not intervening.accepted_for_scoring
            assert (event.action, event.accepted_for_scoring) == (expected, True)
            cutoff = received + timedelta(days=3)
            assert accepted_guidance_at_cutoff(db, company.id, cutoff) == event


@pytest.mark.parametrize(
    "different",
    [
        {"period_label": None},
        {"financial_basis": None},
        {"period_label": "FY2027-Q3"},
        {"financial_basis": "EPS_REPORTED"},
        {"currency": "EUR"},
        {"unit": "%"},
        {"company_id": 2},
    ],
)
def test_guidance_comparator_fails_closed_on_unproved_or_incompatible_identity(different):
    def evidence(source_id, **changes):
        basis = changes.pop("financial_basis", "EPS_ADJUSTED")
        fields = {
            "company_id": 1,
            "metric": "EPS_DILUTED",
            "period_type": "CURRENT_QUARTER",
            "period_label": "FY2027-Q2",
            "currency": "USD",
            "unit": "PER_SHARE",
            "point_value": Decimal("2.20"),
            **changes,
        }
        return (
            CeriGuidanceEvent(source_record_id=source_id, **fields),
            CeriSourceRecord(
                id=source_id,
                provider="sec",
                dataset="guidance",
                restricted_normalized_json={
                    "period_label": fields["period_label"],
                    "guidance_financial_basis": basis,
                },
            ),
        )

    current, current_source = evidence(2)
    prior, prior_source = evidence(1)
    assert (
        compare_guidance(
            current,
            prior,
            current_source=current_source,
            prior_source=prior_source,
        ).action
        == "MAINTAINED"
    )
    incompatible, incompatible_source = evidence(3, **different)
    assert (
        compare_guidance(
            current,
            incompatible,
            current_source=current_source,
            prior_source=incompatible_source,
        ).action
        == "UNKNOWN"
    )
