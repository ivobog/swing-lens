from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import PriceBar
from app.services.price_bar_evidence import price_bar_immutable_evidence_hash

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_0070_upgrade_from_0069_adds_nullable_pit_provenance(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database, "0069_lifecycle_current_selection")
    _upgrade(disposable_postgres_database)

    engine = create_engine(disposable_postgres_database)
    inspector = inspect(engine)
    columns = {
        column["name"]: column for column in inspector.get_columns("ceri_price_response_features")
    }
    assert columns["calculation_cutoff_at"]["nullable"] is True
    assert columns["calculation_context_id"]["nullable"] is True
    assert columns["calendar_version"]["nullable"] is True
    indexes = {index["name"] for index in inspector.get_indexes("ceri_price_response_features")}
    assert "ix_ceri_price_response_features_cutoff" in indexes
    assert "ix_ceri_price_response_features_context" in indexes
    foreign_keys = {
        constraint["name"]: constraint
        for constraint in inspector.get_foreign_keys("ceri_price_response_features")
    }
    assert (
        foreign_keys["fk_ceri_price_response_features_calculation_context"]["referred_table"]
        == "market_calculation_contexts"
    )


def test_0072_enforces_pipeline_context_and_preserves_legacy_unknown(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    inspector = inspect(engine)
    for table in (
        "ceri_revision_features",
        "ceri_derived_features",
        "ceri_feature_build_states",
        "ceri_price_response_features",
    ):
        columns = {item["name"] for item in inspector.get_columns(table)}
        assert {"calculation_context_id", "ownership_mode"} <= columns
        checks = {item["name"] for item in inspector.get_check_constraints(table)}
        assert any(name and name.endswith("pipeline_context") for name in checks)
        assert any(name and name.endswith("ownership_mode") for name in checks)

    with engine.begin() as connection:
        company_id = connection.scalar(
            text("INSERT INTO ceri_companies (ticker,exchange) VALUES ('TBLA','US') RETURNING id")
        )
    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO ceri_price_response_features "
                    "(company_id,ticker,event_type,evidence_hash,event_key,config_version,"
                    "config_hash,calculation_version,ownership_mode) VALUES "
                    "(:company_id,'TBLA','NONE','hash','missing-context','v','h','c','PIPELINE')"
                ),
                {"company_id": company_id},
            )
        transaction.rollback()

    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO ceri_price_response_features "
                    "(company_id,ticker,event_type,evidence_hash,event_key,config_version,"
                    "config_hash,calculation_version,ownership_mode) VALUES "
                    "(:company_id,'TBLA','NONE','hash','invalid-owner','v','h','c','UNKNOWN')"
                ),
                {"company_id": company_id},
            )
        transaction.rollback()


def test_price_bar_immutable_hash_survives_postgresql_roundtrip(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    observed = datetime(2026, 9, 9, 9, 31, tzinfo=UTC)
    with Session(engine, expire_on_commit=False) as db:
        row = PriceBar(
            ticker="DRS",
            bar_date=date(2026, 9, 8),
            timeframe="1 day",
            open=Decimal("6.10"),
            high=Decimal("6.40"),
            low=Decimal("6.00"),
            close=Decimal("6.30"),
            volume=Decimal("1000000"),
            source="IB",
            what_to_show="TRADES",
            first_seen_at=observed,
            last_seen_at=observed,
            revision_count=0,
            data_hash="bar-data-hash",
        )
        db.add(row)
        db.flush()
        expected = price_bar_immutable_evidence_hash(row)
        row_id = row.id
        db.commit()
    with Session(engine) as db:
        assert price_bar_immutable_evidence_hash(db.get(PriceBar, row_id)) == expected


def _upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    run_guarded_alembic_upgrade(config, database_url, revision)
