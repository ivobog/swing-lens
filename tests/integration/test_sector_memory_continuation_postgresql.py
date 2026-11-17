"""A fenced real pipeline resumes Sector with its original sealed inputs."""

from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import pytest
from alembic.config import Config
from native_mutation_support import seed_price_frame
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker
from test_fundamental_ranker_v2 import _quality_values
from test_technical_work import _synthetic_frame

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import (
    BackgroundJob,
    BackgroundWorker,
    CombinedResult,
    FundamentalScore,
    MarketDataSessionDisposition,
    PipelineRun,
    PipelineStep,
    RankingResult,
    RawCompanyRow,
    SectorRotationRow,
    SectorRotationSnapshot,
    TechnicalScore,
    UploadRun,
)
from app.services import pipeline_executor
from app.services.background_job_service import (
    claim_next_job,
    reconcile_jobs_for_worker_loss,
    requeue_stalled_jobs,
)
from app.services.background_worker import default_job_handlers, run_worker_once
from app.services.configuration_delivery import (
    binding_reference,
    configuration_delivery_scope,
    load_configuration_delivery,
)
from app.services.contextual_effective_configuration import ContextualEffectiveConfiguration
from app.services.core_effective_configuration import CoreEffectiveConfiguration
from app.services.market_calculation_context_service import market_context_for_pipeline
from app.services.pipeline_service import start_pipeline
from app.services.runtime_mutation_authority import RecoveryAuthority
from app.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@pytest.mark.parametrize("published_before_fence", [False, True])
def test_memory_fence_requeue_real_handler_preserves_upstream_and_frozen_authority(
    disposable_postgres_database,
    monkeypatch,
    published_before_fence,
):
    from app.services.combined_decision import refresh_combined_results
    from app.services.fundamental_score_service import recalculate_run_fundamentals
    from app.services.market_regime_command_center import MarketRegimeCommandCenterService
    from app.services.ranking_profile_service import refresh_ranking_profile
    from app.services.sector_rotation_service import SectorRotationService
    from app.services.technical_score_service import score_run_technicals

    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database)
    settings = Settings(
        _env_file=None,
        database_url=disposable_postgres_database,
        ceri_enabled=False,
        ceri_run_capture_enabled=False,
        ceri_provider_ingest_enabled=False,
        setup_lifecycle_pipeline_step_enabled=False,
        winner_probability_capture_in_pipeline=False,
        market_data_prewarm_enabled=False,
        technical_v5_enabled=False,
        technical_v5_shadow_compare_enabled=False,
        technical_process_pool_enabled=False,
        technical_artifact_cache_writes_enabled=False,
    )
    for module in (
        "app.settings",
        "app.services.pipeline_service",
        "app.services.background_worker",
        "app.services.pipeline_executor",
        "app.services.technical_score_service",
    ):
        monkeypatch.setattr(module + ".get_settings", lambda: settings)
    engine = create_engine(disposable_postgres_database)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    authority = RecoveryAuthority.normal("test.sector_memory_continuation")
    now = datetime.now(UTC)
    memory = {
        "observed_at": now.isoformat(),
        "registered_pid": 12345,
        "worker_instance_id": "sector-owner-1",
        "private_bytes": 6442450945,
        "rss_bytes": 6000000000,
        "critical_mb": 6144,
        "critical_bytes": 6442450944,
    }
    upstream_models = (FundamentalScore, TechnicalScore, CombinedResult, RankingResult)

    def upstream_rows(db, run_id):
        return {
            model.__name__: [
                (row.id, row.evidence_id)
                for row in db.scalars(
                    select(model).where(model.run_id == run_id).order_by(model.id)
                )
            ]
            for model in upstream_models
        }

    try:
        with sessions() as db:
            upload = UploadRun(filename="sector-memory.csv", status="COMPLETED", row_count=1)
            db.add(upload)
            db.flush()
            db.add(
                RawCompanyRow(
                    run_id=upload.id,
                    row_number=1,
                    ticker="ACME",
                    sector="Technology",
                    sector_canonical="Information Technology",
                    raw_json={"Symbol": "ACME", **_quality_values()},
                )
            )
            db.flush()
            pipeline = start_pipeline(db, upload.id)
            db.commit()
            pipeline_id, run_id = pipeline.id, upload.id
            cutoff = market_context_for_pipeline(db, pipeline)
            delivery = load_configuration_delivery(
                db, binding_reference(db, pipeline_run_id=pipeline.id)
            )
            with configuration_delivery_scope(delivery):
                frame = _synthetic_frame()
                frame["date"] = pd.bdate_range(
                    end=cutoff.latest_completed_session, periods=len(frame)
                )
                for ticker in ("ACME", "SPY", "QQQ", "XLK"):
                    seed_price_frame(db, ticker, frame, cutoff.cutoff_at)
                from app.services.market_data_session_readiness import classify_session_readiness

                db.add(
                    MarketDataSessionDisposition(
                        pipeline_run_id=pipeline.id,
                        upload_run_id=run_id,
                        market_calculation_context_id=cutoff.context_id,
                        ticker="ACME",
                        expected_session=cutoff.latest_completed_session,
                        latest_bar_session=cutoff.latest_completed_session,
                        **classify_session_readiness(
                            ticker="ACME",
                            expected_session=cutoff.latest_completed_session,
                            latest_bar_session=cutoff.latest_completed_session,
                            lifecycle=None,
                        ),
                    )
                )
                db.flush()
                args = dict(market_cutoff=cutoff, pipeline_run_id=pipeline.id)

                def config(name):
                    cls = (
                        ContextualEffectiveConfiguration
                        if name.startswith("contextual.")
                        else CoreEffectiveConfiguration
                    )
                    return cls(delivery.configurations[name].snapshot)

                fundamental = recalculate_run_fundamentals(
                    db, run_id, **args, effective_configuration=config("core.fundamental")
                )[0]
                technical = score_run_technicals(
                    db,
                    run_id,
                    **args,
                    frozen_tickers=("ACME",),
                    effective_configuration=config("core.technical"),
                )[0]
                MarketRegimeCommandCenterService().build_snapshot(
                    db,
                    run_id=run_id,
                    market_cutoff=cutoff,
                    effective_configuration=config("contextual.regime"),
                )
                refresh_combined_results(
                    db,
                    run_id,
                    **args,
                    effective_configuration=config("core.combined"),
                    source_evidence={
                        "ACME": {
                            "fundamental": fundamental.evidence_id,
                            "technical": technical.evidence_id,
                        }
                    },
                )
                for key in delivery.configurations:
                    if key.startswith("core.ranking:"):
                        refresh_ranking_profile(
                            db,
                            run_id,
                            key.split(":", 1)[1],
                            **args,
                            effective_configuration=config(key),
                        )
                if published_before_fence:
                    SectorRotationService().build_sector_rotation_snapshot(
                        db,
                        run_id=run_id,
                        market_cutoff=cutoff,
                        effective_configuration=config("contextual.sector"),
                    )
                db.commit()
            pipeline.status = pipeline.current_step = "SECTOR_ROTATION_SNAPSHOT"
            rankings = list(db.scalars(select(RankingResult).where(RankingResult.run_id == run_id)))
            pipeline.result_json = {
                **pipeline.result_json,
                "market_data_mode": "IB_GATEWAY",
                "ranking_results": len(rankings),
                "ranking_profiles": len({row.ranking_profile for row in rankings}),
                "ranking_status": "COMPLETED",
            }
            steps = list(
                db.scalars(select(PipelineStep).where(PipelineStep.pipeline_run_id == pipeline.id))
            )
            sector = next(step for step in steps if step.step_name == "SECTOR_ROTATION_SNAPSHOT")
            for step in steps:
                if step.step_order < sector.step_order:
                    step.status = "COMPLETED"
                    step.started_at = step.completed_at = now
            sector.status, sector.started_at = "RUNNING", now
            db.add(
                BackgroundWorker(
                    worker_id="sector-owner", instance_id="sector-owner-1", generation=1
                )
            )
            db.commit()
            with configuration_delivery_scope(delivery):
                # Damage one complete profile in a rolled-back disposable
                # transaction. Other profiles still cover the exact ticker set.
                savepoint = db.begin_nested()
                db.execute(
                    delete(RankingResult).where(
                        RankingResult.run_id == run_id,
                        RankingResult.ranking_profile == rankings[0].ranking_profile,
                    )
                )
                with pytest.raises(ValueError, match="SECTOR_RESUME_RANKING_COHORT_CHANGED"):
                    pipeline_executor._validate_sector_resume_checkpoint(
                        db,
                        pipeline=pipeline,
                        upload_run_id=run_id,
                        market_cutoff=cutoff,
                    )
                savepoint.rollback()
                if published_before_fence:
                    child = db.scalar(
                        select(SectorRotationRow).where(
                            SectorRotationRow.snapshot_id.in_(
                                select(SectorRotationSnapshot.id).where(
                                    SectorRotationSnapshot.run_id == run_id,
                                )
                            ),
                        )
                    )
                    with db.no_autoflush:
                        child.ticker_count += 1
                        with pytest.raises(
                            ValueError, match="MUTATION_SOURCE_FINANCIAL_VALUE_MISMATCH"
                        ):
                            pipeline_executor._validate_sector_resume_checkpoint(
                                db,
                                pipeline=pipeline,
                                upload_run_id=run_id,
                                market_cutoff=cutoff,
                            )
                    db.expire(child)
            job = claim_next_job(db, "sector-owner", worker_instance_id="sector-owner-1")
            assert job.id == pipeline.result_json["background_job_id"]
            job.progress_stage = "SECTOR_ROTATION_SNAPSHOT"
            job.progress_current_item = "sector-snapshot"
            job.operational_metadata_json = {
                **job.operational_metadata_json,
                "sector_progress": {"phase": "PERSISTENCE_STARTED"},
            }
            db.commit()
            job_id = job.id
            now = datetime.now(UTC)
            memory["observed_at"] = now.isoformat()
            frozen = {
                name: getattr(pipeline, name)
                for name in (
                    "scope_id",
                    "refresh_cycle_id",
                    "acquisition_plan_id",
                    "upload_run_id",
                )
            }
            original_payload = dict(job.payload_json)
            original_upstream = upstream_rows(db, run_id)
            original_steps = {
                step.id: (step.started_at, step.completed_at, step.retry_count)
                for step in steps
                if step.step_order < sector.step_order
            }
            original_sector = [
                (row.id, row.evidence_id)
                for row in db.scalars(
                    select(SectorRotationSnapshot).where(SectorRotationSnapshot.run_id == run_id)
                )
            ]
        with sessions() as db:
            assert reconcile_jobs_for_worker_loss(
                db,
                authority=authority,
                worker_id="sector-owner",
                worker_instance_id="sector-owner-1",
                reason="Worker exceeded 6144 MB memory budget.",
                failure_code="WORKER_MEMORY_CRITICAL",
                memory_evidence=memory,
                now=now,
            ).fenced_job_ids == (job_id,)
            assert requeue_stalled_jobs(db, authority=authority, job_ids=[job_id], now=now) == 1
            db.commit()
            job = db.get(BackgroundJob, job_id)
            assert job.payload_json == {
                **original_payload,
                "resume_from_step": "SECTOR_ROTATION_SNAPSHOT",
            }
            retained_memory = job.operational_metadata_json["progress_recovery"]["memory_evidence"]
            assert all(retained_memory[key] == value for key, value in memory.items())
            assert retained_memory["stage"] == "SECTOR_ROTATION_SNAPSHOT"
            assert retained_memory["sector_phase"] == "PERSISTENCE_STARTED"
            assert retained_memory["attempt_number"] == 1

        calls = []
        original_dependencies = pipeline_executor.PipelineExecutionDependencies

        def forbidden(*_args, **_kwargs):
            raise AssertionError(
                "Completed upstream stage or provider called during Sector recovery"
            )

        def dependencies(**kwargs):
            base = original_dependencies(**kwargs)
            build_sector = base.build_sector_rotation_snapshot

            def sector_builder(*args, **values):
                calls.append("sector")
                return build_sector(*args, **values)

            return replace(
                base,
                recalculate_fundamentals=forbidden,
                score_technicals=forbidden,
                check_ib_gateway=forbidden,
                build_fetch_plan=forbidden,
                execute_fetch_plan=forbidden,
                build_market_regime_snapshot=forbidden,
                refresh_combined=forbidden,
                refresh_rankings=forbidden,
                build_sector_rotation_snapshot=sector_builder,
            )

        monkeypatch.setattr(pipeline_executor, "PipelineExecutionDependencies", dependencies)
        handlers = default_job_handlers()
        actual_handler = handlers["FULL_PIPELINE"]
        actual_owner = {}

        def capture_claimed_owner(db, job):
            actual_owner.update(
                {
                    name: getattr(job, name)
                    for name in (
                        "worker_id",
                        "worker_instance_id",
                        "lease_owner",
                        "execution_token",
                        "locked_at",
                        "heartbeat_at",
                        "lease_expires_at",
                    )
                }
            )
            return actual_handler(db, job)

        handlers["FULL_PIPELINE"] = capture_claimed_owner
        assert run_worker_once(
            worker_id="sector-replacement",
            worker_instance_id="sector-replacement-1",
            session_factory=sessions,
            handlers=handlers,
            queues=("interactive", "broker", "background"),
            heartbeat_interval_seconds=0.25,
            stale_after_seconds=900,
        )
        with sessions() as db:
            pipeline = db.get(PipelineRun, pipeline_id)
            job = db.get(BackgroundJob, job_id)
            assert pipeline.status in {"COMPLETED", "PARTIAL"}, (
                pipeline.status,
                pipeline.error_message,
            )
            assert job.status == "COMPLETED", job.error_message
            assert {name: getattr(pipeline, name) for name in frozen} == frozen
            assert upstream_rows(db, run_id) == original_upstream
            assert market_context_for_pipeline(db, pipeline) == cutoff
            for step in db.scalars(
                select(PipelineStep).where(PipelineStep.pipeline_run_id == pipeline_id)
            ):
                if step.id in original_steps:
                    assert (step.started_at, step.completed_at, step.retry_count) == original_steps[
                        step.id
                    ]
            assert calls == ([] if published_before_fence else ["sector"])
            final_sector = [
                (row.id, row.evidence_id)
                for row in db.scalars(
                    select(SectorRotationSnapshot).where(SectorRotationSnapshot.run_id == run_id)
                )
            ]
            assert len(final_sector) == 1
            if published_before_fence:
                assert final_sector == original_sector
            assert pipeline.result_json["recovery_continuation"]["validation_state"] == "VALIDATED"
            if published_before_fence:
                # Simulate terminal financial publication before the root job's
                # final status commit. Recovery must retain the winning result.
                terminal_status = pipeline.status
                terminal_result = dict(pipeline.result_json)
                job.status, job.completed_at, job.payload_json = "RUNNING", None, original_payload
                job.result_json = None
                for name, value in actual_owner.items():
                    setattr(job, name, value)
                db.commit()
        if published_before_fence:
            calls.clear()
            with sessions() as db:
                reconciled = reconcile_jobs_for_worker_loss(
                    db,
                    authority=authority,
                    worker_id=actual_owner["worker_id"],
                    worker_instance_id=actual_owner["worker_instance_id"],
                    reason="Worker exited after terminal financial publication.",
                )
                assert reconciled.finalized_job_ids == (job_id,)
                assert reconciled.fenced_job_ids == reconciled.untouched_job_ids == ()
                assert requeue_stalled_jobs(db, authority=authority, job_ids=[job_id]) == 0
                db.commit()
                assert db.get(PipelineRun, pipeline_id).status == terminal_status
            assert not run_worker_once(
                worker_id="sector-replacement",
                worker_instance_id="sector-replacement-1",
                session_factory=sessions,
                handlers=default_job_handlers(),
                stale_after_seconds=900,
                queues=("interactive", "broker", "background"),
                heartbeat_interval_seconds=0.25,
            )
            with sessions() as db:
                pipeline = db.get(PipelineRun, pipeline_id)
                assert pipeline.status == terminal_status
                assert pipeline.result_json == terminal_result
                closed = db.get(BackgroundJob, job_id)
                assert closed.status == "COMPLETED"
                assert closed.execution_token is closed.worker_id is closed.lease_owner is None
                receipt = closed.operational_metadata_json["terminal_publication_recovery"]
                assert receipt["cause_code"] == "WORKER_PROCESS_LOST"
                assert calls == []
                assert upstream_rows(db, run_id) == original_upstream
    finally:
        engine.dispose()
