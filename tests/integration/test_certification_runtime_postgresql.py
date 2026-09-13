from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import and_, create_engine, func, or_, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import BackgroundJob, PipelineRun, UploadRun
from app.observability.correlation import CausalityContext
from app.services.background_job_service import (
    JobStatus,
    claim_next_job,
    enqueue_job,
    mark_job_completed,
)
from app.services.ceri.sec.processor_capability import SEC_CAPABILITY_JOB_TYPES
from app.services.certification_runtime import (
    CertificationRuntimeViolation,
    certification_claimable_job_ids,
    queue_isolation_status,
)
from app.services.worker_registry import register_worker
from app.settings import ProcessRole, RuntimeMode, Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def _upgrade(database_url: str) -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    configure_guarded_alembic(config, database_url)
    command.upgrade(config, "head")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        runtime_mode=RuntimeMode.CERTIFICATION,
        process_role=ProcessRole.DURABLE_WORKER,
        use_durable_pipeline=True,
        durable_worker_process_enabled=True,
        winner_probability_auto_maturation_enabled=False,
        winner_probability_auto_cohort_refresh_enabled=False,
        market_data_prewarm_enabled=False,
        runtime_instance_id="cert-session-current",
    )


def test_due_unrelated_matrix_is_deferred_while_authorized_lineage_executes(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    due_types = (
        "WINNER_OUTCOME_MATURATION",
        "WINNER_COHORT_REFRESH",
        "WINNER_LATEST_RESCORE",
        "CERI_PROVIDER_INGEST",
        "MARKET_DATA_PREWARM",
        "STALE_CONTINUATION",
    )
    with Session(engine, expire_on_commit=False) as db:
        worker = register_worker(
            db,
            worker_id="certification-worker",
            queues=("interactive", "broker", "background"),
            heartbeat_timeout_seconds=30,
            now=now,
            instance_id="certification-instance",
        )
        for index, job_type in enumerate(due_types):
            db.add(
                BackgroundJob(
                    job_type=job_type,
                    status=JobStatus.QUEUED,
                    priority=index,
                    payload_json={},
                    max_retries=3,
                    retry_count=0,
                    run_after=now,
                )
            )
        db.add(
            BackgroundJob(
                job_type="WINNER_OUTCOME_MATURATION",
                status="RETRYING",
                priority=0,
                payload_json={},
                max_retries=3,
                retry_count=1,
                run_after=now,
            )
        )
        db.add(
            BackgroundJob(
                job_type="CERI_NORMALIZE_BATCH",
                status=JobStatus.RUNNING,
                priority=0,
                payload_json={},
                max_retries=3,
                retry_count=0,
                run_after=now - timedelta(hours=1),
                lease_expires_at=now - timedelta(seconds=1),
                worker_id="absent-worker",
                lease_owner="absent-worker",
            )
        )
        root = BackgroundJob(
            job_type="FULL_PIPELINE",
            status=JobStatus.QUEUED,
            priority=0,
            payload_json={
                "certification_authorized": True,
                "transition_preflight_plan_id": 3,
                "certification_session_id": "cert-session-current",
            },
            max_retries=3,
            retry_count=0,
            run_after=now,
            root_correlation_id="certification-root",
        )
        db.add(root)
        db.flush()
        root.root_job_id = root.id
        db.add(
            BackgroundJob(
                job_type="CERI_CAPTURE_RUN",
                status=JobStatus.QUEUED,
                priority=1,
                payload_json={},
                max_retries=3,
                retry_count=0,
                run_after=now,
                root_job_id=root.id,
                parent_job_id=root.id,
                root_correlation_id="certification-root",
            )
        )
        db.commit()

        isolation = queue_isolation_status(
            db,
            now=now,
            allow_authorized_lineage=True,
            certification_session_id="cert-session-current",
        )
        assert isolation.isolated is False
        # Six queued unrelated jobs, one RETRYING job, and the unstamped child.
        assert isolation.unrelated_runnable_jobs == len(due_types) + 2

        claimed_root = claim_next_job(
            db,
            worker.worker_id,
            worker_instance_id="certification-instance",
            queues=("interactive", "broker", "background"),
            certification_only=True,
            certification_session_id="cert-session-current",
        )
        assert claimed_root is not None
        assert claimed_root.job_type == "FULL_PIPELINE"
        mark_job_completed(
            db,
            claimed_root,
            execution_token=claimed_root.execution_token,
        )
        db.commit()

        claimed_child = claim_next_job(
            db,
            worker.worker_id,
            worker_instance_id="certification-instance",
            queues=("interactive", "broker", "background"),
            certification_only=True,
            certification_session_id="cert-session-current",
        )
        assert claimed_child is None
        db.rollback()

        remaining = list(
            db.scalars(select(BackgroundJob).where(BackgroundJob.job_type.in_(due_types)))
        )
        assert len(remaining) == len(due_types) + 1
        assert {job.status for job in remaining} == {JobStatus.QUEUED, "RETRYING"}
    engine.dispose()


def test_terminal_certification_root_cannot_reauthorize_historical_descendants(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as db:
        worker = register_worker(
            db,
            worker_id="certification-worker",
            queues=("interactive", "broker", "background"),
            heartbeat_timeout_seconds=30,
            now=now,
            instance_id="certification-instance",
        )
        root = BackgroundJob(
            job_type="FULL_PIPELINE",
            status=JobStatus.CANCELLED,
            priority=0,
            payload_json={
                "certification_authorized": True,
                "transition_preflight_plan_id": 4,
                "certification_session_id": "prior-session",
            },
            max_retries=3,
            retry_count=1,
            run_after=now - timedelta(hours=1),
            root_correlation_id="historical-certification-root",
        )
        db.add(root)
        db.flush()
        root.root_job_id = root.id
        child = BackgroundJob(
            job_type="CERI_CHANGE_DETECTION",
            status=JobStatus.QUEUED,
            priority=0,
            payload_json={},
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=1),
            root_job_id=root.id,
            parent_job_id=root.id,
            root_correlation_id=root.root_correlation_id,
        )
        db.add(child)
        db.commit()

        claimed = claim_next_job(
            db,
            worker.worker_id,
            worker_instance_id="certification-instance",
            queues=("interactive", "broker", "background"),
            certification_only=True,
            certification_session_id="cert-session-current",
        )
        assert claimed is None

        strict_isolation = queue_isolation_status(db, now=now)
        assert strict_isolation.isolated is False
        assert strict_isolation.unrelated_runnable_jobs == 1

        pre_enqueue_isolation = queue_isolation_status(
            db,
            now=now,
            ignore_inactive_authorized_lineage=True,
        )
        assert pre_enqueue_isolation.isolated is True
        assert pre_enqueue_isolation.unrelated_runnable_jobs == 0
    engine.dispose()


def test_certification_enqueue_boundary_rejects_unrelated_and_allows_lineage(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    monkeypatch.setattr(
        "app.services.background_job_service.get_settings",
        _settings,
    )
    with Session(engine) as db:
        with pytest.raises(CertificationRuntimeViolation):
            enqueue_job(db, "WINNER_OUTCOME_MATURATION", {})

        root = enqueue_job(
            db,
            "FULL_PIPELINE",
            {
                "certification_authorized": True,
                "transition_preflight_plan_id": 3,
                "certification_session_id": "cert-session-current",
            },
            request_key="certification-root",
        )
        db.flush()
        root.status = JobStatus.RUNNING
        child = enqueue_job(
            db,
            "CERI_PROVIDER_INGEST",
            {},
            causality=CausalityContext(
                root_correlation_id=root.root_correlation_id,
                causation_id="child-cause",
                background_job_id=root.id,
            ),
        )

        assert root.job_type == "FULL_PIPELINE"
        assert child.root_correlation_id == root.root_correlation_id
        assert child.payload_json["certification_session_id"] == "cert-session-current"
    engine.dispose()


def test_sec_capability_exclusion_claims_non_sec_but_never_sec(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as db:
        worker = register_worker(
            db,
            worker_id="sec-capability-worker",
            queues=("interactive", "broker", "background"),
            heartbeat_timeout_seconds=30,
            now=now,
            instance_id="sec-capability-instance",
        )
        sec_job = BackgroundJob(
            job_type="CERI_PROVIDER_INGEST",
            status=JobStatus.QUEUED,
            priority=0,
            payload_json={},
            max_retries=3,
            retry_count=0,
            run_after=now,
        )
        non_sec_job = BackgroundJob(
            job_type="WORKER_RECOVERY_PROBE",
            status=JobStatus.QUEUED,
            priority=1,
            payload_json={},
            max_retries=3,
            retry_count=0,
            run_after=now,
        )
        db.add_all((sec_job, non_sec_job))
        db.commit()

        claimed = claim_next_job(
            db,
            worker.worker_id,
            worker_instance_id="sec-capability-instance",
            queues=("interactive", "broker", "background"),
            excluded_job_types=SEC_CAPABILITY_JOB_TYPES,
        )

        assert claimed is not None
        assert claimed.id == non_sec_job.id
        assert claimed.job_type == "WORKER_RECOVERY_PROBE"
        assert db.get(BackgroundJob, sec_job.id).status == JobStatus.QUEUED
    engine.dispose()


def test_run159_topology_is_session_fenced_without_descendants_or_ceri_mutation(
    disposable_postgres_database: str,
) -> None:
    """Reproduce the incident shape and compare old, 416, and session predicates."""
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    current_session = "cert-session-new"
    prior_session = "cert-session-prior"
    protected_ids: list[int] = []
    with Session(engine, expire_on_commit=False) as db:
        upload = UploadRun(filename="run-159.csv", status="CANCELLED")
        db.add(upload)
        db.flush()
        pipeline = PipelineRun(
            upload_run_id=upload.id,
            status="CANCELLED",
            current_step="CERI_CAPTURE_RUN",
            completed_at=now - timedelta(minutes=10),
        )
        db.add(pipeline)
        db.flush()
        cancelled_root = BackgroundJob(
            job_type="FULL_PIPELINE",
            related_run_id=upload.id,
            status=JobStatus.CANCELLED,
            priority=0,
            payload_json={
                "pipeline_run_id": pipeline.id,
                "certification_authorized": True,
                "transition_preflight_plan_id": 4,
                "certification_session_id": prior_session,
            },
            max_retries=3,
            retry_count=1,
            run_after=now - timedelta(hours=1),
            root_correlation_id="run-159-historical-root",
        )
        db.add(cancelled_root)
        db.flush()
        cancelled_root.root_job_id = cancelled_root.id

        direct_child = BackgroundJob(
            job_type="CERI_PROVIDER_INGEST",
            related_run_id=upload.id,
            status=JobStatus.QUEUED,
            priority=1,
            payload_json={
                "certification_authorized": True,
                "certification_session_id": current_session,
            },
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=9),
            root_job_id=cancelled_root.id,
            parent_job_id=cancelled_root.id,
            triggered_by_job_id=cancelled_root.id,
            root_correlation_id=cancelled_root.root_correlation_id,
            created_at=now - timedelta(minutes=20),
        )
        db.add(direct_child)
        db.flush()
        nested_child = BackgroundJob(
            job_type="CERI_CAPTURE_RUN",
            related_run_id=upload.id,
            status=JobStatus.RECOVERING,
            priority=2,
            payload_json={},
            max_retries=3,
            retry_count=1,
            run_after=now - timedelta(minutes=8),
            root_job_id=cancelled_root.id,
            parent_job_id=direct_child.id,
            triggered_by_job_id=direct_child.id,
            root_correlation_id=cancelled_root.root_correlation_id,
            created_at=now - timedelta(minutes=19),
        )
        retrying_child = BackgroundJob(
            job_type="CERI_CHANGE_DETECTION",
            related_run_id=upload.id,
            status="RETRYING",
            priority=3,
            payload_json={},
            max_retries=3,
            retry_count=1,
            run_after=now - timedelta(minutes=7),
            root_job_id=cancelled_root.id,
            parent_job_id=direct_child.id,
            triggered_by_job_id=direct_child.id,
            root_correlation_id=cancelled_root.root_correlation_id,
            created_at=now - timedelta(minutes=18),
        )
        db.add_all((nested_child, retrying_child))
        db.flush()
        protected_ids.extend((direct_child.id, nested_child.id, retrying_child.id))

        completed_root = BackgroundJob(
            job_type="FULL_PIPELINE",
            status=JobStatus.COMPLETED,
            priority=3,
            payload_json={
                "certification_authorized": True,
                "transition_preflight_plan_id": 3,
                "certification_session_id": current_session,
            },
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=7),
            root_correlation_id="completed-current-root",
        )
        db.add(completed_root)
        db.flush()
        completed_child = BackgroundJob(
            job_type="CERI_CAPTURE_RUN",
            status=JobStatus.QUEUED,
            priority=4,
            payload_json={
                "certification_authorized": True,
                "certification_session_id": current_session,
            },
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=6),
            parent_job_id=completed_root.id,
            root_correlation_id=completed_root.root_correlation_id,
        )
        db.add(completed_child)
        db.flush()
        protected_ids.append(completed_child.id)

        # 416 still trusts an *active* historical root correlation; this is the
        # adversarial case that prevented deployment of that commit as-is.
        active_prior_root = BackgroundJob(
            job_type="FULL_PIPELINE",
            status=JobStatus.RUNNING,
            priority=4,
            payload_json={
                "certification_authorized": True,
                "transition_preflight_plan_id": 2,
                "certification_session_id": prior_session,
            },
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=6),
            root_correlation_id="active-prior-root",
        )
        db.add(active_prior_root)
        db.flush()
        active_prior_child = BackgroundJob(
            job_type="CERI_NORMALIZE_BATCH",
            status=JobStatus.QUEUED,
            priority=5,
            payload_json={},
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=5),
            parent_job_id=active_prior_root.id,
            root_correlation_id=active_prior_root.root_correlation_id,
        )
        same_correlation_unauthorized = BackgroundJob(
            job_type="CERI_FEATURE_REBUILD_BATCH",
            status=JobStatus.QUEUED,
            priority=6,
            payload_json={},
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=4),
            root_correlation_id="current-correlation",
        )
        same_run_unauthorized = BackgroundJob(
            job_type="WINNER_LATEST_RESCORE",
            related_run_id=upload.id,
            status=JobStatus.QUEUED,
            priority=7,
            payload_json={},
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=3),
        )
        stale_explicit = BackgroundJob(
            job_type="CERI_CAPTURE_RUN",
            status=JobStatus.QUEUED,
            priority=8,
            payload_json={
                "certification_authorized": True,
                "certification_session_id": prior_session,
            },
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=2),
        )
        current_root = BackgroundJob(
            job_type="FULL_PIPELINE",
            status=JobStatus.RUNNING,
            priority=9,
            payload_json={
                "certification_authorized": True,
                "transition_preflight_plan_id": 5,
                "certification_session_id": current_session,
            },
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=1),
            root_correlation_id="current-correlation",
        )
        db.add(current_root)
        db.flush()
        current_explicit = BackgroundJob(
            job_type="WORKER_RECOVERY_PROBE",
            status=JobStatus.QUEUED,
            priority=10,
            payload_json={
                "certification_authorized": True,
                "certification_session_id": current_session,
            },
            max_retries=3,
            retry_count=0,
            run_after=now - timedelta(minutes=1),
            parent_job_id=current_root.id,
            root_correlation_id="current-correlation",
        )
        db.add_all(
            (
                active_prior_child,
                same_correlation_unauthorized,
                same_run_unauthorized,
                stale_explicit,
                current_explicit,
            )
        )
        db.flush()
        protected_ids.extend(
            (
                active_prior_child.id,
                same_correlation_unauthorized.id,
                same_run_unauthorized.id,
                stale_explicit.id,
            )
        )
        worker = register_worker(
            db,
            worker_id="certification-worker",
            queues=("interactive", "broker", "background"),
            heartbeat_timeout_seconds=30,
            now=now,
            instance_id="worker-instance",
        )
        db.commit()

        old_roots = select(BackgroundJob.root_correlation_id).where(
            BackgroundJob.job_type == "FULL_PIPELINE",
            BackgroundJob.payload_json["certification_authorized"].as_boolean().is_(True),
        )
        old_incident_ids = set(
            db.scalars(
                select(BackgroundJob.id).where(
                    BackgroundJob.status.in_((JobStatus.QUEUED, JobStatus.RECOVERING)),
                    BackgroundJob.root_correlation_id.in_(old_roots),
                )
            )
        )
        assert {direct_child.id, nested_child.id}.issubset(old_incident_ids)

        active_roots_416 = select(BackgroundJob.root_correlation_id).where(
            BackgroundJob.job_type == "FULL_PIPELINE",
            BackgroundJob.payload_json["certification_authorized"].as_boolean().is_(True),
            BackgroundJob.status.in_(("QUEUED", "RECOVERING", "RETRYING", "RUNNING")),
        )
        visible_under_416 = set(
            db.scalars(
                select(BackgroundJob.id).where(
                    BackgroundJob.status.in_((JobStatus.QUEUED, JobStatus.RECOVERING)),
                    or_(
                        and_(
                            BackgroundJob.job_type == "FULL_PIPELINE",
                            BackgroundJob.payload_json[
                                "certification_authorized"
                            ].as_boolean().is_(True),
                        ),
                        BackgroundJob.root_correlation_id.in_(active_roots_416),
                    ),
                )
            )
        )
        assert direct_child.id not in visible_under_416
        assert nested_child.id not in visible_under_416
        assert completed_child.id not in visible_under_416
        assert active_prior_child.id in visible_under_416

        assert certification_claimable_job_ids(
            db, certification_session_id=current_session, now=now
        ) == (current_explicit.id,)
        jobs_before = int(db.scalar(select(func.count(BackgroundJob.id))) or 0)
        ceri_before = int(db.scalar(text("select count(*) from ceri_score_snapshots")) or 0)

        claimed = claim_next_job(
            db,
            worker.worker_id,
            worker_instance_id="worker-instance",
            queues=("interactive", "broker", "background"),
            certification_only=True,
            certification_session_id=current_session,
        )
        assert claimed is not None and claimed.id == current_explicit.id
        mark_job_completed(db, claimed, execution_token=claimed.execution_token)
        db.commit()
        assert (
            claim_next_job(
                db,
                worker.worker_id,
                worker_instance_id="worker-instance",
                certification_only=True,
                certification_session_id=current_session,
            )
            is None
        )
        db.rollback()

        protected = list(
            db.scalars(select(BackgroundJob).where(BackgroundJob.id.in_(protected_ids)))
        )
        assert all(job.worker_id is None for job in protected)
        assert all(job.lease_owner is None for job in protected)
        assert all(job.execution_token is None for job in protected)
        assert int(db.scalar(select(func.count(BackgroundJob.id))) or 0) == jobs_before
        assert int(db.scalar(text("select count(*) from ceri_score_snapshots")) or 0) == ceri_before
    engine.dispose()


