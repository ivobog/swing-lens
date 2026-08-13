from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriIngestionRun,
    CeriSecDocumentExtraction,
    CeriSecFilingDocument,
    CeriSourceRecord,
)
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.orchestration import (
    CeriIngestionCancelled,
    CeriIngestionRequest,
    CeriIngestionService,
)
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.sec.client import SecClientConfig
from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService
from app.services.ceri.sec.provider import SecCeriProvider
from app.services.ceri.sec.state_service import (
    SecDocumentIdentity,
    SecDocumentStateService,
    SecExtractionStatus,
)
from app.services.ceri.source_record_service import CeriSourceRecordService
from app.settings import SecDocumentIncrementalMode, Settings


class _FakeSecClient:
    def __init__(
        self,
        document_text: str,
        *,
        accession: str = "0000123456-26-000001",
        download_error: Exception | None = None,
    ) -> None:
        self.config = SecClientConfig()
        self.document_text = document_text
        self.accession = accession
        self.download_error = download_error
        self.requests = 0
        self.failures = 0
        self.last_success_at = None
        self.company_calls = 0
        self.submission_calls = 0
        self.download_calls = 0

    def company_tickers(self):
        self.company_calls += 1
        return {"0": {"ticker": "TEST", "cik_str": 123456}}

    def submissions(self, cik: str):
        self.submission_calls += 1
        return {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "accessionNumber": [self.accession],
                    "primaryDocument": ["test-8k.htm"],
                    "filingDate": ["2026-08-01"],
                }
            }
        }

    def archive_document(self, cik: str, accession: str, document: str):
        self.download_calls += 1
        if self.download_error is not None:
            raise self.download_error
        return self.document_text


class _CountingExtractor(GuidanceExtractionService):
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, text: str, *, locator: str = "document"):
        self.calls += 1
        return super().extract(text, locator=locator)


class _FailingExtractor(GuidanceExtractionService):
    def extract(self, text: str, *, locator: str = "document"):
        raise ValueError("parser/extractor failed")


class _FailingSourceRecordService(CeriSourceRecordService):
    def store_source_record(self, *args, **kwargs):
        raise SQLAlchemyError("database write failed")


