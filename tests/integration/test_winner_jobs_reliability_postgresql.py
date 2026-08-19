from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from time import perf_counter

import pytest
from sqlalchemy import create_engine, event, func, insert, select
from sqlalchemy.orm import Session

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
    execute_latest_rescore_job,
)
from app.services.winner_probability.outcome_service import WinnerOutcomeRepository
from app.services.winner_probability.probability_estimator import ProbabilityEstimator
from app.services.winner_probability.reproduction_service import ReproductionService


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
                db.scalar(
                    select(func.count()).select_from(WinnerEstimateEvidenceMember)
                )
                or 0
            )
            # Generation manifests have their own member table; use persisted
            # manifest member counts without coupling this assertion to rows
            # created by latest-rescore estimates.
            generation_memberships = int(
                db.scalar(
                    select(func.sum(WinnerEvidenceManifest.member_count))
                    .join(
                        WinnerCohortStatistic,
                        WinnerCohortStatistic.evidence_manifest_id
                        == WinnerEvidenceManifest.id,
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
            assert prediction_selects <= 2
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
        source_data_cutoff_at=observed - timedelta(days=20),
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
            "source_data_cutoff_at": observed - timedelta(days=20),
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
                "capture_training_candidate": True,
                "evidence_training_eligible": True,
                "training_rejection_reasons": [],
            },
        }
        for index in range(1, row_count)
    ]
    prediction_ids = list(
        db.scalars(
            insert(WinnerPredictionSnapshot)
            .returning(WinnerPredictionSnapshot.id),
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


def _upgrade(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )
