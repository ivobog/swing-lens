from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_VERSIONS = REPO_ROOT / "alembic" / "versions"
EXPECTED_HEAD = "0023_immutable_snapshot_evidence"
DEFAULT_ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"
LIVE_MODEL_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+app\.models(?:\.|\s)", re.MULTILINE)


@dataclass(frozen=True)
class MigrationImportViolation:
    path: Path
    line_number: int
    line: str


def _admin_database_url() -> str:
    return os.environ.get("SWINGLENS_TEST_POSTGRES_ADMIN_URL", DEFAULT_ADMIN_URL)


def _database_url_for(clean_db_name: str) -> str:
    return f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{clean_db_name}"


def _connect_admin_or_skip() -> psycopg.Connection:
    try:
        return psycopg.connect(_admin_database_url(), autocommit=True)
    except psycopg.Error as exc:
        pytest.skip(f"PostgreSQL admin database unavailable: {exc}")


def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def find_live_model_imports() -> list[MigrationImportViolation]:
    violations: list[MigrationImportViolation] = []
    for path in sorted(ALEMBIC_VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if not LIVE_MODEL_IMPORT_RE.search(text):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if LIVE_MODEL_IMPORT_RE.match(line):
                violations.append(MigrationImportViolation(path, line_number, line.strip()))
    return violations


def test_clean_postgresql_database_can_upgrade_to_alembic_head() -> None:
    clean_db_name = f"swinglens_pytest_migration_{uuid.uuid4().hex[:12]}"
    with _connect_admin_or_skip() as conn:
        try:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(clean_db_name)))

            database_url = _database_url_for(clean_db_name)
            upgrade = _run_alembic(database_url, "upgrade", "head")
            assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

            current = _run_alembic(database_url, "current")
            assert current.returncode == 0, current.stdout + current.stderr
            assert EXPECTED_HEAD in current.stdout
        finally:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(clean_db_name)
                )
            )


def test_migration_import_lint_rejects_live_model_imports() -> None:
    violations = find_live_model_imports()

    assert violations == []
