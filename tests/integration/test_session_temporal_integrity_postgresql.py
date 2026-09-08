from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import BackgroundJob, PipelineRun, UploadRun
from app.services.market_calculation_context_service import (
    create_pipeline_market_context,
    resolve_pipeline_market_context,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_market_calculation_context_migration_on_disposable_postgresql(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database, "0067_worker_quiesce")
    engine = create_engine(disposable_postgres_database)
    before = inspect(engine)
    assert "market_calculation_contexts" not in before.get_table_names()

    _upgrade(disposable_postgres_database)
    schema = inspect(engine)
    assert "market_calculation_contexts" in schema.get_table_names()
    with engine.connect() as connection:
        assert tuple(
            connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
        ) == ("0068_market_calc_context",)

    context_columns = {
        item["name"]: item for item in schema.get_columns("market_calculation_contexts")
    }
    assert context_columns["cutoff_at"]["nullable"] is False
    assert context_columns["latest_completed_session"]["nullable"] is False
    assert {item["name"] for item in schema.get_indexes("market_calculation_contexts")} >= {
        "idx_market_calculation_contexts_cutoff",
        "idx_market_calculation_contexts_upload_session",
    }

    for table_name in (
        "technical_scores",
        "market_regime_snapshots",
        "sector_rotation_snapshots",
        "setup_signal_snapshots",
    ):
        columns = {item["name"]: item for item in schema.get_columns(table_name)}
        for column_name in (
            "calculation_context_id",
            "calculation_cutoff_at",
            "input_as_of_session",
            "calendar_version",
        ):
            assert columns[column_name]["nullable"] is True

    with engine.begin() as connection:
        inserted = connection.execute(
            text(
                """
                INSERT INTO market_calculation_contexts (
                    pipeline_run_id, upload_run_id, cutoff_at, exchange_timezone,
                    latest_completed_session, daily_bar_ready_at, calendar_version,
                    bar_readiness_version, cutoff_reason
                ) VALUES (
                    NULL, NULL, :cutoff_at, 'America/New_York', :session,
                    :ready_at, 'swinglens-us-equities-v1',
                    'daily-close-plus-15m-v1', 'DISPOSABLE_MIGRATION_TEST'
                ) RETURNING id
                """
            ),
            {
                "cutoff_at": datetime(2026, 9, 8, 20, 15, tzinfo=UTC),
                "session": date(2026, 9, 8),
                "ready_at": datetime(2026, 9, 8, 20, 15, tzinfo=UTC),
            },
        ).scalar_one()
        assert inserted > 0


def test_pipeline_job_context_round_trip_on_disposable_postgresql(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    frozen_at = datetime(2026, 9, 8, 10, 6, 38, tzinfo=UTC)

    with Session(engine) as db:
        upload = UploadRun(filename="context-round-trip.csv", status="COMPLETED")
        db.add(upload)
        db.flush()
        pipeline = PipelineRun(upload_run_id=upload.id, status="QUEUED")
        db.add(pipeline)
        db.flush()
        cutoff = create_pipeline_market_context(db, pipeline, cutoff_at=frozen_at)
        payload = {
            "run_id": upload.id,
            "workflow_key": f"ceri:pipeline:{upload.id}:round-trip",
            "calculation_context_id": cutoff.context_id,
            "cutoff_at": cutoff.cutoff_at.isoformat(),
            "as_of_session": cutoff.latest_completed_session.isoformat(),
            "calendar_version": cutoff.calendar_version,
        }
        job = BackgroundJob(
            job_type="CERI_CAPTURE_RUN",
            related_run_id=upload.id,
            workflow_key=payload["workflow_key"],
            request_key="context-round-trip",
            status="QUEUED",
            payload_json=payload,
        )
        db.add(job)
        db.commit()
        job_id = job.id

    with Session(engine) as restarted_worker_db:
        restored = restarted_worker_db.get(BackgroundJob, job_id)
        assert restored is not None
        payload = restored.payload_json
        resolved = resolve_pipeline_market_context(
            restarted_worker_db,
            calculation_context_id=int(payload["calculation_context_id"]),
            upload_run_id=int(payload["run_id"]),
            expected_cutoff_at=datetime.fromisoformat(payload["cutoff_at"]),
            expected_latest_completed_session=date.fromisoformat(payload["as_of_session"]),
            expected_calendar_version=str(payload["calendar_version"]),
        )

    assert resolved == cutoff


def _upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    run_guarded_alembic_upgrade(config, database_url, revision)
