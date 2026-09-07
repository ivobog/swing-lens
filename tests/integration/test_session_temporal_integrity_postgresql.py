from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.database_safety import run_guarded_alembic_upgrade

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


def _upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    run_guarded_alembic_upgrade(config, database_url, revision)
