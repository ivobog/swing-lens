from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier

from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic, run_guarded_alembic_upgrade
from app.models.tables import (
    SetupSignalSnapshot,
    SetupSignalSnapshotCurrentSelection,
    SetupSignalSnapshotSelectionEvent,
)
from app.services.setup_lifecycle.canonicalization import SetupLifecycleCanonicalizer
from app.services.setup_lifecycle.repository import (
    SetupLifecycleRepository,
    SetupSignalSnapshotWrite,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
AS_OF = date(2026, 9, 4)


def test_0069_migration_and_later_run_preserve_historical_evidence(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database, "0068_market_calc_context")
    engine = create_engine(disposable_postgres_database)
    with engine.begin() as connection:
        run_a = _insert_upload(connection, "run-a.csv")
        run_b = _insert_upload(connection, "run-b.csv")
        snapshot_a = _insert_snapshot(
            connection,
            run_id=run_a,
            source_hash="source-a",
            calculated_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
            legacy_canonical=True,
        )
        snapshot_b = _insert_snapshot(
            connection,
            run_id=run_b,
            source_hash="source-b",
            calculated_at=datetime(2026, 9, 8, 11, tzinfo=UTC),
        )
        before = _row_hash(connection, snapshot_a)

    _upgrade(disposable_postgres_database)

    inspector = inspect(engine)
    assert {
        "setup_signal_snapshot_current_selections",
        "setup_signal_snapshot_selection_events",
    }.issubset(inspector.get_table_names())
    assert {
        "uq_setup_signal_snapshot_current_selection_key",
        "uq_setup_signal_snapshot_current_selection_snapshot",
    } == {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "setup_signal_snapshot_current_selections"
        )
    }
    snapshot_indexes = {
        index["name"]: index for index in inspector.get_indexes("setup_signal_snapshots")
    }
    assert "uq_setup_signal_snapshots_canonical_day" not in snapshot_indexes
    assert snapshot_indexes["idx_setup_signal_snapshots_canonical_at_decision"]["unique"] is False

    with Session(engine) as db:
        bootstrap = db.scalar(select(SetupSignalSnapshotCurrentSelection))
        assert bootstrap is not None
        assert bootstrap.selected_snapshot_id == snapshot_a
        assert bootstrap.selection_reason == "LEGACY_CURRENT_FLAG_BOOTSTRAP"

        canonicalizer = SetupLifecycleCanonicalizer()
        first = canonicalizer.canonicalize_run(db, run_id=run_b, snapshot_ids=(snapshot_b,))
        db.commit()
        assert first.selected_snapshot_ids == (snapshot_b,)
        assert first.changed_snapshot_ids == (snapshot_b,)

    with engine.connect() as connection:
        assert _row_hash(connection, snapshot_a) == before
        old = connection.execute(
            text(
                "SELECT is_canonical, superseded_by_snapshot_id "
                "FROM setup_signal_snapshots WHERE id=:id"
            ),
            {"id": snapshot_a},
        ).one()
        assert old == (True, None)

    repository = SetupLifecycleRepository()
    with Session(engine) as db:
        assert repository.load_snapshots_for_run(db, run_id=run_a)[0].id == snapshot_a
        assert repository.latest_canonical_snapshot(db, ticker="MSFT").id == snapshot_b
        assert db.scalar(select(SetupSignalSnapshotSelectionEvent.id)) is not None
        event_count = db.scalar(
            select(text("count(*)")).select_from(SetupSignalSnapshotSelectionEvent)
        )

        retry = SetupLifecycleCanonicalizer(repository=repository).canonicalize_run(
            db, run_id=run_b, snapshot_ids=(snapshot_b,)
        )
        db.commit()
        assert retry.changed_snapshot_ids == ()
        assert (
            db.scalar(select(text("count(*)")).select_from(SetupSignalSnapshotSelectionEvent))
            == event_count
        )

    with engine.connect() as connection:
        before_retry = _row_hash(connection, snapshot_b)
    with Session(engine) as db:
        retried = repository.upsert_snapshot(
            db,
            SetupSignalSnapshotWrite(
                run_id=run_b,
                source_run_id_text=str(run_b),
                ticker="MSFT",
                timeframe="1d",
                data_as_of_date=AS_OF,
                calculated_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
                origin_type="LIVE_RUN",
                engine_version="slse-test",
                config_version="test-v1",
                config_hash="config-hash",
                source_data_hash="source-b",
                schema_version="snapshot-v1",
                data_quality_label="LOW",
            ),
        )
        assert retried.id == snapshot_b
        db.commit()
    with engine.connect() as connection:
        assert _row_hash(connection, snapshot_b) == before_retry


