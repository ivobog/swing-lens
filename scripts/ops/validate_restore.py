from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

DEFAULT_CRITICAL_TABLES = (
    "upload_runs",
    "raw_company_rows",
    "price_bars",
    "combined_results",
    "pipeline_runs",
    "background_jobs",
    "setup_signal_snapshots",
    "setup_lifecycle_administrative_audit_events",
    "winner_prediction_snapshots",
    "ceri_source_records",
    "ceri_score_snapshots",
    "ceri_purge_audits",
)

HASH_COLUMN_SUFFIXES = ("hash", "hashes")
DEFAULT_EVIDENCE_HASH_COLUMNS = (
    ("price_bars", "data_hash"),
    ("market_regime_snapshots", "evidence_hash"),
    ("sector_rotation_snapshots", "evidence_hash"),
    ("setup_signal_snapshots", "source_data_hash"),
    ("setup_signal_snapshots", "config_hash"),
    ("winner_prediction_snapshots", "feature_vector_hash"),
    ("winner_prediction_snapshots", "config_hash"),
    ("winner_evidence_manifests", "manifest_hash"),
    ("ceri_source_records", "content_hash"),
    ("ceri_revision_features", "evidence_hash"),
    ("ceri_score_snapshots", "evidence_hash"),
    ("ceri_score_snapshots", "config_hash"),
    ("ceri_purge_audits", "preview_manifest_hash"),
)


@dataclass(frozen=True)
class RestoreValidationReport:
    generated_at: str
    database_url_fingerprint: str
    expected_alembic_heads: list[str]
    current_alembic_revision: str | None
    schema_head_ok: bool
    critical_row_counts: dict[str, int | None]
    missing_critical_tables: list[str]
    foreign_key_violations: list[dict[str, Any]]
    hash_column_checks: dict[str, dict[str, int]]
    passed: bool


def validate_database(
    engine: Engine,
    *,
    expected_alembic_heads: list[str] | None = None,
    critical_tables: tuple[str, ...] = DEFAULT_CRITICAL_TABLES,
    evidence_hash_columns: tuple[tuple[str, str], ...] = DEFAULT_EVIDENCE_HASH_COLUMNS,
) -> RestoreValidationReport:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    current_revision = _current_alembic_revision(engine, table_names)
    expected_heads = expected_alembic_heads or _repository_alembic_heads()
    critical_counts = _critical_row_counts(engine, table_names, critical_tables)
    missing_tables = sorted(table for table in critical_tables if table not in table_names)
    fk_violations = _foreign_key_violations(engine, inspector, table_names)
    hash_checks = _hash_column_checks(engine, inspector, table_names, evidence_hash_columns)
    schema_head_ok = (
        current_revision in expected_heads if expected_heads else current_revision is not None
    )
    passed = (
        schema_head_ok
        and not missing_tables
        and not fk_violations
        and all(check["blank_or_null"] == 0 for check in hash_checks.values())
    )
    return RestoreValidationReport(
        generated_at=datetime.now(UTC).isoformat(),
        database_url_fingerprint=_database_url_fingerprint(str(engine.url)),
        expected_alembic_heads=expected_heads,
        current_alembic_revision=current_revision,
        schema_head_ok=schema_head_ok,
        critical_row_counts=critical_counts,
        missing_critical_tables=missing_tables,
        foreign_key_violations=fk_violations,
        hash_column_checks=hash_checks,
        passed=passed,
    )


def write_report(report: RestoreValidationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a restored SwingLens database.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--expected-alembic-head",
        action="append",
        dest="expected_heads",
        help="Expected Alembic head revision. May be passed more than once.",
    )
    args = parser.parse_args()

    engine = create_engine(args.database_url, pool_pre_ping=True)
    report = validate_database(engine, expected_alembic_heads=args.expected_heads)
    write_report(report, args.report)
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 0 if report.passed else 1


def _current_alembic_revision(engine: Engine, table_names: set[str]) -> str | None:
    if "alembic_version" not in table_names:
        return None
    with engine.connect() as connection:
        return connection.execute(text("select version_num from alembic_version limit 1")).scalar()


def _repository_alembic_heads() -> list[str]:
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    return sorted(script.get_heads())


def _critical_row_counts(
    engine: Engine,
    table_names: set[str],
    critical_tables: tuple[str, ...],
) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    with engine.connect() as connection:
        for table_name in critical_tables:
            if table_name not in table_names:
                counts[table_name] = None
                continue
            count_query = text(f"select count(*) from {_quote_ident(table_name)}")
            counts[table_name] = int(
                connection.execute(count_query).scalar() or 0
            )
    return counts


def _foreign_key_violations(
    engine: Engine,
    inspector,
    table_names: set[str],
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    with engine.connect() as connection:
        for table_name in sorted(table_names):
            for fk in inspector.get_foreign_keys(table_name):
                referred_table = fk.get("referred_table")
                constrained_columns = fk.get("constrained_columns") or []
                referred_columns = fk.get("referred_columns") or []
                if not referred_table or referred_table not in table_names:
                    continue
                if len(constrained_columns) != 1 or len(referred_columns) != 1:
                    continue
                child_column = constrained_columns[0]
                parent_column = referred_columns[0]
                query = text(
                    "select count(*) "
                    f"from {_quote_ident(table_name)} child "
                    f"left join {_quote_ident(referred_table)} parent "
                    f"on child.{_quote_ident(child_column)} = parent.{_quote_ident(parent_column)} "
                    f"where child.{_quote_ident(child_column)} is not null "
                    f"and parent.{_quote_ident(parent_column)} is null"
                )
                count = int(connection.execute(query).scalar() or 0)
                if count:
                    violations.append(
                        {
                            "table": table_name,
                            "column": child_column,
                            "referred_table": referred_table,
                            "referred_column": parent_column,
                            "orphan_count": count,
                        }
                    )
    return violations


def _hash_column_checks(
    engine: Engine,
    inspector,
    table_names: set[str],
    evidence_hash_columns: tuple[tuple[str, str], ...],
) -> dict[str, dict[str, int]]:
    checks: dict[str, dict[str, int]] = {}
    with engine.connect() as connection:
        for table_name, column_name in evidence_hash_columns:
            if table_name not in table_names:
                continue
            table_columns = {str(column["name"]) for column in inspector.get_columns(table_name)}
            if column_name not in table_columns or not _is_hash_column(column_name):
                continue
            key = f"{table_name}.{column_name}"
            count_query = text(f"select count(*) from {_quote_ident(table_name)}")
            total = int(
                connection.execute(count_query).scalar() or 0
            )
            blank_or_null = int(
                connection.execute(
                    text(
                        f"select count(*) from {_quote_ident(table_name)} "
                        f"where {_quote_ident(column_name)} is null "
                        f"or trim(cast({_quote_ident(column_name)} as text)) = ''"
                    )
                ).scalar()
                or 0
            )
            checks[key] = {"rows": total, "blank_or_null": blank_or_null}
    return checks


def _is_hash_column(column_name: str) -> bool:
    normalized = column_name.lower()
    return normalized.endswith(HASH_COLUMN_SUFFIXES) or normalized in {
        "content_hash",
        "data_hash",
    }


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _database_url_fingerprint(database_url: str) -> str:
    if "@" not in database_url:
        return database_url
    scheme_and_auth, host = database_url.rsplit("@", 1)
    scheme = scheme_and_auth.split("://", 1)[0]
    return f"{scheme}://<redacted>@{host}"


if __name__ == "__main__":
    raise SystemExit(main())
