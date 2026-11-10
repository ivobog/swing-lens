"""Terminal bookkeeping cannot lend authority to malformed or foreign roots."""

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import (
    BackgroundJob,
    BackgroundWorker,
    PipelineDependency,
    RawCompanyRow,
    UploadRun,
)
from app.services.background_job_service import (
    _job_recovery_authority_predicate,
    claim_next_job,
    reconcile_jobs_for_worker_loss,
)
from app.services.pipeline_service import start_pipeline
from app.services.runtime_mutation_authority import RecoveryAuthority
from app.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_terminal_recovery_rejects_malformed_foreign_inactive_and_historical_owners(
    disposable_postgres_database,
    monkeypatch,
):
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
    )
    for module in (
        "app.services.pipeline_service",
        "app.services.pipeline_execution_authority",
        "app.services.background_job_service",
    ):
        monkeypatch.setattr(module + ".get_settings", lambda: settings)
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine, expire_on_commit=False) as db:
            older = BackgroundJob(job_type="TEST", status="COMPLETED", payload_json={})
            db.add(older)
            db.flush()
            pipelines = []
            for ticker in ("FIRST", "SECOND"):
                upload = UploadRun(filename=ticker + ".csv", status="COMPLETED", row_count=1)
                db.add(upload)
                db.flush()
                db.add(
                    RawCompanyRow(
                        run_id=upload.id, row_number=1, ticker=ticker, raw_json={"Symbol": ticker}
                    )
                )
                db.flush()
                pipelines.append(start_pipeline(db, upload.id))
            first, second = pipelines
            first_job = db.get(BackgroundJob, first.result_json["background_job_id"])
            second_job = db.get(BackgroundJob, second.result_json["background_job_id"])
            second_job.status = "CANCELLED"
            second.status = "BLOCKED"
            db.add(
                BackgroundWorker(
                    worker_id="terminal-owner", instance_id="terminal-owner-1", generation=1
                )
            )
            db.commit()
            claimed = claim_next_job(db, "terminal-owner", worker_instance_id="terminal-owner-1")
            assert claimed.id == first_job.id
            first.status = "COMPLETED"
            first.result_json = {**first.result_json, "published": "preserved"}
            db.commit()
            source_result = dict(first.result_json)
            dependency = PipelineDependency(
                pipeline_run_id=second.id,
                dependency_type="TEST",
                dependency_key="foreign",
                state="FAILED",
                continuation_step="SECTOR_ROTATION_SNAPSHOT",
                continuation_identity="foreign",
                root_job_id=second_job.id,
            )
            db.add(dependency)
            db.flush()
            forged = []

            def forged_job(payload=None, **overrides):
                values = dict(
                    job_type="FULL_PIPELINE",
                    status="RUNNING",
                    worker_id="terminal-owner",
                    worker_instance_id="terminal-owner-1",
                    execution_token="forged-" + str(len(forged)),
                    payload_json=dict(first_job.payload_json) if payload is None else payload,
                    related_run_id=first.upload_run_id,
                    scope_id=first.scope_id,
                    refresh_cycle_id=first.refresh_cycle_id,
                    acquisition_plan_id=first.acquisition_plan_id,
                )
                values.update(overrides)
                row = BackgroundJob(**values)
                db.add(row)
                forged.append(row)

            for value in (
                "invalid",
                "9223372036854775808",
                "999999999999999999999999",
                True,
                None,
                {"bad": 1},
                [first.id],
                0,
                "001",
            ):
                forged_job({**first_job.payload_json, "pipeline_run_id": value})
            foreign = {
                name: getattr(second, name)
                for name in (
                    "scope_id",
                    "refresh_cycle_id",
                    "acquisition_plan_id",
                )
            }
            forged_job({**first_job.payload_json, **foreign}, **foreign)
            forged_job({**first_job.payload_json, "scope_id": second.scope_id})
            forged_job(pipeline_dependency_id=dependency.id)
            forged_job(related_run_id=second.upload_run_id)
            db.commit()

            def selected():
                return set(
                    db.scalars(
                        select(BackgroundJob.id).where(
                            BackgroundJob.worker_id == "terminal-owner",
                            _job_recovery_authority_predicate(),
                        )
                    )
                )

            assert selected() == {first_job.id}
            first.execution_authority_state = "REVOKED"
            db.flush()
            assert selected() == set()
            first.execution_authority_state = "ACTIVE"
            db.flush()
            for limits in (
                {"job_activation_min_id": first_job.id, "pipeline_activation_min_id": second.id},
                {"job_activation_min_id": first_job.id + 1, "pipeline_activation_min_id": first.id},
            ):
                restricted = settings.model_copy(update=limits)
                with pytest.MonkeyPatch.context() as patch:
                    for module in (
                        "app.services.pipeline_execution_authority",
                        "app.services.background_job_service",
                    ):
                        patch.setattr(
                            module + ".get_settings", lambda selected=restricted: selected,
                        )
                    assert selected() == set()
            savepoint = db.begin_nested()
            first_job.parent_job_id = older.id
            db.flush()
            restricted = settings.model_copy(
                update={
                    "job_activation_min_id": first_job.id,
                    "pipeline_activation_min_id": first.id,
                }
            )
            with pytest.MonkeyPatch.context() as patch:
                for module in (
                    "app.services.pipeline_execution_authority",
                    "app.services.background_job_service",
                ):
                    patch.setattr(module + ".get_settings", lambda: restricted)
                assert selected() == set()
            savepoint.rollback()
            # A cancellation arriving after the terminal financial commit
            # records intent without changing the authoritative outcome.
            first_job.requested_cancel = True
            db.commit()
            outcome = reconcile_jobs_for_worker_loss(
                db,
                authority=RecoveryAuthority.normal("test.terminal_publication"),
                worker_id="terminal-owner",
                worker_instance_id="terminal-owner-1",
                reason="Worker exited after terminal publication.",
            )
            assert outcome.finalized_job_ids == (first_job.id,)
            assert set(outcome.untouched_job_ids) == {row.id for row in forged}
            assert outcome.fenced_job_ids == ()
            assert outcome.cancelled_job_ids == ()
            db.commit()
            assert first.status == first_job.status == "COMPLETED"
            assert first_job.requested_cancel is True
            assert first.result_json == source_result
            assert all(row.status == "RUNNING" for row in forged)
    finally:
        engine.dispose()
