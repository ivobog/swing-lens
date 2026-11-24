from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCompany,
    CeriGuidanceEvent,
    CeriSecFilingDocument,
    CeriSourceRecord,
)
from app.services.ceri import source_record_service
from app.services.ceri.config import load_ceri_config
from app.services.ceri.dtos import GuidanceRequest
from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.ceri.enums import CeriDataset
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.pit_eligibility import source_record_historical_eligibility_at
from app.services.ceri.processing_run_service import CeriProcessingRunService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.query_service import _guidance_summary, accepted_guidance_at_cutoff
from app.services.ceri.sec import incremental_ingestion
from app.services.ceri.sec.client import SecClientConfig
from app.services.ceri.sec.provider import SecCeriProvider
from app.settings import SecDocumentIncrementalMode, Settings

TICKER = "C06TEMP"
CIK = "0900000999"


class OfflineSec:
    config = SecClientConfig()

    def __init__(self, number, day, acceptance, amount):
        self.acceptance = acceptance
        self.day = day
        self.accession = f"0000000999-26-{number:06}"
        self.filename = f"fixture-{number}.htm"
        self.body = (
            f"<SEC-HEADER>\nCENTRAL INDEX KEY: {CIK}\n</SEC-HEADER>\n"
            f"<p>We expect full year revenue guidance of USD {amount} million.</p>"
        )

    def submissions(self, issuer):
        assert issuer == CIK
        return {
            "cik": issuer,
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "filingDate": [str(self.day)],
                    "accessionNumber": [self.accession],
                    "primaryDocument": [self.filename],
                    "acceptanceDateTime": [self.acceptance],
                }
            },
        }

    def archive_document(self, issuer, accession, filename):
        assert (issuer, accession, filename) == (CIK, self.accession, self.filename)
        return self.body


class Collections:
    def __init__(self, events, sources):
        self.collections = {CeriGuidanceEvent: events, CeriSourceRecord: sources}


def _at(value):
    return datetime.fromisoformat(value).astimezone(UTC)


def _acquire(db, cache, number, filing_day, acceptance, possessed, amount):
    client = OfflineSec(number, filing_day, acceptance, amount)
    provider = SecCeriProvider(client=client)
    registry = CeriProviderRegistry(
        providers={"sec": provider},
        config=load_ceri_config(),
    )
    settings = Settings(
        _env_file=None,
        sec_document_incremental_mode=SecDocumentIncrementalMode.SHADOW,
        cache_dir=cache,
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return possessed.astimezone(tz) if tz is not None else possessed.replace(tzinfo=None)

    request_key = f"c06-time-fixture-{number}"
    request = CeriIngestionRequest(
        provider="sec",
        dataset=CeriDataset.GUIDANCE,
        ticker=TICKER,
        end=filing_day,
        request_key=request_key,
        scope={"ticker": TICKER},
    )
    with (
        patch.object(incremental_ingestion, "datetime", Clock),
        patch.object(
            source_record_service,
            "_utcnow",
            lambda: possessed,
        ),
    ):
        receipt = CeriIngestionService(
            registry=registry,
            settings=settings,
        ).ingest(db, request)
    db.commit()
    assert (receipt.status, receipt.inserted) == ("COMPLETED", 1), receipt.as_dict()
    source = db.scalar(
        select(CeriSourceRecord).where(
            CeriSourceRecord.ingestion_run_id == receipt.ingestion_run_id,
        )
    )
    assert source.company_hint_json["cik"] == CIK
    locator = f"{client.accession}/{client.filename}#paragraph-2"
    assert source.provider_record_id == f"{client.accession}:{locator}"
    assert source.source_reference == locator and len(source.content_hash) == 64
    registry_row = db.scalar(
        select(CeriSecFilingDocument).where(
            CeriSecFilingDocument.accession_number == client.accession,
        )
    )
    assert registry_row.last_downloaded_at == source.retrieved_at == possessed
    assert source.published_at is None and source.observed_at is None
    assert source.source_timestamp == (_at(acceptance) if acceptance else None)
    processing, _ = CeriProcessingRunService().create_or_get(
        db,
        job_type="CERI_NORMALIZE_BATCH",
        request_key=request_key + "-normalize",
    )
    outcome = CeriNormalizationService().normalize(
        db,
        processing_run=processing,
        ingestion_run_id=receipt.ingestion_run_id,
    )
    db.commit()
    assert (outcome.normalized, outcome.failed) == (1, 0)
    event = db.scalar(
        select(CeriGuidanceEvent).where(
            CeriGuidanceEvent.source_record_id == source.id,
        )
    )
    assert (event.point_value, event.currency, event.unit, event.evidence_locator) == (
        Decimal(amount),
        "USD",
        "MILLION",
        locator,
    )
    return source, event


def test_sec_acceptance_possession_and_session_gate_the_real_selector(
    disposable_postgres_database,
    tmp_path,
):
    url = disposable_postgres_database
    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            company = CeriCompany(ticker=TICKER, cik=CIK, exchange="US")
            db.add(company)
            db.commit()
            _, baseline = _acquire(
                db,
                tmp_path,
                1,
                date(2026, 9, 21),
                "2026-09-21T18:00:00Z",
                _at("2026-09-21T18:00:00Z"),
                100,
            )
            assert baseline.action == "UNKNOWN" and not baseline.accepted_for_scoring
            initial, previous = _acquire(
                db,
                tmp_path,
                2,
                date(2026, 9, 22),
                "2026-09-22T18:00:00Z",
                _at("2026-09-22T18:00:00Z"),
                120,
            )
            assert previous.accepted_for_scoring
            after, event = _acquire(
                db,
                tmp_path,
                3,
                date(2026, 9, 23),
                "2026-09-23T21:00:00Z",
                _at("2026-09-23T21:00:00Z"),
                125,
            )
            event.supersedes_id = previous.id
            db.commit()
            assert (event.action, event.confidence, event.accepted_for_scoring) == (
                "RAISED",
                "HIGH",
                True,
            )
            assert event.effective_session == date(2026, 9, 24)
            assert source_record_historical_eligibility_at(after) == _at("2026-09-23T21:00:00Z")
            fixture = Collections([baseline, previous, event], [initial, after])
            for raw in (
                "2026-09-23T18:00:00Z",
                "2026-09-23T20:59:00Z",
                "2026-09-23T21:00:00Z",
                "2026-09-24T03:59:00Z",
            ):
                cutoff = _at(raw)
                for backend in (db, fixture):
                    assert (
                        accepted_guidance_at_cutoff(backend, company.id, cutoff).id == previous.id
                    )
            for backend in (db, fixture):
                assert (
                    accepted_guidance_at_cutoff(
                        backend,
                        company.id,
                        _at("2026-09-24T14:00:00Z"),
                    ).id
                    == event.id
                )

            delayed, delayed_event = _acquire(
                db,
                tmp_path,
                4,
                date(2026, 9, 24),
                "2026-09-24T14:00:00Z",
                _at("2026-09-24T17:00:00Z"),
                130,
            )
            delayed_event.supersedes_id = event.id
            delayed_event.expires_at = _at("2026-09-24T18:00:00Z")
            db.commit()
            assert delayed_event.effective_session == date(2026, 9, 24)
            assert source_record_historical_eligibility_at(delayed) == _at("2026-09-24T17:00:00Z")
            fixture = Collections([previous, event, delayed_event], [initial, after, delayed])
            for backend in (db, fixture):
                assert (
                    accepted_guidance_at_cutoff(
                        backend,
                        company.id,
                        _at("2026-09-24T15:00:00Z"),
                    ).id
                    == event.id
                )
                assert (
                    accepted_guidance_at_cutoff(
                        backend,
                        company.id,
                        _at("2026-09-24T17:00:00Z"),
                    ).id
                    == delayed_event.id
                )
                # An expired newer observation does not resurrect its predecessor.
                assert (
                    accepted_guidance_at_cutoff(
                        backend,
                        company.id,
                        _at("2026-09-24T18:00:00Z"),
                    )
                    is None
                )

            date_only, timeless_event = _acquire(
                db,
                tmp_path,
                5,
                date(2026, 9, 25),
                None,
                _at("2026-09-25T17:00:00Z"),
                135,
            )
            assert timeless_event.effective_at is None and not timeless_event.accepted_for_scoring
            assert source_record_historical_eligibility_at(date_only) == _at("2026-09-25T17:00:00Z")
            earlier = _guidance_summary(db, company.id, _at("2026-09-25T15:00:00Z"))
            assert timeless_event.id not in {row["id"] for row in earlier["rejected"]}
            later = _guidance_summary(db, company.id, _at("2026-09-25T17:00:00Z"))
            assert timeless_event.id in {row["id"] for row in later["rejected"]}

            # With a source quarantine, the otherwise-eligible replacement
            # cannot suppress the older, genuinely available filing.
            delayed.quarantine_reason = "test-quarantine"
            db.commit()
            for backend in (db, fixture):
                assert (
                    accepted_guidance_at_cutoff(
                        backend,
                        company.id,
                        _at("2026-09-24T17:15:00Z"),
                    ).id
                    == event.id
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("instant", "session"),
    [
        ("2026-09-23T14:00:00Z", date(2026, 9, 23)),
        ("2026-09-25T21:00:00Z", date(2026, 9, 28)),
        ("2026-09-26T17:30:00Z", date(2026, 9, 28)),
        ("2026-07-03T14:00:00Z", date(2026, 7, 6)),
        ("2026-11-27T18:05:00Z", date(2026, 11, 30)),
        ("2026-10-30T20:30:00Z", date(2026, 11, 2)),
        ("2026-11-02T20:30:00Z", date(2026, 11, 2)),
    ],
)
def test_sec_timestamp_uses_the_exchange_calendar(instant, session):
    provider = SecCeriProvider(client=OfflineSec(9, _at(instant).date(), instant, 101))
    (document,) = provider.discover_guidance_documents(
        GuidanceRequest(company_id=None, ticker=TICKER, end=_at(instant).date()),
        cik=CIK,
    )
    assert document.accepted_at == _at(instant)
    assert (
        CeriEffectiveSessionService().resolve(timestamp=document.accepted_at).effective_session
        == session
    )


def test_ambiguous_or_missing_acceptance_never_becomes_midnight():
    class Unaligned(OfflineSec):
        def submissions(self, issuer):
            value = super().submissions(issuer)
            value["filings"]["recent"]["acceptanceDateTime"].append("2026-09-23T19:00:00Z")
            return value

    class Unbound(OfflineSec):
        def submissions(self, issuer):
            value = super().submissions(issuer)
            del value["cik"]
            return value

    day = date(2026, 9, 23)
    for client in (
        OfflineSec(11, day, "2026-09-23T19:00:00", 101),
        Unaligned(12, day, None, 101),
        Unbound(13, day, "2026-09-23T19:00:00Z", 101),
    ):
        provider = SecCeriProvider(client=client)
        (document,) = provider.discover_guidance_documents(
            GuidanceRequest(company_id=None, ticker=TICKER, end=day),
            cik=CIK,
        )
        (record,) = provider.extract_guidance_document(document, text=client.body)
        assert document.accepted_at is None
        assert record.published_at is None and record.observed_at is None
        assert record.payload["source_timestamp"] is None
        assert "announced_at" not in record.payload