def test_concurrent_certification_claims_lease_current_job_exactly_once(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as db:
        current = BackgroundJob(
            job_type="FULL_PIPELINE",
            status=JobStatus.QUEUED,
            priority=1,
            payload_json={
                "certification_authorized": True,
                "transition_preflight_plan_id": 1,
                "certification_session_id": "current-session",
            },
            max_retries=1,
            retry_count=0,
            run_after=now,
        )
        historical = BackgroundJob(
            job_type="CERI_CAPTURE_RUN",
            status=JobStatus.QUEUED,
            priority=0,
            payload_json={
                "certification_authorized": True,
                "certification_session_id": "prior-session",
            },
            max_retries=1,
            retry_count=0,
            run_after=now,
        )
        db.add_all((current, historical))
        for worker_id in ("concurrent-a", "concurrent-b"):
            register_worker(
                db,
                worker_id=worker_id,
                queues=("interactive", "broker", "background"),
                heartbeat_timeout_seconds=30,
                now=now,
                instance_id=f"{worker_id}-instance",
            )
        db.commit()
        current_id, historical_id = current.id, historical.id

    def attempt(worker_id: str) -> int | None:
        with Session(engine) as db:
            job = claim_next_job(
                db,
                worker_id,
                worker_instance_id=f"{worker_id}-instance",
                certification_only=True,
                certification_session_id="current-session",
            )
            claimed_id = job.id if job is not None else None
            db.commit()
            return claimed_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("concurrent-a", "concurrent-b")))
    assert results.count(current_id) == 1
    assert results.count(None) == 1
    with Session(engine) as db:
        historical = db.get(BackgroundJob, historical_id)
        assert historical is not None
        assert historical.status == JobStatus.QUEUED
        assert historical.worker_id is None
        assert historical.lease_owner is None
        assert historical.execution_token is None
        assert historical.locked_at is None
        assert historical.lease_expires_at is None
    engine.dispose()


