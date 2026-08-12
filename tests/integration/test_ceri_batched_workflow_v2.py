from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from threading import Barrier

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriChangeEvent,
    CeriCompany,
    CeriEstimateSnapshot,
    CeriIngestionRun,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import BackgroundJob, RawCompanyRow, UploadRun
from app.services.background_job_service import JobStatus, enqueue_job
from app.services.ceri.batched_job_handlers import (
    execute_feature_batch_job,
    execute_normalize_batch_job,
    execute_run_finalize_job,
)
from app.services.ceri.batched_workflow import (
    CERI_FEATURE_BATCH,
    CERI_NORMALIZE_BATCH,
    CERI_PROVIDER_INGEST_BATCH,
    CERI_RUN_FINALIZE,
)
from app.services.ceri.job_handlers import (
    CERI_ALERT_REBUILD,
    CERI_CAPTURE_RUN,
    CERI_CHANGE_DETECTION,
    CERI_REBUILD_FEATURES,
    execute_alert_rebuild_job,
    execute_capture_run_job,
    execute_change_detection_job,
    execute_normalize_job,
    execute_rebuild_features_job,
)
from app.settings import Settings


def test_concurrent_finalizers_create_one_capture_in_postgresql(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    workflow_key = "ceri:pipeline:95:config-a"
    with Session(engine) as db:
        db.add_all(
            [
                BackgroundJob(
                    job_type=CERI_FEATURE_BATCH,
                    workflow_key=workflow_key,
                    request_key=f"{workflow_key}:feature:{index}",
                    related_run_id=95,
                    status=JobStatus.COMPLETED,
                    priority=130,
                    payload_json={},
                    max_retries=3,
                )
                for index in (1, 2)
            ]
        )
        finalizer = BackgroundJob(
            job_type=CERI_RUN_FINALIZE,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:finalize",
            related_run_id=95,
            status=JobStatus.COMPLETED,
            priority=140,
            payload_json={
                "workflow_key": workflow_key,
                "run_id": 95,
                "expected_feature_batches": 2,
            },
            max_retries=3,
        )
        db.add(finalizer)
        db.commit()
        finalizer_id = finalizer.id

    monkeypatch.setattr(
        "app.services.ceri.batched_job_handlers.ceri_flags",
        lambda: type("Flags", (), {"enabled": True})(),
    )
    barrier = Barrier(2)

    def finalize() -> int | None:
        with Session(engine) as db:
            job = db.get(BackgroundJob, finalizer_id)
            barrier.wait(timeout=10)
            result = execute_run_finalize_job(db, job)
            db.commit()
            return result["capture_job_id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        capture_ids = list(executor.map(lambda _index: finalize(), range(2)))

    with Session(engine) as db:
        capture_count = db.scalar(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.workflow_key == workflow_key,
                BackgroundJob.job_type == "CERI_CAPTURE_RUN",
            )
        )
        capture = db.scalar(
            select(BackgroundJob).where(
                BackgroundJob.workflow_key == workflow_key,
                BackgroundJob.job_type == "CERI_CAPTURE_RUN",
            )
        )
    engine.dispose()

    assert capture_count == 1
    assert len(set(capture_ids)) == 1
    assert capture.request_key == f"{workflow_key}:capture"


def test_legacy_enqueue_remains_available_while_live_migration_waits(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database, revision="0034_slse_dashboard_indexes")
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        job = enqueue_job(
            db,
            "FULL_PIPELINE",
            {"pipeline_run_id": 1},
            request_key="full-pipeline:pre-migration-probe",
        )
        db.commit()
        job_id = job.id
    with engine.connect() as connection:
        persisted = connection.exec_driver_sql(
            "select job_type, request_key from background_jobs where id = %s",
            (job_id,),
        ).one()
    engine.dispose()

    assert persisted == (
        "FULL_PIPELINE",
        "full-pipeline:pre-migration-probe",
    )


def test_batched_workflow_outputs_match_legacy_workflow_in_postgresql(
    disposable_postgres_database_factory: Callable[[], AbstractContextManager[str]],
    monkeypatch,
) -> None:
    enabled_settings = Settings(
        _env_file=None,
        ceri_enabled=True,
        ceri_provider_ingest_enabled=True,
        ceri_run_capture_enabled=True,
        ceri_alerts_enabled=True,
        ceri_legacy_pipeline_scheduling_enabled=False,
        ceri_batched_workflow_enabled=True,
    )
    monkeypatch.setattr(
        "app.services.ceri.feature_flags.get_settings",
        lambda: enabled_settings,
    )
    fixed_now = datetime(2026, 8, 12, 16, 30, tzinfo=UTC)
    monkeypatch.setattr("app.services.ceri.capture_service._utcnow", lambda: fixed_now)

    with disposable_postgres_database_factory() as legacy_url:
        with disposable_postgres_database_factory() as batched_url:
            _upgrade(legacy_url)
            _upgrade(batched_url)
            legacy = _execute_legacy_fixture(legacy_url)
            batched = _execute_batched_fixture(batched_url)

    assert batched == legacy
    assert len(batched["source_records"]) == 4
    assert len(batched["normalized"]) == 4
    assert len(batched["features"]) >= 3
    assert len(batched["snapshots"]) == 1
    assert len(batched["changes"]) == 1
    assert len(batched["alerts"]) == 1


def _upgrade(database_url: str, *, revision: str = "head") -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        check=True,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )


