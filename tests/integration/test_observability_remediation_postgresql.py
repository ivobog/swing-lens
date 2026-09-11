from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from time import perf_counter

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.models.ceri_tables import CeriProcessingRun
from app.models.ib_market_intelligence_tables import IBIntelligenceRun
from app.models.tables import (
    BackgroundJob,
    BackgroundJobEnqueueAttempt,
    BackgroundJobFanoutRoot,
    TechnicalFeatureArtifact,
)
from app.observability.correlation import root_action_scope, worker_job_scope
from app.observability.metrics import operational_metrics
from app.services.background_job_service import (
    JobStatus,
    enqueue_job,
    mark_job_completed,
    mark_job_failed_or_retry,
)
from app.services.cleanup_service import execute_durable_evidence_retention
from app.services.operations_service import OperationsService
from app.services.readiness_service import ReadinessService
from app.services.winner_probability.job_handlers import WINNER_OUTCOME_MATURATION
from app.services.winner_probability.scheduler import schedule_primary_h5_maturation
from app.services.winner_probability.trading_session_service import (
    latest_completed_session,
)
from app.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def remediated_postgres(disposable_postgres_database_factory) -> Iterator:
    with disposable_postgres_database_factory() as database_url:
        config = Config("alembic.ini")
        config.attributes["database_url"] = database_url
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                database_name = connection.execute(text("select current_database() ")).scalar()
                assert str(database_name).startswith("swinglens_pytest_")
                assert connection.execute(
                    text("select version_num from alembic_version")
                ).scalar() == ("0072_ceri_artifact_context_lineage")
            yield engine
        finally:
            engine.dispose()


@pytest.fixture(autouse=True)
def reset_metrics() -> Iterator[None]:
    operational_metrics.reset()
    yield
    operational_metrics.reset()


def _job(job_type: str, request_key: str, status: str) -> BackgroundJob:
    now = datetime.now(UTC)
    return BackgroundJob(
        job_type=job_type,
        request_key=request_key,
        status=status,
        payload_json={},
        priority=100,
        retry_count=0,
        max_retries=0,
        requested_cancel=False,
        run_after=now,
        completed_at=now if status in {JobStatus.COMPLETED, JobStatus.FAILED} else None,
        operational_metadata_json={},
    )


def test_postgresql_rollback_discards_state_and_commit_metric(remediated_postgres) -> None:
    key = "obs-transaction-rollback"
    with Session(remediated_postgres) as session:
        with root_action_scope("ADMINISTRATIVE", "rollback-certification"):
            enqueue_job(session, "OBS_TRANSACTION_FIXTURE", {}, request_key=key)
        session.flush()
        assert operational_metrics.total("swinglens_jobs_enqueued_total") == 0
        session.rollback()

    with Session(remediated_postgres) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(BackgroundJob.request_key == key)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(BackgroundJobEnqueueAttempt)
                .where(BackgroundJobEnqueueAttempt.request_key == key)
            )
            == 0
        )
    assert operational_metrics.total("swinglens_jobs_enqueued_total") == 0

    with Session(remediated_postgres) as session:
        with root_action_scope("ADMINISTRATIVE", "commit-certification"):
            enqueue_job(session, "OBS_TRANSACTION_FIXTURE", {}, request_key=key)
        session.commit()
    assert (
        operational_metrics.total(
            "swinglens_jobs_enqueued_total", job_type="OBS_TRANSACTION_FIXTURE"
        )
        == 1
    )


def test_postgresql_final_persistence_boundary_redacts_all_error_families(
    remediated_postgres,
) -> None:
    secret = "password=SECRET Authorization: Bearer SECRET"
    with Session(remediated_postgres) as session:
        job = _job("OBS_REDACTION", "obs-redaction-job", JobStatus.FAILED)
        job.error_message = secret
        job.result_json = {"error": secret, "nested": {"client_secret": "SECRET"}}
        ceri = CeriProcessingRun(
            job_type="OBS_REDACTION",
            deterministic_request_key="obs-redaction-ceri",
            errors_json={"error": secret},
        )
        ibmi = IBIntelligenceRun(
            job_type="OBS_REDACTION",
            module="OBS",
            status="FAILED",
            deterministic_request_key="obs-redaction-ibmi",
            config_version="test",
            config_hash="test",
            error_message=secret,
        )
        technical = TechnicalFeatureArtifact(
            ticker="OBS",
            timeframe="1 day",
            artifact_kind="LOCAL",
            input_signature="obs-redaction",
            artifact_schema_version="test",
            technical_engine_version="test",
            feature_config_hash="test",
            scoring_config_hash="test",
            input_versions_json={},
            artifact_json={},
            status="READY",
            last_shadow_mismatch_json={"error": secret},
        )
        session.add_all((job, ceri, ibmi, technical))
        session.commit()
        rendered = repr(
            (
                job.error_message,
                job.result_json,
                ceri.errors_json,
                ibmi.error_message,
                technical.last_shadow_mismatch_json,
            )
        )
        assert "SECRET" not in rendered


def test_postgresql_ib_readiness_is_workload_aware(remediated_postgres) -> None:
    settings = get_settings().model_copy(update={"observability_ib_required": False})
    unavailable = ReadinessService(
        engine=remediated_postgres,
        settings=settings,
        ib_available=False,
    )
    assert unavailable._ib_check().status == "optional_unavailable"

    with Session(remediated_postgres) as session:
        required_job = _job("FULL_PIPELINE", "obs-ib-required", JobStatus.QUEUED)
        required_job.run_after = unavailable.now - timedelta(seconds=1)
        session.add(required_job)
        session.commit()
    assert unavailable._ib_check().message == "required_unavailable:runnable_work"
    available = ReadinessService(
        engine=remediated_postgres,
        settings=settings,
        ib_available=True,
    )
    assert available._ib_check().status == "ok"


def test_metrics_off_retention_prunes_postgresql_evidence(remediated_postgres) -> None:
    operational_metrics.configure(enabled=False)
    with Session(remediated_postgres) as session:
        with root_action_scope("ADMINISTRATIVE", "metrics-off-retention"):
            enqueue_job(
                session,
                "OBS_RETENTION",
                {},
                request_key="obs-metrics-off-retention",
            )
        session.commit()
        old = datetime.now(UTC) - timedelta(days=60)
        for attempt in session.scalars(select(BackgroundJobEnqueueAttempt)).all():
            attempt.occurred_at = old
        for root in session.scalars(select(BackgroundJobFanoutRoot)).all():
            root.last_occurred_at = old
        session.commit()

        result = execute_durable_evidence_retention(session, get_settings())
        session.commit()
        assert result == {"attempts": 1, "roots": 1}
        assert session.scalar(select(func.count()).select_from(BackgroundJobEnqueueAttempt)) == 0
        assert session.scalar(select(func.count()).select_from(BackgroundJobFanoutRoot)) == 0
    assert operational_metrics.samples() == []


@pytest.mark.parametrize(
    ("existing_status", "duplicate_allowed"),
    [
        (JobStatus.QUEUED, False),
        (JobStatus.RUNNING, False),
        (JobStatus.RECOVERING, False),
        (JobStatus.COMPLETED, True),
    ],
)
def test_active_request_key_fence_status_matrix(
    remediated_postgres, existing_status: str, duplicate_allowed: bool
) -> None:
    key = f"obs-fence-{existing_status.lower()}"
    with Session(remediated_postgres) as first:
        first.add(_job("OBS_FENCE_FIXTURE", key, existing_status))
        first.commit()

    with Session(remediated_postgres) as second:
        second.add(_job("OBS_FENCE_FIXTURE", key, JobStatus.QUEUED))
        if duplicate_allowed:
            second.commit()
        else:
            with pytest.raises(IntegrityError):
                second.commit()
            second.rollback()

    with Session(remediated_postgres) as verify:
        active = verify.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.job_type == "OBS_FENCE_FIXTURE")
            .where(BackgroundJob.request_key == key)
            .where(
                BackgroundJob.status.in_(
                    (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RECOVERING)
                )
            )
        )
    assert active == 1


