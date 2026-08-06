from __future__ import annotations

import json

from sqlalchemy import create_engine, text

from scripts.ops.evidence_manifest import (
    EVIDENCE_MANIFEST_VERSION,
    capture_database_manifest,
    compare_database_to_manifest,
    read_manifest,
    write_manifest,
)


def test_evidence_manifest_is_deterministic_and_round_trips(tmp_path) -> None:
    engine = _engine_with_evidence()

    first = capture_database_manifest(engine, table_names=("evidence",))
    second = capture_database_manifest(engine, table_names=("evidence",))
    manifest_path = tmp_path / "evidence.json"
    write_manifest(first, manifest_path)

    assert first.manifest_version == EVIDENCE_MANIFEST_VERSION
    assert first.tables == second.tables
    assert first.tables["evidence"].row_count == 2
    assert read_manifest(manifest_path).tables == first.tables
    assert "secret" not in manifest_path.read_text(encoding="utf-8")


def test_evidence_manifest_reports_row_and_content_mismatches() -> None:
    source = _engine_with_evidence()
    restored = _engine_with_evidence()
    expected = capture_database_manifest(source, table_names=("evidence",))

    with restored.begin() as connection:
        connection.execute(
            text("update evidence set payload = :payload where id = 1"),
            {"payload": json.dumps({"ticker": "ALTERED"})},
        )
        connection.execute(
            text("insert into evidence values (3, :payload, :amount)"),
            {"payload": json.dumps({"ticker": "EXTRA"}), "amount": "1.00"},
        )

    report = compare_database_to_manifest(restored, expected)

    assert report.passed is False
    assert report.row_count_mismatches == {"evidence": {"expected": 2, "actual": 3}}
    assert set(report.content_hash_mismatches) == {"evidence"}


def test_evidence_manifest_rejects_missing_tables() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    try:
        capture_database_manifest(engine, table_names=("missing",))
    except ValueError as exc:
        assert str(exc) == "evidence manifest tables are missing: missing"
    else:
        raise AssertionError("missing evidence tables must fail manifest capture")


def _engine_with_evidence():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("create table alembic_version (version_num text not null)"))
        connection.execute(text("insert into alembic_version values ('test-head')"))
        connection.execute(
            text(
                "create table evidence ("
                "id integer primary key, payload text not null, amount numeric not null)"
            )
        )
        connection.execute(
            text("insert into evidence values (2, :payload, :amount)"),
            {"payload": json.dumps({"ticker": "MSFT", "unicode": "Zürich"}), "amount": "8.20"},
        )
        connection.execute(
            text("insert into evidence values (1, :payload, :amount)"),
            {"payload": json.dumps({"ticker": "AAPL"}), "amount": "7.10"},
        )
    return engine
