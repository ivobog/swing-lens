from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.db import get_db
from app.main import create_app
from app.models.ceri_tables import CeriSecProcessorRelease
from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus, requeue_stalled_jobs
from app.services.ceri.sec.processor_lifecycle import (
    establish_worker_processor_identity,
)
from app.services.runtime_mutation_authority import (
    RecoveryAuthority,
    RuntimeMutationAuthority,
)
from app.settings import RuntimeMode, Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def _upgrade(database_url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    configure_guarded_alembic(config, database_url)
    command.upgrade(config, "head")


def _certification_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        runtime_mode=RuntimeMode.CERTIFICATION,
        use_durable_pipeline=True,
        durable_worker_process_enabled=True,
        winner_probability_auto_maturation_enabled=False,
        winner_probability_auto_cohort_refresh_enabled=False,
        market_data_prewarm_enabled=False,
        job_worker_enabled=False,
        db_monitor_enabled=False,
        runtime_instance_id="cert-session-current",
    )


def _row_snapshot(db: Session, model, row_id) -> dict[str, object]:
    columns = tuple(model.__table__.columns)
    primary_key = tuple(model.__table__.primary_key.columns)
    assert len(primary_key) == 1
    row = db.execute(
        select(*columns).where(primary_key[0] == row_id)
    ).mappings().one()
    return deepcopy(dict(row))


def _table_snapshot(db: Session, model) -> tuple[dict[str, object], ...]:
    columns = tuple(model.__table__.columns)
    primary_key = tuple(model.__table__.primary_key.columns)
    rows = db.execute(select(*columns).order_by(*primary_key)).mappings().all()
    return tuple(deepcopy(dict(row)) for row in rows)


