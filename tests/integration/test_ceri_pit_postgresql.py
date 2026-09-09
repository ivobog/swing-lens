from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.database_safety import run_guarded_alembic_upgrade

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


def _upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    run_guarded_alembic_upgrade(config, database_url, revision)
