from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import Base
from app.services.price_series_version_service import maintain_price_series_versions
from app.services.technical_artifact_cache import (
    ARTIFACT_SCHEMA_VERSION,
    build_local_artifact_key,
    canonical_json,
    config_hash,
)


def test_artifact_schema_tables_are_registered() -> None:
    assert "price_series_versions" in Base.metadata.tables
    assert "technical_feature_artifacts" in Base.metadata.tables


def test_local_artifact_signature_is_canonical_and_revision_aware() -> None:
    first = build_local_artifact_key(
        ticker="msft",
        adjusted_series_version=12,
        trades_series_version=15,
        feature_config_hash=config_hash({"ema": 20, "rsi": 14}),
        scoring_config_hash="scoring-a",
        technical_engine_version="3.2.0",
    )
    reordered = build_local_artifact_key(
        ticker="MSFT",
        adjusted_series_version=12,
        trades_series_version=15,
        feature_config_hash=config_hash({"rsi": 14, "ema": 20}),
        scoring_config_hash="scoring-a",
        technical_engine_version="3.2.0",
    )
    revised = build_local_artifact_key(
        ticker="MSFT",
        adjusted_series_version=13,
        trades_series_version=15,
        feature_config_hash=first.input_versions["feature_config_hash"],
        scoring_config_hash="scoring-a",
        technical_engine_version="3.2.0",
    )

    assert first.input_signature == reordered.input_signature
    assert first.input_signature != revised.input_signature
    assert first.input_versions["adjusted_series_version"] == 12
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert ARTIFACT_SCHEMA_VERSION == "1"


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("adjusted_series_version", 13),
        ("trades_series_version", 16),
        ("feature_config_hash", "feature-b"),
        ("technical_engine_version", "3.3.0"),
        ("artifact_schema_version", "2"),
        ("timeframe", "1 hour"),
    ],
)
def test_local_artifact_signature_invalidates_every_local_dependency(
    changed_field: str,
    changed_value: object,
) -> None:
    inputs = {
        "ticker": "MSFT",
        "timeframe": "1 day",
        "adjusted_series_version": 12,
        "trades_series_version": 15,
        "feature_config_hash": "feature-a",
        "scoring_config_hash": "scoring-a",
        "technical_engine_version": "3.2.0",
    }
    baseline = build_local_artifact_key(**inputs)
    inputs[changed_field] = changed_value

    changed = build_local_artifact_key(**inputs)

    assert changed.input_signature != baseline.input_signature


def test_local_artifact_signature_ignores_final_scoring_identity() -> None:
    inputs = {
        "ticker": "MSFT",
        "adjusted_series_version": 12,
        "trades_series_version": 15,
        "feature_config_hash": "feature-a",
        "technical_engine_version": "3.2.0",
    }
    baseline = build_local_artifact_key(**inputs, scoring_config_hash="v5-baseline")
    reweighted = build_local_artifact_key(**inputs, scoring_config_hash="v5-reweighted")

    assert reweighted.input_signature == baseline.input_signature
    assert reweighted.feature_config_hash == baseline.feature_config_hash
    assert reweighted.scoring_config_hash != baseline.scoring_config_hash


def test_series_version_maintenance_is_a_noop_without_changed_series() -> None:
    class UnusedDb:
        def flush(self):
            raise AssertionError("database should not be touched")

    assert maintain_price_series_versions(UnusedDb(), [], set()) == 0


def test_series_version_maintenance_advances_once_per_changed_series() -> None:
    bars = [
        SimpleNamespace(
            ticker="MSFT",
            timeframe="1 day",
            what_to_show="TRADES",
            bar_date=date(2026, 8, 1),
        )
    ]

    class FakeDb:
        def __init__(self) -> None:
            self.series = None

        def flush(self) -> None:
            pass

        def scalar(self, _statement):
            return self.series

        def scalars(self, _statement):
            return bars

        def add(self, row) -> None:
            self.series = row

    db = FakeDb()
    identity = {("MSFT", "1 day", "TRADES")}

    assert maintain_price_series_versions(db, bars, identity) == 1
    assert db.series.series_version == 1
    assert db.series.bar_count == 1

    assert maintain_price_series_versions(db, bars, identity) == 1
    assert db.series.series_version == 2


def test_phase4_migration_follows_current_head() -> None:
    migration = Path(
        "alembic/versions/20260805_0026_add_technical_artifact_cache.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0026_technical_artifact_cache"' in migration
    assert 'down_revision: str | None = "0025_winner_combined_result_set_null"' in migration
    assert "price_series_versions" in migration
    assert "technical_feature_artifacts" in migration