def test_denied_certification_web_mutation_is_byte_for_byte_unchanged(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        job = BackgroundJob(
            job_type="RUNTIME_AUTHORITY_WEB_PROOF",
            status=JobStatus.COMPLETED,
            payload_json={"proof": "denied-web-mutation"},
            result_json={"preserve": True},
            completed_at=datetime.now(UTC) - timedelta(days=365),
        )
        db.add(job)
        db.commit()
        job_id = job.id
        before = _row_snapshot(db, BackgroundJob, job_id)

    dependency_calls = 0

    def disposable_db():
        nonlocal dependency_calls
        dependency_calls += 1
        with Session(engine) as db:
            yield db

    app = create_app(_certification_settings(disposable_postgres_database))
    app.dependency_overrides[get_db] = disposable_db
    response = TestClient(app).post(
        "/ops/cleanup/execute",
        headers={"x-swinglens-certification-session": "cert-session-current"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CERTIFICATION_MUTATION_FORBIDDEN"
    assert dependency_calls == 0
    with Session(engine) as db:
        assert _row_snapshot(db, BackgroundJob, job_id) == before
    engine.dispose()


def test_certification_sec_startup_is_byte_for_byte_read_only(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        release = establish_worker_processor_identity(
            db,
            authority=RuntimeMutationAuthority.normal("worker.startup.sec_identity"),
            git_sha="certified-git-sha",
        )
        db.commit()
        assert release.processor_signature
        deployed_git_sha = release.deployed_git_sha
        before = _table_snapshot(db, CeriSecProcessorRelease)

        authority = RuntimeMutationAuthority.certification(
            "worker.startup.sec_identity", session_id="cert-session-current"
        )
        first = establish_worker_processor_identity(
            db, authority=authority, git_sha=deployed_git_sha
        )
        second = establish_worker_processor_identity(
            db, authority=authority, git_sha=deployed_git_sha
        )
        db.commit()

        assert first.processor_signature == release.processor_signature
        assert second.processor_signature == release.processor_signature
        assert _table_snapshot(db, CeriSecProcessorRelease) == before
    engine.dispose()


def test_recovery_authority_scopes_certification_and_preserves_normal_recovery(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    with Session(engine) as db:
        current_root = _certification_job(
            request_key="current-root",
            status=JobStatus.RUNNING,
            session_id="cert-session-current",
            root_correlation_id="current-root",
            job_type="FULL_PIPELINE",
        )
        prior_root = _certification_job(
            request_key="prior-root",
            status=JobStatus.RUNNING,
            session_id="cert-session-prior",
            root_correlation_id="prior-root",
            job_type="FULL_PIPELINE",
        )
        current = _certification_job(
            request_key="current-child",
            status=JobStatus.STALLED,
            session_id="cert-session-current",
            root_correlation_id="current-root",
        )
        current_cancel = _certification_job(
            request_key="current-cancel",
            status=JobStatus.STALLED,
            session_id="cert-session-current",
            root_correlation_id="current-root",
            requested_cancel=True,
        )
        unrelated = _certification_job(
            request_key="prior-child",
            status=JobStatus.STALLED,
            session_id="cert-session-prior",
            root_correlation_id="prior-root",
        )
        terminal_root = _certification_job(
            request_key="terminal-root",
            status=JobStatus.FAILED,
            session_id="cert-session-current",
            root_correlation_id="terminal-root",
            job_type="FULL_PIPELINE",
        )
        terminal_descendant = _certification_job(
            request_key="terminal-descendant",
            status=JobStatus.STALLED,
            session_id="cert-session-current",
            root_correlation_id="terminal-root",
        )
        normal = BackgroundJob(
            job_type="NORMAL_RECOVERY_PROOF",
            status=JobStatus.STALLED,
            request_key="normal-child",
            payload_json={"proof": "normal-recovery"},
            run_after=now - timedelta(minutes=5),
        )
        db.add_all(
            [
                current_root,
                prior_root,
                current,
                current_cancel,
                unrelated,
                terminal_root,
                terminal_descendant,
                normal,
            ]
        )
        db.commit()
        ids = {
            "current": current.id,
            "current_cancel": current_cancel.id,
            "unrelated": unrelated.id,
            "terminal_descendant": terminal_descendant.id,
            "normal": normal.id,
        }
        unrelated_before = _row_snapshot(db, BackgroundJob, unrelated.id)
        terminal_before = _row_snapshot(db, BackgroundJob, terminal_descendant.id)

        recovered = requeue_stalled_jobs(
            db,
            authority=RecoveryAuthority.certification(
                "supervisor.requeue_stalled", session_id="cert-session-current"
            ),
            job_ids=[
                current.id,
                current_cancel.id,
                unrelated.id,
                terminal_descendant.id,
            ],
            now=now,
        )
        db.commit()

        assert recovered == 1
        assert db.get(BackgroundJob, ids["current"]).status == JobStatus.RECOVERING
        cancelled = db.get(BackgroundJob, ids["current_cancel"])
        assert cancelled.status == JobStatus.CANCELLED
        assert cancelled.requested_cancel is True
        assert _row_snapshot(db, BackgroundJob, ids["unrelated"]) == unrelated_before
        assert (
            _row_snapshot(db, BackgroundJob, ids["terminal_descendant"])
            == terminal_before
        )

        normal_recovered = requeue_stalled_jobs(
            db,
            authority=RecoveryAuthority.normal("supervisor.requeue_stalled"),
            job_ids=[normal.id],
            now=now,
        )
        db.commit()

        assert normal_recovered == 1
        assert db.get(BackgroundJob, ids["normal"]).status == JobStatus.RECOVERING
    engine.dispose()


def _certification_job(
    *,
    request_key: str,
    status: str,
    session_id: str,
    root_correlation_id: str,
    job_type: str = "CERI_PROVIDER_INGEST",
    requested_cancel: bool = False,
) -> BackgroundJob:
    return BackgroundJob(
        job_type=job_type,
        status=status,
        request_key=request_key,
        payload_json={
            "certification_authorized": True,
            "certification_session_id": session_id,
            "transition_preflight_plan_id": 1001,
        },
        requested_cancel=requested_cancel,
        root_correlation_id=root_correlation_id,
        run_after=datetime.now(UTC) - timedelta(minutes=5),
        worker_id="authority-proof-worker" if status == JobStatus.RUNNING else None,
        worker_instance_id=(
            "authority-proof-instance" if status == JobStatus.RUNNING else None
        ),
        lease_owner="authority-proof-worker" if status == JobStatus.RUNNING else None,
        execution_token="authority-proof-token" if status == JobStatus.RUNNING else None,
        heartbeat_at=(datetime.now(UTC) if status == JobStatus.RUNNING else None),
        lease_expires_at=(
            datetime.now(UTC) + timedelta(minutes=5)
            if status == JobStatus.RUNNING
            else None
        ),
    )
