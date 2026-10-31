from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from alembic.config import Config
from market_sector_rotation_helpers import complete_preview
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from test_market_sector_intelligence_rotation_storage import capture, counts

from app.database_safety import run_guarded_alembic_upgrade
from app.models.market_sector_intelligence_tables import (
    MarketSectorRotationRow,
    MarketSectorRotationSnapshot,
)
from app.models.tables import SectorRotationSnapshot


def retained_schema(connection, tables):
    """Read baseline columns, constraints, indexes and triggers without changing them."""
    queries = {
        "columns": """SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
            a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname=current_schema() AND c.relname::text=ANY(:tables)
            AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum""",
        "constraints": """SELECT c.relname, k.conname, pg_get_constraintdef(k.oid)
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND c.relname::text=ANY(:tables)
            ORDER BY c.relname,k.conname""",
        "indexes": """SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname=current_schema() AND tablename=ANY(:tables)
            ORDER BY tablename,indexname""",
        "triggers": """SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid)
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND c.relname::text=ANY(:tables)
            AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""",
    }
    return {
        name: [tuple(row) for row in connection.execute(text(query), {"tables": sorted(tables)})]
        for name, query in queries.items()
    }


@pytest.mark.integration
def test_full_postgresql_upgrade_capture_concurrency_and_immutability(
    disposable_postgres_database, record_property,
):
    url = disposable_postgres_database
    cfg = Config("alembic.ini")
    run_guarded_alembic_upgrade(cfg, url, "0092_phase2_eligibility_ledgers")
    engine = create_engine(url)
    try:
        before = set(inspect(engine).get_table_names())
        with engine.begin() as connection:
            prior = dict(connection.execute(
                SectorRotationSnapshot.__table__.insert().values(
                    as_of_date=date(2026, 10, 20), calculation_version="MSI_INTEGRATION_SENTINEL",
                    mode="universe", config_hash="0" * 64,
                ).returning(SectorRotationSnapshot.__table__)
            ).mappings().one())
            schema_before = retained_schema(connection, before)
        run_guarded_alembic_upgrade(cfg, url, "0093_market_sector_rotation")
        after = set(inspect(engine).get_table_names())
        assert after - before == {
            MarketSectorRotationSnapshot.__tablename__,
            MarketSectorRotationRow.__tablename__,
        }
        with engine.connect() as connection:
            assert retained_schema(connection, before) == schema_before
            assert dict(connection.execute(
                SectorRotationSnapshot.__table__.select().where(
                    SectorRotationSnapshot.id == prior["id"]
                )
            ).mappings().one()) == prior
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0093_market_sector_rotation"
            )
        preview = complete_preview()
        start = Barrier(2)

        def invoke(_):
            start.wait(timeout=10)
            return capture(engine, preview)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(invoke, range(2)))
        assert {result["status"] for result in outcomes} == {"CREATED", "ALREADY_EXISTS"}
        assert len({result["snapshot_id"] for result in outcomes}) == 1
        assert counts(engine) == (1, 11)
        with engine.connect() as connection:
            assert dict(connection.execute(
                SectorRotationSnapshot.__table__.select().where(
                    SectorRotationSnapshot.id == prior["id"]
                )
            ).mappings().one()) == prior
        record_property("baseline_tables_preserved", len(before))
        record_property("baseline_constraints_preserved", len(schema_before["constraints"]))
        record_property("legacy_snapshot_preserved", True)
        for table in (
            MarketSectorRotationSnapshot.__tablename__,
            MarketSectorRotationRow.__tablename__,
        ):
            with pytest.raises(DBAPIError, match="IMMUTABLE"), engine.begin() as connection:
                connection.exec_driver_sql(f"UPDATE {table} SET id=id")
    finally:
        engine.dispose()