def _execute_legacy_fixture(database_url: str) -> dict:
    engine = create_engine(database_url)
    with Session(engine) as db:
        run_id, ingestion_run_id = _seed_fixture(db, request_key="legacy:ingest:MSFT")
        normalize = BackgroundJob(
            job_type="CERI_NORMALIZE",
            related_run_id=run_id,
            request_key="legacy:normalize:MSFT",
            status=JobStatus.RUNNING,
            priority=70,
            payload_json={
                "request_key": "legacy:normalize:MSFT",
                "ingestion_run_id": ingestion_run_id,
                "provider": "eodhd",
                "dataset": "estimates",
                "ticker": "MSFT",
                "run_id": run_id,
                "scope": {"ticker": "MSFT", "run_id": run_id},
            },
            max_retries=3,
        )
        db.add(normalize)
        db.flush()
        _execute_handler(db, normalize, execute_normalize_job)
        feature = db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == CERI_REBUILD_FEATURES)
        )
        _execute_handler(db, feature, execute_rebuild_features_job)
        capture = db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == CERI_CAPTURE_RUN)
        )
        _execute_handler(db, capture, execute_capture_run_job)
        change = db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == CERI_CHANGE_DETECTION)
        )
        _execute_handler(db, change, execute_change_detection_job)
        alert = db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == CERI_ALERT_REBUILD)
        )
        if alert is not None:
            _execute_handler(db, alert, execute_alert_rebuild_job)
        fingerprint = _parity_fingerprint(db)
    engine.dispose()
    return fingerprint


def _execute_batched_fixture(database_url: str) -> dict:
    engine = create_engine(database_url)
    workflow_key = "ceri:pipeline:1:fixture-config"
    with Session(engine) as db:
        run_id, _ = _seed_fixture(
            db,
            request_key=f"{workflow_key}:ingest:eodhd:estimates:MSFT",
        )
        provider = BackgroundJob(
            job_type=CERI_PROVIDER_INGEST_BATCH,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:provider:eodhd:estimates:0001",
            related_run_id=run_id,
            status=JobStatus.COMPLETED,
            priority=80,
            payload_json={},
            max_retries=3,
        )
        normalize = BackgroundJob(
            job_type=CERI_NORMALIZE_BATCH,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:normalize:eodhd:estimates:0001",
            related_run_id=run_id,
            status=JobStatus.RUNNING,
            priority=81,
            payload_json={
                "workflow_key": workflow_key,
                "provider": "eodhd",
                "dataset": "estimates",
                "tickers": ["MSFT"],
                "run_id": run_id,
                "checkpoint_interval": 1,
            },
            max_retries=3,
        )
        db.add_all([provider, normalize])
        db.commit()
        _execute_handler(db, normalize, execute_normalize_batch_job)
        feature = BackgroundJob(
            job_type=CERI_FEATURE_BATCH,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:feature:0001",
            related_run_id=run_id,
            status=JobStatus.RUNNING,
            priority=130,
            payload_json={
                "workflow_key": workflow_key,
                "tickers": ["MSFT"],
                "run_id": run_id,
                "expected_normalization_batches": 1,
                "checkpoint_interval": 1,
            },
            max_retries=3,
        )
        db.add(feature)
        db.commit()
        _execute_handler(db, feature, execute_feature_batch_job)
        finalizer = BackgroundJob(
            job_type=CERI_RUN_FINALIZE,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:finalize",
            related_run_id=run_id,
            status=JobStatus.RUNNING,
            priority=140,
            payload_json={
                "workflow_key": workflow_key,
                "run_id": run_id,
                "expected_feature_batches": 1,
            },
            max_retries=3,
        )
        db.add(finalizer)
        db.commit()
        _execute_handler(db, finalizer, execute_run_finalize_job)
        capture = db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == CERI_CAPTURE_RUN)
        )
        _execute_handler(db, capture, execute_capture_run_job)
        change = db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == CERI_CHANGE_DETECTION)
        )
        _execute_handler(db, change, execute_change_detection_job)
        alert = db.scalar(
            select(BackgroundJob).where(BackgroundJob.job_type == CERI_ALERT_REBUILD)
        )
        _execute_handler(db, alert, execute_alert_rebuild_job)
        effects_before_retry = _effect_counts(db)
        _execute_handler(db, capture, execute_capture_run_job)
        _execute_handler(db, change, execute_change_detection_job)
        _execute_handler(db, alert, execute_alert_rebuild_job)
        assert _effect_counts(db) == effects_before_retry
        fingerprint = _parity_fingerprint(db)
    engine.dispose()
    return fingerprint


def _seed_fixture(db: Session, *, request_key: str) -> tuple[int, int]:
    run = UploadRun(filename="ceri-parity.csv", row_count=1, status="COMPLETED")
    company = CeriCompany(
        ticker="MSFT",
        exchange="US",
        current_provider_ids_json={"eodhd": "MSFT.US"},
    )
    db.add_all([run, company])
    db.flush()
    db.add(
        RawCompanyRow(
            run_id=run.id,
            row_number=1,
            ticker="MSFT",
            company_name="Microsoft",
            sector="Technology",
            raw_json={
                "ticker": "MSFT",
                "fundamental_score": 8,
                "technical_score": 7,
                "market_regime": "Bull trend",
            },
        )
    )
    ingestion = CeriIngestionRun(
        provider="eodhd",
        provider_terms_version="fixture-1",
        dataset="estimates",
        status="COMPLETED",
        request_key=request_key,
        scope_json={"ticker": "MSFT", "run_id": run.id},
        requested_count=4,
        fetched_count=4,
        inserted_count=4,
        completed_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
    )
    db.add(ingestion)
    db.flush()
    observations = (
        ("2026-04-30T20:00:00+00:00", "10.0"),
        ("2026-07-01T20:00:00+00:00", "11.0"),
        ("2026-08-01T20:00:00+00:00", "12.0"),
        ("2026-08-11T20:00:00+00:00", "13.0"),
    )
    for index, (effective_at, consensus) in enumerate(observations, start=1):
        db.add(
            CeriSourceRecord(
                ingestion_run_id=ingestion.id,
                provider="eodhd",
                provider_terms_version="fixture-1",
                dataset="estimates",
                provider_record_id=f"MSFT-estimate-{index}",
                company_hint_json={"ticker": "MSFT", "exchange": "US"},
                restricted_normalized_json={
                    "ticker": "MSFT",
                    "metric": "EPS_DILUTED",
                    "period_type": "NEXT_FISCAL_YEAR",
                    "fiscal_period_end": "2027-06-30",
                    "consensus": consensus,
                    "high": str(float(consensus) + 1),
                    "low": str(float(consensus) - 1),
                    "analyst_count": 12,
                    "upward_count": 8,
                    "downward_count": 2,
                    "currency": "USD",
                    "effective_at": effective_at,
                },
                observed_at=datetime.fromisoformat(effective_at),
                content_hash=f"content-{index}",
                idempotency_key=f"fixture-estimate-{index}",
                export_policy="exportable",
                redistribution_allowed=False,
                purge_eligible=False,
            )
        )
    db.commit()
    return run.id, ingestion.id


def _execute_handler(db: Session, job: BackgroundJob, handler) -> dict:
    result = handler(db, job)
    job.status = (
        JobStatus.PARTIAL
        if result and result.get("status") == JobStatus.PARTIAL
        else JobStatus.COMPLETED
    )
    db.commit()
    return result or {}


def _parity_fingerprint(db: Session) -> dict:
    sources = list(db.scalars(select(CeriSourceRecord).order_by(CeriSourceRecord.id)))
    normalized = list(db.scalars(select(CeriEstimateSnapshot).order_by(CeriEstimateSnapshot.id)))
    features = list(db.scalars(select(CeriRevisionFeature).order_by(CeriRevisionFeature.id)))
    snapshots = list(db.scalars(select(CeriScoreSnapshot).order_by(CeriScoreSnapshot.id)))
    changes = list(db.scalars(select(CeriChangeEvent).order_by(CeriChangeEvent.id)))
    alerts = list(db.scalars(select(CeriAlertEvent).order_by(CeriAlertEvent.id)))
    return {
        "source_records": [
            (
                row.provider,
                row.dataset,
                row.provider_record_id,
                row.restricted_normalized_json,
                row.content_hash,
                row.idempotency_key,
                row.observed_at.isoformat() if row.observed_at else None,
                row.quarantine_reason,
            )
            for row in sources
        ],
        "normalized": [
            (
                row.metric,
                row.period_type,
                row.fiscal_period_end.isoformat(),
                str(row.consensus),
                str(row.high),
                str(row.low),
                row.analyst_count,
                row.upward_count,
                row.downward_count,
                row.effective_session.isoformat() if row.effective_session else None,
                row.canonical_observation_key,
                row.quality_flags_json,
            )
            for row in normalized
        ],
        "features": [
            (
                row.metric,
                row.period_key,
                row.as_of_session.isoformat(),
                row.window_days,
                str(row.absolute_change),
                str(row.pct_change),
                str(row.acceleration),
                row.revision_confidence_label,
                row.warnings_json,
                row.unavailable_reason,
                row.evidence_hash,
            )
            for row in features
        ],
        "snapshots": [
            (
                row.ticker,
                row.as_of_session.isoformat(),
                row.opportunity_score,
                row.event_risk_score,
                row.data_confidence,
                row.coverage_pct,
                row.posture,
                row.alignment_flags_json,
                row.component_json,
                row.reasons_json,
                row.warnings_json,
                row.evidence_hash,
            )
            for row in snapshots
        ],
        "changes": [
            (
                row.change_type,
                row.severity,
                row.delta_json,
                row.dedup_key,
            )
            for row in changes
        ],
        "alerts": [
            (
                row.event_key,
                row.ticker,
                row.severity,
                row.status,
                row.evidence_json,
            )
            for row in alerts
        ],
    }


def _effect_counts(db: Session) -> tuple[int, int, int]:
    return (
        int(db.scalar(select(func.count(CeriScoreSnapshot.id))) or 0),
        int(db.scalar(select(func.count(CeriChangeEvent.id))) or 0),
        int(db.scalar(select(func.count(CeriAlertEvent.id))) or 0),
    )