def test_independent_transaction_recovering_race_is_fenced(remediated_postgres) -> None:
    key = "obs-recovering-race"
    with Session(remediated_postgres) as seed:
        original = _job("OBS_RECOVERING_RACE", key, JobStatus.COMPLETED)
        seed.add(original)
        seed.commit()
        original_id = original.id

    recovering_flushed = Event()
    release_recovering = Event()
    duplicate_started = Event()
    outcomes: list[str] = []

    def recover() -> None:
        with Session(remediated_postgres) as session:
            row = session.get(BackgroundJob, original_id)
            assert row is not None
            row.status = JobStatus.RECOVERING
            row.completed_at = None
            session.flush()
            recovering_flushed.set()
            assert release_recovering.wait(10)
            session.commit()
            outcomes.append("recovering_committed")

    def duplicate() -> None:
        assert recovering_flushed.wait(10)
        with Session(remediated_postgres) as session:
            session.add(_job("OBS_RECOVERING_RACE", key, JobStatus.QUEUED))
            duplicate_started.set()
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                outcomes.append("duplicate_fenced")

    first = Thread(target=recover)
    second = Thread(target=duplicate)
    first.start()
    second.start()
    assert duplicate_started.wait(10)
    release_recovering.set()
    first.join(15)
    second.join(15)
    assert not first.is_alive() and not second.is_alive()
    assert sorted(outcomes) == ["duplicate_fenced", "recovering_committed"]

    with Session(remediated_postgres) as verify:
        assert (
            verify.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(BackgroundJob.job_type == "OBS_RECOVERING_RACE")
                .where(BackgroundJob.request_key == key)
                .where(
                    BackgroundJob.status.in_(
                        (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RECOVERING)
                    )
                )
            )
            == 1
        )


@pytest.mark.destructive
def test_migration_0066_downgrade_and_reupgrade_are_consistent(
    disposable_postgres_database_factory,
) -> None:
    with disposable_postgres_database_factory() as database_url:
        config = Config("alembic.ini")
        config.attributes["database_url"] = database_url
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        try:
            command.downgrade(config, "0064_observability_downstream_correlation")
            with engine.connect() as connection:
                assert connection.scalar(text("select version_num from alembic_version")) == (
                    "0064_observability_downstream_correlation"
                )
                assert (
                    connection.scalar(
                        text("select to_regclass('public.background_job_fanout_roots')")
                    )
                    is None
                )
                legacy_predicate = connection.scalar(
                    text(
                        "select pg_get_expr(indpred, indrelid) from pg_index "
                        "where indexrelid='uq_background_jobs_active_request_key'::regclass"
                    )
                )
                assert "RECOVERING" not in str(legacy_predicate)
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert connection.scalar(text("select version_num from alembic_version")) == (
                    "0072_ceri_artifact_context_lineage"
                )
                assert (
                    connection.scalar(
                        text("select to_regclass('public.background_job_fanout_roots')")
                    )
                    == "background_job_fanout_roots"
                )
                active_predicate = connection.scalar(
                    text(
                        "select pg_get_expr(indpred, indrelid) from pg_index "
                        "where indexrelid='uq_background_jobs_active_request_key'::regclass"
                    )
                )
                assert "RECOVERING" in str(active_predicate)
                assert (
                    connection.scalar(
                        text(
                            "select count(*) from information_schema.columns "
                            "where table_name in ('background_workers','background_supervisors') "
                            "and column_name='control_loop_heartbeat_at'"
                        )
                    )
                    == 2
                )
        finally:
            engine.dispose()


