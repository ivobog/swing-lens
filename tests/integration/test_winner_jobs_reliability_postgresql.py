from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.routers.winner_probability_routes as winner_routes
from app.db import get_db
from app.main import create_app
from app.models.tables import (
    BackgroundJob,
    UploadRun,
    WinnerCohortGeneration,
    WinnerCohortRefreshState,
    WinnerCohortStatistic,
    WinnerEstimateEvidenceMember,
    WinnerEvidenceManifest,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerProcessingRun,
    WinnerTargetStopOutcome,
)
from app.services.background_job_service import (
    JobLeaseLost,
    JobStatus,
    claim_next_job,
    enqueue_job,
    heartbeat_job,
    mark_job_deferred,
    recover_stale_jobs,
)
from app.services.background_worker import JobDeferred
from app.services.winner_probability.cohort_generation_service import (
    CohortGenerationService,
    CohortGenerationStatus,
    EvidenceWatermarkService,
    GenerationInvariantViolation,
    contract_for,
)
from app.services.winner_probability.cohort_materialization_service import (
    CohortMaterializationCancelled,
    CohortMaterializationService,
)
from app.services.winner_probability.cohort_refresh_planner import CohortRefreshPlanner
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.job_handlers import (
    WINNER_LATEST_RESCORE,
    WINNER_MATURATION_WORKFLOW_KEY,
    WINNER_OUTCOME_MATURATION,
    enqueue_outcome_maturation_workflow,
    execute_latest_rescore_job,
    execute_outcome_maturation_job,
)
from app.services.winner_probability.outcome_orchestration_service import (
    H5DrainResult,
)
from app.services.winner_probability.outcome_service import WinnerOutcomeRepository
from app.services.winner_probability.probability_estimator import ProbabilityEstimator
from app.services.winner_probability.reproduction_service import ReproductionService
from app.services.winner_probability.scheduler import schedule_primary_h5_maturation
from app.settings import Settings


def test_incident_shape_4227_retry_deferred_rows_creates_no_child(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    observed = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)
    retry_at = observed + timedelta(minutes=15)
    with Session(engine) as db:
        _seed_retry_deferred_outcomes(db, observed=observed, retry_at=retry_at, row_count=4227)
        root = enqueue_outcome_maturation_workflow(
            db,
            payload={"limit": 500, "max_batches": 10},
            trigger_source="MANUAL",
        )
        db.commit()
        root_id = root.id

    with Session(engine) as db:
        job = claim_next_job(db, "incident-reproduction-worker")
        assert job is not None and job.id == root_id
        db.commit()
        started = perf_counter()
        with pytest.raises(JobDeferred, match="RETRY_DEFERRED") as deferred:
            execute_outcome_maturation_job(db, job, now=observed)
        elapsed = perf_counter() - started
        assert deferred.value.delay_seconds == 900
        assert elapsed < 5
        mark_job_deferred(
            db,
            job,
            delay=timedelta(seconds=deferred.value.delay_seconds),
            reason=deferred.value.reason,
            execution_token=job.execution_token,
        )
        db.commit()

    with Session(engine) as db:
        repeated = enqueue_outcome_maturation_workflow(
            db, payload={"limit": 500}, trigger_source="MANUAL"
        )
        assert repeated.id == root_id
        assert getattr(repeated, "_coalesced", False)
        db.commit()

    with Session(engine) as db:
        jobs = list(
            db.scalars(
                select(BackgroundJob).where(BackgroundJob.job_type == WINNER_OUTCOME_MATURATION)
            )
        )
        attempt = db.scalar(
            select(WinnerProcessingRun).where(WinnerProcessingRun.background_job_id == root_id)
        )
        assert len(jobs) == 1
        assert jobs[0].parent_job_id is None
        assert jobs[0].root_job_id == root_id
        assert attempt is not None
        assert attempt.counts_json["due_total"] == 4227
        assert attempt.counts_json["retry_eligible_now"] == 0
        assert attempt.counts_json["retry_deferred"] == 4227
        assert attempt.counts_json["processed_h5"] == 0
        assert attempt.counts_json["eligible_remaining"] == 0
        assert attempt.counts_json["continuation_decision"] == "DEFER_SAME_JOB"
        assert attempt.terminal_reason_code == "RETRY_DEFERRED"
    engine.dispose()


def test_lineage_migration_keeps_historical_jobs_readable(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database, "0056_cover_ceri_freshness")
    engine = create_engine(disposable_postgres_database)
    with engine.begin() as connection:
        legacy_id = connection.scalar(
            text(
                """
                INSERT INTO background_jobs (job_type, status, payload_json)
                VALUES ('WINNER_OUTCOME_MATURATION', 'COMPLETED', '{}')
                RETURNING id
                """
            )
        )

    _upgrade(disposable_postgres_database)
    with Session(engine) as db:
        legacy = db.get(BackgroundJob, legacy_id)
        assert legacy is not None
        assert legacy.status == JobStatus.COMPLETED
        assert legacy.root_job_id is None
        assert legacy.parent_job_id is None
        assert legacy.continuation_depth is None
        assert legacy.trigger_source is None
    engine.dispose()


