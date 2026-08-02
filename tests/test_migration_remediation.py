from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.services.background_job_service import (
    JobLeaseLost,
    JobStatus,
    claim_next_job,
    enqueue_job,
    mark_job_completed,
    recover_stale_jobs,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_VERSIONS = REPO_ROOT / "alembic" / "versions"
EXPECTED_HEAD = "0024_background_job_request_keys"
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


def test_postgresql_duplicate_request_key_enqueue_coalesces_across_sessions() -> None:
    clean_db_name = f"swinglens_pytest_enqueue_{uuid.uuid4().hex[:12]}"
    with _connect_admin_or_skip() as conn:
        try:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(clean_db_name)))
            database_url = _database_url_for(clean_db_name)

            upgrade = _run_alembic(database_url, "upgrade", "head")
            assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

            engine = create_engine(database_url)
            try:
                with Session(engine, expire_on_commit=False) as first_session:
                    first_job = enqueue_job(
                        first_session,
                        job_type="FULL_PIPELINE",
                        payload={"pipeline_run_id": 1},
                        request_key="full-pipeline:run:7",
                    )
                    first_session.commit()

                with Session(engine, expire_on_commit=False) as second_session:
                    second_job = enqueue_job(
                        second_session,
                        job_type="FULL_PIPELINE",
                        payload={"pipeline_run_id": 2},
                        request_key="full-pipeline:run:7",
                    )
                    second_session.commit()

                assert second_job.id == first_job.id
                assert second_job.__dict__.get("_coalesced") is True

                with Session(engine) as check_session:
                    active_count = check_session.scalar(
                        select(func.count())
                        .select_from(BackgroundJob)
                        .where(BackgroundJob.job_type == "FULL_PIPELINE")
                        .where(BackgroundJob.request_key == "full-pipeline:run:7")
                        .where(BackgroundJob.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)))
                    )
                    assert active_count == 1
            finally:
                engine.dispose()
        finally:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(clean_db_name)
                )
            )


def test_postgresql_old_worker_cannot_complete_after_stale_recovery() -> None:
    clean_db_name = f"swinglens_pytest_lease_{uuid.uuid4().hex[:12]}"
    with _connect_admin_or_skip() as conn:
        try:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(clean_db_name)))
            database_url = _database_url_for(clean_db_name)

            upgrade = _run_alembic(database_url, "upgrade", "head")
            assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

            engine = create_engine(database_url)
            try:
                with Session(engine, expire_on_commit=False) as setup_session:
                    enqueue_job(
                        setup_session,
                        job_type="FULL_PIPELINE",
                        payload={"pipeline_run_id": 1},
                    )
                    setup_session.commit()

                with (
                    Session(engine, expire_on_commit=False) as old_worker_session,
                    Session(engine) as recovery_session,
                ):
                    claimed = claim_next_job(
                        old_worker_session,
                        worker_id="old-worker",
                        lease_seconds=1,
                    )
                    assert claimed is not None
                    old_token = claimed.execution_token
                    claimed.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                    old_worker_session.commit()

                    recovered_count = recover_stale_jobs(recovery_session, stale_after_seconds=1)
                    recovery_session.commit()
                    assert recovered_count == 1

                    with pytest.raises(JobLeaseLost):
                        mark_job_completed(
                            old_worker_session,
                            claimed,
                            {"ok": True},
                            execution_token=old_token,
                        )
                    old_worker_session.rollback()

                with Session(engine) as check_session:
                    job = check_session.scalar(select(BackgroundJob))
                    assert job is not None
                    assert job.status == JobStatus.QUEUED
                    assert job.result_json is None
                    assert job.execution_token is None
            finally:
                engine.dispose()
        finally:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(clean_db_name)
                )
            )