def test_zero_guidance_is_terminal_and_active_skips_before_download(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = load_ceri_config()
    client = _FakeSecClient("Administrative filing only.")
    extractor = _CountingExtractor()
    provider = SecCeriProvider(client=client, extractor=extractor)
    registry = CeriProviderRegistry(providers={"sec": provider}, config=config)

    with Session(engine) as db:
        cold_active = CeriIngestionService(
            config=config,
            registry=registry,
            settings=_settings(SecDocumentIncrementalMode.ACTIVE),
        ).ingest(db, _request("zero-cold-active"))
        db.commit()
        assert cold_active.status == "PARTIAL"
        assert cold_active.documents_downloaded == 0
        assert client.submission_calls == 0

        shadow = CeriIngestionService(
            config=config,
            registry=registry,
            settings=_settings(SecDocumentIncrementalMode.SHADOW),
        ).ingest(db, _request("zero-shadow"))
        db.commit()
        assert shadow.status == "COMPLETED"
        assert shadow.documents_downloaded == 1
        extraction = db.scalar(select(CeriSecDocumentExtraction))
        assert extraction is not None
        assert extraction.status == SecExtractionStatus.COMPLETED_NO_RECORDS.value
        assert extraction.record_count == 0

        active = CeriIngestionService(
            config=config,
            registry=registry,
            settings=_settings(SecDocumentIncrementalMode.ACTIVE),
        ).ingest(db, _request("zero-active"))
        db.commit()
        assert active.status == "COMPLETED", db.get(
            CeriIngestionRun, active.ingestion_run_id
        ).errors_json
        assert active.documents_skipped == 1
        assert active.documents_downloaded == 0
        assert client.download_calls == 1
        assert extractor.calls == 1
    engine.dispose()


def test_failures_never_become_completed_no_records(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = load_ceri_config()
    cases = (
        TimeoutError("timeout"),
        RuntimeError("HTTP 429"),
        RuntimeError("HTTP 503"),
        OSError("network unavailable"),
    )
    with Session(engine) as db:
        for index, error in enumerate(cases, start=10):
            client = _FakeSecClient(
                "unused",
                accession=f"0000123456-26-0000{index}",
                download_error=error,
            )
            provider = SecCeriProvider(client=client)
            result = CeriIngestionService(
                config=config,
                registry=CeriProviderRegistry(providers={"sec": provider}, config=config),
                settings=_settings(SecDocumentIncrementalMode.SHADOW),
            ).ingest(db, _request(f"failure-{index}"))
            db.commit()
            assert result.status == "PARTIAL"
        parser_client = _FakeSecClient("parse me", accession="0000123456-26-000099")
        parser_result = CeriIngestionService(
            config=config,
            registry=CeriProviderRegistry(
                providers={
                    "sec": SecCeriProvider(client=parser_client, extractor=_FailingExtractor())
                },
                config=config,
            ),
            settings=_settings(SecDocumentIncrementalMode.SHADOW),
        ).ingest(db, _request("failure-parser"))
        db.commit()
        assert parser_result.status == "PARTIAL"
        statuses = set(db.scalars(select(CeriSecDocumentExtraction.status)).all())
        assert statuses == {SecExtractionStatus.FAILED_RETRYABLE.value}
        assert SecExtractionStatus.COMPLETED_NO_RECORDS.value not in statuses

        db_client = _FakeSecClient(
            "The company expects revenue guidance of $100 to $110 million.",
            accession="0000123456-26-000100",
        )
        with pytest.raises(SQLAlchemyError):
            CeriIngestionService(
                config=config,
                registry=CeriProviderRegistry(
                    providers={"sec": SecCeriProvider(client=db_client)}, config=config
                ),
                source_records=_FailingSourceRecordService(),
                settings=_settings(SecDocumentIncrementalMode.SHADOW),
            ).ingest(db, _request("failure-database"))
        db.rollback()
        database_failure = db.scalar(
            select(CeriSecDocumentExtraction)
            .join(CeriSecFilingDocument)
            .where(CeriSecFilingDocument.accession_number == "0000123456-26-000100")
        )
        assert database_failure is not None
        assert database_failure.status == SecExtractionStatus.FAILED_RETRYABLE.value
        assert db.query(CeriSourceRecord).count() == 0
    engine.dispose()


def test_cancellation_after_extraction_is_retryable_and_not_completed(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = load_ceri_config()
    client = _FakeSecClient("The company expects revenue guidance of $100 to $110 million.")
    calls = 0

    def cancel_after_download() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 2

    with Session(engine) as db:
        with pytest.raises(CeriIngestionCancelled):
            CeriIngestionService(
                config=config,
                registry=CeriProviderRegistry(
                    providers={"sec": SecCeriProvider(client=client)}, config=config
                ),
                settings=_settings(SecDocumentIncrementalMode.SHADOW),
            ).ingest(db, _request("cancel-after-extract"), should_cancel=cancel_after_download)
        db.rollback()
        extraction = db.scalar(select(CeriSecDocumentExtraction))
        assert extraction is not None
        assert extraction.status == SecExtractionStatus.CANCELLED.value
        assert db.query(CeriSourceRecord).count() == 0
    engine.dispose()


def test_crash_before_atomic_commit_leaves_no_records_and_stale_claim_recovers(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    states = SecDocumentStateService()
    with Session(engine) as db:
        document = states.register_document(
            db,
            SecDocumentIdentity(
                cik="123456",
                accession_number="0000123456-26-000003",
                document_name="crash.htm",
            ),
        )
        db.commit()
        claim = states.claim(
            db,
            document_id=document.id,
            dataset="guidance",
            processor_signature="v1",
            worker_id="crashed-worker",
            lease_seconds=900,
        )
        db.commit()
        assert claim.acquired
        # A process crash before the final commit rolls back both tentative data
        # and completion; the durable claim remains RUNNING until lease expiry.
        db.add(
            CeriSourceRecord(
                provider="sec",
                dataset="guidance",
                provider_record_id="tentative",
                content_hash="b" * 64,
                idempotency_key="tentative",
                export_policy="restricted",
            )
        )
        states.complete(
            db,
            extraction_id=claim.extraction_id,
            execution_token=claim.execution_token or "",
            record_count=1,
            content_hash="b" * 64,
            content_bytes=1,
        )
        db.rollback()
        assert db.query(CeriSourceRecord).count() == 0
        extraction = db.get(CeriSecDocumentExtraction, claim.extraction_id)
        assert extraction is not None
        assert extraction.status == SecExtractionStatus.RUNNING.value
        db.execute(
            update(CeriSecDocumentExtraction)
            .where(CeriSecDocumentExtraction.id == claim.extraction_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
        recovered = states.claim(
            db,
            document_id=document.id,
            dataset="guidance",
            processor_signature="v1",
            worker_id="recovery-worker",
            lease_seconds=900,
        )
        assert recovered.acquired and recovered.stale_recovered
    engine.dispose()


def test_shadow_output_uses_existing_record_idempotency_then_active_skips(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = load_ceri_config()
    client = _FakeSecClient(
        "The company expects full year revenue guidance of $100 to $110 million."
    )
    provider = SecCeriProvider(client=client)
    registry = CeriProviderRegistry(providers={"sec": provider}, config=config)

    with Session(engine) as db:
        service = CeriIngestionService(
            config=config,
            registry=registry,
            settings=_settings(SecDocumentIncrementalMode.SHADOW),
        )
        first = service.ingest(db, _request("records-shadow-1"))
        db.commit()
        second = service.ingest(db, _request("records-shadow-2"))
        db.commit()
        assert second.status == "COMPLETED", db.get(
            CeriIngestionRun, second.ingestion_run_id
        ).errors_json
        assert first.inserted > 0
        assert second.inserted == 0
        assert second.deduplicated == first.inserted
        assert second.documents_would_skip == 1
        extraction = db.scalar(select(CeriSecDocumentExtraction))
        assert extraction is not None
        assert extraction.status == SecExtractionStatus.COMPLETED_WITH_RECORDS.value
        assert extraction.record_count == first.inserted
        assert db.query(CeriSourceRecord).count() == first.inserted

        active = CeriIngestionService(
            config=config,
            registry=registry,
            settings=_settings(SecDocumentIncrementalMode.ACTIVE),
        ).ingest(db, _request("records-active"))
        db.commit()
        assert active.documents_downloaded == 0
        assert active.documents_skipped == 1
        assert client.download_calls == 2
    engine.dispose()


def test_claim_is_single_owner_stale_safe_and_signature_versioned(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    states = SecDocumentStateService()
    with Session(engine) as first:
        document = states.register_document(
            first,
            SecDocumentIdentity(
                cik="123456",
                accession_number="0000123456-26-000002",
                document_name="lease.htm",
            ),
        )
        first.commit()
        document_id = document.id
        owner = states.claim(
            first,
            document_id=document_id,
            dataset="guidance",
            processor_signature="v1",
            worker_id="worker-1",
            lease_seconds=900,
        )
        first.commit()
        assert owner.acquired

    with Session(engine) as second:
        rejected = states.claim(
            second,
            document_id=document_id,
            dataset="guidance",
            processor_signature="v1",
            worker_id="worker-2",
            lease_seconds=900,
        )
        second.rollback()
        assert not rejected.acquired
        second.execute(
            update(CeriSecDocumentExtraction)
            .where(CeriSecDocumentExtraction.id == owner.extraction_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        second.commit()
        recovered = states.claim(
            second,
            document_id=document_id,
            dataset="guidance",
            processor_signature="v1",
            worker_id="worker-2",
            lease_seconds=900,
        )
        second.commit()
        assert recovered.acquired and recovered.stale_recovered
        assert recovered.execution_token != owner.execution_token
        with pytest.raises(RuntimeError, match="lease was lost"):
            states.complete(
                second,
                extraction_id=owner.extraction_id,
                execution_token=owner.execution_token or "",
                record_count=0,
                content_hash="a" * 64,
                content_bytes=1,
            )
        second.rollback()
        states.complete(
            second,
            extraction_id=recovered.extraction_id,
            execution_token=recovered.execution_token or "",
            record_count=0,
            content_hash="a" * 64,
            content_bytes=1,
        )
        second.commit()
        version_two = states.claim(
            second,
            document_id=document_id,
            dataset="guidance",
            processor_signature="v2",
            worker_id="worker-2",
            lease_seconds=900,
        )
        second.commit()
        assert version_two.acquired
        assert db_count(second, CeriSecFilingDocument) == 1
        assert db_count(second, CeriSecDocumentExtraction) == 2
    engine.dispose()


def _settings(mode: SecDocumentIncrementalMode) -> Settings:
    return Settings(
        _env_file=None,
        sec_document_incremental_mode=mode,
        sec_document_lease_seconds=900,
        sec_document_retry_base_seconds=1,
    )


def _request(key: str) -> CeriIngestionRequest:
    return CeriIngestionRequest(
        provider="sec",
        dataset=CeriDataset.GUIDANCE,
        ticker="TEST",
        request_key=key,
        scope={"ticker": "TEST"},
    )


def db_count(db: Session, model) -> int:
    return db.query(model).count()


def _upgrade(database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )
