from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database_safety import run_guarded_alembic_upgrade
from app.models.ceri_tables import (
    CeriIngestionRun,
    CeriIngestionRunSourceRecord,
    CeriProviderRequestTelemetry,
    CeriSourceRecord,
)
from app.models.tables import BackgroundJob, PipelineRun, UploadRun
from app.services.background_job_service import JobStatus, enqueue_job
from app.services.background_worker import run_worker_once
from app.services.ceri.batched_job_handlers import execute_provider_ingest_batch_job
from app.services.ceri.batched_workflow import CERI_PROVIDER_INGEST_BATCH
from app.services.ceri.orchestration import CeriIngestionService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.providers.eodhd_client import EodhdClientConfig, EodhdHttpClient
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider
from app.services.scope_refresh_adoption import admit_frozen_operation, bind_semantic_authority
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember


@pytest.mark.parametrize("recovers", [True, False])
def test_eodhd_worker_retry_preserves_success_and_exhausts_budget(
    disposable_postgres_database, monkeypatch, recovers
) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    run_guarded_alembic_upgrade(config, disposable_postgres_database, "head")
    engine = create_engine(disposable_postgres_database)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(
        "app.services.ceri.batched_job_handlers.ceri_flags",
        lambda: SimpleNamespace(provider_ingest=True),
    )
    calls = []

    def transport(url, _timeout):
        symbol = parse_qs(urlsplit(url).query)["s"][0]
        calls.append(symbol)
        if symbol == "MSFT.US" and (not recovers or calls.count(symbol) <= 2):
            raise TimeoutError("The read operation timed out")
        return [
            {
                "id": symbol,
                "date": "2026-10-16T12:00:00Z",
                "title": f"{symbol} raises earnings guidance",
                "symbols": [symbol],
            }
        ]

    service = CeriIngestionService(
        registry=CeriProviderRegistry(
            providers={
                "eodhd": EodhdCeriProvider(
                    client=EodhdHttpClient(
                        EodhdClientConfig(api_key="fixture", max_attempts=2),
                        transport=transport,
                        sleep=lambda _seconds: None,
                    )
                ),
            }
        )
    )
    monkeypatch.setattr(
        "app.services.ceri.batched_job_handlers.CeriIngestionService", lambda **_kwargs: service
    )
    with factory() as db:
        run = UploadRun(filename="eodhd-recovery.csv", row_count=2, status="COMPLETED")
        db.add(run)
        db.flush()
        authority = admit_frozen_operation(
            db,
            operation_kind="full-pipeline-run",
            subject_kind="ticker",
            members=(ScopeMember("TICKER", "AAPL"), ScopeMember("TICKER", "MSFT")),
            cycle_key=f"eodhd-recovery:{run.id}",
            business_cutoff=date(2026, 10, 17),
            provider_source_class="CERI",
            request_type="PROVIDER_INGEST",
            requirements=(AcquisitionRequirement("CERI_SOURCE_RECORDS"),),
            policy_identity="eodhd-recovery-test",
            scope_definition={"run_id": run.id, "tickers": ["AAPL", "MSFT"]},
        )
        workflow = f"ceri:pipeline:{run.id}:recovery"
        pipeline = PipelineRun(
            upload_run_id=run.id,
            status="WAITING_FOR_CERI_COMPLETION",
            current_step="CERI_PROVIDER_INGEST",
            result_json={"ceri_provider_workflow_key": workflow},
        )
        bind_semantic_authority(pipeline, authority)
        db.add(pipeline)
        db.flush()
        job = enqueue_job(
            db,
            CERI_PROVIDER_INGEST_BATCH,
            {
                "workflow_key": workflow,
                "pipeline_run_id": pipeline.id,
                "provider": "eodhd",
                "dataset": "catalysts",
                "tickers": ["AAPL", "MSFT"],
            },
            related_run_id=run.id,
            workflow_key=workflow,
            request_key=f"{workflow}:provider",
            max_retries=1,
        )
        bind_semantic_authority(job, authority)
        db.commit()
        job_id, pipeline_id = job.id, pipeline.id

    def execute():
        assert run_worker_once(
            worker_id="eodhd-recovery-worker",
            session_factory=factory,
            stale_after_seconds=120,
            handlers={CERI_PROVIDER_INGEST_BATCH: execute_provider_ingest_batch_job},
        )

    execute()
    with factory() as db:
        job = db.get(BackgroundJob, job_id)
        assert job.status == JobStatus.QUEUED
        assert job.retry_count == 1
        assert "TimeoutError" in job.error_message
        assert job.operational_metadata_json["ceri_batch"]["completed_tickers"] == ["AAPL"]
        assert db.get(PipelineRun, pipeline_id).status == "WAITING_FOR_CERI_COMPLETION"
        ingestions = list(db.scalars(select(CeriIngestionRun).order_by(CeriIngestionRun.id)))
        assert [row.status for row in ingestions] == ["COMPLETED", "FAILED"]
        assert "read operation timed out" in str(ingestions[1].errors_json)
        assert db.scalar(select(func.count()).select_from(CeriSourceRecord)) == 1
        assert db.scalar(select(func.count()).select_from(CeriIngestionRunSourceRecord)) == 1
        assert db.scalar(select(func.count()).select_from(CeriProviderRequestTelemetry)) == 2
        # Advance only this disposable job's scheduled retry, keeping real claim,
        # execution-token, rollback and detached-control behavior in the worker.
        job.run_after = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    execute()
    with factory() as db:
        job = db.get(BackgroundJob, job_id)
        assert job.status == (JobStatus.COMPLETED if recovers else JobStatus.FAILED)
        assert calls.count("AAPL.US") == 1
        assert calls.count("MSFT.US") == (3 if recovers else 4)
        assert db.scalar(select(func.count()).select_from(CeriIngestionRun)) == 2
        assert db.scalar(select(func.count()).select_from(CeriSourceRecord)) == (
            2 if recovers else 1
        )
        assert db.scalar(select(func.count()).select_from(CeriIngestionRunSourceRecord)) == (
            2 if recovers else 1
        )
        if not recovers:
            assert job.retry_count == 2
            assert "MSFT" in job.error_message
            assert "TimeoutError" in job.error_message
            assert db.get(PipelineRun, pipeline_id).status in {"FAILED", "PARTIAL"}
    engine.dispose()
