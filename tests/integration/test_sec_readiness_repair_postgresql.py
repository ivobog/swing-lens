from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany, CeriSecSyncState
from app.models.tables import BackgroundJob, PipelineRun, PipelineStep, RawCompanyRow, UploadRun
from app.services.background_job_service import JobStatus
from app.services.ceri.sec.processor_lifecycle import certify_processor, promote_processor
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.provider import SecCeriProvider
from app.services.ceri.sec.readiness_repair import (
    SEC_READINESS_REPAIR_JOB_TYPE,
    SecReadinessRepairUnresolved,
    execute_sec_readiness_repair,
    schedule_sec_readiness_repair,
)
from app.services.pipeline_service import PipelineStatus, PipelineStepStatus
from app.settings import SecDocumentIncrementalMode, Settings


class _RepairSecClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace()
        self.requests = 0
        self.failures = 0
        self.last_success_at = None
        self.download_calls = 0

    def company_tickers(self):
        self.requests += 1
        return {"0": {"ticker": "TEST", "cik_str": 123456}}

    def submissions(self, _cik):
        self.requests += 1
        return {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "accessionNumber": ["0000123456-26-000001"],
                    "primaryDocument": ["test-8k.htm"],
                    "filingDate": ["2026-08-01"],
                }
            }
        }

    def archive_document(self, *_args):
        self.requests += 1
        self.download_calls += 1
        return "The company expects full year revenue guidance of $100 to $110 million."

    def stats(self):
        return SimpleNamespace(
            requests=self.requests,
            filing_document_requests=self.download_calls,
            bytes_downloaded=100 * self.download_calls,
        )


def test_repair_resolves_identity_bootstraps_and_resumes_same_pipeline(
    disposable_postgres_database: str,
    tmp_path,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    signature = sec_guidance_processor_signature()
    settings = Settings(
        _env_file=None,
        cache_dir=tmp_path / "cache",
        sec_document_incremental_mode=SecDocumentIncrementalMode.ACTIVE,
    )
    provider = SecCeriProvider(client=_RepairSecClient())

    with Session(engine) as db:
        certify_processor(
            db,
            processor_signature=signature,
            evidence={"test": True},
            actor="pytest",
        )
        promote_processor(db, processor_signature=signature, actor="pytest")
        run = UploadRun(filename="test.csv", row_count=1, status="COMPLETED")
        db.add(run)
        db.flush()
        db.add(
            RawCompanyRow(
                run_id=run.id,
                row_number=1,
                ticker="TEST",
                raw_json={"ticker": "TEST"},
            )
        )
        pipeline = PipelineRun(
            upload_run_id=run.id,
            status=PipelineStatus.RUNNING,
            current_step="VALIDATING_RUN",
            result_json={},
        )
        db.add(pipeline)
        db.flush()
        db.add(
            PipelineStep(
                pipeline_run_id=pipeline.id,
                step_name="VALIDATING_RUN",
                step_order=1,
                status=PipelineStepStatus.RUNNING,
                retry_count=0,
            )
        )
        db.flush()
        repair = schedule_sec_readiness_repair(
            db,
            pipeline=pipeline,
            diagnostics={
                "processor": {
                    "active_signature": signature,
                    "deployed_signature": signature,
                },
                "readiness": {
                    "processor_signature": signature,
                    "requested_tickers": 1,
                    "ready_tickers": 0,
                    "counts": {"UNRESOLVED_MAPPING": 1},
                },
            },
        )
        db.commit()
        repair.status = JobStatus.RUNNING
        repair.worker_id = "pytest-worker"
        db.commit()

        result = execute_sec_readiness_repair(
            db,
            repair,
            settings=settings,
            provider=provider,
        )
        db.commit()

        assert result["status"] == "COMPLETED"
        assert db.get(PipelineRun, pipeline.id).status == PipelineStatus.PENDING
        assert db.scalar(select(CeriCompany).where(CeriCompany.ticker == "TEST")).cik == (
            "0000123456"
        )
        assert db.scalar(
            select(CeriSecSyncState).where(
                CeriSecSyncState.cik == "0000123456",
                CeriSecSyncState.processor_signature == signature,
            )
        )
        full_jobs = list(
            db.scalars(
                select(BackgroundJob).where(BackgroundJob.job_type == "FULL_PIPELINE")
            ).all()
        )
        assert len(full_jobs) == 1
        assert full_jobs[0].payload_json == {
            "pipeline_run_id": pipeline.id,
            "resume_from_step": "VALIDATING_RUN",
        }
        assert provider.client.download_calls == 1

        repeated = execute_sec_readiness_repair(
            db,
            repair,
            settings=settings,
            provider=provider,
        )
        db.commit()
        assert repeated["resume_job_id"] == full_jobs[0].id
        assert repeated["telemetry"]["documents_downloaded"] == 1
        assert db.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == SEC_READINESS_REPAIR_JOB_TYPE
            )
        ).id == repair.id
        assert len(
            list(
                db.scalars(
                    select(BackgroundJob).where(BackgroundJob.job_type == "FULL_PIPELINE")
                ).all()
            )
        ) == 1
        assert provider.client.download_calls == 1
    engine.dispose()


