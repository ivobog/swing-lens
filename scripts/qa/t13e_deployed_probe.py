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

        # Real cache rows, known before the route reserves its market cutoff.
        # Financial inputs remain available after every process exits.
        seed_cutoff = MarketClockService().cutoff_for(now, reason="T13E_ACQUISITION_FIXTURE")
        dates = pd.bdate_range(end=seed_cutoff.latest_completed_session, periods=400)
        with SessionLocal() as db:
            if db.scalar(select(PriceBar.id).limit(1)) is None:
                for ticker in ("ACME", "SPY", "QQQ", "IWM", "XLK", "TLT", "VIXY"):
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
        raw = {"Symbol": "ACME", "Description": "ACME", "Sector": "Technology"}
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

                cutoff = resolve_pipeline_market_context(
                    db,
                    calculation_context_id=job.payload_json["market_calculation_context_id"],
                    upload_run_id=run_id,
                    pipeline_run_id=pipeline.id,
                )
                company = db.scalar(select(CeriCompany).where(CeriCompany.ticker == "ACME"))
                seed_estimates = company is None
                if company is None:
                    company = CeriCompany(ticker="ACME", exchange="NYSE", company_name="ACME")
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
                            key = f"t13e-{metric}-{slot}-{days}"
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
                enqueue_job(
                    db,
                    "CERI_REBUILD_FEATURES",
                    {
                        "run_id": run_id,
                        "company_ids": [company.id],
                        "calculation_context_id": cutoff.context_id,
                        "cutoff_at": cutoff.cutoff_at.isoformat(),
                        "as_of_session": cutoff.latest_completed_session.isoformat(),
                        "calendar_version": cutoff.calendar_version,
                    },
                    related_run_id=run_id,
                    parent_job_id=job.id,
                )
                enqueue_job(
                    db,
                    "IB_INTELLIGENCE_REBUILD_FEATURES",
                    {"module": "LIQUIDITY", "tickers": ["ACME"]},
                    related_run_id=run_id,
                    parent_job_id=job.id,
                )
                db.commit()
            return {
                "pid": os.getpid(),
                "job_id": job.id,
                "pipeline_id": pipeline.id,
                "run_id": run_id,
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
            enqueue_job(
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
            db.commit()
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

                def scheduling_boundary(*_args, **_kwargs):
                    if mode == "interrupt":
                        raise RuntimeError("T13E transient acquisition scheduling boundary")
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
        records = db.scalars(select(EffectiveConfigurationRecord)).all()
        anchors = db.scalars(select(ExecutionConfigurationAnchor)).all()
        result = {
            "pid": os.getpid(),
            "jobs": [
                {
                    "id": j.id,
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
        }
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
    print("T13E_RESULT=" + json.dumps(main(sys.argv[1]), default=str, sort_keys=True))
