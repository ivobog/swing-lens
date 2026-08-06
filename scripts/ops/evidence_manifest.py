from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import Engine

EVIDENCE_MANIFEST_VERSION = "swinglens-evidence-manifest-v1"
DEFAULT_EVIDENCE_TABLES = (
    "upload_runs",
    "raw_company_rows",
    "price_bars",
    "fundamental_scores",
    "technical_scores",
    "combined_results",
    "pipeline_runs",
    "pipeline_steps",
    "background_jobs",
    "market_regime_snapshots",
    "sector_rotation_snapshots",
    "setup_signal_snapshots",
    "setup_lifecycle_administrative_audit_events",
    "winner_prediction_snapshots",
    "winner_evidence_manifests",
    "ceri_companies",
    "ceri_source_records",
    "ceri_revision_features",
    "ceri_score_snapshots",
    "ceri_purge_audits",
)


@dataclass(frozen=True)
class TableEvidenceManifest:
    row_count: int
    content_sha256: str
    primary_key_columns: list[str]


@dataclass(frozen=True)
class DatabaseEvidenceManifest:
    manifest_version: str
    generated_at: str
    database_url_fingerprint: str
    alembic_revision: str | None
    tables: dict[str, TableEvidenceManifest]


@dataclass(frozen=True)
class EvidenceComparisonReport:
    generated_at: str
    database_url_fingerprint: str
    expected_alembic_revision: str | None
    actual_alembic_revision: str | None
    missing_tables: list[str]
    row_count_mismatches: dict[str, dict[str, int]]
    content_hash_mismatches: dict[str, dict[str, str]]
    passed: bool


def capture_database_manifest(
    engine: Engine,
    *,
    table_names: tuple[str, ...] = DEFAULT_EVIDENCE_TABLES,
) -> DatabaseEvidenceManifest:
    inspector = inspect(engine)
    available_tables = set(inspector.get_table_names())
    missing_tables = sorted(set(table_names) - available_tables)
    if missing_tables:
        missing = ", ".join(missing_tables)
        raise ValueError(f"evidence manifest tables are missing: {missing}")

    manifests = {
        table_name: _capture_table(engine, table_name)
        for table_name in table_names
    }
    return DatabaseEvidenceManifest(
        manifest_version=EVIDENCE_MANIFEST_VERSION,
        generated_at=datetime.now().astimezone().isoformat(),
        database_url_fingerprint=_database_url_fingerprint(str(engine.url)),
        alembic_revision=_alembic_revision(engine, available_tables),
        tables=manifests,
    )


def compare_database_to_manifest(
    engine: Engine,
    expected: DatabaseEvidenceManifest,
) -> EvidenceComparisonReport:
    inspector = inspect(engine)
    available_tables = set(inspector.get_table_names())
    missing_tables = sorted(set(expected.tables) - available_tables)
    actual_revision = _alembic_revision(engine, available_tables)
    row_count_mismatches: dict[str, dict[str, int]] = {}
    content_hash_mismatches: dict[str, dict[str, str]] = {}

    for table_name, expected_table in expected.tables.items():
        if table_name in missing_tables:
            continue
        actual_table = _capture_table(engine, table_name)
        if actual_table.row_count != expected_table.row_count:
            row_count_mismatches[table_name] = {
                "expected": expected_table.row_count,
                "actual": actual_table.row_count,
            }
        if actual_table.content_sha256 != expected_table.content_sha256:
            content_hash_mismatches[table_name] = {
                "expected": expected_table.content_sha256,
                "actual": actual_table.content_sha256,
            }

    passed = (
        actual_revision == expected.alembic_revision
        and not missing_tables
        and not row_count_mismatches
        and not content_hash_mismatches
    )
    return EvidenceComparisonReport(
        generated_at=datetime.now().astimezone().isoformat(),
        database_url_fingerprint=_database_url_fingerprint(str(engine.url)),
        expected_alembic_revision=expected.alembic_revision,
        actual_alembic_revision=actual_revision,
        missing_tables=missing_tables,
        row_count_mismatches=row_count_mismatches,
        content_hash_mismatches=content_hash_mismatches,
        passed=passed,
    )


def write_manifest(manifest: DatabaseEvidenceManifest, path: Path) -> None:
    _write_json(asdict(manifest), path)


def read_manifest(path: Path) -> DatabaseEvidenceManifest:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("manifest_version") != EVIDENCE_MANIFEST_VERSION:
        raise ValueError("unsupported evidence manifest version")
    tables = {
        table_name: TableEvidenceManifest(**table_payload)
        for table_name, table_payload in payload["tables"].items()
    }
    return DatabaseEvidenceManifest(
        manifest_version=payload["manifest_version"],
        generated_at=payload["generated_at"],
        database_url_fingerprint=payload["database_url_fingerprint"],
        alembic_revision=payload.get("alembic_revision"),
        tables=tables,
    )


def write_comparison_report(report: EvidenceComparisonReport, path: Path) -> None:
    _write_json(asdict(report), path)


def _capture_table(engine: Engine, table_name: str) -> TableEvidenceManifest:
    metadata = MetaData()
    table = Table(table_name, metadata, autoload_with=engine)
    primary_keys = [column.name for column in table.primary_key.columns]
    if not primary_keys:
        raise ValueError(f"evidence table has no primary key: {table_name}")

    digest = hashlib.sha256()
    row_count = 0
    statement = select(table).order_by(*(table.c[column] for column in primary_keys))
    with engine.connect() as connection:
        for row in connection.execute(statement).mappings():
            encoded = json.dumps(
                _canonicalize(dict(row)),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            digest.update(encoded)
            digest.update(b"\n")
            row_count += 1
    return TableEvidenceManifest(
        row_count=row_count,
        content_sha256=digest.hexdigest(),
        primary_key_columns=primary_keys,
    )


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, bytes):
        return {"$bytes": value.hex()}
    return value


def _alembic_revision(engine: Engine, table_names: set[str]) -> str | None:
    if "alembic_version" not in table_names:
        return None
    with engine.connect() as connection:
        return connection.execute(text("select version_num from alembic_version limit 1")).scalar()


def _database_url_fingerprint(database_url: str) -> str:
    if "@" not in database_url:
        return database_url
    scheme_and_auth, host = database_url.rsplit("@", 1)
    scheme = scheme_and_auth.split("://", 1)[0]
    return f"{scheme}://<redacted>@{host}"


def _write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture or verify deterministic SwingLens evidence manifests."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture")
    capture.add_argument("--database-url", required=True)
    capture.add_argument("--report", type=Path, required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--database-url", required=True)
    verify.add_argument("--expected", type=Path, required=True)
    verify.add_argument("--report", type=Path, required=True)

    args = parser.parse_args()
    engine = create_engine(args.database_url, pool_pre_ping=True)
    if args.command == "capture":
        manifest = capture_database_manifest(engine)
        write_manifest(manifest, args.report)
        print(json.dumps(asdict(manifest), indent=2, sort_keys=True))
        return 0

    expected = read_manifest(args.expected)
    comparison = compare_database_to_manifest(engine, expected)
    write_comparison_report(comparison, args.report)
    print(json.dumps(asdict(comparison), indent=2, sort_keys=True))
    return 0 if comparison.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