class _PartiallyAmbiguousSecClient(_RepairSecClient):
    def company_tickers(self):
        self.requests += 1
        return {
            "0": {"ticker": "GOOD", "cik_str": 123456},
            "1": {"ticker": "BAD", "cik_str": 111111},
            "2": {"ticker": "BAD", "cik_str": 222222},
        }


def test_ambiguous_identity_does_not_stop_other_safe_repairs(
    disposable_postgres_database: str,
    tmp_path,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    signature = sec_guidance_processor_signature()
    settings = Settings(
        _env_file=None,
        cache_dir=tmp_path / "cache",
        sec_document_incremental_mode=SecDocumentIncrementalMode.ACTIVE,
    )
    provider = SecCeriProvider(client=_PartiallyAmbiguousSecClient())

    with Session(engine) as db:
        certify_processor(
            db,
            processor_signature=signature,
            evidence={"test": True},
            actor="pytest",
        )
        promote_processor(db, processor_signature=signature, actor="pytest")
        run = UploadRun(filename="ambiguous.csv", row_count=2, status="COMPLETED")
        db.add(run)
        db.flush()
        db.add_all(
            [
                RawCompanyRow(
                    run_id=run.id,
                    row_number=index,
                    ticker=ticker,
                    raw_json={"ticker": ticker},
                )
                for index, ticker in enumerate(("BAD", "GOOD"), start=1)
            ]
        )
        pipeline = PipelineRun(
            upload_run_id=run.id,
            status=PipelineStatus.RUNNING,
            current_step="VALIDATING_RUN",
            result_json={},
        )
        db.add(pipeline)
        db.flush()
        db.add(
            PipelineStep(
                pipeline_run_id=pipeline.id,
                step_name="VALIDATING_RUN",
                step_order=1,
                status=PipelineStepStatus.RUNNING,
                retry_count=0,
            )
        )
        repair = schedule_sec_readiness_repair(
            db,
            pipeline=pipeline,
            diagnostics={
                "processor": {"active_signature": signature},
                "readiness": {
                    "processor_signature": signature,
                    "requested_tickers": 2,
                    "ready_tickers": 0,
                    "counts": {"UNRESOLVED_MAPPING": 2},
                },
            },
        )
        db.commit()
        repair.status = JobStatus.RUNNING
        repair.worker_id = "pytest-worker"
        db.commit()

        with pytest.raises(SecReadinessRepairUnresolved, match="BAD"):
            execute_sec_readiness_repair(
                db,
                repair,
                settings=settings,
                provider=provider,
            )

        good = db.scalar(select(CeriCompany).where(CeriCompany.ticker == "GOOD"))
        assert good is not None and good.cik == "0000123456"
        assert db.scalar(select(CeriCompany).where(CeriCompany.ticker == "BAD")) is None
        assert db.scalar(
            select(CeriSecSyncState).where(
                CeriSecSyncState.cik == "0000123456",
                CeriSecSyncState.processor_signature == signature,
            )
        )
        blocked = db.get(PipelineRun, pipeline.id)
        assert blocked.status == PipelineStatus.BLOCKED
        assert blocked.result_json["blocked_reason"] == "SEC_IDENTITY_UNRESOLVED"
        unresolved = blocked.result_json["blocked_diagnostics"]["unresolved_tickers"]
        assert set(unresolved) == {"BAD"}
        assert provider.client.download_calls == 1
        assert not list(
            db.scalars(
                select(BackgroundJob).where(BackgroundJob.job_type == "FULL_PIPELINE")
            ).all()
        )
    engine.dispose()


def _upgrade(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
