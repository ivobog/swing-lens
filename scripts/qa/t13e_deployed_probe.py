"""Disposable-database public route and native worker process certification.

Financial calculators, repositories, handlers and lease fences are production
code. Only external IB connectivity is replaced by a read-only offline boundary.
The parent test provides a task-owned cwd and database before this process imports
the application; application startup never connects to the user's database.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

# PIDs may be reused after exit; this identity belongs to one probe address space.
PROBE_PROCESS_INSTANCE_ID = uuid4().hex


def main(mode: str) -> dict:
    from sqlalchemy import select
    from sqlalchemy.engine import make_url

    from app.database_safety import assert_disposable_database
    from app.db import SessionLocal
    from app.models.tables import (
        BackgroundJob,
        CoreCalculationEvidence,
        EffectiveConfigurationRecord,
        ExecutionConfigurationAnchor,
        IBContract,
        PipelineDependency,
        PipelineRun,
        PriceBar,
    )
    from app.services.configuration_delivery import ANCHOR_KEY, binding_reference
    from app.services.ib_gateway_health_service import IBGatewayHealthStatus
    from app.settings import get_settings

    assert_disposable_database(
        get_settings().database_url,
        active_database_url=make_url(get_settings().database_url).set(database="postgres"),
    )
    now = datetime.now(UTC)
    offline = IBGatewayHealthStatus(
        status="IB_PROCESS_NOT_RUNNING",
        host="127.0.0.1",
        port=1,
        client_id=1,
        api_connected=False,
        api_ready=False,
        process_running=False,
        server_version=None,
        smoke_response_received=False,
        checked_at=now,
        expires_at=now + timedelta(minutes=1),
        latency_ms=0,
        error_code="IB_GATEWAY_API_UNREACHABLE",
        failure_category="external_boundary",
        message="T13E isolated offline acquisition boundary",
    )
    duplicate_resume_proof = None
    if os.environ.get("T13E_READY_BOUNDARY") == "true":
        from dataclasses import replace

        offline = replace(
            offline,
            status="IB_API_READY",
            api_connected=True,
            api_ready=True,
            process_running=True,
            smoke_response_received=True,
            error_code=None,
        )
    if mode == "enqueue":
        import csv
        import io

        import pandas as pd
        from fastapi.testclient import TestClient

        from app.main import create_app
        from app.services.column_mapper import load_alias_map
        from app.services.market_clock_service import MarketClockService
        from app.services.worker_registry import register_worker

        repo = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(repo / "tests"))
        from test_fundamental_ranker_v2 import _quality_values

        fixture_ticker = os.environ.get("T13E_FIXTURE_TICKER", "ACME")

        # Real cache rows, known before the route reserves its market cutoff.
        # Financial inputs remain available after every process exits.
        seed_cutoff = MarketClockService().cutoff_for(now, reason="T13E_ACQUISITION_FIXTURE")
        dates = pd.bdate_range(end=seed_cutoff.latest_completed_session, periods=400)
        with SessionLocal() as db:
            for ticker in (fixture_ticker, "SPY", "QQQ", "IWM", "XLK", "TLT", "VIXY"):
                if db.scalar(select(PriceBar.id).where(PriceBar.ticker == ticker).limit(1)) is None:
                    db.add(IBContract(ticker=ticker, resolution_status="RESOLVED", ib_conid=1000))
                    for index, bar_date in enumerate(dates):
                        close = 100 + index * 0.2
                        for feed in ("TRADES", "ADJUSTED_LAST"):
                            db.add(
                                PriceBar(
                                    ticker=ticker,
                                    bar_date=bar_date.date(),
                                    timeframe="1 day",
                                    what_to_show=feed,
                                    source="IB",
                                    open=close - 0.2,
                                    high=close + 1,
                                    low=close - 1,
                                    close=close,
                                    volume=1_000_000 + index * 100,
                                    created_at=now - timedelta(days=2),
                                    first_seen_at=now - timedelta(days=2),
                                )
                            )
            register_worker(
                db,
                worker_id="t13e-route-availability",
                queues=["interactive"],
                heartbeat_timeout_seconds=30,
            )
            db.commit()
        aliases = load_alias_map()
        raw = {"Symbol": fixture_ticker, "Description": fixture_ticker, "Sector": "Technology"}
        raw.update(
            {
                aliases[key][0]: value
                for key, value in {
                    **_quality_values(),
                    "number_of_shareholders_annual": "100000",
                    "ebit_per_share_ttm": "8",
                    "total_assets_estimate_annual": "12000000000",
                    "upcoming_earnings_date": (now + timedelta(days=90)).date().isoformat(),
                }.items()
                if key in aliases
            }
        )
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(raw))
        writer.writeheader()
        writer.writerow(raw)
        # No lifespan thread is needed for request dispatch. The application's
        # normal dependency opens the isolated database configured before import.
        client = TestClient(create_app(get_settings()))
        uploaded = client.post(
            "/uploads",
            files={"file": ("t13e.csv", stream.getvalue(), "text/csv")},
            follow_redirects=False,
        )
        assert uploaded.status_code == 303, uploaded.text
        run_id = int(uploaded.headers["location"].split("?")[0].rsplit("/", 1)[1])
        with patch("app.routers.run_routes.check_status", return_value=offline):
            response = client.post(
                f"/runs/{run_id}/pipeline",
                data={"market_data_policy": "ALLOW_CACHE_FALLBACK"},
                follow_redirects=False,
            )
        assert response.status_code == 303, response.text
        with SessionLocal() as db:
            pipeline = db.scalar(select(PipelineRun).where(PipelineRun.upload_run_id == run_id))
            job = db.get(BackgroundJob, pipeline.result_json["background_job_id"])
            reference = binding_reference(db, job_id=job.id)
            assert reference == binding_reference(db, pipeline_run_id=pipeline.id)
            assert reference == job.payload_json[ANCHOR_KEY]
            anchor = db.get(ExecutionConfigurationAnchor, reference["anchor_id"])
            if os.environ.get("CERI_RUN_CAPTURE_ENABLED") == "true":
                from decimal import Decimal

                from app.models.ceri_tables import (
                    CeriCompany,
                    CeriEstimateSnapshot,
                    CeriSourceRecord,
                )
                from app.services.background_job_service import enqueue_job
                from app.services.market_calculation_context_service import (
                    resolve_pipeline_market_context,
                )
                from app.services.scope_refresh_adoption import (
                    bind_semantic_authority,
                    require_semantic_authority,
                )

                cutoff = resolve_pipeline_market_context(
                    db,
                    calculation_context_id=job.payload_json["market_calculation_context_id"],
                    upload_run_id=run_id,
                    pipeline_run_id=pipeline.id,
                )
                company = db.scalar(select(CeriCompany).where(CeriCompany.ticker == fixture_ticker))
                seed_estimates = company is None
                if company is None:
                    company = CeriCompany(
                        ticker=fixture_ticker, exchange="NYSE", company_name=fixture_ticker
                    )
                    db.add(company)
                    db.flush()
                for metric in ("EPS_DILUTED", "REVENUE"):
                    for slot in (
                        "CURRENT_QUARTER",
                        "NEXT_QUARTER",
                        "CURRENT_FISCAL_YEAR",
                        "NEXT_FISCAL_YEAR",
                    ):
                        for days in (91, 31, 8, 1):
                            if not seed_estimates:
                                continue
                            known = cutoff.cutoff_at - timedelta(days=days)
                            key = f"t13e-{fixture_ticker}-{metric}-{slot}-{days}"
                            source = CeriSourceRecord(
                                provider="manual",
                                dataset="estimates",
                                provider_record_id=key,
                                content_hash=key,
                                idempotency_key=key,
                                published_at=known,
                                observed_at=known,
                                ingested_at=known,
                            )
                            db.add(source)
                            db.flush()
                            db.add(
                                CeriEstimateSnapshot(
                                    source_record_id=source.id,
                                    company_id=company.id,
                                    metric=metric,
                                    fiscal_period_end=(now + timedelta(days=120)).date(),
                                    period_type=slot,
                                    canonical_period_slot=slot,
                                    consensus=Decimal("10") + Decimal(100 - days) / 100,
                                    analyst_count=12,
                                    upward_count=8,
                                    downward_count=1,
                                    effective_at=known,
                                    known_at=known,
                                    reference_at=known,
                                    effective_session=known.date(),
                                    source_provider="manual",
                                    canonical_observation_key=key,
                                    canonical_currency="USD",
                                    currency_verified=True,
                                    canonical_scale=Decimal(1),
                                )
                            )
                authority = require_semantic_authority(job)
                ceri_job = enqueue_job(
                    db,
                    "CERI_REBUILD_FEATURES",
                    {
                        "run_id": run_id,
                        "pipeline_run_id": pipeline.id,
                        "company_ids": [company.id],
                        "calculation_context_id": cutoff.context_id,
                        "cutoff_at": cutoff.cutoff_at.isoformat(),
                        "as_of_session": cutoff.latest_completed_session.isoformat(),
                        "calendar_version": cutoff.calendar_version,
                    },
                    related_run_id=run_id,
                    parent_job_id=job.id,
                )
                bind_semantic_authority(
                    ceri_job,
                    authority,
                    required_for_parent_completion=True,
                )
                ib_job = enqueue_job(
                    db,
                    "IB_INTELLIGENCE_REBUILD_FEATURES",
                    {"module": "LIQUIDITY", "tickers": [fixture_ticker]},
                    related_run_id=run_id,
                    parent_job_id=job.id,
                )
                bind_semantic_authority(
                    ib_job,
                    authority,
                    required_for_parent_completion=True,
                )
                db.commit()
            return {
                "pid": os.getpid(),
                "job_id": job.id,
                "pipeline_id": pipeline.id,
                "run_id": run_id,
                "job_status": job.status,
                "pipeline_status": pipeline.status,
                "anchor": reference,
                "identities": {
                    key: value["identity"]
                    for key, value in anchor.payload_json["configurations"].items()
                },
            }
    if mode == "queue-current-rules":
        from app.services.background_job_service import enqueue_job

        with SessionLocal() as db:
            enqueue_job(db, "SETUP_LIFECYCLE_REPLAY", {"ticker": "ACME", "persist": False})
            db.commit()
    if mode == "queue-downstream":
        from app.services.background_job_service import enqueue_job

        with SessionLocal() as db:
            parent_id = int(os.environ["T13E_PARENT_JOB_ID"])
            parent = db.get(BackgroundJob, parent_id)
            run_id = parent.related_run_id
            enqueue_job(
                db,
                "SETUP_LIFECYCLE_DAILY_MAINTENANCE",
                {"as_of_date": (now + timedelta(days=45)).date().isoformat()},
                related_run_id=run_id,
                parent_job_id=parent_id,
            )
            enqueue_job(
                db,
                "SETUP_ALERT_REBUILD",
                {"ticker": "ACME"},
                related_run_id=run_id,
                parent_job_id=parent_id,
                priority=200,
            )
            db.commit()
    if mode == "post-terminal-winner-fence":
        from app.services.background_job_service import enqueue_job
        from app.services.pipeline_execution_authority import (
            claimable_pipeline_job_ids,
            pipeline_execution_is_authorized,
        )
        from app.services.scope_refresh_adoption import bind_semantic_authority
        from app.services.winner_probability.scope_refresh import admit_prediction_capture

        parent_id = int(os.environ["T13E_PARENT_JOB_ID"])
        with SessionLocal() as db:
            from app.models.tables import WinnerPredictionSnapshot

            parent = db.get(BackgroundJob, parent_id)
            run_id = parent.related_run_id
            pipeline = db.get(PipelineRun, parent.payload_json["pipeline_run_id"])
            winner_count_before = len(db.scalars(select(WinnerPredictionSnapshot.id)).all())
            winner_authority = admit_prediction_capture(
                db,
                run_id=run_id,
                cycle_key=f"t13e:post-terminal-winner-fence:{run_id}:{parent_id}",
                cutoff=datetime.fromisoformat(parent.payload_json["market_cutoff_at"]),
                configuration_identity=parent.payload_json["effective_configuration_anchor"][
                    "fingerprint"
                ],
            )
            winner_job = enqueue_job(
                db,
                "WINNER_PREDICTION_CAPTURE",
                {
                    "run_id": run_id,
                    "pipeline_run_id": parent.payload_json["pipeline_run_id"],
                    "decision_handoff_manifest_id": db.get(
                        PipelineRun, parent.payload_json["pipeline_run_id"]
                    ).result_json["decision_handoff_manifest_id"],
                    **{
                        key: parent.payload_json[key]
                        for key in (
                            "market_calculation_context_id",
                            "market_cutoff_at",
                            "input_as_of_session",
                            "market_calendar_version",
                            "bar_readiness_version",
                        )
                    },
                },
                related_run_id=run_id,
                parent_job_id=parent_id,
                priority=200,
            )
            bind_semantic_authority(
                winner_job,
                winner_authority,
                required_for_parent_completion=True,
            )
            db.flush()
            candidate_id = int(winner_job.id)
            claimable_ids = claimable_pipeline_job_ids(db, upload_run_id=run_id)
            proof = {
                "candidate_id": candidate_id,
                "candidate_status": winner_job.status,
                "claimable": candidate_id in claimable_ids,
                "pipeline_status": pipeline.status,
                "pipeline_execution_authority_state": pipeline.execution_authority_state,
                "pipeline_execution_authorized": pipeline_execution_is_authorized(pipeline),
                "winner_count_before": winner_count_before,
                "winner_count_during": len(
                    db.scalars(select(WinnerPredictionSnapshot.id)).all()
                ),
            }
            db.rollback()
        with SessionLocal() as db:
            proof.update(
                candidate_persisted=db.get(BackgroundJob, candidate_id) is not None,
                winner_count_after=len(db.scalars(select(WinnerPredictionSnapshot.id)).all()),
            )
        return {"pid": os.getpid(), "post_terminal_winner": proof}
    if mode == "claim":
        from app.services.background_job_service import claim_next_job
        from app.services.worker_registry import register_worker

        with SessionLocal() as db:
            worker = f"t13e-abandoned-{os.getpid()}"
            register_worker(
                db, worker_id=worker, queues=["interactive"], heartbeat_timeout_seconds=30
            )
            db.commit()
            job = claim_next_job(db, worker_id=worker, queues=["interactive"], lease_seconds=1)
            assert job is not None
            # Advance only the task fixture's execution lease; semantic rows
            # and bindings remain immutable. A new process performs recovery.
            job.lease_expires_at = now - timedelta(seconds=1)
            db.commit()
    if mode == "benchmark":
        from scripts.qa.t13e_configuration_benchmark import benchmark

        with SessionLocal() as db:
            reference = binding_reference(db, job_id=int(os.environ["T13E_PARENT_JOB_ID"]))
        return {"pid": os.getpid(), **benchmark(SessionLocal, reference)}
    if mode in {"resume", "retry-ready"}:
        with SessionLocal() as db:
            parent = db.get(BackgroundJob, int(os.environ["T13E_PARENT_JOB_ID"]))
            resume_run_id = parent.related_run_id
            resume_pipeline_id = parent.payload_json["pipeline_run_id"]
            parent.run_after = (
                now + timedelta(days=1) if mode == "resume" else now - timedelta(seconds=1)
            )
            db.commit()
        if mode == "resume":
            from fastapi.testclient import TestClient

            from app.main import create_app

            client = TestClient(create_app(get_settings()))
            response = client.post(
                f"/runs/{resume_run_id}/pipeline/{resume_pipeline_id}/resume",
                follow_redirects=False,
            )
            assert response.status_code == 303, response.text
    if mode == "duplicate-resume":
        from fastapi.testclient import TestClient

        from app.main import create_app
        from app.services.pipeline_execution_authority import claimable_pipeline_job_ids

        with SessionLocal() as db:
            parent = db.get(BackgroundJob, int(os.environ["T13E_PARENT_JOB_ID"]))
            resume_run_id = parent.related_run_id
            resume_pipeline_id = parent.payload_json["pipeline_run_id"]
            pipeline = db.get(PipelineRun, resume_pipeline_id)
            job_ids_before = list(
                db.scalars(select(BackgroundJob.id).order_by(BackgroundJob.id)).all()
            )
            claimable_before = list(
                claimable_pipeline_job_ids(db, upload_run_id=resume_run_id)
            )
            pipeline_status_before = pipeline.status

        client = TestClient(create_app(get_settings()))
        response = client.post(
            f"/runs/{resume_run_id}/pipeline/{resume_pipeline_id}/resume",
            follow_redirects=False,
        )
        assert response.status_code == 409, response.text

        with SessionLocal() as db:
            pipeline = db.get(PipelineRun, resume_pipeline_id)
            job_ids_after = list(
                db.scalars(select(BackgroundJob.id).order_by(BackgroundJob.id)).all()
            )
            claimable_after = list(
                claimable_pipeline_job_ids(db, upload_run_id=resume_run_id)
            )
            duplicate_resume_proof = {
                "status_code": response.status_code,
                "detail": response.json()["detail"],
                "job_ids_before": job_ids_before,
                "job_ids_after": job_ids_after,
                "claimable_job_ids_before": claimable_before,
                "claimable_job_ids_after": claimable_after,
                "pipeline_status_before": pipeline_status_before,
                "pipeline_status_after": pipeline.status,
            }
    if mode in {"execute", "prerequisites", "interrupt", "interrupt-early"}:
        from dataclasses import replace

        from app.services import pipeline_executor
        from app.services.background_worker import run_worker_once

        native_dependencies = pipeline_executor.PipelineExecutionDependencies

        def offline_dependencies(*args, **kwargs):
            # Keep every financial dependency and default production handler.
            changes = {"check_ib_gateway": lambda: offline}
            if mode == "interrupt-early":

                def transient_gateway_boundary():
                    raise RuntimeError("T13E transient acquisition readiness boundary")

                changes["check_ib_gateway"] = transient_gateway_boundary
            if os.environ.get("T13E_SCHEDULER_BOUNDARY") == "true":

                def scheduling_boundary(db, run_id, market_cutoff=None):
                    if mode == "interrupt":
                        raise RuntimeError("T13E transient acquisition scheduling boundary")
                    from app.services.domain_write_fence import current_domain_write_ownership
                    from app.services.pipeline_dependency_service import (
                        CERI_WORKFLOW_DEPENDENCY,
                        DEPENDENCY_ACTIVE_STATES,
                        prepare_ceri_workflow_dependency,
                    )

                    ownership = current_domain_write_ownership()
                    assert ownership is not None
                    root_job = db.get(BackgroundJob, ownership.job_id)
                    assert root_job is not None and root_job.job_type == "FULL_PIPELINE"
                    assert root_job.related_run_id == run_id
                    pipeline = db.get(PipelineRun, root_job.payload_json["pipeline_run_id"])
                    assert pipeline is not None and pipeline.upload_run_id == run_id
                    assert market_cutoff is not None
                    assert root_job.payload_json["market_calculation_context_id"] == (
                        market_cutoff.context_id
                    )
                    persisted_cutoff = root_job.payload_json.get("market_cutoff_at")
                    if isinstance(persisted_cutoff, datetime):
                        parsed_cutoff = persisted_cutoff
                    elif isinstance(persisted_cutoff, str) and persisted_cutoff.strip():
                        normalized_cutoff = persisted_cutoff.strip()
                        if normalized_cutoff.upper().endswith("Z"):
                            normalized_cutoff = normalized_cutoff[:-1] + "+00:00"
                        try:
                            parsed_cutoff = datetime.fromisoformat(normalized_cutoff)
                        except ValueError as exc:
                            raise AssertionError("invalid frozen market cutoff") from exc
                    else:
                        raise AssertionError("missing frozen market cutoff")
                    if parsed_cutoff.tzinfo is None or parsed_cutoff.utcoffset() is None:
                        raise AssertionError("frozen market cutoff must be timezone-aware")
                    if (
                        market_cutoff.cutoff_at.tzinfo is None
                        or market_cutoff.cutoff_at.utcoffset() is None
                    ):
                        raise AssertionError("admitted market cutoff must be timezone-aware")
                    assert parsed_cutoff.astimezone(UTC) == market_cutoff.cutoff_at.astimezone(UTC)
                    workflow_key = pipeline_executor._ceri_provider_workflow_key(run_id, 0)
                    dependency = prepare_ceri_workflow_dependency(
                        db,
                        pipeline=pipeline,
                        workflow_key=workflow_key,
                        resume_from_step=pipeline_executor.DECISION_HANDOFF_PIPELINE_STEP,
                    )
                    repeated = prepare_ceri_workflow_dependency(
                        db,
                        pipeline=pipeline,
                        workflow_key=workflow_key,
                        resume_from_step=pipeline_executor.DECISION_HANDOFF_PIPELINE_STEP,
                    )
                    matching = list(
                        db.scalars(
                            select(PipelineDependency).where(
                                PipelineDependency.pipeline_run_id == pipeline.id,
                                PipelineDependency.root_job_id == root_job.id,
                                PipelineDependency.dependency_type == CERI_WORKFLOW_DEPENDENCY,
                                PipelineDependency.state.in_(DEPENDENCY_ACTIVE_STATES),
                            )
                        )
                    )
                    assert repeated.id == dependency.id
                    assert matching == [dependency]
                    assert dependency.state == "RUNNING"
                    assert dependency.child_job_id is None
                    assert dependency.continuation_job_id is None
                    assert dependency.result_json == {"workflow_key": workflow_key}
                    return 0

                changes.update(
                    validate_pipeline_preflight=lambda *_args: {
                        "source": "isolated acquisition readiness"
                    },
                    schedule_ceri_provider_ingest=scheduling_boundary,
                )
            return replace(native_dependencies(*args, **kwargs), **changes)

        native_open = Path.open

        def guarded_open(path, *args, **kwargs):
            # Windows timezone resources are ordinary package reads, not config.
            if "config" in path.parts:
                raise AssertionError("queued work reopened current C2: " + path.name)
            return native_open(path, *args, **kwargs)

        with (
            patch.object(pipeline_executor, "PipelineExecutionDependencies", offline_dependencies),
            patch("pathlib.Path.open", guarded_open),
        ):
            worked = False
            for _ in range(12 if mode == "prerequisites" else 1):
                next_work = run_worker_once(
                    worker_id=f"t13e-native-{os.getpid()}",
                    queues=["background", "broker"] if mode == "prerequisites" else ["interactive"],
                    stale_after_seconds=120,
                    session_factory=SessionLocal,
                )
                worked = worked or next_work
                if not next_work:
                    break
        assert worked
    with SessionLocal() as db:
        evidence = db.scalars(
            select(CoreCalculationEvidence).order_by(CoreCalculationEvidence.id)
        ).all()
        jobs = db.scalars(select(BackgroundJob).order_by(BackgroundJob.id)).all()
        pipelines = db.scalars(select(PipelineRun).order_by(PipelineRun.id)).all()
        dependencies = db.scalars(
            select(PipelineDependency).order_by(PipelineDependency.id)
        ).all()
        records = db.scalars(select(EffectiveConfigurationRecord)).all()
        anchors = db.scalars(select(ExecutionConfigurationAnchor)).all()
        result = {
            "pid": os.getpid(),
            "jobs": [
                {
                    "id": j.id,
                    "type": j.job_type,
                    "status": j.status,
                    "error": j.error_message,
                    "result": j.result_json,
                    "payload": j.payload_json,
                    "token": j.execution_token,
                    "attempts": j.operational_metadata_json,
                }
                for j in jobs
            ],
            "evidence": [
                {
                    "id": e.id,
                    "kind": e.artifact_kind,
                    "identity": e.calculation_identity_json,
                    "payload": e.payload_json,
                }
                for e in evidence
            ],
            "records": [r.payload_json for r in records],
            "anchors": [a.payload_json for a in anchors],
            "pipelines": [
                {
                    "id": pipeline.id,
                    "status": pipeline.status,
                    "current_step": pipeline.current_step,
                    "message": pipeline.message,
                    "error": pipeline.error_message,
                }
                for pipeline in pipelines
            ],
            "dependencies": [
                {
                    "id": dependency.id,
                    "pipeline_run_id": dependency.pipeline_run_id,
                    "dependency_type": dependency.dependency_type,
                    "dependency_key": dependency.dependency_key,
                    "state": dependency.state,
                    "continuation_step": dependency.continuation_step,
                    "continuation_identity": dependency.continuation_identity,
                    "root_job_id": dependency.root_job_id,
                    "root_worker_instance_id": dependency.root_worker_instance_id,
                    "child_job_id": dependency.child_job_id,
                    "continuation_job_id": dependency.continuation_job_id,
                    "result": dependency.result_json,
                }
                for dependency in dependencies
            ],
        }
        if duplicate_resume_proof is not None:
            result["duplicate_resume"] = duplicate_resume_proof
        from app.models.tables import (
            SetupLifecycleEvaluationEvidence,
            SetupLifecycleTransitionEvidence,
            SignalAlertDecisionEvidence,
            WinnerPredictionSnapshot,
        )

        for name, model, field in (
            ("lifecycle", SetupLifecycleEvaluationEvidence, "payload_json"),
            ("transitions", SetupLifecycleTransitionEvidence, "payload_json"),
            ("alerts", SignalAlertDecisionEvidence, "payload_json"),
            ("winner", WinnerPredictionSnapshot, "lineage_json"),
        ):
            result[name] = [
                getattr(row, field) for row in db.scalars(select(model).order_by(model.id))
            ]
        assert "T13E_SECRET_MUST_NOT_APPEAR" not in json.dumps(result, default=str)
        return result


if __name__ == "__main__":
    print(
        "T13E_RESULT="
        + json.dumps(
            {**main(sys.argv[1]), "probe_process_instance_id": PROBE_PROCESS_INSTANCE_ID},
            default=str,
            sort_keys=True,
        )
    )
