from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus
from app.services.market_data_prewarm_service import (
    MARKET_DATA_PREWARM,
    record_pipeline_prewarm_reuse,
    request_active_prewarm_preemption,
    resolve_pipeline_prewarm_context,
)
from app.settings import Settings


def test_running_prewarm_receives_foreground_cancellation_request(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)
    with Session(engine) as db:
        job = BackgroundJob(
            job_type=MARKET_DATA_PREWARM,
            status=JobStatus.RUNNING,
            priority=200,
            payload_json={"tickers": ["AAPL"]},
            max_retries=3,
            run_after=now,
            requested_cancel=False,
        )
        db.add(job)
        db.commit()
        job_id = job.id

        requested = request_active_prewarm_preemption(
            db,
            pipeline_run_id=44,
            settings=Settings(_env_file=None, market_data_prewarm_enabled=True),
            now=now,
        )
        db.commit()

        reloaded = db.get(BackgroundJob, job_id)
        assert requested == [job_id]
        assert reloaded.requested_cancel is True
        assert reloaded.payload_json["foreground_preemption"] == {
            "pipeline_run_id": 44,
            "requested_at": "2026-08-12T14:00:00+00:00",
            "deadline_at": "2026-08-12T14:00:45+00:00",
        }
    engine.dispose()


def test_pipeline_context_records_actual_prewarmer_reuse(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime(2026, 8, 12, 18, 0, tzinfo=UTC)
    with Session(engine) as db:
        job = BackgroundJob(
            job_type=MARKET_DATA_PREWARM,
            status=JobStatus.COMPLETED,
            priority=200,
            payload_json={},
            result_json={
                "tickers": ["AAPL", "MSFT"],
                "effective_session": "2026-08-11",
                "coverage_ready_tickers": ["AAPL", "MSFT"],
                "fetched_tickers": ["AAPL"],
                "already_current_tickers": ["MSFT"],
            },
            max_retries=3,
            run_after=now - timedelta(minutes=6),
            completed_at=now - timedelta(minutes=5),
        )
        db.add(job)
        latest_verification = BackgroundJob(
            job_type=MARKET_DATA_PREWARM,
            status=JobStatus.COMPLETED,
            priority=200,
            payload_json={},
            result_json={
                "tickers": ["AAPL", "MSFT"],
                "effective_session": "2026-08-11",
                "coverage_ready_tickers": ["AAPL", "MSFT"],
                "fetched_tickers": [],
                "already_current_tickers": ["AAPL", "MSFT"],
            },
            max_retries=3,
            run_after=now - timedelta(minutes=3),
            completed_at=now - timedelta(minutes=2),
        )
        db.add(latest_verification)
        db.commit()

        context = resolve_pipeline_prewarm_context(db, ["AAPL", "MSFT"], now=now)
        record_pipeline_prewarm_reuse(
            db,
            context,
            pipeline_run_id=55,
            reused_tickers=["AAPL"],
            now=now,
        )
        db.commit()

        reloaded = db.get(BackgroundJob, latest_verification.id)
        assert context.fresh_for_session is True
        assert context.job_id == latest_verification.id
        assert context.age_seconds == 120
        assert context.covered_tickers == ("AAPL", "MSFT")
        # A later no-op verification must not mask the same-session job that
        # originally populated AAPL for foreground reuse attribution.
        assert context.fetched_tickers == ("AAPL",)
        assert reloaded.result_json["foreground_reuse_ticker_count"] == 1
        assert reloaded.result_json["foreground_reuse_observations"][0]["pipeline_run_id"] == 55
    engine.dispose()


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
