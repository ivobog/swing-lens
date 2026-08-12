from __future__ import annotations

from datetime import datetime

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystSource,
    CeriCompany,
    CeriEstimateSnapshot,
    CeriProcessingRun,
    CeriSourceRecord,
)
from app.services.ceri.identity_resolver import CeriIdentityResolver
from app.services.ceri.normalization_service import CeriNormalizationService


def test_normalization_service_persists_estimate_and_processing_lineage() -> None:
    db = FakeDb()
    processing_run = CeriProcessingRun(
        id=5,
        job_type="CERI_NORMALIZE",
        status="RUNNING",
        deterministic_request_key="normalize-1",
        started_at=datetime(2026, 8, 1),
    )
    source = CeriSourceRecord(
        id=7,
        provider="manual",
        dataset="estimates",
        provider_record_id="est-1",
        company_hint_json={"ticker": "MSFT", "exchange": "NASDAQ"},
        raw_json={
            "ticker": "MSFT",
            "exchange": "NASDAQ",
            "metric": "EPS_DILUTED",
            "period_type": "ANNUAL",
            "fiscal_year": 2026,
            "consensus": "14.25",
            "currency": "USD",
            "published_at": "2026-08-03T12:00:00-04:00",
        },
        published_at=datetime.fromisoformat("2026-08-03T12:00:00-04:00"),
        content_hash="hash",
        idempotency_key="key",
    )
    service = CeriNormalizationService(
        identity_resolver=CeriIdentityResolver(
            companies=[CeriCompany(id=42, ticker="MSFT", exchange="NASDAQ")]
        )
    )

    result = service.normalize(db, processing_run=processing_run, source_records=[source])

    estimates = [row for row in db.added if isinstance(row, CeriEstimateSnapshot)]
    assert result.status == "COMPLETED"
    assert result.read == 1
    assert result.normalized == 1
    assert estimates[0].company_id == 42
    assert estimates[0].consensus is not None
    assert processing_run.checkpoint_json["last_source_record_id"] == 7


def test_normalization_service_preserves_same_session_estimate_corrections() -> None:
    db = FakeDb()
    processing_run = CeriProcessingRun(
        id=6,
        job_type="CERI_NORMALIZE",
        status="RUNNING",
        deterministic_request_key="normalize-corrections",
        started_at=datetime(2026, 8, 9),
    )
    payload = {
        "ticker": "A",
        "metric": "EPS_DILUTED",
        "period_type": "NEXT_FISCAL_YEAR",
        "fiscal_period_end": "2026-10-31",
        "consensus": "6.0213",
    }
    original = CeriSourceRecord(
        id=935,
        provider="eodhd",
        dataset="estimates",
        provider_record_id="A.US:NEXT_FISCAL_YEAR:2026-10-31:EPS_DILUTED",
        company_hint_json={"ticker": "A"},
        restricted_normalized_json=payload,
        observed_at=datetime.fromisoformat("2026-08-08T17:33:41+02:00"),
        content_hash="original-hash",
        idempotency_key="original-key",
    )
    correction = CeriSourceRecord(
        id=2681,
        provider="eodhd",
        dataset="estimates",
        provider_record_id=original.provider_record_id,
        company_hint_json={"ticker": "A"},
        restricted_normalized_json=payload,
        observed_at=datetime.fromisoformat("2026-08-09T01:30:32+02:00"),
        supersedes_id=original.id,
        correction_type="CORRECTION",
        content_hash="correction-hash",
        idempotency_key="correction-key",
    )
    service = CeriNormalizationService(
        identity_resolver=CeriIdentityResolver(companies=[CeriCompany(id=1, ticker="A")])
    )

    result = service.normalize(
        db,
        processing_run=processing_run,
        source_records=[original, correction],
    )

    estimates = [row for row in db.added if isinstance(row, CeriEstimateSnapshot)]
    assert result.status == "COMPLETED"
    assert result.normalized == 2
    assert [row.source_record_id for row in estimates] == [935, 2681]
    assert estimates[0].canonical_observation_key == estimates[1].canonical_observation_key


def test_normalization_service_reuses_existing_catalyst_event_for_duplicate_source() -> None:
    existing = CeriCatalystEvent(
        id=99,
        company_id=42,
        category="CONTRACT",
        subtype="award",
        subject_key="mega-contract",
    )
    db = FakeDb(scalar_queue=[existing])
    processing_run = CeriProcessingRun(
        id=6,
        job_type="CERI_NORMALIZE",
        status="RUNNING",
        deterministic_request_key="normalize-2",
    )
    source = CeriSourceRecord(
        id=8,
        provider="manual",
        dataset="catalysts",
        provider_record_id="cat-1",
        company_hint_json={"ticker": "MSFT"},
        raw_json={
            "ticker": "MSFT",
            "category": "CONTRACT",
            "subtype": "award",
            "subject": "Mega contract",
            "source_date": "2026-08-03",
        },
        content_hash="hash",
        idempotency_key="key-2",
    )
    service = CeriNormalizationService(
        identity_resolver=CeriIdentityResolver(companies=[CeriCompany(id=42, ticker="MSFT")])
    )

    result = service.normalize(db, processing_run=processing_run, source_records=[source])

    assert result.normalized == 1
    assert not any(isinstance(row, CeriCatalystEvent) for row in db.added)
    sources = [row for row in db.added if isinstance(row, CeriCatalystSource)]
    assert len(sources) == 1
    assert sources[0].catalyst_event_id == 99


def test_normalization_resume_starts_after_durable_checkpoint() -> None:
    db = FakeDb()
    processing_run = CeriProcessingRun(
        id=8,
        job_type="CERI_NORMALIZE_BATCH",
        status="RUNNING",
        deterministic_request_key="normalize-resume",
        checkpoint_json={"last_source_record_id": 3, "last_record_index": 3},
        read_count=3,
        normalized_count=3,
        failed_count=0,
        warning_count=0,
        counts_json={"quarantined": 0},
        started_at=datetime(2026, 8, 9),
    )
    records = [
        CeriSourceRecord(
            id=index,
            provider="manual",
            dataset="estimates",
            provider_record_id=f"est-{index}",
            company_hint_json={"ticker": "MSFT"},
            raw_json={
                "ticker": "MSFT",
                "metric": "EPS_DILUTED",
                "period_type": "ANNUAL",
                "fiscal_year": 2026 + index,
                "consensus": "10.0",
                "currency": "USD",
            },
            content_hash=f"hash-{index}",
            idempotency_key=f"key-{index}",
        )
        for index in range(1, 9)
    ]
    checkpoints = []
    service = CeriNormalizationService(
        identity_resolver=CeriIdentityResolver(
            companies=[CeriCompany(id=42, ticker="MSFT")]
        )
    )

    result = service.normalize(
        db,
        processing_run=processing_run,
        source_records=records,
        checkpoint_interval=2,
        checkpoint_callback=lambda checkpoint: checkpoints.append(checkpoint),
    )

    estimates = [row for row in db.added if isinstance(row, CeriEstimateSnapshot)]
    assert [row.source_record_id for row in estimates] == [4, 5, 6, 7, 8]
    assert result.read == 8
    assert result.normalized == 8
    assert [checkpoint["last_source_record_id"] for checkpoint in checkpoints] == [5, 7]
    assert processing_run.checkpoint_json["last_source_record_id"] == 8


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