@pytest.mark.parametrize("trigger_pair", [("MANUAL", "MANUAL"), ("MANUAL", "SCHEDULER")])
def test_maturation_single_flight_coalesces_concurrent_root_triggers(
    disposable_postgres_database: str,
    trigger_pair: tuple[str, str],
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    barrier = Barrier(2)

    def request(trigger_source: str) -> int:
        with Session(engine) as db:
            barrier.wait()
            if trigger_source == "SCHEDULER":
                job = schedule_primary_h5_maturation(
                    db, now=datetime(2026, 9, 5, 11, 0, tzinfo=UTC)
                )
            else:
                job = enqueue_outcome_maturation_workflow(
                    db, payload={"limit": 500}, trigger_source="MANUAL"
                )
            db.commit()
            return job.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        job_ids = list(pool.map(request, trigger_pair))

    assert len(set(job_ids)) == 1
    with Session(engine) as db:
        jobs = list(
            db.scalars(
                select(BackgroundJob).where(BackgroundJob.job_type == WINNER_OUTCOME_MATURATION)
            )
        )
        assert len(jobs) == 1
        assert jobs[0].workflow_key == WINNER_MATURATION_WORKFLOW_KEY
        assert jobs[0].root_job_id == jobs[0].id
        assert jobs[0].continuation_depth == 0
        with pytest.raises(IntegrityError):
            enqueue_job(
                db,
                WINNER_OUTCOME_MATURATION,
                {"limit": 500},
                request_key="forced-duplicate",
                workflow_key=WINNER_MATURATION_WORKFLOW_KEY,
                coalesce=False,
                continuation_depth=0,
                trigger_source="MANUAL",
            )
        db.rollback()
    engine.dispose()


def test_two_concurrent_api_clients_receive_one_maturation_workflow(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    barrier = Barrier(2)
    real_enqueue = winner_routes.enqueue_outcome_maturation_workflow

    def synchronized_enqueue(*args, **kwargs):
        barrier.wait()
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(winner_routes, "enqueue_outcome_maturation_workflow", synchronized_enqueue)
    app = create_app(
        Settings(
            _env_file=None,
            database_url=disposable_postgres_database,
            job_worker_enabled=False,
            winner_probability_admin_enabled=True,
        )
    )

    def session_dependency():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session_dependency

    def post() -> dict:
        with TestClient(app) as client:
            response = client.post("/api/winner-probability/outcomes/process?limit=500")
            assert response.status_code == 200
            return response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _index: post(), range(2)))

    assert responses[0]["job_id"] == responses[1]["job_id"]
    assert sorted(response["coalesced"] for response in responses) == [False, True]
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count(BackgroundJob.id)).where(
                    BackgroundJob.job_type == WINNER_OUTCOME_MATURATION
                )
            )
            == 1
        )
    engine.dispose()


def test_recovered_maturation_job_retains_single_flight_identity(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    expired_at = datetime.now(UTC) - timedelta(minutes=5)
    with Session(engine) as db:
        root = enqueue_outcome_maturation_workflow(
            db, payload={"limit": 500}, trigger_source="SCHEDULER"
        )
        root.status = JobStatus.RUNNING
        root.execution_token = "stale-token"
        root.worker_id = "stale-worker"
        root.lease_owner = "stale-worker"
        root.lease_expires_at = expired_at
        db.commit()
        root_id = root.id

    with Session(engine) as db:
        assert recover_stale_jobs(db, stale_after_seconds=1) == 1
        db.commit()
        recovered = db.get(BackgroundJob, root_id)
        assert recovered is not None
        assert recovered.status == JobStatus.QUEUED
        assert recovered.root_job_id == root_id
        assert recovered.workflow_key == WINNER_MATURATION_WORKFLOW_KEY

        manual = enqueue_outcome_maturation_workflow(
            db, payload={"limit": 500}, trigger_source="MANUAL"
        )
        assert manual.id == root_id
        assert getattr(manual, "_coalesced", False)
        db.commit()

    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count(BackgroundJob.id)).where(
                    BackgroundJob.job_type == WINNER_OUTCOME_MATURATION
                )
            )
            == 1
        )
    engine.dispose()


