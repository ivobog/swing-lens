from __future__ import annotations

from app.models.ceri_tables import CeriIngestionRun, CeriProcessingRun, CeriSourceRecord
from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.background_worker import default_job_handlers
from app.services.ceri.enums import CeriDataset
from app.services.ceri.job_handlers import (
    CERI_NORMALIZE,
    CERI_PROVIDER_INGEST,
    execute_normalize_job,
    execute_provider_ingest_job,
    implemented_ceri_job_handlers,
)
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.providers.manual_provider import ManualCeriProvider


def test_manual_ingestion_persists_source_records_and_counts() -> None:
    db = FakeDb()
    service = _ingestion_service(
        [
            {"provider_record_id": "est-1", "ticker": "MSFT"},
            {"ticker": "MSFT"},
        ]
    )

    result = service.ingest(
        db,
        CeriIngestionRequest(provider="manual", dataset=CeriDataset.ESTIMATES, ticker="MSFT"),
    )

    assert result.status == "PARTIAL"
    assert result.requested == 2
    assert result.inserted == 2
    assert result.quarantined == 1
    assert len([row for row in db.added if isinstance(row, CeriSourceRecord)]) == 2


def test_duplicate_ingestion_request_returns_existing_completed_run() -> None:
    existing = CeriIngestionRun(
        id=7,
        provider="manual",
        dataset="estimates",
        status="COMPLETED",
        request_key="ceri:manual:estimates:MSFT",
        requested_count=1,
        fetched_count=1,
        inserted_count=1,
    )
    db = FakeDb(scalar_queue=[existing])
    service = _ingestion_service([{"provider_record_id": "est-1", "ticker": "MSFT"}])

    result = service.ingest(
        db,
        CeriIngestionRequest(provider="manual", dataset=CeriDataset.ESTIMATES, ticker="MSFT"),
    )

    assert result.ingestion_run_id == 7
    assert result.status == "COMPLETED"
    assert result.inserted == 1


def test_ceri_job_handlers_are_registered_with_default_worker() -> None:
    handlers = default_job_handlers()

    assert CERI_PROVIDER_INGEST in implemented_ceri_job_handlers()
    assert CERI_NORMALIZE in implemented_ceri_job_handlers()
    assert CERI_PROVIDER_INGEST in handlers
    assert CERI_NORMALIZE in handlers


def test_provider_ingest_job_marks_partial_when_ingestion_has_failures() -> None:
    db = FakeDb()
    job = BackgroundJob(
        id=5,
        job_type=CERI_PROVIDER_INGEST,
        status=JobStatus.RUNNING,
        payload_json={"provider": "manual", "dataset": "estimates", "ticker": "MSFT"},
    )
    service = _ingestion_service([{"ticker": "MSFT"}])

    result = execute_provider_ingest_job(db, job, ingestion_service=service)

    assert result["job_type"] == CERI_PROVIDER_INGEST
    assert result["status"] == "PARTIAL"
    assert result["quarantined"] == 1
    assert result["normalize_job_id"] is not None
    assert job.status == JobStatus.PARTIAL
    normalize_jobs = [
        row
        for row in db.added
        if isinstance(row, BackgroundJob) and row.job_type == CERI_NORMALIZE
    ]
    assert len(normalize_jobs) == 1
    assert normalize_jobs[0].payload_json["request_key"].startswith("ceri:normalize:")


def test_normalize_job_persists_processing_run_lineage() -> None:
    db = FakeDb()
    job = BackgroundJob(
        id=6,
        job_type=CERI_NORMALIZE,
        status=JobStatus.RUNNING,
        payload_json={"request_key": "normalize-1", "scope": {"ticker": "MSFT"}},
    )

    result = execute_normalize_job(db, job)

    processing_runs = [row for row in db.added if isinstance(row, CeriProcessingRun)]
    assert len(processing_runs) == 1
    assert processing_runs[0].deterministic_request_key == "normalize-1"
    assert processing_runs[0].checkpoint_json == {"phase": "phase_3_placeholder"}
    assert result["job_type"] == CERI_NORMALIZE


def test_normalize_job_coalesces_existing_processing_run() -> None:
    existing = CeriProcessingRun(
        id=9,
        job_type=CERI_NORMALIZE,
        status="COMPLETED",
        deterministic_request_key="normalize-1",
        normalized_count=3,
    )
    db = FakeDb(scalar_queue=[existing])
    job = BackgroundJob(
        id=7,
        job_type=CERI_NORMALIZE,
        status=JobStatus.RUNNING,
        payload_json={"request_key": "normalize-1"},
    )

    result = execute_normalize_job(db, job)

    assert result["processing_run_id"] == 9
    assert result["normalized"] == 3
    assert result["coalesced"] is True
    assert db.added == []


def _ingestion_service(rows: list[dict]) -> CeriIngestionService:
    provider = ManualCeriProvider({CeriDataset.ESTIMATES: rows})
    registry = CeriProviderRegistry(providers={"manual": provider})
    return CeriIngestionService(registry=registry)


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
