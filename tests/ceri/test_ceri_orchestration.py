from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from app.models.ceri_tables import CeriProcessingRun, CeriPurgeAudit
from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.background_worker import default_job_handlers
from app.services.ceri.backfill_service import CeriBackfillRequest, CeriBackfillService
from app.services.ceri.capture_service import CeriRunCaptureResult
from app.services.ceri.feature_flags import CeriFeatureFlags
from app.services.ceri.job_handlers import (
    CERI_ALERT_REBUILD,
    CERI_BACKFILL,
    CERI_CAPTURE_RUN,
    CERI_CHANGE_DETECTION,
    CERI_NORMALIZE,
    CERI_PROVIDER_INGEST,
    CERI_PURGE_LICENSED_DATA,
    CERI_REBUILD_FEATURES,
    execute_alert_rebuild_job,
    execute_backfill_job,
    execute_capture_run_job,
    execute_purge_licensed_data_job,
    implemented_ceri_job_handlers,
)
from app.services.market_clock_service import MarketClockService


def test_phase_7_job_handlers_are_registered() -> None:
    handlers = implemented_ceri_job_handlers()
    default_handlers = default_job_handlers()

    assert set(handlers) >= {
        CERI_PROVIDER_INGEST,
        CERI_NORMALIZE,
        CERI_REBUILD_FEATURES,
        CERI_CAPTURE_RUN,
        CERI_CHANGE_DETECTION,
        CERI_BACKFILL,
        CERI_ALERT_REBUILD,
        CERI_PURGE_LICENSED_DATA,
    }
    for job_type in handlers:
        assert default_handlers[job_type] is handlers[job_type]


def test_backfill_request_is_idempotent_and_records_checkpoint() -> None:
    db = FakeDb()
    service = CeriBackfillService()
    request = CeriBackfillRequest(
        provider="manual",
        dataset="estimates",
        ticker="MSFT",
        start=date(2026, 1, 1),
        end=date(2026, 8, 1),
    )

    first = service.run(db, request)
    db.scalar_queue = [next(row for row in db.added if isinstance(row, CeriProcessingRun))]
    second = service.run(db, request)

    assert first.status == "COMPLETED"
    assert first.checkpoints["ticker"] == "MSFT"
    assert second.skipped == 1


def test_capture_run_job_marks_processing_run_and_returns_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_ceri(monkeypatch)
    db = FakeDb()
    job = BackgroundJob(
        id=3,
        job_type=CERI_CAPTURE_RUN,
        status=JobStatus.RUNNING,
        payload_json={"run_id": 7},
    )

    result = execute_capture_run_job(
        db,
        job,
        capture_service=FakeCaptureService(),
    )

    assert result["score_snapshots"] == 1
    assert result["change_events"] == 2
    assert result["alerts"] == 1
    assert any(isinstance(row, CeriProcessingRun) for row in db.added)


def test_pipeline_capture_job_uses_serialized_frozen_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_ceri(monkeypatch)
    cutoff = (
        MarketClockService()
        .cutoff_for(
            datetime(2026, 9, 8, 10, 6, tzinfo=UTC),
            reason="FULL_PIPELINE_FROZEN_AT_ENQUEUE",
        )
        .with_context_id(42)
    )
    monkeypatch.setattr(
        "app.services.ceri.job_handlers.resolve_pipeline_market_context",
        lambda *_args, **_kwargs: cutoff,
        raising=False,
    )
    service = FrozenContextCaptureService()
    job = BackgroundJob(
        id=4,
        job_type=CERI_CAPTURE_RUN,
        status=JobStatus.RUNNING,
        related_run_id=7,
        workflow_key="ceri:pipeline:7:test",
        payload_json={
            "run_id": 7,
            "workflow_key": "ceri:pipeline:7:test",
            "calculation_context_id": 42,
            "cutoff_at": cutoff.cutoff_at.isoformat(),
            "as_of_session": cutoff.latest_completed_session.isoformat(),
            "calendar_version": cutoff.calendar_version,
        },
    )

    execute_capture_run_job(FakeDb(), job, capture_service=service)

    assert service.market_cutoff == cutoff


def test_pipeline_capture_job_fails_closed_when_context_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_ceri(monkeypatch)
    job = BackgroundJob(
        id=5,
        job_type=CERI_CAPTURE_RUN,
        status=JobStatus.RUNNING,
        related_run_id=7,
        workflow_key="ceri:pipeline:7:test",
        payload_json={"run_id": 7, "workflow_key": "ceri:pipeline:7:test"},
    )

    with pytest.raises(ValueError, match="missing frozen context fields"):
        execute_capture_run_job(FakeDb(), job, capture_service=FrozenContextCaptureService())


