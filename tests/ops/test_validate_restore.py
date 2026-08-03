from __future__ import annotations

import json

from sqlalchemy import create_engine, text

from scripts.ops.validate_restore import validate_database, write_report


def test_restore_validation_passes_for_clean_schema(tmp_path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("create table alembic_version (version_num text not null)"))
        connection.execute(text("insert into alembic_version values ('test-head')"))
        connection.execute(
            text("create table parent (id integer primary key, evidence_hash text not null)")
        )
        connection.execute(
            text(
                "create table child ("
                "id integer primary key, "
                "parent_id integer references parent(id), "
                "content_hash text not null)"
            )
        )
        connection.execute(text("insert into parent values (1, 'parent-hash')"))
        connection.execute(text("insert into child values (1, 1, 'child-hash')"))

    report = validate_database(
        engine,
        expected_alembic_heads=["test-head"],
        critical_tables=("parent", "child"),
        evidence_hash_columns=(("parent", "evidence_hash"), ("child", "content_hash")),
    )
    report_path = tmp_path / "restore_report.json"
    write_report(report, report_path)

    assert report.passed is True
    assert report.schema_head_ok is True
    assert report.critical_row_counts == {"parent": 1, "child": 1}
    assert report.foreign_key_violations == []
    assert report.hash_column_checks["parent.evidence_hash"]["blank_or_null"] == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["passed"] is True


def test_restore_validation_reports_orphans_and_blank_hashes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("create table alembic_version (version_num text not null)"))
        connection.execute(text("insert into alembic_version values ('old-head')"))
        connection.execute(text("create table parent (id integer primary key, evidence_hash text)"))
        connection.execute(
            text(
                "create table child ("
                "id integer primary key, "
                "parent_id integer references parent(id), "
                "content_hash text)"
            )
        )
        connection.execute(text("insert into parent values (1, '')"))
        connection.execute(text("insert into child values (1, 404, null)"))

    report = validate_database(
        engine,
        expected_alembic_heads=["test-head"],
        critical_tables=("parent", "child", "missing_table"),
        evidence_hash_columns=(("parent", "evidence_hash"), ("child", "content_hash")),
    )

    assert report.passed is False
    assert report.schema_head_ok is False
    assert report.missing_critical_tables == ["missing_table"]
    assert report.foreign_key_violations == [
        {
            "table": "child",
            "column": "parent_id",
            "referred_table": "parent",
            "referred_column": "id",
            "orphan_count": 1,
        }
    ]
    assert report.hash_column_checks["parent.evidence_hash"]["blank_or_null"] == 1
    assert report.hash_column_checks["child.content_hash"]["blank_or_null"] == 1
