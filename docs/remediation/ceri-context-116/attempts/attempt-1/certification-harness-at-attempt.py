"""Native historical CERI reenactment, restricted to the retained disposable DB.

Uses the retained configuration and semantic authority from job 2546. An
operator-resume transition is exercised ONLY on the disposable parent copy;
the original failed job and old scores are retained. No full pipeline starts.
"""

from __future__ import annotations

import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from scripts.qa.ceri_validation_environment import REPORT, ROOT, SOURCE_CLONE, credentials


def main():
    source_url = credentials()
    assert SOURCE_CLONE.startswith("swinglens_qa_ceri_") and SOURCE_CLONE != source_url.database
    metadata = json.loads((REPORT / "disposable-environment.json").read_text())
    assert metadata["database"] == SOURCE_CLONE and metadata["post_data_restore_exit"] == 0
    qa_url = source_url.set(database=SOURCE_CLONE).render_as_string(hide_password=False)
    os.environ.update(DATABASE_URL=qa_url, SWINGLENS_DATABASE_SAFETY_CONTEXT="DISPOSABLE_TEST")
    from alembic.config import Config
    from sqlalchemy import create_engine, func, select, text
    from sqlalchemy.orm import Session

    from alembic import command
    from app.database_safety import DatabaseSafetyContext, assert_disposable_database
    from app.models.ceri_tables import CeriScoreSnapshot
    from app.models.tables import BackgroundJob, PipelineRun, RawCompanyRow
    from app.services.background_job_service import claim_next_job, enqueue_job, mark_job_completed
    from app.services.ceri.capture_service import _existing_snapshot_company_ids
    from app.services.ceri.job_handlers import (
        execute_alert_rebuild_job,
        execute_capture_run_job,
        execute_change_detection_job,
    )
    from app.services.ceri.score_population import certified_scores
    from app.services.configuration_delivery import (
        binding_reference,
        configuration_delivery_scope,
        load_configuration_delivery,
    )
    from app.services.contextual_effective_configuration import ContextualEffectiveConfiguration
    from app.services.market_calculation_context_service import (
        market_context_for_pipeline,
        resolve_pipeline_ceri_context,
    )
    from app.services.pipeline_dependency_service import prepare_ceri_workflow_dependency
    from app.services.pipeline_execution_authority import ACTIVE_EXECUTION_AUTHORITY
    from app.services.pipeline_executor import _validate_ceri_completion_barrier
    from app.services.pipeline_state_machine import transition_pipeline
    from app.services.scope_refresh_adoption import (
        bind_semantic_authority,
        require_semantic_authority,
    )
    from app.services.worker_registry import register_worker

    result = {"decision": "BLOCKED", "database": SOURCE_CLONE, "steps": {}}
    path = REPORT / "context-116-certification.json"

    def retain():
        path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    engine = create_engine(qa_url, pool_pre_ping=True)
    try:
        identity = assert_disposable_database(qa_url, active_database_url=source_url)
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes.update(
            database_url=qa_url,
            disposable_database_identity=identity,
            database_safety_context=DatabaseSafetyContext.DISPOSABLE_TEST,
        )
        command.upgrade(config, "0096_ceri_native_context")
        result["steps"]["migration"] = "PASS"
        retain()

        def history(db, maximum):
            return [
                list(row)
                for row in db.execute(
                    text("""SELECT s.id,s.evidence_hash,
                md5(to_jsonb(s)::text),md5(to_jsonb(e)::text),
                (SELECT md5(coalesce(jsonb_agg(to_jsonb(d) ORDER BY d.id),'[]'::jsonb)::text)
                 FROM ceri_evidence_dispositions d WHERE d.ceri_snapshot_id=s.id)
                FROM ceri_score_snapshots s
                LEFT JOIN core_calculation_evidence e ON e.id=s.evidence_id
                WHERE s.id <= :maximum ORDER BY s.id"""),
                    {"maximum": maximum},
                )
            ]

        with Session(engine) as db:
            maximum = db.scalar(select(func.max(CeriScoreSnapshot.id)))
            baseline = history(db, maximum)
            job_hash = db.scalar(
                text("SELECT md5(to_jsonb(j)::text) FROM background_jobs j WHERE id=2546")
            )
            manifest_hash = db.scalar(
                text(
                    "SELECT md5(to_jsonb(m)::text) "
                    "FROM transition_decision_handoff_manifests m WHERE id=41"
                )
            )
            assert manifest_hash
            (REPORT / "historical-before.json").write_text(json.dumps(baseline), encoding="utf-8")
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(CeriScoreSnapshot)
                    .where(CeriScoreSnapshot.calculation_context_id == 113)
                )
                == 447
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(CeriScoreSnapshot)
                    .where(CeriScoreSnapshot.calculation_context_id == 116)
                )
                == 0
            )
            original = db.get(BackgroundJob, 2546)
            payload = dict(original.payload_json)
            anchor = payload["effective_configuration_anchor"]
            assert anchor == binding_reference(db, pipeline_run_id=66)
            delivery = load_configuration_delivery(db, anchor)
            frozen = ContextualEffectiveConfiguration(
                delivery.configurations["contextual.ceri"].snapshot
            )
            assert (
                frozen.ceri_config().config_hash
                == "28adaf159aa4a4af9a62827496436c81be7da239b58bade6b7272897e49e0d9a"
            )
            result["frozen_anchor"] = anchor
            result["frozen_configuration"] = frozen.snapshot.semantic_hash
            owner = db.get(PipelineRun, 66)
            # Reenact pre-failure execution via the real state machine. Never
            # manufacture ACTIVE authority from a revoked historical owner.
            if owner.execution_authority_state != ACTIVE_EXECUTION_AUTHORITY:
                raise RuntimeError("GENUINE_PIPELINE_EXECUTION_AUTHORITY_UNAVAILABLE")
            authority = require_semantic_authority(original)
            assert authority == require_semantic_authority(owner)
            original_workflow = original.workflow_key
            upstream = list(
                db.execute(
                    select(BackgroundJob.job_type, BackgroundJob.status).where(
                        BackgroundJob.workflow_key == original_workflow,
                        BackgroundJob.job_type != "CERI_CAPTURE_RUN",
                    )
                )
            )
            assert upstream and all(status == "COMPLETED" for _, status in upstream)
            result["genuine_upstream_job_counts"] = {
                kind: sum(1 for observed, _ in upstream if observed == kind) for kind, _ in upstream
            }
            # Repair sequence positions in a partial historical restore, without
            # touching any retained row or financial envelope.
            for table in [
                "background_jobs",
                "pipeline_dependencies",
                "ceri_processing_runs",
                "ceri_score_snapshots",
                "core_calculation_evidence",
                "core_calculation_current_projections",
                "ceri_change_events",
                "ceri_alert_events",
                "transition_preflight_plans",
                "transition_decision_handoff_manifests",
            ]:
                sequence = db.scalar(
                    text("SELECT pg_get_serial_sequence(:table,'id')"), {"table": table}
                )
                if sequence:
                    max_id = db.scalar(text('SELECT coalesce(max(id),1) FROM "' + table + '"'))
                    db.execute(
                        text("SELECT setval(CAST(:sequence AS regclass),:maximum,true)"),
                        {"sequence": sequence, "maximum": max_id},
                    )
            transition_pipeline(db, owner, "PENDING", actor="operator", operator_resume=True)
            workflow = f"ceri:pipeline:owner:66:certification:{uuid4().hex[:12]}"
            owner.result_json = {**owner.result_json, "ceri_provider_workflow_key": workflow}
            transition_pipeline(
                db, owner, "WAITING_FOR_CERI_COMPLETION", actor="pipeline_orchestrator"
            )
            dependency = prepare_ceri_workflow_dependency(
                db,
                pipeline=owner,
                workflow_key=workflow,
                resume_from_step="FREEZING_DECISION_HANDOFF_MANIFEST",
            )
            dependency_id = dependency.id
            payload.update(workflow_key=workflow)
            worker = register_worker(
                db,
                worker_id="ceri-context-116-disposable",
                queues=("background", "interactive"),
                heartbeat_timeout_seconds=7200,
                hostname="disposable-certification",
                process_id=os.getpid(),
            )
            worker_id, worker_instance = worker.worker_id, worker.instance_id
            db.commit()

        def enqueue_capture(suffix):
            with Session(engine) as db, configuration_delivery_scope(delivery):
                request = f"{workflow}:capture:{suffix}"
                job = enqueue_job(
                    db,
                    "CERI_CAPTURE_RUN",
                    {**payload, "request_key": request},
                    related_run_id=62,
                    priority=0,
                    request_key=request,
                    workflow_key=workflow,
                    pipeline_dependency_id=dependency_id,
                )
                bind_semantic_authority(job, authority)
                db.commit()
                return job.id

        def execute(job_id, handler):
            with Session(engine) as db:
                job = db.get(BackgroundJob, job_id)
                job.priority = 0
                db.commit()
                claimed = (
                    job
                    if job.status == "RUNNING"
                    else claim_next_job(
                        db,
                        worker_id=worker_id,
                        worker_instance_id=worker_instance,
                        included_job_types=(job.job_type,),
                        lease_seconds=3600,
                    )
                )
                if claimed is None or claimed.id != job_id:
                    raise RuntimeError("GENUINE_JOB_CLAIM_UNAVAILABLE")
                db.commit()
                values = handler(db, claimed)
                if values.get("failed") or values.get("status") in {"FAILED", "PARTIAL", "SKIPPED"}:
                    raise RuntimeError("NATIVE_SERVICE_DID_NOT_COMPLETE: " + str(values))
                mark_job_completed(db, claimed, values, execution_token=claimed.execution_token)
                db.commit()
                print("completed native job", job_id, claimed.job_type, flush=True)
                return values

        capture = execute(enqueue_capture("first"), execute_capture_run_job)
        assert capture["score_snapshots"] == 447
        result["steps"]["native_capture"] = capture
        retain()
        change = execute(capture["change_job_id"], execute_change_detection_job)
        alert = execute(change["alert_job_id"], execute_alert_rebuild_job)
        result["steps"]["change"] = change
        result["steps"]["alert"] = alert
        retain()
        with Session(engine) as db, configuration_delivery_scope(delivery):
            owner = db.get(PipelineRun, 66)
            scores = certified_scores(
                db, 66, 116, expected_ids=owner.result_json["ceri_certified_score_ids"]
            )
            new_ids = [row.id for row in scores]
            assert len(new_ids) == 447 and min(new_ids) > maximum
            from app.services.ceri.snapshot_service import CeriSnapshotService

            reproducer = CeriSnapshotService(effective_configuration=frozen)
            assert all(reproducer.reproduce_snapshot(row).matches for row in scores)
            result["steps"]["immutable_score_reproduction"] = {"matched": len(scores)}
            (REPORT / "new-context-116-scores.json").write_text(json.dumps([
                {"id": row.id, "ticker": row.ticker, "company_id": row.company_id,
                 "context": row.calculation_context_id, "cutoff": row.cutoff_at,
                 "config_hash": row.config_hash, "version": row.calculation_version,
                 "evidence_id": row.evidence_id, "evidence_hash": row.evidence_hash}
                for row in scores], indent=2, default=str), encoding="utf-8")
            expected = list(
                db.scalars(select(RawCompanyRow.ticker).where(RawCompanyRow.run_id == 62))
            )
            _validate_ceri_completion_barrier(db, owner, tickers=expected)
            from app.services.transition_preflight_plan_service import (
                freeze_transition_decision_handoff_manifest,
            )

            main_cutoff = market_context_for_pipeline(db, owner)
            manifest = freeze_transition_decision_handoff_manifest(
                db, upload_run_id=62, market_cutoff=main_cutoff
            )
            assert sorted(
                row["id"] for row in manifest.manifest_json["ceri_score_snapshots"]
            ) == sorted(new_ids)
            from app.services.setup_lifecycle.repository import SetupLifecycleRepository
            from app.services.setup_lifecycle.snapshot_builder import (
                SetupLifecycleSnapshotBuilder,
                build_run_context_snapshots,
            )
            from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader
            from app.services.transition_preflight_plan_service import (
                verify_transition_decision_manifests_before_mutation,
            )

            repository = SetupLifecycleRepository()
            context = SetupLifecycleSourceLoader().load_run_context(
                db, 62, market_cutoff=main_cutoff, tickers=set(expected)
            )
            built = build_run_context_snapshots(
                db, context, builder=SetupLifecycleSnapshotBuilder(), repository=repository
            )
            verify_transition_decision_manifests_before_mutation(
                db,
                upload_run_id=62,
                market_cutoff=main_cutoff,
                built_rows=built,
                repository=repository,
            )
            db.commit()
            result["steps"]["handoff"] = {
                "id": manifest.id,
                "fingerprint": manifest.manifest_fingerprint,
                "score_ids": new_ids,
                "count": len(new_ids),
            }
        retry = execute(enqueue_capture("retry"), execute_capture_run_job)
        assert retry["score_snapshots"] == 0 and retry["skipped"] == 447
        retry_ids = [enqueue_capture("concurrent-a"), enqueue_capture("concurrent-b")]
        for job_id in retry_ids:
            with Session(engine) as db:
                claimed = claim_next_job(
                    db,
                    worker_id=worker_id,
                    worker_instance_id=worker_instance,
                    included_job_types=("CERI_CAPTURE_RUN",),
                    lease_seconds=3600,
                )
                assert claimed is not None and claimed.id == job_id
                db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(
                pool.map(lambda job_id: execute(job_id, execute_capture_run_job), retry_ids)
            )
        assert all(item["score_snapshots"] == 0 and item["skipped"] == 447 for item in attempts)
        result["steps"]["retry"] = retry
        result["steps"]["concurrent_retries"] = attempts
        with Session(engine) as db:
            cutoff = resolve_pipeline_ceri_context(
                db, calculation_context_id=116, upload_run_id=62, pipeline_run_id=66
            )
            from types import SimpleNamespace

            bad = SimpleNamespace(config_hash="incompatible", engine=frozen.ceri_config().engine)
            try:
                _existing_snapshot_company_ids(
                    db, 62, {row.company_id for row in scores}, bad, cutoff, 66
                )
            except ValueError as exc:
                assert str(exc) == "CERI_SAME_CONTEXT_CONFIGURATION_MISMATCH"
                result["steps"]["incompatible_same_context"] = str(exc)
            else:
                raise AssertionError("INCOMPATIBLE_SAME_CONTEXT_WAS_ACCEPTED")
            after = history(db, maximum)
            assert after == baseline
            assert (
                db.scalar(
                    text("SELECT md5(to_jsonb(j)::text) FROM background_jobs j WHERE id=2546")
                )
                == job_hash
            )
            assert (
                db.scalar(
                    text(
                        "SELECT md5(to_jsonb(m)::text) "
                        "FROM transition_decision_handoff_manifests m WHERE id=41"
                    )
                )
                == manifest_hash
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(CeriScoreSnapshot)
                    .where(CeriScoreSnapshot.calculation_context_id == 116)
                )
                == 447
            )
            (REPORT / "historical-after.json").write_text(json.dumps(after), encoding="utf-8")
            result["steps"]["historical_preservation"] = {
                "scores": len(baseline),
                "equal": True,
                "manifest41_unchanged": True,
                "job2546_unchanged": True,
            }
        result["decision"] = "PASS"
        retain()
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
        retain()
        traceback.print_exc()
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