def test_winner_idle_polling_has_stable_evidence_and_next_day_schedules(
    remediated_postgres,
) -> None:
    observed = datetime(2026, 9, 8, 23, 0, tzinfo=UTC)
    completed_session = latest_completed_session(observed)
    request_key = f"winner:h5-next-open:session:{completed_session.isoformat()}"
    with Session(remediated_postgres) as session:
        session.add(_job(WINNER_OUTCOME_MATURATION, request_key, JobStatus.COMPLETED))
        session.commit()
        before = (
            session.scalar(select(func.count()).select_from(BackgroundJob)),
            session.scalar(select(func.count()).select_from(BackgroundJobEnqueueAttempt)),
            session.scalar(select(func.count()).select_from(BackgroundJobFanoutRoot)),
        )
        fanout_metric_before = sum(
            sample.value
            for sample in operational_metrics.samples()
            if sample.name == "swinglens_job_fanout_total"
        )
        started = perf_counter()
        for _ in range(5_000):
            schedule_primary_h5_maturation(session, now=observed)
        idle_poll_elapsed = perf_counter() - started
        session.commit()
        after = (
            session.scalar(select(func.count()).select_from(BackgroundJob)),
            session.scalar(select(func.count()).select_from(BackgroundJobEnqueueAttempt)),
            session.scalar(select(func.count()).select_from(BackgroundJobFanoutRoot)),
        )
        fanout_metric_after = sum(
            sample.value
            for sample in operational_metrics.samples()
            if sample.name == "swinglens_job_fanout_total"
        )
        assert after == before
        assert fanout_metric_after == fanout_metric_before
        print(
            "OBS-PERF winner-idle-poll "
            f"polls=5000 elapsed_seconds={idle_poll_elapsed:.6f} "
            f"mean_us={idle_poll_elapsed / 5000 * 1_000_000:.3f} "
            f"jobs_before_after={before[0]}/{after[0]} "
            f"attempts_before_after={before[1]}/{after[1]} "
            f"roots_before_after={before[2]}/{after[2]} "
            f"fanout_metric_before_after={fanout_metric_before:.0f}/{fanout_metric_after:.0f}"
        )
        assert idle_poll_elapsed / 5_000 < 0.003

        next_day = observed + timedelta(days=1)
        scheduled = schedule_primary_h5_maturation(session, now=next_day)
        session.commit()
        assert scheduled.request_key != request_key
        assert session.scalar(select(func.count()).select_from(BackgroundJob)) == before[0] + 1
        assert (
            session.scalar(select(func.count()).select_from(BackgroundJobEnqueueAttempt))
            == before[1] + 1
        )
        assert (
            session.scalar(select(func.count()).select_from(BackgroundJobFanoutRoot))
            == before[2] + 1
        )


def test_winner_every_edge_status_and_two_pollers_have_zero_idle_evidence_delta(
    remediated_postgres,
) -> None:
    observed = datetime(2026, 9, 8, 23, 0, tzinfo=UTC)
    completed_session = latest_completed_session(observed)
    request_key = f"winner:h5-next-open:session:{completed_session.isoformat()}"
    statuses = (
        "QUEUED",
        "RUNNING",
        "COMPLETED",
        "PARTIAL",
        "FAILED",
        "BLOCKED",
        "RECOVERING",
        "STALLED",
        "CANCELLED",
        "STALE",
    )
    for status in statuses:
        with Session(remediated_postgres) as session:
            row = _job(WINNER_OUTCOME_MATURATION, request_key, status)
            session.add(row)
            session.commit()
            before = (
                session.scalar(select(func.count()).select_from(BackgroundJob)),
                session.scalar(select(func.count()).select_from(BackgroundJobEnqueueAttempt)),
                session.scalar(select(func.count()).select_from(BackgroundJobFanoutRoot)),
            )
            for _ in range(2_000):
                assert schedule_primary_h5_maturation(session, now=observed).id == row.id
            session.commit()
            after = (
                session.scalar(select(func.count()).select_from(BackgroundJob)),
                session.scalar(select(func.count()).select_from(BackgroundJobEnqueueAttempt)),
                session.scalar(select(func.count()).select_from(BackgroundJobFanoutRoot)),
            )
            assert after == before
            print(f"WINNER-IDLE status={status} deltas=jobs:0,attempts:0,roots:0")
            session.delete(row)
            session.commit()

    with Session(remediated_postgres) as session:
        prior = _job(
            WINNER_OUTCOME_MATURATION,
            "winner:h5-next-open:session:2026-09-04",
            JobStatus.RUNNING,
        )
        session.add(prior)
        session.commit()
        prior_id = prior.id
        before = session.scalar(select(func.count()).select_from(BackgroundJob))
        assert schedule_primary_h5_maturation(session, now=observed).id == prior_id
        session.commit()
        assert session.scalar(select(func.count()).select_from(BackgroundJob)) == before

    failures: list[Exception] = []

    def poll() -> None:
        try:
            with Session(remediated_postgres) as session:
                for _ in range(1_000):
                    assert schedule_primary_h5_maturation(session, now=observed).id == prior_id
        except Exception as exc:
            failures.append(exc)

    first = Thread(target=poll)
    second = Thread(target=poll)
    first.start()
    second.start()
    first.join(30)
    second.join(30)
    assert not first.is_alive() and not second.is_alive()
    assert failures == []