def test_pipeline_capture_retry_after_network_delay_and_worker_restart_reuses_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_ceri(monkeypatch)
    cutoff = (
        MarketClockService()
        .cutoff_for(
            datetime(2026, 9, 8, 10, 6, tzinfo=UTC),
            reason="FULL_PIPELINE_FROZEN_AT_ENQUEUE",
        )
        .with_context_id(42)
    )
    monkeypatch.setattr(
        "app.services.ceri.job_handlers.resolve_pipeline_market_context",
        lambda *_args, **_kwargs: cutoff,
    )
    serialized = json.dumps(
        {
            "run_id": 7,
            "workflow_key": "ceri:pipeline:7:network-delay",
            "calculation_context_id": 42,
            "cutoff_at": cutoff.cutoff_at.isoformat(),
            "as_of_session": cutoff.latest_completed_session.isoformat(),
            "calendar_version": cutoff.calendar_version,
        }
    )
    first = BackgroundJob(
        id=6,
        job_type=CERI_CAPTURE_RUN,
        status=JobStatus.RUNNING,
        related_run_id=7,
        workflow_key="ceri:pipeline:7:network-delay",
        payload_json=json.loads(serialized),
    )
    with pytest.raises(ConnectionError, match="provider unavailable"):
        execute_capture_run_job(FakeDb(), first, capture_service=TransientFailureCaptureService())

    delayed_execution_at = cutoff.cutoff_at + timedelta(hours=24)
    delayed_wall_clock_context = MarketClockService().cutoff_for(
        delayed_execution_at,
        reason="DELAYED_EXECUTION_WOULD_BE_WRONG",
    )
    restarted_service = FrozenContextCaptureService()
    retry = BackgroundJob(
        id=6,
        job_type=CERI_CAPTURE_RUN,
        status=JobStatus.RUNNING,
        retry_count=1,
        related_run_id=7,
        workflow_key="ceri:pipeline:7:network-delay",
        payload_json=json.loads(serialized),
    )
    execute_capture_run_job(FakeDb(), retry, capture_service=restarted_service)

    assert retry.payload_json == first.payload_json
    assert delayed_wall_clock_context.cutoff_at != cutoff.cutoff_at
    assert restarted_service.market_cutoff == cutoff


def test_backfill_job_uses_scope_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_ceri(monkeypatch)
    db = FakeDb()
    job = BackgroundJob(
        id=4,
        job_type=CERI_BACKFILL,
        status=JobStatus.RUNNING,
        payload_json={"provider": "manual", "dataset": "estimates", "ticker": "MSFT"},
    )

    result = execute_backfill_job(db, job)

    assert result["job_type"] == CERI_BACKFILL
    assert result["status"] == "COMPLETED"


def test_alert_rebuild_job_records_terminal_processing_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_ceri(monkeypatch)
    db = FakeDb()
    job = BackgroundJob(
        id=5,
        job_type=CERI_ALERT_REBUILD,
        status=JobStatus.RUNNING,
        payload_json={"alerts_enabled": True},
    )

    result = execute_alert_rebuild_job(db, job)
    processing_run = next(row for row in db.added if isinstance(row, CeriProcessingRun))

    assert result["job_type"] == CERI_ALERT_REBUILD
    assert result["status"] == "COMPLETED"
    assert result["processing_run_id"] == processing_run.id
    assert processing_run.status == "COMPLETED"
    assert processing_run.alert_event_count == 0


def test_purge_job_records_processing_run_and_purge_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_ceri(monkeypatch)
    db = FakeDb()
    job = BackgroundJob(
        id=6,
        job_type=CERI_PURGE_LICENSED_DATA,
        status=JobStatus.RUNNING,
        payload_json={
            "provider": "eodhd",
            "license_scope": "personal",
        },
    )

    result = execute_purge_licensed_data_job(db, job)
    processing_run = next(row for row in db.added if isinstance(row, CeriProcessingRun))
    purge_audit = next(row for row in db.added if isinstance(row, CeriPurgeAudit))

    assert result["job_type"] == CERI_PURGE_LICENSED_DATA
    assert result["status"] == "PREVIEWED"
    assert result["processing_run_id"] == processing_run.id
    assert result["purge_audit_id"] == purge_audit.id
    assert processing_run.status == "COMPLETED"
    assert processing_run.checkpoint_json["preview_manifest_hash"]


class FakeCaptureService:
    def capture_run(self, _db, _run_id):
        return CeriRunCaptureResult(score_snapshots=1, change_events=2, alerts=1)


class FrozenContextCaptureService:
    def __init__(self) -> None:
        self.market_cutoff = None

    def capture_run(self, _db, _run_id, *, market_cutoff=None):
        self.market_cutoff = market_cutoff
        return CeriRunCaptureResult(score_snapshots=1)


class TransientFailureCaptureService:
    def capture_run(self, _db, _run_id, *, market_cutoff=None):
        assert market_cutoff is not None
        raise ConnectionError("provider unavailable")


def _enable_ceri(monkeypatch: pytest.MonkeyPatch) -> None:
    flags = CeriFeatureFlags(True, True, True, True, True, True, True)
    monkeypatch.setattr("app.services.ceri.job_handlers.ceri_flags", lambda: flags)


class FakeDb:
    def __init__(self, scalar_queue=None) -> None:
        self.scalar_queue = list(scalar_queue or [])
        self.added = []
        self.next_id = 1

    def scalar(self, _statement):
        if self.scalar_queue:
            return self.scalar_queue.pop(0)
        return None

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1