def test_restart_epoch_and_normal_runtime_semantics(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as db:
        old_epoch = BackgroundJob(
            job_type="FULL_PIPELINE",
            status=JobStatus.QUEUED,
            priority=0,
            payload_json={
                "certification_authorized": True,
                "transition_preflight_plan_id": 1,
                "certification_session_id": "runtime-a",
            },
            max_retries=1,
            retry_count=0,
            run_after=now,
        )
        normal_unmarked = BackgroundJob(
            job_type="WORKER_RECOVERY_PROBE",
            status=JobStatus.QUEUED,
            priority=1,
            payload_json={},
            max_retries=1,
            retry_count=0,
            run_after=now,
        )
        db.add_all((old_epoch, normal_unmarked))
        for worker_id in ("restart-worker", "normal-worker"):
            register_worker(
                db,
                worker_id=worker_id,
                queues=("interactive", "broker", "background"),
                heartbeat_timeout_seconds=30,
                now=now,
                instance_id=f"{worker_id}-instance",
            )
        db.commit()

        # A new canonical lifecycle epoch cannot resurrect runtime-a's job.
        assert (
            claim_next_job(
                db,
                "restart-worker",
                worker_instance_id="restart-worker-instance",
                certification_only=True,
                certification_session_id="runtime-b",
            )
            is None
        )
        db.rollback()
        assert old_epoch.status == JobStatus.QUEUED
        assert old_epoch.worker_id is None

        # A supervised process crash within runtime-a retains that exact epoch.
        claimed_after_process_restart = claim_next_job(
            db,
            "restart-worker",
            worker_instance_id="restart-worker-instance",
            certification_only=True,
            certification_session_id="runtime-a",
        )
        assert claimed_after_process_restart is not None
        assert claimed_after_process_restart.id == old_epoch.id
        mark_job_completed(
            db,
            claimed_after_process_restart,
            execution_token=claimed_after_process_restart.execution_token,
        )
        db.commit()

        # Outside certification mode, the existing queue semantics are unchanged.
        claimed_normal = claim_next_job(
            db,
            "normal-worker",
            worker_instance_id="normal-worker-instance",
            certification_only=False,
        )
        assert claimed_normal is not None
        assert claimed_normal.id == normal_unmarked.id
    engine.dispose()