def test_per_root_fanout_counts_created_coalesced_and_depth(remediated_postgres) -> None:
    with Session(remediated_postgres) as session:
        for index in range(100):
            with root_action_scope("ADMINISTRATIVE", f"small-{index}"):
                enqueue_job(
                    session,
                    "OBS_SMALL_ROOT",
                    {},
                    request_key=f"small-{index}",
                )

        with root_action_scope("ADMINISTRATIVE", "winner-abnormal"):
            root = enqueue_job(
                session,
                WINNER_OUTCOME_MATURATION,
                {},
                request_key="winner-abnormal-root",
            )
            for index in range(22):
                with worker_job_scope(root):
                    enqueue_job(
                        session,
                        WINNER_OUTCOME_MATURATION,
                        {},
                        request_key=f"winner-child-{index}",
                    )
                    if index == 0:
                        enqueue_job(
                            session,
                            WINNER_OUTCOME_MATURATION,
                            {},
                            request_key="winner-child-0",
                        )

        with root_action_scope("ADMINISTRATIVE", "winner-deep"):
            deep_root = enqueue_job(
                session,
                WINNER_OUTCOME_MATURATION,
                {},
                request_key="winner-deep-root",
            )
            parent = deep_root
            for index in range(6):
                with worker_job_scope(parent):
                    parent = enqueue_job(
                        session,
                        WINNER_OUTCOME_MATURATION,
                        {},
                        request_key=f"winner-deep-{index}",
                    )
        session.commit()

        small = session.scalars(
            select(BackgroundJobFanoutRoot).where(
                BackgroundJobFanoutRoot.workflow_family == "OTHER"
            )
        ).all()
        abnormal = session.get(BackgroundJobFanoutRoot, root.root_correlation_id)
        deep = session.get(BackgroundJobFanoutRoot, deep_root.root_correlation_id)

        assert len(small) == 100
        assert all(row.created_jobs == 1 and row.total_descendant_count == 0 for row in small)
        assert abnormal is not None
        assert abnormal.attempted_enqueues == 24
        assert abnormal.created_jobs == 23
        assert abnormal.coalesced_attempts == 1
        assert abnormal.total_descendant_count == 22
        assert abnormal.warning_emitted is True
        assert deep is not None
        assert deep.created_jobs == 7
        assert deep.total_descendant_count == 6
        assert deep.maximum_depth == 6
        assert deep.warning_emitted is True

        parent.status = JobStatus.RUNNING
        parent.execution_token = "non-secret-test-token"
        parent.max_retries = 1
        parent.started_at = datetime.now(UTC)
        session.commit()
        before_retry = (
            deep.attempted_enqueues,
            deep.created_jobs,
            deep.coalesced_attempts,
            deep.total_descendant_count,
        )
        mark_job_failed_or_retry(
            session,
            parent,
            "transient fixture failure",
            execution_token="non-secret-test-token",
        )
        session.commit()
        session.refresh(deep)
        assert (
            deep.attempted_enqueues,
            deep.created_jobs,
            deep.coalesced_attempts,
            deep.total_descendant_count,
        ) == before_retry


