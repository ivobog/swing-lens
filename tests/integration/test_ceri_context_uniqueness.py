"""Exercise the upgrade, downgrade and concurrent conflict arbitration on PostgreSQL."""

import runpy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError


def test_context_keys_on_postgresql(disposable_postgres_database):
    migration = runpy.run_path(
        str(
            Path(__file__).resolve().parents[2]
            / "alembic/versions/20261116_0096_ceri_native_context.py"
        )
    )
    engine = create_engine(disposable_postgres_database)
    insert = "INSERT INTO ceri_score_snapshots VALUES (62,1,'cfg','v1',%s,%s)"
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE ceri_score_snapshots (
                run_id int, company_id int, config_hash text, calculation_version text,
                calculation_context_id int, controlled_replay_id int,
                CONSTRAINT uq_ceri_score_snapshots_run_company_version
                    UNIQUE(run_id,company_id,config_hash,calculation_version),
                UNIQUE(controlled_replay_id,company_id)
            )""")
        conn.exec_driver_sql(insert, (113, None))
        with Operations.context(MigrationContext.configure(conn)):
            migration["upgrade"]()
            migration["downgrade"]()
            migration["upgrade"]()
    barrier = Barrier(2)

    def attempt():
        with engine.begin() as conn:
            barrier.wait(timeout=15)
            return conn.exec_driver_sql(
                insert + " ON CONFLICT DO NOTHING RETURNING company_id", (116, None)
            ).scalar()

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        assert {future.result() for future in futures} == {1, None}
    with engine.begin() as conn:
        conn.exec_driver_sql(insert, (None, None))
        conn.exec_driver_sql(insert, (113, 45))
        for key in [(None, None), (116, 45), (116, None)]:
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.exec_driver_sql(insert, key)
        with pytest.raises(IntegrityError), conn.begin_nested():
            with Operations.context(MigrationContext.configure(conn)):
                migration["downgrade"]()
        assert conn.exec_driver_sql("SELECT count(*) FROM ceri_score_snapshots").scalar() == 4
        null_upload = insert.replace("VALUES (62,", "VALUES (NULL,")
        conn.exec_driver_sql(null_upload, (116, None))
        with pytest.raises(IntegrityError), conn.begin_nested():
            conn.exec_driver_sql(null_upload, (116, None))
        for _ in range(2):
            conn.exec_driver_sql(null_upload, (None, None))
    engine.dispose()