def test_concurrent_completion_converges_on_deterministic_selection(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with engine.begin() as connection:
        base_run = _insert_upload(connection, "base.csv")
        run_b = _insert_upload(connection, "run-b.csv")
        run_c = _insert_upload(connection, "run-c.csv")
        base_snapshot = _insert_snapshot(
            connection,
            run_id=base_run,
            source_hash="base",
            calculated_at=datetime(2026, 9, 8, 9, tzinfo=UTC),
        )

    with Session(engine) as db:
        SetupLifecycleCanonicalizer().canonicalize_run(
            db, run_id=base_run, snapshot_ids=(base_snapshot,)
        )
        db.commit()

    barrier = Barrier(2)

    def complete(run_id: int, source_hash: str, hour: int) -> int:
        with Session(engine) as db:
            snapshot = _snapshot_model(
                run_id=run_id,
                source_hash=source_hash,
                calculated_at=datetime(2026, 9, 8, hour, tzinfo=UTC),
            )
            db.add(snapshot)
            db.flush()
            snapshot_id = snapshot.id
            barrier.wait(timeout=10)
            SetupLifecycleCanonicalizer().canonicalize_run(
                db, run_id=run_id, snapshot_ids=(snapshot_id,)
            )
            db.commit()
            return snapshot_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_b = executor.submit(complete, run_b, "source-b", 10)
        future_c = executor.submit(complete, run_c, "source-c", 11)
        snapshot_b = future_b.result(timeout=30)
        snapshot_c = future_c.result(timeout=30)

    with Session(engine) as db:
        pointer = db.scalar(select(SetupSignalSnapshotCurrentSelection))
        assert pointer is not None
        assert pointer.selected_snapshot_id == snapshot_c
        assert pointer.selected_snapshot_id != snapshot_b
        assert (
            db.scalar(select(text("count(*)")).select_from(SetupSignalSnapshotCurrentSelection))
            == 1
        )
        assert db.scalar(
            select(text("count(*)")).select_from(SetupSignalSnapshotSelectionEvent)
        ) in {
            2,
            3,
        }


def test_0069_downgrade_is_inspectable_and_restores_legacy_shape(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0069_lifecycle_current_selection"
        )

    config = Config(str(REPO_ROOT / "alembic.ini"))
    configure_guarded_alembic(config, disposable_postgres_database)
    command.downgrade(config, "0068_market_calc_context")

    inspector = inspect(engine)
    assert "setup_signal_snapshot_current_selections" not in inspector.get_table_names()
    assert "setup_signal_snapshot_selection_events" not in inspector.get_table_names()
    snapshot_indexes = {
        index["name"]: index for index in inspector.get_indexes("setup_signal_snapshots")
    }
    assert snapshot_indexes["uq_setup_signal_snapshots_canonical_day"]["unique"] is True
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0068_market_calc_context"
        )


def _snapshot_model(*, run_id: int, source_hash: str, calculated_at: datetime):
    return SetupSignalSnapshot(
        run_id=run_id,
        source_run_id_text=str(run_id),
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=AS_OF,
        calculated_at=calculated_at,
        captured_at=calculated_at,
        origin_type="LIVE_RUN",
        engine_version="slse-test",
        config_version="test-v1",
        config_hash="config-hash",
        source_data_hash=source_hash,
        schema_version="snapshot-v1",
        data_quality_label="HIGH",
        required_feature_coverage=1,
        market_regime_snapshot_id=None,
        sector_rotation_snapshot_id=None,
        warning_flags_json=[],
        source_lineage_json={"latest_bar": {"bar_date": AS_OF.isoformat()}},
    )


def _insert_upload(connection, filename: str) -> int:
    return connection.execute(
        text(
            "INSERT INTO upload_runs (filename, status) "
            "VALUES (:filename, 'COMPLETED') RETURNING id"
        ),
        {"filename": filename},
    ).scalar_one()


def _insert_snapshot(
    connection,
    *,
    run_id: int,
    source_hash: str,
    calculated_at: datetime,
    legacy_canonical: bool = False,
) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO setup_signal_snapshots (
                run_id, source_run_id_text, ticker, timeframe, data_as_of_date,
                calculated_at, captured_at, origin_type, engine_version,
                config_version, config_hash, source_data_hash, schema_version,
                data_quality_label, required_feature_coverage, is_canonical,
                warning_flags_json, source_lineage_json
            ) VALUES (
                :run_id, :run_id_text, 'MSFT', '1d', :as_of, :calculated_at,
                :calculated_at, 'LIVE_RUN', 'slse-test', 'test-v1',
                'config-hash', :source_hash, 'snapshot-v1', 'HIGH', 1,
                :legacy_canonical, '[]'::jsonb,
                jsonb_build_object(
                    'latest_bar', jsonb_build_object('bar_date', CAST(:bar_date AS text))
                )
            ) RETURNING id
            """
        ),
        {
            "run_id": run_id,
            "run_id_text": str(run_id),
            "as_of": AS_OF,
            "bar_date": AS_OF.isoformat(),
            "calculated_at": calculated_at,
            "source_hash": source_hash,
            "legacy_canonical": legacy_canonical,
        },
    ).scalar_one()


def _row_hash(connection, snapshot_id: int) -> str:
    row = connection.execute(
        text("SELECT to_jsonb(t) FROM setup_signal_snapshots t WHERE id=:id"),
        {"id": snapshot_id},
    ).scalar_one()
    return hashlib.sha256(json.dumps(row, default=str, sort_keys=True).encode()).hexdigest()


def _upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    run_guarded_alembic_upgrade(config, database_url, revision)