@pytest.mark.performance
@pytest.mark.destructive
def test_operations_plans_at_representative_isolated_volumes(
    remediated_postgres, settings_factory
) -> None:
    if os.environ.get("SWINGLENS_RUN_OBSERVABILITY_LOAD_CERTIFICATION") != "1":
        pytest.skip("set SWINGLENS_RUN_OBSERVABILITY_LOAD_CERTIFICATION=1 for the 1M-row gate")

    def percentile(values: list[float], fraction: float) -> float:
        return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]

    enqueue_times: list[float] = []
    completion_times: list[float] = []
    with Session(remediated_postgres) as performance_session:
        jobs: list[BackgroundJob] = []
        for index in range(25):
            started = perf_counter()
            with root_action_scope("ADMINISTRATIVE", "observability-performance"):
                job = enqueue_job(
                    performance_session,
                    "OBS_PERFORMANCE_FIXTURE",
                    {},
                    request_key=f"obs-performance-{index}",
                )
            performance_session.commit()
            enqueue_times.append(perf_counter() - started)
            jobs.append(job)
        for index, job in enumerate(jobs):
            token = f"performance-token-{index}"
            job.status = JobStatus.RUNNING
            job.execution_token = token
            job.worker_id = "performance-worker"
            job.lease_owner = "performance-worker"
            performance_session.commit()
            started = perf_counter()
            mark_job_completed(
                performance_session,
                job,
                {"certification": True},
                execution_token=token,
            )
            performance_session.commit()
            completion_times.append(perf_counter() - started)
    print(
        "OBS-PERF database-hot-paths "
        f"enqueue_mean_ms={sum(enqueue_times) / len(enqueue_times) * 1000:.3f} "
        f"enqueue_p95_ms={percentile(enqueue_times, 0.95) * 1000:.3f} "
        f"completion_mean_ms={sum(completion_times) / len(completion_times) * 1000:.3f} "
        f"completion_p95_ms={percentile(completion_times, 0.95) * 1000:.3f}"
    )
    assert percentile(enqueue_times, 0.95) < 0.1
    assert percentile(completion_times, 0.95) < 0.1

    service = OperationsService(engine=remediated_postgres, settings=settings_factory())
    with remediated_postgres.begin() as connection:
        for volume in (10, 1_000, 100_000, 1_000_000):
            connection.execute(text("TRUNCATE background_job_fanout_roots"))
            connection.execute(text("TRUNCATE ceri_provider_request_telemetry"))
            connection.execute(
                text(
                    """
                    INSERT INTO background_job_fanout_roots (
                        root_correlation_id, workflow_family, first_occurred_at,
                        last_occurred_at, attempted_enqueues, created_jobs,
                        coalesced_attempts, rejected_attempts, total_descendant_count,
                        maximum_depth, warning_emitted, critical_emitted,
                        job_family_distribution_json
                    )
                    SELECT 'load-' || value, 'CERI_PIPELINE', now(), now(), 1, 1,
                           0, 0, 1, 0, false, false, '{}'::jsonb
                    FROM generate_series(1, :volume) AS value
                    """
                ),
                {"volume": volume},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO ceri_provider_request_telemetry (
                        provider, dataset, endpoint, status_code, call_cost, latency_ms,
                        retry_count, response_bytes, stored_bytes, observed_at
                    )
                    SELECT CASE WHEN value % 2 = 0 THEN 'SEC' ELSE 'EODHD' END,
                           'fixture', 'fixture', 200, 1, value % 1000, 0, 100, 100,
                           now() - ((value % 3600) * interval '1 second')
                    FROM generate_series(1, :volume) AS value
                    """
                ),
                {"volume": volume},
            )
            connection.execute(text("ANALYZE background_job_fanout_roots"))
            connection.execute(text("ANALYZE ceri_provider_request_telemetry"))
            root_plan = "\n".join(
                row[0]
                for row in connection.execute(
                    text(
                        """
                        EXPLAIN (ANALYZE, BUFFERS)
                        SELECT root_correlation_id, workflow_family, last_occurred_at
                        FROM background_job_fanout_roots
                        WHERE last_occurred_at >= now() - interval '24 hours'
                        ORDER BY last_occurred_at DESC
                        LIMIT 20
                        """
                    )
                )
            )
            provider_plan = "\n".join(
                row[0]
                for row in connection.execute(
                    text(
                        """
                        EXPLAIN (ANALYZE, BUFFERS)
                        WITH recent AS (
                            SELECT provider, error_code, retry_count, latency_ms, observed_at
                            FROM ceri_provider_request_telemetry
                            WHERE observed_at >= now() - interval '24 hours'
                            ORDER BY observed_at DESC
                            LIMIT 10000
                        )
                        SELECT provider, count(*),
                               percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                        FROM recent GROUP BY provider
                        """
                    )
                )
            )
            print(f"OBS-011 volume={volume}\nROOT\n{root_plan}\nPROVIDER\n{provider_plan}")
            started = perf_counter()
            with Session(bind=connection) as snapshot_session:
                snapshot = service.snapshot(snapshot_session)
            snapshot_elapsed = perf_counter() - started
            print(
                f"OBS-011 volume={volume} SNAPSHOT elapsed_seconds={snapshot_elapsed:.6f} "
                f"providers={len(snapshot['providers'])} roots={len(snapshot['recent_roots'])}"
            )
            if volume >= 100_000:
                assert "idx_fanout_roots_recent" in root_plan
                assert "ix_ceri_provider_telemetry_observed_provider" in provider_plan
            assert "Limit  (" in root_plan
            assert "Limit  (" in provider_plan
            assert snapshot_elapsed < 3.0
            assert len(snapshot["providers"]) <= 50
            assert len(snapshot["recent_roots"]) <= 20
