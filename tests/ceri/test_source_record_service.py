from __future__ import annotations

from datetime import UTC, datetime

from app.models.ceri_tables import CeriIngestionRun, CeriSourceRecord
from app.services.ceri.dtos import RawProviderRecord
from app.services.ceri.enums import CeriDataset
from app.services.ceri.source_record_service import (
    CeriSourceRecordService,
    source_record_content_hash,
)


def test_source_record_content_hash_is_stable_for_key_order() -> None:
    assert source_record_content_hash({"b": 2, "a": 1}) == source_record_content_hash(
        {"a": 1, "b": 2}
    )


def test_source_record_service_stores_exportable_payload() -> None:
    db = FakeDb()
    service = CeriSourceRecordService()
    run = service.create_ingestion_run(
        db,
        provider="manual",
        provider_terms_version="manual-fixture-1.0",
        dataset="estimates",
        scope={"ticker": "MSFT"},
        request_key="request-1",
        config_version="2026-07-31",
        config_hash="hash",
    )

    result = service.store_source_record(
        db,
        ingestion_run_id=run.id,
        record=_record("est-1"),
        raw_payload_allowed=True,
    )

    assert result.inserted is True
    assert result.source_record.raw_json["ticker"] == "MSFT"
    assert result.source_record.restricted_normalized_json is None
    assert result.source_record.company_hint_json["ticker"] == "MSFT"


def test_source_record_service_stores_restricted_payload_without_raw_json() -> None:
    db = FakeDb()
    service = CeriSourceRecordService()

    result = service.store_source_record(
        db,
        ingestion_run_id=None,
        record=_record("est-1"),
        raw_payload_allowed=False,
    )

    assert result.source_record.raw_json is None
    assert result.source_record.restricted_normalized_json["ticker"] == "MSFT"


def test_source_record_service_deduplicates_existing_idempotency_key() -> None:
    existing = CeriSourceRecord(
        id=3,
        provider="manual",
        dataset="estimates",
        provider_record_id="est-1",
        content_hash="hash",
        idempotency_key="key",
    )
    db = FakeDb(scalar_queue=[existing])
    service = CeriSourceRecordService()

    result = service.store_source_record(
        db,
        ingestion_run_id=None,
        record=_record("est-1"),
        raw_payload_allowed=True,
    )

    assert result.inserted is False
    assert result.deduplicated is True
    assert result.corrected is False
    assert result.source_record is existing


def test_source_record_service_deduplicates_same_provider_record_content() -> None:
    payload = {"provider_terms_version": "manual-fixture-1.0", "ticker": "MSFT"}
    existing = CeriSourceRecord(
        id=3,
        provider="manual",
        dataset="estimates",
        provider_record_id="est-1",
        content_hash=source_record_content_hash(payload),
        idempotency_key="legacy-key",
    )
    db = FakeDb(scalar_queue=[None, existing])
    service = CeriSourceRecordService()

    result = service.store_source_record(
        db,
        ingestion_run_id=None,
        record=_record("est-1", payload=payload),
        raw_payload_allowed=True,
    )

    assert result.inserted is False
    assert result.deduplicated is True
    assert result.corrected is False
    assert result.source_record is existing
    assert db.added == []


def test_source_record_service_creates_correction_for_changed_provider_record() -> None:
    prior_payload = {"provider_terms_version": "manual-fixture-1.0", "ticker": "MSFT", "eps": 1}
    corrected_payload = {
        "provider_terms_version": "manual-fixture-1.0",
        "ticker": "MSFT",
        "eps": 2,
    }
    existing = CeriSourceRecord(
        id=3,
        provider="manual",
        dataset="estimates",
        provider_record_id="est-1",
        content_hash=source_record_content_hash(prior_payload),
        idempotency_key="legacy-key",
        ingested_at=datetime(2026, 8, 1, 20, 15, tzinfo=UTC),
    )
    db = FakeDb(scalar_queue=[None, existing])
    service = CeriSourceRecordService()

    result = service.store_source_record(
        db,
        ingestion_run_id=11,
        record=_record("est-1", payload=corrected_payload),
        raw_payload_allowed=True,
    )

    assert result.inserted is True
    assert result.deduplicated is False
    assert result.corrected is True
    assert result.source_record.supersedes_id == existing.id
    assert result.source_record.correction_type == "CORRECTION"
    assert result.source_record.content_hash != existing.content_hash
    assert result.source_record.raw_json == corrected_payload


def test_source_record_service_quarantines_malformed_records() -> None:
    db = FakeDb()
    service = CeriSourceRecordService()

    result = service.store_source_record(
        db,
        ingestion_run_id=None,
        record=_record(
            "malformed:1",
            payload={"ticker": "MSFT", "_ceri_quarantine_reason": "missing_provider_record_id"},
        ),
        raw_payload_allowed=True,
    )

    assert result.quarantined is True
    assert result.corrected is False
    assert result.source_record.quarantine_reason == "missing_provider_record_id"
    assert result.source_record.raw_json is None


def test_source_record_service_finishes_ingestion_run_with_counts() -> None:
    db = FakeDb()
    service = CeriSourceRecordService()
    run = CeriIngestionRun(
        id=1,
        provider="manual",
        dataset="estimates",
        status="RUNNING",
        request_key="request-1",
        started_at=datetime(2026, 8, 1, 20, 15, tzinfo=UTC),
    )

    service.finish_ingestion_run(
        db,
        run,
        status="PARTIAL",
        requested_count=2,
        fetched_count=2,
        inserted_count=1,
        deduplicated_count=0,
        corrected_count=0,
        quarantined_count=1,
        failed_count=0,
        warning_count=1,
        checkpoint={"last_record_index": 2},
    )

    assert run.status == "PARTIAL"
    assert run.inserted_count == 1
    assert run.quarantined_count == 1
    assert run.checkpoint_json == {"last_record_index": 2}
    assert run.duration_ms is not None


def _record(
    provider_record_id: str,
    payload: dict | None = None,
) -> RawProviderRecord:
    return RawProviderRecord(
        provider="manual",
        dataset=CeriDataset.ESTIMATES,
        provider_record_id=provider_record_id,
        payload=payload or {
            "provider_terms_version": "manual-fixture-1.0",
            "ticker": "MSFT",
        },
        published_at=datetime(2026, 8, 1, 20, 15, tzinfo=UTC),
        observed_at=datetime(2026, 8, 1, 20, 15, tzinfo=UTC),
    )


class FakeDb:
    def __init__(self, scalar_queue=None) -> None:
        self.scalar_queue = list(scalar_queue or [])
        self.added = []
        self.flushes = 0
        self.next_id = 1

    def scalar(self, _statement):
        if self.scalar_queue:
            return self.scalar_queue.pop(0)
        return None

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushes += 1
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1
