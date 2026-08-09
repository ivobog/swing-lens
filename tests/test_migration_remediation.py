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
from alembic.config import Config
from alembic.script import ScriptDirectory
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


def _alembic_heads() -> tuple[str, ...]:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    return tuple(ScriptDirectory.from_config(config).get_heads())


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
            assert any(head in current.stdout for head in _alembic_heads())
        finally:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(clean_db_name)
                )
            )


def test_wave1_ceri_lineage_migration_preserves_populated_rows() -> None:
    database_name = f"swinglens_pytest_populated_ceri_{uuid.uuid4().hex[:12]}"
    with _connect_admin_or_skip() as conn:
        try:
            conn.execute(sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(database_name)))
            database_url = _database_url_for(database_name)
            upgrade = _run_alembic(database_url, "upgrade", "0027_ceri_provider_policy_telemetry")
            assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
            with psycopg.connect(
                database_url.replace("+psycopg", ""), autocommit=True
            ) as populated:
                populated.execute(
                    "INSERT INTO ceri_companies (ticker, exchange) VALUES (%s, %s)",
                    ("MSFT", "US"),
                )
                populated.execute(
                    """
                    INSERT INTO ceri_source_records
                    (provider, dataset, provider_record_id, content_hash, idempotency_key,
                     export_policy, redistribution_allowed, purge_eligible,
                     raw_json, restricted_normalized_json, source_url)
                    VALUES ('eodhd', 'estimates', 'fixture-1', 'hash-1', 'idem-1',
                            'restricted', false, true,
                            '{"consensus": 2.0, "original_document": "restricted"}'::jsonb,
                            NULL, 'https://restricted.example')
                    """
                )
                populated.execute(
                    """
                    INSERT INTO ceri_guidance_events
                    (source_record_id, company_id, action, confidence)
                    VALUES (1, 1, 'UNKNOWN', 'Normal')
                    """
                )
            upgrade = _run_alembic(database_url, "upgrade", "head")
            assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
            with psycopg.connect(database_url.replace("+psycopg", ""), autocommit=True) as check:
                source = check.execute(
                    """
                    SELECT provider_record_id, retrieved_at, raw_json,
                           restricted_normalized_json, payload_remediation_version,
                           source_url
                    FROM ceri_source_records
                    """
                ).fetchone()
                guidance = check.execute(
                    "SELECT action, point_value, filing_accession FROM ceri_guidance_events"
                ).fetchone()
                assert source[0:2] == ("fixture-1", None)
                assert source[2] is None
                assert source[3] == {"consensus": 2.0}
                assert source[4] == "wave4-evidence-projection-v1"
                assert source[5] is None
                assert guidance == ("UNKNOWN", None, None)
        finally:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )


def test_ceri_estimate_identity_migration_allows_corrected_observation() -> None:
    database_name = f"swinglens_pytest_ceri_estimate_{uuid.uuid4().hex[:12]}"
    with _connect_admin_or_skip() as conn:
        try:
            conn.execute(sql.SQL("CREATE DATABASE {} ").format(sql.Identifier(database_name)))
            database_url = _database_url_for(database_name)
            upgrade = _run_alembic(
                database_url,
                "upgrade",
                "0029_ceri_wave4_evidence_features",
            )
            assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
            with psycopg.connect(
                database_url.replace("+psycopg", ""), autocommit=True
            ) as populated:
                populated.execute(
                    "INSERT INTO ceri_companies (ticker, exchange) VALUES (%s, %s)",
                    ("A", "US"),
                )
                populated.execute(
                    """
                    INSERT INTO ceri_source_records
                    (provider, dataset, provider_record_id, content_hash, idempotency_key,
                     export_policy, redistribution_allowed, purge_eligible)
                    VALUES
                    ('eodhd', 'estimates', 'A.US:estimate', 'hash-1', 'idem-1',
                     'restricted', false, true),
                    ('eodhd', 'estimates', 'A.US:estimate', 'hash-2', 'idem-2',
                     'restricted', false, true)
                    """
                )
                populated.execute(
                    """
                    INSERT INTO ceri_estimate_snapshots
                    (source_record_id, company_id, metric, fiscal_period_end,
                     period_type, canonical_observation_key)
                    VALUES (1, 1, 'EPS_DILUTED', '2026-10-31',
                            'NEXT_FISCAL_YEAR', 'same-canonical-observation')
                    """
                )

            upgrade = _run_alembic(database_url, "upgrade", "head")
            assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
            with psycopg.connect(
                database_url.replace("+psycopg", ""), autocommit=True
            ) as check:
                check.execute(
                    """
                    INSERT INTO ceri_estimate_snapshots
                    (source_record_id, company_id, metric, fiscal_period_end,
                     period_type, canonical_observation_key)
                    VALUES (2, 1, 'EPS_DILUTED', '2026-10-31',
                            'NEXT_FISCAL_YEAR', 'same-canonical-observation')
                    """
                )
                count = check.execute(
                    "SELECT count(*) FROM ceri_estimate_snapshots"
                ).fetchone()[0]
                assert count == 2
                with pytest.raises(psycopg.errors.UniqueViolation):
                    check.execute(
                        """
                        INSERT INTO ceri_estimate_snapshots
                        (source_record_id, company_id, metric, fiscal_period_end,
                         period_type, canonical_observation_key)
                        VALUES (2, 1, 'REVENUE', '2026-10-31',
                                'NEXT_FISCAL_YEAR', 'different-observation')
                        """
                    )
        finally:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
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