def test_cohort_refresh_and_maturation_remain_independent_single_flights(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        maturation = enqueue_outcome_maturation_workflow(
            db, payload={"limit": 500}, trigger_source="MANUAL"
        )
        cohort = enqueue_job(
            db,
            "WINNER_COHORT_REFRESH",
            {},
            request_key="winner:cohort-refresh:stage1",
        )
        repeated = enqueue_outcome_maturation_workflow(
            db, payload={"limit": 500}, trigger_source="MANUAL"
        )
        db.commit()
        assert repeated.id == maturation.id
        assert cohort.id != maturation.id

    with Session(engine) as db:
        counts = {
            job_type: count
            for job_type, count in db.execute(
                select(BackgroundJob.job_type, func.count(BackgroundJob.id)).group_by(
                    BackgroundJob.job_type
                )
            )
        }
        assert counts[WINNER_OUTCOME_MATURATION] == 1
        assert counts["WINNER_COHORT_REFRESH"] == 1
    engine.dispose()


def test_manual_race_with_continuation_has_one_active_workflow(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        parent = BackgroundJob(
            job_type=WINNER_OUTCOME_MATURATION,
            status=JobStatus.PARTIAL,
            workflow_key=WINNER_MATURATION_WORKFLOW_KEY,
            request_key="parent",
            payload_json={},
            run_after=datetime.now(UTC),
            continuation_depth=0,
            trigger_source="MANUAL",
        )
        db.add(parent)
        db.flush()
        parent.root_job_id = parent.id
        db.commit()
        parent_id = parent.id

    def manual() -> int:
        with Session(engine) as db:
            job = enqueue_outcome_maturation_workflow(
                db, payload={"limit": 500}, trigger_source="MANUAL"
            )
            db.commit()
            return job.id

    def continuation() -> int:
        with Session(engine) as db:
            job = enqueue_job(
                db,
                WINNER_OUTCOME_MATURATION,
                {"limit": 500, "continuation": True},
                request_key="continuation-race",
                workflow_key=WINNER_MATURATION_WORKFLOW_KEY,
                single_flight_workflow=True,
                root_job_id=parent_id,
                parent_job_id=parent_id,
                continuation_depth=1,
                trigger_source="CONTINUATION",
            )
            db.commit()
            return job.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        job_ids = list(pool.map(lambda call: call(), (manual, continuation)))

    assert len(set(job_ids)) == 1
    with Session(engine) as db:
        active_count = db.scalar(
            select(func.count(BackgroundJob.id)).where(
                BackgroundJob.job_type == WINNER_OUTCOME_MATURATION,
                BackgroundJob.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
            )
        )
        assert active_count == 1
    engine.dispose()


def test_useful_slice_persists_one_child_with_root_parent_and_depth(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    observed = datetime(2026, 9, 5, 11, 0, tzinfo=UTC)
    with Session(engine) as db:
        _seed_retry_deferred_outcomes(db, observed=observed, retry_at=observed, row_count=1)
        root = enqueue_outcome_maturation_workflow(
            db, payload={"limit": 500}, trigger_source="MANUAL"
        )
        db.commit()
        root_id = root.id

    result = H5DrainResult(
        due_h5_next_open=1200,
        oldest_due_h5_session=date(2026, 8, 7),
        oldest_due_h5_age=20,
        processed_h5=500,
        matured_h5=0,
        pending_h5_after_cycle=1200,
        excluded_h5=0,
        failed_h5=0,
        target_stop_matured=0,
        unvisited_h5_after_cycle=700,
        last_successful_full_drain_at=None,
        due_total=1200,
        retry_eligible_now=1200,
        retry_deferred=0,
        unvisited_total=700,
        eligible_remaining=700,
    )
    with Session(engine) as db:
        job = claim_next_job(db, "useful-slice-worker")
        assert job is not None and job.id == root_id
        db.commit()
        response = execute_outcome_maturation_job(
            db, job, orchestration_service=_StaticDrain(result), now=observed
        )
        db.commit()
        assert response["continuation_decision"] == "ENQUEUE_CONTINUATION"

    with Session(engine) as db:
        jobs = list(
            db.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.job_type == WINNER_OUTCOME_MATURATION)
                .order_by(BackgroundJob.id)
            )
        )
        assert len(jobs) == 2
        parent, child = jobs
        assert parent.status == JobStatus.PARTIAL
        assert child.status == JobStatus.QUEUED
        assert child.workflow_key == parent.workflow_key == WINNER_MATURATION_WORKFLOW_KEY
        assert child.root_job_id == parent.id
        assert child.parent_job_id == parent.id
        assert child.continuation_depth == 1
        assert child.trigger_source == "CONTINUATION"
    engine.dispose()


def test_watermark_generation_idempotency_and_material_revision(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    observed = datetime(2026, 8, 17, 10, 33, 9, 896000, tzinfo=UTC)
    config = load_winner_probability_config()
    with Session(engine) as db:
        definition = _seed_material_evidence(db, observed=observed)
        db.commit()
        definition_id = definition.id

    service = EvidenceWatermarkService()
    generations = CohortGenerationService()
    with Session(engine) as db:
        definition = db.get(WinnerOutcomeDefinition, definition_id)
        assert definition is not None
        first = service.advance_to_current_material_evidence(
            db, outcome_definition=definition, config=config, observed_at=observed
        )
        assert first.advanced
        generation = generations.capture_or_resume(
            db,
            state=first.state,
            contract=contract_for(definition, config),
            requested_at=observed,
        )
        db.commit()
        generation_id = generation.id
        first_hash = first.state.desired_watermark_hash

    with Session(engine) as db:
        definition = db.get(WinnerOutcomeDefinition, definition_id)
        assert definition is not None
        repeated = service.advance_to_current_material_evidence(
            db,
            outcome_definition=definition,
            config=config,
            observed_at=observed + timedelta(milliseconds=227),
        )
        same_generation = generations.capture_or_resume(
            db,
            state=repeated.state,
            contract=contract_for(definition, config),
            requested_at=observed + timedelta(milliseconds=227),
        )
        assert not repeated.advanced
        assert repeated.state.desired_watermark_hash == first_hash
        assert same_generation.id == generation_id
        assert db.scalar(select(func.count(WinnerCohortGeneration.id))) == 1
        _append_material_revision(db, definition_id, observed + timedelta(hours=1))
        db.commit()

    with Session(engine) as db:
        definition = db.get(WinnerOutcomeDefinition, definition_id)
        revised = service.advance_to_current_material_evidence(
            db,
            outcome_definition=definition,
            config=config,
            observed_at=observed + timedelta(hours=1),
        )
        assert revised.advanced
        assert revised.state.desired_watermark_hash != first_hash
        db.commit()
    engine.dispose()


def test_concurrent_refresh_requests_coalesce_without_losing_desired_state(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = load_winner_probability_config()
    observed = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    with Session(engine) as db:
        definition = _seed_material_evidence(db, observed=observed)
        db.commit()
        definition_id = definition.id

    def request() -> int:
        with Session(engine) as db:
            definition = db.get(WinnerOutcomeDefinition, definition_id)
            result = CohortRefreshPlanner().request_for_current_evidence(
                db,
                outcome_definition=definition,
                config=config,
                observed_at=observed,
            )
            db.commit()
            assert result.job is not None
            return result.job.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        job_ids = list(pool.map(lambda _index: request(), range(2)))

    assert len(set(job_ids)) == 1
    with Session(engine) as db:
        state = db.scalar(select(WinnerCohortRefreshState))
        assert state is not None
        assert state.desired_target_stop_revision_id > 0
        assert (
            db.scalar(
                select(func.count(BackgroundJob.id)).where(
                    BackgroundJob.job_type == "WINNER_COHORT_REFRESH",
                    BackgroundJob.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
                )
            )
            == 1
        )
    engine.dispose()


def test_partial_generation_cannot_publish_and_recovery_closes_attempt(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = load_winner_probability_config()
    observed = datetime(2026, 8, 17, 13, 0, tzinfo=UTC)
    with Session(engine) as db:
        definition = _seed_material_evidence(db, observed=observed)
        watermark = EvidenceWatermarkService().advance_to_current_material_evidence(
            db, outcome_definition=definition, config=config, observed_at=observed
        )
        generation = CohortGenerationService().capture_or_resume(
            db,
            state=watermark.state,
            contract=contract_for(definition, config),
            requested_at=observed,
        )
        generation.status = CohortGenerationStatus.READY
        generation.planned_group_count = 2
        generation.completed_group_count = 1
        with pytest.raises(GenerationInvariantViolation, match="partially"):
            CohortGenerationService().publish(db, generation=generation, lease_guard=lambda: None)

        job = BackgroundJob(
            job_type="WINNER_COHORT_REFRESH",
            status=JobStatus.RUNNING,
            payload_json={},
            execution_token="old-token",
            worker_id="old-worker",
            lease_owner="old-worker",
            lease_expires_at=observed - timedelta(seconds=1),
            max_retries=3,
            retry_count=0,
            run_after=observed,
        )
        db.add(job)
        db.flush()
        attempt = WinnerProcessingRun(
            background_job_id=job.id,
            process_type="WINNER_COHORT_REFRESH",
            status=JobStatus.RUNNING,
            attempt_no=1,
            cohort_generation_id=generation.id,
            started_at=observed - timedelta(minutes=10),
            counts_json={},
            checkpoint_json={},
            metadata_json={},
        )
        db.add(attempt)
        db.commit()

    with Session(engine) as db:
        assert recover_stale_jobs(db, stale_after_seconds=1) == 1
        db.commit()
        attempt = db.scalar(select(WinnerProcessingRun))
        assert attempt.status == "LOST"
        assert attempt.terminal_reason_code == "LEASE_EXPIRED_SUPERSEDED"
    engine.dispose()


def test_bounded_generation_resume_coalescing_and_atomic_publication(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = load_winner_probability_config()
    observed = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)
    watermark_service = EvidenceWatermarkService()
    generation_service = CohortGenerationService()
    materializer = CohortMaterializationService(generation_service=generation_service)
    with Session(engine) as db:
        definition = _seed_material_evidence(db, observed=observed)
        advance = watermark_service.advance_to_current_material_evidence(
            db, outcome_definition=definition, config=config, observed_at=observed
        )
        generation = generation_service.capture_or_resume(
            db,
            state=advance.state,
            contract=contract_for(definition, config),
            requested_at=observed,
        )
        db.commit()
        generation_id = generation.id
        definition_id = definition.id

    with Session(engine) as db:
        generation = db.get(WinnerCohortGeneration, generation_id)
        definition = db.get(WinnerOutcomeDefinition, definition_id)
        first_slice = materializer.materialize_slice(
            db,
            generation=generation,
            outcome_definition=definition,
            config=config,
            lease_guard=db.commit,
            should_cancel=lambda: False,
            max_groups=1,
            max_wall_seconds=60,
        )
        assert first_slice.continuation_required
        assert first_slice.completed_groups == 1
        first_statistic = db.scalar(
            select(WinnerCohortStatistic).where(
                WinnerCohortStatistic.generation_id == generation_id
            )
        )
        assert first_statistic.metadata_json["cohort_level"] == "L5"

        _append_material_revision(db, definition_id, observed + timedelta(hours=1))
        db.commit()
        newer = watermark_service.advance_to_current_material_evidence(
            db,
            outcome_definition=definition,
            config=config,
            observed_at=observed + timedelta(hours=1),
        )
        assert newer.advanced
        db.commit()

        generation = db.get(WinnerCohortGeneration, generation_id)
        completed = materializer.materialize_slice(
            db,
            generation=generation,
            outcome_definition=definition,
            config=config,
            lease_guard=db.commit,
            should_cancel=lambda: False,
            max_groups=100,
            max_wall_seconds=60,
        )
        assert completed.status == CohortGenerationStatus.PUBLISHED
        assert completed.desired_watermark_advanced
        assert completed.completed_groups == completed.planned_groups == 6
        state = db.get(WinnerCohortRefreshState, generation.refresh_state_id)
        assert state.published_generation_id == generation_id
        assert state.published_watermark_hash != state.desired_watermark_hash
        assert db.scalar(select(func.count(WinnerProbabilityEstimate.id))) == 0

        replacement = generation_service.capture_or_resume(
            db,
            state=state,
            contract=contract_for(definition, config),
            requested_at=observed + timedelta(hours=1),
        )
        db.commit()
        replacement_id = replacement.id
        replacement = db.get(WinnerCohortGeneration, replacement_id)
        caught_up = materializer.materialize_slice(
            db,
            generation=replacement,
            outcome_definition=definition,
            config=config,
            lease_guard=db.commit,
            should_cancel=lambda: False,
            max_groups=100,
            max_wall_seconds=60,
        )
        assert caught_up.status == CohortGenerationStatus.PUBLISHED
        assert not caught_up.continuation_required
        old = db.get(WinnerCohortGeneration, generation_id)
        assert old.status == CohortGenerationStatus.SUPERSEDED
        state = db.get(WinnerCohortRefreshState, old.refresh_state_id)
        assert state.published_generation_id == replacement_id
        assert state.published_watermark_hash == state.desired_watermark_hash
        assert db.scalar(select(func.count(WinnerProbabilityEstimate.id))) == 0
        replacement_l5 = db.scalar(
            select(WinnerCohortStatistic)
            .where(WinnerCohortStatistic.generation_id == replacement_id)
            .where(WinnerCohortStatistic.metadata_json["cohort_level"].astext == "L5")
        )
        replacement_manifest = db.get(WinnerEvidenceManifest, replacement_l5.evidence_manifest_id)
        # The revision was written exactly at the observed watermark boundary.
        # Generation cutoffs include that revision, while their frozen IDs
        # still exclude evidence written after capture.
        assert replacement_manifest.payload_json["members"][0]["target_stop_revision"] == 2

        source_prediction = db.scalar(select(WinnerPredictionSnapshot).limit(1))
        current_prediction = WinnerPredictionSnapshot(
            run_id=source_prediction.run_id,
            ticker="CURRENT",
            prediction_as_of_date=date(2026, 8, 17),
            source_data_cutoff_at=observed + timedelta(hours=2),
            entry_schedule_status="RESOLVED",
            entry_data_status="AVAILABLE",
            eligibility_status="ELIGIBLE",
            feature_schema_version=config.feature_schema.version,
            feature_vector_hash="current-vector",
            config_hash=config.config_hash,
            calculation_version=config.engine.calculation_version,
            feature_json={"setup_family": "breakout"},
            source_ids_json={},
            warning_flags_json=[],
            lineage_json={"point_in_time_validated": True},
        )
        db.add(current_prediction)
        db.flush()
        decision_time_before = db.scalar(
            select(func.count(WinnerProbabilityEstimate.id)).where(
                WinnerProbabilityEstimate.estimate_kind == "DECISION_TIME"
            )
        )
        rescore = ProbabilityEstimator().create_latest_rescore_from_generation(
            db,
            prediction=current_prediction,
            outcome_definition=definition,
            generation=replacement,
            config=config,
        )
        assert rescore.status == "insufficient"
        assert rescore.estimate.point_probability is None
        assert rescore.estimate.cohort_generation_id == replacement_id
        assert rescore.estimate.evidence_manifest_id is not None
        assert (
            db.scalar(
                select(func.count(WinnerEstimateEvidenceMember.id)).where(
                    WinnerEstimateEvidenceMember.estimate_id == rescore.estimate.id
                )
            )
            == 0
        )
        reproduced = ReproductionService().reproduce_estimate(
            db, estimate_id=rescore.estimate.id, config=config
        )
        assert reproduced.matches
        duplicate = ProbabilityEstimator().create_latest_rescore_from_generation(
            db,
            prediction=current_prediction,
            outcome_definition=definition,
            generation=replacement,
            config=config,
        )
        assert duplicate.status == "duplicate"
        assert duplicate.estimate.id == rescore.estimate.id
        assert (
            db.scalar(
                select(func.count(WinnerProbabilityEstimate.id)).where(
                    WinnerProbabilityEstimate.estimate_kind == "DECISION_TIME"
                )
            )
            == decision_time_before
        )

        targeted_job = enqueue_job(
            db,
            WINNER_LATEST_RESCORE,
            {
                "cohort_generation_id": replacement_id,
                "scope": {
                    "type": "EXPLICIT_PREDICTIONS",
                    "prediction_ids": [current_prediction.id, source_prediction.id],
                },
                "batch_size": 1,
            },
            request_key=f"winner:latest-rescore:generation:{replacement_id}:fixture",
        )
        db.commit()
        targeted_job = claim_next_job(db, "rescore-worker", lease_seconds=60)
        db.commit()
        with pytest.raises(JobDeferred):
            execute_latest_rescore_job(db, targeted_job)
        db.commit()
        frozen_targets = targeted_job.payload_json["target_prediction_ids"]
        assert frozen_targets == sorted([current_prediction.id, source_prediction.id])
        assert targeted_job.payload_json["cursor_prediction_id"] == frozen_targets[0]
        completed_rescore = execute_latest_rescore_job(db, targeted_job)
        assert completed_rescore["remaining"] == 0
        assert targeted_job.payload_json["cursor_prediction_id"] == frozen_targets[-1]
        assert (
            db.scalar(
                select(func.count(WinnerProcessingRun.id)).where(
                    WinnerProcessingRun.background_job_id == targeted_job.id,
                    WinnerProcessingRun.status == JobStatus.RUNNING,
                )
            )
            == 0
        )

        # Cancellation of a later incomplete replacement cannot move the
        # published pointer away from the fully validated serving generation.
        _append_material_revision(db, definition_id, observed + timedelta(hours=2))
        db.commit()
        latest = watermark_service.advance_to_current_material_evidence(
            db,
            outcome_definition=definition,
            config=config,
            observed_at=observed + timedelta(hours=2),
        )
        cancelled = generation_service.capture_or_resume(
            db,
            state=latest.state,
            contract=contract_for(definition, config),
        )
        db.commit()
        decision_time_before_cancel = db.scalar(
            select(func.count(WinnerProbabilityEstimate.id)).where(
                WinnerProbabilityEstimate.estimate_kind == "DECISION_TIME"
            )
        )
        cancel_checks = {"count": 0}

        def cancel_during_evidence_load() -> bool:
            cancel_checks["count"] += 1
            return cancel_checks["count"] >= 2

        with pytest.raises(CohortMaterializationCancelled):
            materializer.materialize_slice(
                db,
                generation=cancelled,
                outcome_definition=definition,
                config=config,
                lease_guard=db.commit,
                should_cancel=cancel_during_evidence_load,
            )
        state = db.get(WinnerCohortRefreshState, latest.state.id)
        assert state.published_generation_id == replacement_id
        assert db.get(WinnerCohortGeneration, cancelled.id).status == "CANCELLED"
        assert (
            db.scalar(
                select(func.count(WinnerProbabilityEstimate.id)).where(
                    WinnerProbabilityEstimate.estimate_kind == "DECISION_TIME"
                )
            )
            == decision_time_before_cancel
        )
    engine.dispose()


@pytest.mark.performance
def test_390_row_42_cohort_heartbeat_commits_have_bounded_selects(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = load_winner_probability_config()
    observed = datetime(2026, 8, 17, 14, 30, tzinfo=UTC)
    with Session(engine) as db:
        definition = _seed_material_evidence(db, observed=observed)
        _expand_material_evidence(db, definition=definition, observed=observed, row_count=390)
        advance = EvidenceWatermarkService().advance_to_current_material_evidence(
            db, outcome_definition=definition, config=config, observed_at=observed
        )
        generation = CohortGenerationService().capture_or_resume(
            db,
            state=advance.state,
            contract=contract_for(definition, config),
            requested_at=observed,
        )
        db.commit()
        generation_id = generation.id
        definition_id = definition.id

    selects: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = statement.lstrip().lower()
        if normalized.startswith("select"):
            selects.append(normalized)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with Session(engine) as db:
            generation = db.get(WinnerCohortGeneration, generation_id)
            definition = db.get(WinnerOutcomeDefinition, definition_id)
            selects.clear()
            started = perf_counter()
            result = CohortMaterializationService().materialize_slice(
                db,
                generation=generation,
                outcome_definition=definition,
                config=config,
                lease_guard=db.commit,
                should_cancel=lambda: False,
                max_groups=100,
                max_wall_seconds=45,
            )
            elapsed = perf_counter() - started
            prediction_selects = sum(
                "winner_prediction_snapshots" in statement for statement in selects
            )
            manifest_memberships = int(
                db.scalar(select(func.count()).select_from(WinnerEstimateEvidenceMember)) or 0
            )
            # Generation manifests have their own member table; use persisted
            # manifest member counts without coupling this assertion to rows
            # created by latest-rescore estimates.
            generation_memberships = int(
                db.scalar(
                    select(func.sum(WinnerEvidenceManifest.member_count))
                    .join(
                        WinnerCohortStatistic,
                        WinnerCohortStatistic.evidence_manifest_id == WinnerEvidenceManifest.id,
                    )
                    .where(WinnerCohortStatistic.generation_id == generation_id)
                )
                or 0
            )
            assert manifest_memberships == 0
            assert result.status == CohortGenerationStatus.PUBLISHED
            assert result.evidence_rows_loaded == 390
            assert result.planned_groups == result.completed_groups == 42
            assert generation_memberships == 2_340
            # Temporal publication certification adds one set-based prediction
            # validation query; the bound remains constant at 390 rows/42 groups.
            assert prediction_selects <= 3
            assert len(selects) <= 260
            assert elapsed < 45
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
        engine.dispose()


def test_stale_lease_owner_cannot_publish_generation(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = load_winner_probability_config()
    observed = datetime(2026, 8, 17, 16, 0, tzinfo=UTC)
    with Session(engine) as setup:
        definition = _seed_material_evidence(setup, observed=observed)
        advance = EvidenceWatermarkService().advance_to_current_material_evidence(
            setup, outcome_definition=definition, config=config, observed_at=observed
        )
        generation = CohortGenerationService().capture_or_resume(
            setup,
            state=advance.state,
            contract=contract_for(definition, config),
        )
        generation.status = CohortGenerationStatus.READY
        generation.planned_group_count = 1
        generation.completed_group_count = 1
        job = BackgroundJob(
            job_type="WINNER_COHORT_REFRESH",
            status=JobStatus.RUNNING,
            payload_json={},
            execution_token="old-token",
            worker_id="worker-old",
            lease_owner="worker-old",
            lease_expires_at=observed - timedelta(seconds=1),
            max_retries=3,
            retry_count=0,
            run_after=observed,
        )
        setup.add(job)
        setup.commit()
        job_id = job.id
        generation_id = generation.id

    stale = Session(engine)
    try:
        stale_job = stale.get(BackgroundJob, job_id)
        with Session(engine) as recovery:
            assert recover_stale_jobs(recovery, stale_after_seconds=1) == 1
            recovery.commit()
            claimed = claim_next_job(recovery, "worker-new")
            assert claimed is not None and claimed.execution_token != "old-token"
            recovery.commit()

        generation = stale.get(WinnerCohortGeneration, generation_id)
        with pytest.raises(JobLeaseLost):
            CohortGenerationService().publish(
                stale,
                generation=generation,
                lease_guard=lambda: heartbeat_job(stale, stale_job, execution_token="old-token"),
            )
        stale.rollback()
    finally:
        stale.close()

    with Session(engine) as verify:
        generation = verify.get(WinnerCohortGeneration, generation_id)
        state = verify.get(WinnerCohortRefreshState, generation.refresh_state_id)
        assert generation.status == CohortGenerationStatus.READY
        assert state.published_generation_id is None
    engine.dispose()


@pytest.mark.performance
def test_h5_batch_context_prefetch_is_constant_query_count_for_thousands(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    observed = datetime(2026, 8, 17, 20, 0, tzinfo=UTC)
    row_count = 3_000
    with Session(engine) as db:
        upload = UploadRun(filename="h5-prefetch.csv", uploaded_at=observed, status="COMPLETED")
        db.add(upload)
        db.flush()
        db.execute(
            insert(WinnerPredictionSnapshot),
            [
                {
                    "id": index + 1,
                    "run_id": upload.id,
                    "ticker": f"T{index:04d}",
                    "prediction_as_of_date": date(2026, 8, 1),
                    "source_data_cutoff_at": observed - timedelta(days=10),
                    "entry_schedule_status": "RESOLVED",
                    "entry_data_status": "AVAILABLE",
                    "eligibility_status": "ELIGIBLE",
                    "feature_schema_version": "owpe-features-1.0.0",
                    "feature_vector_hash": f"vector-{index}",
                    "config_hash": "fixture",
                    "calculation_version": "fixture",
                    "feature_json": {"canonical_sector": "Unknown"},
                    "source_ids_json": {},
                    "warning_flags_json": [],
                    "lineage_json": {"point_in_time_validated": True},
                }
                for index in range(row_count)
            ],
        )
        db.execute(
            insert(WinnerForwardOutcome),
            [
                {
                    "id": index + 1,
                    "prediction_id": index + 1,
                    "entry_model": "NEXT_OPEN",
                    "horizon_sessions": 5,
                    "entry_session": date(2026, 8, 3),
                    "due_session": date(2026, 8, 7),
                    "status": "PENDING",
                    "revision": 1,
                    "is_current_revision": True,
                    "metadata_json": {},
                }
                for index in range(row_count)
            ],
        )
        db.commit()
        outcomes = list(db.scalars(select(WinnerForwardOutcome).order_by(WinnerForwardOutcome.id)))

        statements = 0

        def count_statement(*_args) -> None:
            nonlocal statements
            statements += 1

        event.listen(engine, "before_cursor_execute", count_statement)
        started = perf_counter()
        context = WinnerOutcomeRepository().load_batch_context(db, outcomes)
        elapsed = perf_counter() - started
        event.remove(engine, "before_cursor_execute", count_statement)

        assert len(context.predictions) == row_count
        assert statements == 3
        assert elapsed < 10
    engine.dispose()


def _seed_material_evidence(db: Session, *, observed: datetime) -> WinnerOutcomeDefinition:
    config = load_winner_probability_config()
    upload = UploadRun(
        filename="winner-reliability.csv",
        uploaded_at=observed - timedelta(days=30),
        status="COMPLETED",
    )
    db.add(upload)
    db.flush()
    primary = config.primary_outcome_definition
    definition = WinnerOutcomeDefinition(
        definition_id=primary.id,
        label="Winner reliability integration",
        entry_model=primary.entry_model,
        horizon_sessions=primary.horizon_sessions,
        target_pct=Decimal(str(primary.target_pct)),
        stop_pct=Decimal(str(primary.stop_pct)),
        same_bar_conflict_policy=primary.same_bar_conflict_policy,
        calculation_version=config.engine.calculation_version,
        config_hash=config.config_hash,
        is_primary=True,
        is_active=True,
        metadata_json={},
    )
    db.add(definition)
    db.flush()
    prediction = WinnerPredictionSnapshot(
        run_id=upload.id,
        ticker="TEST",
        prediction_as_of_date=date(2026, 7, 1),
        source_data_cutoff_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
        decision_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
        captured_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
        planned_entry_session=date(2026, 7, 2),
        entry_schedule_status="RESOLVED",
        entry_data_status="AVAILABLE",
        eligibility_status="ELIGIBLE",
        feature_schema_version=config.feature_schema.version,
        feature_vector_hash="fixture-vector",
        config_hash=config.config_hash,
        calculation_version=config.engine.calculation_version,
        feature_json={"setup_family": "breakout"},
        source_ids_json={},
        warning_flags_json=[],
        lineage_json={
            "point_in_time_validated": True,
            "point_in_time_validation": {"semantic_input_time": "VALID"},
            "capture_training_candidate": True,
            "evidence_training_eligible": True,
            "training_rejection_reasons": [],
        },
    )
    db.add(prediction)
    db.flush()
    forward = WinnerForwardOutcome(
        prediction_id=prediction.id,
        entry_model=definition.entry_model,
        horizon_sessions=definition.horizon_sessions,
        entry_session=date(2026, 7, 2),
        due_session=date(2026, 7, 8),
        status="MATURED",
        revision=1,
        is_current_revision=True,
        close_return_pct=Decimal("3.0"),
        mfe_pct=Decimal("4.0"),
        mae_pct=Decimal("-1.0"),
        source_bar_lineage_hash="lineage-1",
        source_revision_cutoff_at=observed - timedelta(days=10),
        matured_at=observed - timedelta(days=10),
        metadata_json={},
    )
    db.add(forward)
    db.flush()
    target = WinnerTargetStopOutcome(
        prediction_id=prediction.id,
        outcome_definition_id=definition.id,
        forward_outcome_id=forward.id,
        entry_model=definition.entry_model,
        horizon_sessions=definition.horizon_sessions,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        target_pct=definition.target_pct,
        stop_pct=definition.stop_pct,
        target_hit=True,
        stop_hit=False,
        first_event="TARGET_FIRST",
        primary_winner=True,
        optimistic_winner=True,
        conservative_winner=True,
        source_bar_lineage_hash="lineage-1",
        evaluated_at=observed - timedelta(days=10),
        metadata_json={},
    )
    db.add(target)
    db.flush()
    return definition


class _StaticDrain:
    def __init__(self, result: H5DrainResult) -> None:
        self.result = result

    def drain_due(self, _db: Session, **_kwargs) -> H5DrainResult:
        return self.result


def _seed_retry_deferred_outcomes(
    db: Session,
    *,
    observed: datetime,
    retry_at: datetime,
    row_count: int,
) -> None:
    config = load_winner_probability_config()
    upload = UploadRun(
        filename="winner-maturation-reliability.csv",
        uploaded_at=observed - timedelta(days=30),
        status="COMPLETED",
    )
    db.add(upload)
    db.flush()
    primary = config.primary_outcome_definition
    db.add(
        WinnerOutcomeDefinition(
            definition_id=primary.id,
            label="Winner maturation reliability",
            entry_model=primary.entry_model,
            horizon_sessions=primary.horizon_sessions,
            target_pct=Decimal(str(primary.target_pct)),
            stop_pct=Decimal(str(primary.stop_pct)),
            same_bar_conflict_policy=primary.same_bar_conflict_policy,
            calculation_version=config.engine.calculation_version,
            config_hash=config.config_hash,
            is_primary=True,
            is_active=True,
            metadata_json={},
        )
    )
    prediction_values = [
        {
            "run_id": upload.id,
            "ticker": f"R{index:05d}",
            "prediction_as_of_date": date(2026, 8, 3),
            "source_data_cutoff_at": datetime(2026, 7, 31, 20, 0, tzinfo=UTC),
            "decision_at": datetime(2026, 7, 31, 20, 0, tzinfo=UTC),
            "captured_at": datetime(2026, 7, 31, 20, 0, tzinfo=UTC),
            "planned_entry_session": date(2026, 8, 3),
            "entry_schedule_status": "RESOLVED",
            "entry_data_status": "AVAILABLE",
            "eligibility_status": "ELIGIBLE",
            "feature_schema_version": config.feature_schema.version,
            "feature_vector_hash": f"retry-vector-{index}",
            "config_hash": config.config_hash,
            "calculation_version": config.engine.calculation_version,
            "feature_json": {"setup_family": "breakout"},
            "source_ids_json": {},
            "warning_flags_json": [],
            "lineage_json": {
                "point_in_time_validated": True,
                "point_in_time_validation": {"semantic_input_time": "VALID"},
            },
        }
        for index in range(row_count)
    ]
    prediction_ids = list(
        db.scalars(
            insert(WinnerPredictionSnapshot).returning(WinnerPredictionSnapshot.id),
            prediction_values,
        )
    )
    db.execute(
        insert(WinnerForwardOutcome),
        [
            {
                "prediction_id": prediction_id,
                "entry_model": "NEXT_OPEN",
                "horizon_sessions": 5,
                "entry_session": date(2026, 8, 3),
                "due_session": date(2026, 8, 7),
                "status": "PENDING",
                "revision": 1,
                "is_current_revision": True,
                "pending_reason_code": "MISSING_HORIZON_BAR",
                "last_attempted_at": observed,
                "retry_not_before_at": retry_at,
                "metadata_json": {"pending_reason": "missing_horizon_bar"},
            }
            for prediction_id in prediction_ids
        ],
    )
    db.flush()


def _expand_material_evidence(
    db: Session,
    *,
    definition: WinnerOutcomeDefinition,
    observed: datetime,
    row_count: int,
) -> None:
    """Expand the one-row fixture to the incident's exact 390/42/2,340 shape."""
    if row_count < 1:
        raise ValueError("row_count must include the existing fixture row")
    config = load_winner_probability_config()
    existing = db.scalar(select(WinnerPredictionSnapshot).limit(1))
    existing.feature_json = _incident_feature_pattern(0)
    db.flush()
    prediction_values = [
        {
            "run_id": existing.run_id,
            "ticker": f"Q{index:04d}",
            "prediction_as_of_date": date(2026, 7, 1),
            "source_data_cutoff_at": datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
            "decision_at": datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
            "captured_at": datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
            "planned_entry_session": date(2026, 7, 2),
            "entry_schedule_status": "RESOLVED",
            "entry_data_status": "AVAILABLE",
            "eligibility_status": "ELIGIBLE",
            "feature_schema_version": config.feature_schema.version,
            "feature_vector_hash": f"incident-vector-{index}",
            "config_hash": config.config_hash,
            "calculation_version": config.engine.calculation_version,
            "feature_json": _incident_feature_pattern(index),
            "source_ids_json": {},
            "warning_flags_json": [],
            "lineage_json": {
                "point_in_time_validated": True,
                "point_in_time_validation": {"semantic_input_time": "VALID"},
                "capture_training_candidate": True,
                "evidence_training_eligible": True,
                "training_rejection_reasons": [],
            },
        }
        for index in range(1, row_count)
    ]
    prediction_ids = list(
        db.scalars(
            insert(WinnerPredictionSnapshot).returning(WinnerPredictionSnapshot.id),
            prediction_values,
        )
    )
    forward_values = [
        {
            "prediction_id": prediction_id,
            "entry_model": definition.entry_model,
            "horizon_sessions": definition.horizon_sessions,
            "entry_session": date(2026, 7, 2),
            "due_session": date(2026, 7, 8),
            "status": "MATURED",
            "revision": 1,
            "is_current_revision": True,
            "close_return_pct": Decimal(str((index % 11) - 5)),
            "mfe_pct": Decimal(str((index % 9) + 1)),
            "mae_pct": Decimal(str(-((index % 7) + 1))),
            "source_bar_lineage_hash": f"incident-lineage-{index}",
            "source_revision_cutoff_at": observed - timedelta(days=10),
            "matured_at": observed - timedelta(days=10),
            "metadata_json": {},
        }
        for index, prediction_id in enumerate(prediction_ids, start=1)
    ]
    forward_ids = list(
        db.scalars(
            insert(WinnerForwardOutcome).returning(WinnerForwardOutcome.id),
            forward_values,
        )
    )
    target_values = [
        {
            "prediction_id": prediction_id,
            "outcome_definition_id": definition.id,
            "forward_outcome_id": forward_id,
            "entry_model": definition.entry_model,
            "horizon_sessions": definition.horizon_sessions,
            "status": "MATURED",
            "revision": 1,
            "is_current_revision": True,
            "target_pct": definition.target_pct,
            "stop_pct": definition.stop_pct,
            "target_hit": index % 2 == 0,
            "stop_hit": index % 2 != 0,
            "first_event": "TARGET_FIRST" if index % 2 == 0 else "STOP_FIRST",
            "primary_winner": index % 2 == 0,
            "optimistic_winner": index % 2 == 0,
            "conservative_winner": index % 2 == 0,
            "source_bar_lineage_hash": f"incident-lineage-{index}",
            "evaluated_at": observed - timedelta(days=10),
            "metadata_json": {},
        }
        for index, (prediction_id, forward_id) in enumerate(
            zip(prediction_ids, forward_ids, strict=True), start=1
        )
    ]
    db.execute(insert(WinnerTargetStopOutcome), target_values)
    db.flush()


def _incident_feature_pattern(index: int) -> dict[str, str]:
    bases = (
        ("setup-0", "score-a"),
        ("setup-0", "score-b"),
        ("setup-1", "score-a"),
        ("setup-1", "score-b"),
        ("setup-2", "score-a"),
        ("setup-3", "score-a"),
        ("setup-4", "score-a"),
        ("setup-4", "score-b"),
    )
    pattern = index % 12
    if pattern < 8:
        base_index = pattern // 2 if pattern < 4 else pattern - 2
        variant = pattern % 2 if pattern < 4 else 0
    else:
        base_index = pattern - 4
        variant = 1
    setup, score = bases[base_index]
    return {
        "setup_family": setup,
        "score_band": score,
        "dual_score_band": f"dual-{base_index}-{variant}",
        "market_risk_state": f"risk-{base_index}",
        "sector_state": f"sector-{base_index}",
        "ranking_profile": "profile",
        "sector_leadership_bucket": f"lead-{base_index}",
        "market_regime_family": f"regime-{base_index}",
    }


def _append_material_revision(db: Session, definition_id: int, observed: datetime) -> None:
    current_forward = db.scalar(
        select(WinnerForwardOutcome).where(WinnerForwardOutcome.is_current_revision.is_(True))
    )
    current_target = db.scalar(
        select(WinnerTargetStopOutcome).where(WinnerTargetStopOutcome.is_current_revision.is_(True))
    )
    current_forward.is_current_revision = False
    current_forward.superseded_at = observed
    current_target.is_current_revision = False
    current_target.superseded_at = observed
    revision = current_forward.revision + 1
    lineage_hash = f"lineage-{revision}"
    revised_forward = WinnerForwardOutcome(
        prediction_id=current_forward.prediction_id,
        entry_model=current_forward.entry_model,
        horizon_sessions=current_forward.horizon_sessions,
        entry_session=current_forward.entry_session,
        due_session=current_forward.due_session,
        status="MATURED",
        revision=revision,
        is_current_revision=True,
        close_return_pct=Decimal("-1.0"),
        mfe_pct=Decimal("1.0"),
        mae_pct=Decimal("-3.0"),
        source_bar_lineage_hash=lineage_hash,
        source_revision_cutoff_at=observed,
        matured_at=observed,
        metadata_json={},
    )
    db.add(revised_forward)
    db.flush()
    db.add(
        WinnerTargetStopOutcome(
            prediction_id=current_target.prediction_id,
            outcome_definition_id=definition_id,
            forward_outcome_id=revised_forward.id,
            entry_model=current_target.entry_model,
            horizon_sessions=current_target.horizon_sessions,
            status="MATURED",
            revision=current_target.revision + 1,
            is_current_revision=True,
            target_pct=current_target.target_pct,
            stop_pct=current_target.stop_pct,
            target_hit=False,
            stop_hit=True,
            first_event="STOP_FIRST",
            primary_winner=False,
            optimistic_winner=False,
            conservative_winner=False,
            source_bar_lineage_hash=lineage_hash,
            evaluated_at=observed,
            metadata_json={},
        )
    )


def _upgrade(database_url: str, revision: str = "head") -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        check=True,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )
