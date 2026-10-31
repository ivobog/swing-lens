"""Disposable full-pipeline cardinality gates with real internal writers."""

import csv
import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.models.tables import (
    BackgroundJob,
    BackgroundWorker,
    IBContract,
    PipelineDependency,
    PipelineRun,
    WinnerCohortGeneration,
    WinnerCohortRefreshState,
    WinnerCohortStatistic,
    WinnerEvidenceManifestMember,
    WinnerForwardOutcome,
    WinnerMarketDataObligation,
    WinnerOutcomeDefinition,
    WinnerPredictionEpisode,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
)
from app.services.bar_cache_service import cache_bars
from app.services.ceri.enums import CeriDataset
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.processing_run_service import CeriProcessingRunService
from app.services.ib_data_fetcher import HistoricalBar
from app.services.winner_probability.cohort_generation_service import (
    CohortGenerationService,
    CohortGenerationStatus,
    EvidenceWatermarkService,
    GenerationPublicationStatus,
    contract_for,
)
from app.services.winner_probability.cohort_materialization_service import (
    CohortMaterializationService,
)
from app.services.winner_probability.config import (
    load_winner_probability_config,
    winner_probability_config_hash,
)
from app.services.winner_probability.market_data_obligation_service import (
    MarketDataObligationService,
    required_outcome_sessions,
)
from app.services.winner_probability.outcome_service import OutcomeMaturationService
from app.settings import SecDocumentIncrementalMode, Settings
from single_run_certification.fixtures import (
    _seed_price_bars,
    _seed_setup_lifecycle_baseline,
    certification_as_of_session,
)
from single_run_certification.frozen_ceri import frozen_ceri_registry
from single_run_certification.reporting import CertificationRecorder
from single_run_certification.test_single_run_certification import (
    _launch_run_through_gui,
    _run_pipeline_through_gui,
    certification_environment,  # noqa: F401
    certification_page,  # noqa: F401
    certification_provider_mode,  # noqa: F401
    certification_setup_capture_handoff_enabled,  # noqa: F401
)

EXACT_TEN = ("BHE", "BLLN", "KLIC", "LSCC", "PDFS", "ACMR", "RDVT", "AVT", "DVN", "JNJ")


@pytest.fixture
def certification_provider_ingest_enabled() -> bool:
    return True


@pytest.fixture
def certification_golden_version(request, certification_provider_mode):  # noqa: F811
    from single_run_certification.golden_oracle import VERSION

    return (VERSION if request.node.callspec.params["size"] == 10
            and certification_provider_mode == "deterministic" else None)


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.destructive
@pytest.mark.parametrize(
    "size",
    (
        10,
        pytest.param(88, marks=pytest.mark.extended_scale),
        pytest.param(100, marks=pytest.mark.extended_scale),
    ),
)
@pytest.mark.parametrize(
    "certification_setup_capture_handoff_enabled", (False, True), indirect=True
)
def test_frozen_real_writer_cardinality(
    certification_environment,  # noqa: F811
    certification_page,  # noqa: F811
    size,
):
    env = certification_environment
    golden = env.environment.get("golden_version")
    tickers = EXACT_TEN + tuple(f"F{index:04d}" for index in range(size - len(EXACT_TEN)))
    with env.csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        exemplar = next(reader)
    with env.csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for ticker in tickers:
            row = {**exemplar, "Symbol": ticker, "Description": f"{ticker} frozen fixture"}
            writer.writerow(row)
    if golden:
        from single_run_certification.golden_oracle import DATA, load_golden

        load_golden()
        env.csv_path.write_bytes((DATA / "universe.csv").read_bytes())

    engine = create_engine(env.database_url)
    try:
        from single_run_certification.golden_runtime import business_inputs

        clock = business_inputs(env.database_url) if golden else nullcontext()
        with Session(engine) as db, clock:
            db.add_all(
                IBContract(
                    ticker=ticker,
                    ib_conid=9_500_000 + index,
                    symbol=ticker,
                    exchange="SMART",
                    primary_exchange="NASDAQ",
                    currency="USD",
                    sec_type="STK",
                    resolution_status="RESOLVED",
                )
                for index, ticker in enumerate(tickers)
            )
            _seed_setup_lifecycle_baseline(
                db,
                run_id=env.seed.decoy_run_id,
                as_of_session=certification_as_of_session(),
                tickers=tickers,
            )
            _seed_price_bars(
                db,
                tickers=tickers,
                as_of_session=certification_as_of_session(),
            )
            db.commit()

        recorder = CertificationRecorder(execution_id=env.execution_id)
        run_id = _launch_run_through_gui(certification_page, env, recorder)
        with business_inputs(env.database_url) if golden else nullcontext():
            _seed_retained_provider_history(engine, tickers=tickers)
        pipeline_id, status = _run_pipeline_through_gui(
            certification_page,
            env,
            recorder,
            run_id,
            absolute_deadline_seconds=3600 if size >= 88 else 900,
            progress_deadline_seconds=900 if size >= 88 else 180,
            admission_deadline_seconds=600 if size == 100 else 300,
        )
        if size == 10:
            _retain_terminal_status_evidence(engine, env, run_id, pipeline_id)
        if status != "COMPLETED":
            with engine.connect() as diagnostic_db:
                pipeline_diagnostics = (
                    diagnostic_db.execute(
                        text(
                            "select status, error_message, "
                            "result_json->>'ceri_completion_state' as ceri_completion_state, "
                            "result_json->>'ceri_score_snapshots' as ceri_score_snapshots "
                            "from pipeline_runs where id=:pipeline_id"
                        ),
                        {"pipeline_id": pipeline_id},
                    )
                    .mappings()
                    .one()
                )
                job_diagnostics = (
                    diagnostic_db.execute(
                        text(
                            "select id, job_type, status, error_message, "
                            "result_json->>'provider' as provider, "
                            "result_json->>'dataset' as dataset, "
                            "result_json->>'failed' as failed, "
                            "jsonb_path_query_array(result_json, "
                            "'$.results.* ? (@.status == \"PARTIAL\")') as partial_results "
                            "from background_jobs where related_run_id=:run_id "
                            "and status <> 'COMPLETED' order by id"
                        ),
                        {"run_id": run_id},
                    )
                    .mappings()
                    .all()
                )
                job_summaries = (
                    diagnostic_db.execute(
                        text(
                            "select id, job_type, status, "
                            "result_json->>'provider' as provider, "
                            "result_json->>'dataset' as dataset, "
                            "result_json->>'requested' as requested, "
                            "result_json->>'inserted' as inserted, "
                            "result_json->>'normalized' as normalized, "
                            "result_json->>'features' as features, "
                            "result_json->>'score_snapshots' as score_snapshots, "
                            "operational_metadata_json#>'{ceri_feature_rebuild,rows_loaded}' "
                            "as rows_loaded from background_jobs "
                            "where related_run_id=:run_id order by id"
                        ),
                        {"run_id": run_id},
                    )
                    .mappings()
                    .all()
                )
            pytest.fail(
                repr(
                    {
                        "size": size,
                        "status": status,
                        "artifacts": str(env.artifact_dir),
                        "pipeline": dict(pipeline_diagnostics),
                        "jobs": [dict(row) for row in job_diagnostics],
                        "job_summaries": [dict(row) for row in job_summaries],
                    }
                )
            )
        with engine.connect() as db:
            members = tuple(
                db.scalars(
                    text(
                        "select ticker from raw_company_rows "
                        "where run_id=:run_id order by row_number"
                    ),
                    {"run_id": run_id},
                )
            )
            assert members == tickers
            stages = db.execute(
                text(
                    "select step_name, status from pipeline_steps "
                    "where pipeline_run_id=:pipeline_id order by step_order"
                ),
                {"pipeline_id": pipeline_id},
            ).all()
            assert stages and all(row.status == "COMPLETED" for row in stages), stages
            job_counts = dict(
                db.execute(
                    text(
                        "select job_type, count(*) from background_jobs "
                        "where related_run_id=:run_id group by job_type"
                    ),
                    {"run_id": run_id},
                ).all()
            )
            pipeline_metrics = (
                db.execute(
                    text(
                        "select extract(epoch from (completed_at-started_at)) as total_seconds, "
                        "(select result_json#>>"
                        "'{performance,step_durations_ms,SCORING_TECHNICALS}' "
                        "from background_jobs where related_run_id=:run_id "
                        "and job_type='FULL_PIPELINE' order by id limit 1) as technical_ms, "
                        "result_json->>'ceri_provider_workflow_key' as workflow_key "
                        "from pipeline_runs where id=:pipeline_id"
                    ),
                    {"pipeline_id": pipeline_id, "run_id": run_id},
                )
                .mappings()
                .one()
            )
            ceri_metrics = (
                db.execute(
                    text(
                        "select extract(epoch from (max(completed_at)-min(started_at))) "
                        "as workflow_seconds, "
                        "max(extract(epoch from (completed_at-started_at))) as max_job_seconds, "
                        "max(greatest(coalesce((operational_metadata_json#>>"
                        "'{ceri_batch,max_checkpoint_gap_seconds}')::numeric, 0), "
                        "coalesce((operational_metadata_json#>>"
                        "'{ceri_capture,max_checkpoint_gap_seconds}')::numeric, 0), "
                        "coalesce((operational_metadata_json#>>"
                        "'{ceri_normalization,max_checkpoint_gap_seconds}')::numeric, 0), "
                        "coalesce(extract(epoch from (completed_at-last_progress_at)), 0))) "
                        "as max_checkpoint_gap_seconds, "
                        "max(recovery_count) as max_recovery_count, "
                        "max(retry_count) as max_retry_count, "
                        "count(*) filter (where stall_detected_at is not null) as stalled_jobs "
                        "from background_jobs where workflow_key=:workflow_key"
                    ),
                    {"workflow_key": pipeline_metrics["workflow_key"]},
                )
                .mappings()
                .one()
            )
            full_pipeline_control = (
                db.execute(
                    text(
                        "select min(progress_sequence) as min_progress_sequence, "
                        "max(recovery_count) as max_recovery_count, "
                        "count(*) filter (where stall_detected_at is not null) as stalled_jobs "
                        "from background_jobs where related_run_id=:run_id "
                        "and job_type='FULL_PIPELINE'"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .one()
            )
            provider_source_count = int(
                db.scalar(
                    text(
                        "select count(*) from ceri_source_records "
                        "where provider in ('eodhd','sec') "
                        "and restricted_normalized_json->>'ticker' = any(:tickers)"
                    ),
                    {"tickers": list(tickers)},
                )
                or 0
            )
            counts = {
                name: db.scalar(
                    text(f"select count(*) from {table} where run_id=:run_id"),
                    {"run_id": run_id},
                )
                for name, table in (
                    ("fundamental", "fundamental_scores"),
                    ("technical", "technical_scores"),
                    ("combined", "combined_results"),
                    ("ceri", "ceri_score_snapshots"),
                    ("setup", "setup_signal_snapshots"),
                    ("winner", "winner_prediction_snapshots"),
                )
            }
            assert counts["fundamental"] == size, counts
            assert counts["technical"] == size, counts
            assert counts["combined"] == size, counts
            assert counts["ceri"] > 0 and counts["setup"] > 0 and counts["winner"] > 0, counts
            if size == 88:
                assert all(counts[name] == size for name in ("ceri", "setup", "winner")), counts
            assert job_counts.get("CERI_PROVIDER_INGEST_BATCH", 0) > 0, job_counts
            assert job_counts.get("CERI_NORMALIZE_BATCH", 0) > 0, job_counts
            assert job_counts.get("CERI_FEATURE_BATCH", 0) > 0, job_counts
            assert job_counts.get("CERI_RUN_FINALIZE", 0) == 1, job_counts
            assert job_counts.get("CERI_CAPTURE_RUN", 0) == 1, job_counts
            assert job_counts.get("CERI_CHANGE_DETECTION", 0) == 1, job_counts
            assert job_counts.get("CERI_ALERT_REBUILD", 0) == 1, job_counts
            assert job_counts.get("FULL_PIPELINE", 0) == 2, job_counts
            assert float(pipeline_metrics["technical_ms"] or 0) < 300_000, pipeline_metrics
            assert float(ceri_metrics["max_checkpoint_gap_seconds"] or 0) < 60, ceri_metrics
            assert int(ceri_metrics["max_recovery_count"] or 0) == 0, ceri_metrics
            assert int(ceri_metrics["max_retry_count"] or 0) == 0, ceri_metrics
            assert int(ceri_metrics["stalled_jobs"] or 0) == 0, ceri_metrics
            assert int(full_pipeline_control["min_progress_sequence"] or 0) > 1, (
                full_pipeline_control
            )
            assert int(full_pipeline_control["max_recovery_count"] or 0) == 0, full_pipeline_control
            assert int(full_pipeline_control["stalled_jobs"] or 0) == 0, full_pipeline_control
            assert provider_source_count == 6 * size, provider_source_count
            metrics_path = env.artifact_dir / "db-results" / f"provider-{size}-metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "size": size,
                        "total_pipeline_seconds": float(pipeline_metrics["total_seconds"] or 0),
                        "technical_ms": float(pipeline_metrics["technical_ms"] or 0),
                        "ceri_workflow_seconds": float(ceri_metrics["workflow_seconds"] or 0),
                        "ceri_max_job_seconds": float(ceri_metrics["max_job_seconds"] or 0),
                        "ceri_max_checkpoint_gap_seconds": float(
                            ceri_metrics["max_checkpoint_gap_seconds"] or 0
                        ),
                        "full_pipeline_control": dict(full_pipeline_control),
                        "provider_source_records": provider_source_count,
                        "job_counts": job_counts,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        if size == 10:
            if env.provider_mode == "deterministic":
                _assert_production_worker_fidelity(engine, env, run_id, pipeline_id)
            if golden:
                _assert_independent_golden_subset(engine, env, run_id, pipeline_id)
                from single_run_certification.golden_assertions import assert_completed_oracle

                assert_completed_oracle(engine, env, run_id, pipeline_id)
                from single_run_certification.evidence import capture_pipeline_evidence

                capture_pipeline_evidence(engine, env, recorder, run_id, pipeline_id)
            publication = _mature_and_publish_one_generation(engine, run_id=run_id)
            assert publication == {
                "evidence_rows": 1,
                "episodes": size,
                "published_generations": 1,
                "serving_estimates": 1,
            }
            if golden:
                contract = json.loads((env.artifact_dir / "certificate-contract.json").read_text())
                (env.artifact_dir / "checks-reached.json").write_text(json.dumps({
                    "execution_id": env.execution_id, "test_id": contract["identity"]["test_id"],
                    "all_test_checks_reached": True,
                }, indent=2), encoding="utf-8")
    finally:
        engine.dispose()


def _retain_terminal_status_evidence(engine, env, run_id, pipeline_id) -> None:
    """Read committed predicate sources before fixture cleanup, for either outcome."""
    queries = {
        "pipeline": "select * from pipeline_runs where id=:pipeline_id",
        "steps": (
            "select * from pipeline_steps where pipeline_run_id=:pipeline_id order by step_order"
        ),
        "jobs": (
            "select id,job_type,status,error_message,result_json from background_jobs "
            "where related_run_id=:run_id order by id"
        ),
        "fetch_runs": "select * from ib_fetch_runs where run_id=:run_id order by id",
        "fetch_items": (
            "select i.id,i.fetch_run_id,i.ticker,i.what_to_show,i.status,i.error_message,"
            "i.decision_metadata_json from ib_fetch_items i join ib_fetch_runs r "
            "on r.id=i.fetch_run_id where r.run_id=:run_id order by i.id"
        ),
        "combined": (
            "select id,ticker,evidence_id,is_complete,has_warning,warning_flags_json "
            "from combined_results where run_id=:run_id order by id"
        ),
        "technicals": (
            "select id,ticker,evidence_id,technical_confidence,insufficient_data,"
            "warning_flags_json "
            "from technical_scores where run_id=:run_id order by id"
        ),
        "regime": (
            "select id,evidence_id,confidence,warnings_json from market_regime_snapshots "
            "where run_id=:run_id order by id"
        ),
        "ceri": (
            "select id,ticker,calculation_context_id,evidence_id from ceri_score_snapshots "
            "where run_id=:run_id order by id"
        ),
    }
    with engine.connect() as db:
        retained = {
            name: [
                dict(row)
                for row in db.execute(
                    text(query), {"run_id": run_id, "pipeline_id": pipeline_id}
                ).mappings()
            ]
            for name, query in queries.items()
        }
    (env.artifact_dir / "db-results" / "terminal-status-sources.json").write_text(
        json.dumps(retained, indent=2, default=str), encoding="utf-8"
    )


def _assert_production_worker_fidelity(engine, env, run_id, pipeline_id) -> None:
    """Reconcile observations of untouched code with native committed lineage."""
    from app.services.configuration_delivery import (
        binding_reference,
        execution_configuration_reference,
    )

    log_path = env.artifact_dir / "logs" / "worker-fidelity.jsonl"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    entered = [row for row in records if row.get("pipeline_id") == pipeline_id
               and row["event"] == "native_handler_entered"]
    returned = [row for row in records if row.get("pipeline_id") == pipeline_id
                and row["event"] == "native_handler_returned"]
    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        jobs = list(db.scalars(select(BackgroundJob).where(BackgroundJob.related_run_id == run_id)))
        full_jobs = sorted((job for job in jobs if job.job_type == "FULL_PIPELINE"),
                           key=lambda job: job.id)
        dependencies = list(db.scalars(select(PipelineDependency).where(
            PipelineDependency.pipeline_run_id == pipeline_id,
            PipelineDependency.dependency_type == "CERI_WORKFLOW",
        )))
        anchor = binding_reference(db, pipeline_run_id=pipeline_id)
        worker = db.get(BackgroundWorker, entered[0]["worker_id"]) if entered else None
        native_handler = "app.services.background_worker._execute_full_pipeline_job"
        evidence = {
            "execution_id": env.execution_id,
            "database_name": env.database_name,
            "run_id": run_id,
            "pipeline_id": pipeline_id,
            "pipeline_status": pipeline.status,
            "handler_entries": entered,
            "handler_returns": returned,
            "executor_entries": [
                row for row in records if row["event"] == "native_executor_entered"
            ],
            "worker": {
                "worker_id": worker.worker_id,
                "instance_id": worker.instance_id,
                "process_id": worker.process_id,
                "heartbeat_at": worker.heartbeat_at,
                "control_loop_heartbeat_at": worker.control_loop_heartbeat_at,
            } if worker else None,
            "jobs": [{
                "id": job.id, "job_type": job.job_type, "status": job.status,
                "parent_job_id": job.parent_job_id, "root_job_id": job.root_job_id,
                "scope_id": job.scope_id, "refresh_cycle_id": job.refresh_cycle_id,
                "certification_session_id": job.payload_json.get("certification_session_id"),
                "authorized": job.payload_json.get("certification_authorized"),
                "progress_sequence": job.progress_sequence,
                "configuration_anchor": execution_configuration_reference(db, job)["fingerprint"],
                "lease_events": job.operational_metadata_json.get("lease_events", []),
            } for job in jobs],
            "dependencies": [{
                "id": dep.id, "state": dep.state, "root_job_id": dep.root_job_id,
                "child_job_id": dep.child_job_id, "continuation_job_id": dep.continuation_job_id,
                "result": dep.result_json,
            } for dep in dependencies],
        }
        (env.artifact_dir / "db-results" / "production-worker-fidelity.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True, default=str), encoding="utf-8",
        )
        assert len(full_jobs) == len(entered) == len(returned) == 2, evidence
        assert {row["job_id"] for row in entered} == {job.id for job in full_jobs}, evidence
        assert {row["job_id"] for row in returned} == {job.id for job in full_jobs}, evidence
        assert all(row["registered_original"] and row["native_worker_dispatch"]
                   and row["handler"] == native_handler for row in entered), evidence
        assert len({row["worker_instance_id"] for row in entered}) == 1, evidence
        assert len({row["execution_token_hash"] for row in entered}) == 2, evidence
        for job in jobs:
            assert job.status == "COMPLETED", evidence
            assert job.payload_json["certification_session_id"] == env.execution_id, evidence
            assert job.payload_json["certification_authorized"] is True, evidence
            assert (job.scope_id, job.refresh_cycle_id, job.acquisition_plan_id) == (
                pipeline.scope_id, pipeline.refresh_cycle_id, pipeline.acquisition_plan_id,
            ), evidence
            assert execution_configuration_reference(db, job) == anchor, evidence
        for row in entered:
            job = next(job for job in full_jobs if job.id == row["job_id"])
            worker = db.get(BackgroundWorker, row["worker_id"])
            assert worker.instance_id == row["worker_instance_id"], evidence
            assert worker.process_id == row["pid"], evidence
            assert worker.heartbeat_at is not None and worker.control_loop_heartbeat_at is not None
            assert row["configuration_anchor"] == anchor["fingerprint"], evidence
            # Terminal jobs clear their token and redact sensitive metadata.
            # Reconcile the committed claim by worker; compare token hashes at
            # the untouched handler/executor boundary below.
            assert row["execution_token_hash"] and any(
                event["event_type"] == "CLAIMED" and event["worker_id"] == row["worker_id"]
                for event in job.operational_metadata_json["lease_events"]
            ), evidence
            executor = [event for event in records if event["event"] == "native_executor_entered"
                        and event["job_id"] == job.id]
            assert len(executor) == 1 and executor[0]["independent_control_factory"], evidence
            assert executor[0]["execution_token_hash"] == row["execution_token_hash"], evidence
            assert all(name == f"_execute_full_pipeline_job.<locals>.{key}"
                       for key, name in executor[0]["callbacks"].items()), evidence
            completed = next(event for event in returned if event["job_id"] == job.id)
            assert all(completed["callback_calls"].get(name, 0) > 0
                       for name in ("lease_guard", "should_cancel", "progress_callback")), evidence
        root, continuation = full_jobs
        assert root.result_json["status"] == "WAITING_FOR_CERI_COMPLETION", evidence
        assert continuation.result_json["status"] == pipeline.status == "COMPLETED", evidence
        assert len(dependencies) == 1, evidence
        dependency = dependencies[0]
        assert dependency.state == "COMPLETED" and dependency.root_job_id == root.id, evidence
        assert dependency.continuation_job_id == continuation.id, evidence
        assert dependency.child_job_id == continuation.parent_job_id, evidence
        assert continuation.pipeline_dependency_id == dependency.id, evidence
        assert continuation.payload_json["resume_from_step"] == "FREEZING_DECISION_HANDOFF_MANIFEST"
        assert continuation.trigger_source == "CERI_COMPLETION_BARRIER", evidence
        assert dependency.result_json["workflow_key"] == (
            pipeline.result_json["ceri_provider_workflow_key"]
        ), evidence


def _assert_independent_golden_subset(engine, env, run_id, pipeline_id):
    """Check only independently established fields; never issue a certificate."""
    from app.models.ceri_tables import CeriIngestionRun, CeriSourceRecord
    from app.models.tables import (
        CombinedResult,
        FundamentalScore,
        PipelineStep,
        PriceBar,
        RankingResult,
        RawCompanyRow,
        TechnicalScore,
        TransitionDecisionHandoffManifest,
    )
    from single_run_certification.golden_oracle import (
        compare_records,
        expected_manifest,
    )

    expected = json.loads((env.artifact_dir / "expected-manifest.json").read_text())
    assert expected == expected_manifest(expected["flags"]["setup_capture_handoff_enabled"])
    actual = {}
    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        for domain, model in (("raw", RawCompanyRow), ("combined_members", CombinedResult)):
            rows = list(db.scalars(select(model).where(model.run_id == run_id)))
            actual[domain] = [{"ticker": row.ticker, **(
                {"row_number": row.row_number} if domain == "raw" else {}
            )} for row in rows]
        fundamental_fields = expected["domains"]["fundamental"][0]
        actual["fundamental"] = [{
            key: row.v2_warning_flags_json["flags"] if key == "warning_flags"
            else getattr(row, key)
            for key in fundamental_fields
        } for row in db.scalars(select(FundamentalScore).where(FundamentalScore.run_id == run_id))]
        indicator_fields = expected["domains"]["technical_indicators"][0]
        actual["technical_indicators"] = []
        for row in db.scalars(select(TechnicalScore).where(TechnicalScore.run_id == run_id)):
            identity = row.debug_json["calculation_identity"]
            assert identity["ownership"]["run_id"]["value"] == run_id
            assert identity["ownership"]["pipeline_id"]["value"] == pipeline_id
            derived = row.debug_json["derived"]
            observed = {key: derived[key] for key in indicator_fields
                        if key not in {"ticker", "session", "bar_count", "insufficient_data"}}
            observed.update(
                ticker=row.ticker,
                session=identity["temporal"]["as_of_session"]["value"],
                bar_count=db.scalar(select(func.count()).select_from(PriceBar).where(
                    PriceBar.ticker == row.ticker,
                    PriceBar.what_to_show == "ADJUSTED_LAST",
                )),
                insufficient_data=row.insufficient_data,
            )
            actual["technical_indicators"].append(observed)
        actual["rank_members"] = [{"ticker": row.ticker, "profile": row.ranking_profile}
                                  for row in db.scalars(select(RankingResult).where(
                                      RankingResult.run_id == run_id,
                                  ))]
        actual["stages"] = [{"order": row.step_order, "name": row.step_name}
                            for row in db.scalars(select(PipelineStep).where(
                                PipelineStep.pipeline_run_id == pipeline_id,
                            ))]
        sources = db.scalars(select(CeriSourceRecord).where(
            CeriSourceRecord.provider.in_(("eodhd", "sec")),
            CeriSourceRecord.restricted_normalized_json["ticker"].astext.in_(EXACT_TEN),
        ))
        actual["ceri_sources"] = [{
            "provider": row.provider, "dataset": row.dataset,
            "ticker": row.restricted_normalized_json["ticker"],
            "record_key": row.provider_record_id,
            "known_at": row.retrieved_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        } for row in sources]
        handoff = db.scalar(select(TransitionDecisionHandoffManifest).where(
            TransitionDecisionHandoffManifest.pipeline_run_id == pipeline_id,
        ))
        actual["business_time"] = {
            "market_cutoff": pipeline.market_calculation_context.cutoff_at.isoformat(),
            "ceri_cutoff": pipeline.result_json["ceri_calculation_cutoff_at"],
            "decision_at": handoff.created_at.isoformat(),
        }
        full_jobs = sorted(db.scalars(select(BackgroundJob).where(
            BackgroundJob.related_run_id == run_id, BackgroundJob.job_type == "FULL_PIPELINE",
        )), key=lambda row: row.id)
        claims = [next(
            event["occurred_at"] for event in row.operational_metadata_json["lease_events"]
            if event["event_type"] == "CLAIMED"
        ) for row in full_jobs]
        ingestion = list(db.scalars(select(CeriIngestionRun).where(
            CeriIngestionRun.scope_json["run_id"].astext == str(run_id),
        )))
        actual["operational_ingestion_clock"] = {
            "initial_claim_at": claims[0], "continuation_claim_at": claims[1],
            "ingestion_started_at": [row.started_at.isoformat() for row in ingestion],
        }
    # Persist mismatches before asserting; never rewrite reviewed expectations.
    path = env.artifact_dir / "db-results" / "independent-golden-subset.json"
    path.write_text(json.dumps({"expected": expected, "actual": actual,
                                "claim": "INDEPENDENT_SUBSET_ONLY",
                                "unresolved": expected["unresolved"]},
                               indent=2, sort_keys=True, default=str), encoding="utf-8")
    for domain in ("raw", "combined_members", "fundamental", "technical_indicators"):
        compare_records(expected["domains"][domain], actual[domain], keys=("ticker",),
                        tolerance="0.00000001" if domain == "technical_indicators" else "0")
    for domain, keys in (("rank_members", ("profile", "ticker")), ("stages", ("order",)),
                         ("ceri_sources", ("provider", "dataset", "record_key"))):
        compare_records(expected["domains"][domain], actual[domain], keys=keys)
    for name, value in expected["business_time"].items():
        assert datetime.fromisoformat(actual["business_time"][name].replace("Z", "+00:00")) == (
            datetime.fromisoformat(value)
        )
    operational = actual["operational_ingestion_clock"]
    assert operational["ingestion_started_at"]
    assert all(datetime.fromisoformat(operational["initial_claim_at"]) <= (
        datetime.fromisoformat(value)
    ) <= datetime.fromisoformat(operational["continuation_claim_at"])
               for value in operational["ingestion_started_at"])


def _seed_retained_provider_history(engine, *, tickers: tuple[str, ...]) -> None:
    """Create pre-cutoff retained history exclusively through production writers.

    Provider acquisition performed after a pipeline freezes its cutoff is
    correctly ineligible for that same AS_KNOWN calculation. A production-shaped
    clone therefore needs already-known provider history; the in-pipeline async
    DAG still performs a real refresh and deduplicates these frozen responses.
    """

    from single_run_certification.golden_runtime import business_now, current_manifest

    service = CeriIngestionService(
        registry=frozen_ceri_registry(),
        settings=Settings(sec_document_incremental_mode=SecDocumentIncrementalMode.OFF),
    )
    normalizer = CeriNormalizationService()
    processing_runs = CeriProcessingRunService()
    # Revision estimates are the minimal pre-cutoff retained population needed
    # for score certification. The in-pipeline DAG still executes all four
    # provider/dataset families against the frozen responses.
    provider_datasets = {"eodhd": (CeriDataset.ESTIMATES,)}
    with Session(engine) as db:
        for provider, datasets in provider_datasets.items():
            for dataset in datasets:
                for ticker in tickers:
                    result = service.ingest(
                        db,
                        CeriIngestionRequest(
                            provider=provider,
                            dataset=dataset,
                            ticker=ticker,
                            request_key=(
                                f"certification-retained:{provider}:{dataset.value}:{ticker}"
                            ),
                            scope={"ticker": ticker, "retained_clone": True},
                            business_cutoff=business_now().date() if current_manifest() else None,
                        ),
                    )
                    assert result.failed == 0 and result.quarantined == 0, result.as_dict()
                    processing, _ = processing_runs.create_or_get(
                        db,
                        job_type="CERI_NORMALIZE_BATCH",
                        request_key=(
                            f"certification-retained:normalize:{provider}:{dataset.value}:{ticker}"
                        ),
                        scope={"ticker": ticker, "retained_clone": True},
                    )
                    normalized = normalizer.normalize(
                        db,
                        processing_run=processing,
                        ingestion_run_id=result.ingestion_run_id,
                    )
                    assert normalized.failed == 0, normalized.errors
                    processing_runs.finish(
                        db,
                        processing,
                        status="COMPLETED",
                        counts=normalized.as_dict(),
                        checkpoint={
                            "ticker": ticker,
                            "completed_at": datetime.now(UTC).isoformat(),
                        },
                    )
        db.commit()


def _mature_and_publish_one_generation(
    engine, *, run_id: int, ticker: str = EXACT_TEN[0]
) -> dict[str, int]:
    """Continue the exact provider lineage through positive serving publication."""

    config = load_winner_probability_config()
    config = replace(config, engine=replace(config.engine, enabled=True))
    config = replace(config, config_hash=winner_probability_config_hash(config))
    with Session(engine) as db:
        prediction = db.scalar(
            select(WinnerPredictionSnapshot).where(
                WinnerPredictionSnapshot.run_id == run_id,
                WinnerPredictionSnapshot.ticker == ticker,
            )
        )
        assert prediction is not None
        forward = db.scalar(
            select(WinnerForwardOutcome).where(
                WinnerForwardOutcome.prediction_id == prediction.id,
                WinnerForwardOutcome.entry_model == "NEXT_OPEN",
                WinnerForwardOutcome.horizon_sessions == 5,
                WinnerForwardOutcome.is_current_revision.is_(True),
            )
        )
        assert forward is not None
        sessions = required_outcome_sessions(forward.entry_session, forward.horizon_sessions)
        observed_at = datetime.combine(forward.due_session, time(22), tzinfo=UTC)
        for basis in ("ADJUSTED_LAST", "TRADES"):
            bars = []
            for index, session in enumerate(sessions):
                open_price = Decimal("100") + index
                bars.append(
                    HistoricalBar(
                        ticker=prediction.ticker,
                        bar_date=session,
                        timeframe="1 day",
                        open=float(open_price),
                        high=float(open_price + Decimal("4")),
                        low=float(open_price - Decimal("1")),
                        close=float(open_price + Decimal("2")),
                        volume=2_000_000 + index,
                        source="QA_LATER_MARKET_BARS",
                        what_to_show=basis,
                        adjustment_type="adjusted" if basis == "ADJUSTED_LAST" else "raw",
                    )
                )
            cache_bars(db, bars)
        obligations = list(
            db.scalars(
                select(WinnerMarketDataObligation).where(
                    WinnerMarketDataObligation.forward_outcome_id == forward.id
                )
            )
        )
        evaluated = MarketDataObligationService().evaluate(
            db,
            obligations=obligations,
            now=observed_at + timedelta(days=1),
        )
        assert evaluated.satisfied == len(obligations), evaluated.as_dict()
        matured = OutcomeMaturationService().process_forward_outcome(
            db,
            forward,
            now=observed_at + timedelta(days=1, seconds=1),
        )
        assert matured.matured == 1 and matured.target_stop_matured > 0, matured.as_dict()
        db.commit()

        target = db.scalar(
            select(WinnerTargetStopOutcome).where(
                WinnerTargetStopOutcome.forward_outcome_id == forward.id,
                WinnerTargetStopOutcome.is_current_revision.is_(True),
            )
        )
        assert target is not None
        definition = db.get(WinnerOutcomeDefinition, target.outcome_definition_id)
        assert definition is not None
        publication_at = observed_at + timedelta(days=1, seconds=2)
        advance = EvidenceWatermarkService().advance_to_current_material_evidence(
            db,
            outcome_definition=definition,
            config=config,
            observed_at=publication_at,
        )
        assert advance.advanced, advance
        generation = CohortGenerationService().capture_or_resume(
            db,
            state=advance.state,
            contract=contract_for(definition, config),
            requested_at=publication_at + timedelta(microseconds=1),
            config=config,
        )
        db.commit()
        result = CohortMaterializationService().materialize_slice(
            db,
            generation=generation,
            outcome_definition=definition,
            config=config,
            lease_guard=lambda: None,
            should_cancel=lambda: False,
            operation_at=publication_at + timedelta(microseconds=2),
        )
        assert result.status == CohortGenerationStatus.PUBLISHED, result
        assert result.publication_status == GenerationPublicationStatus.PUBLISHED, result
        assert result.evidence_rows_loaded == 1, result
        db.commit()

        generation = db.get(WinnerCohortGeneration, generation.id)
        state = db.get(WinnerCohortRefreshState, generation.refresh_state_id)
        assert state.published_generation_id == generation.id
        manifest_ids = select(WinnerCohortStatistic.evidence_manifest_id).where(
            WinnerCohortStatistic.generation_id == generation.id
        )
        manifest_members = int(
            db.scalar(
                select(func.count(WinnerEvidenceManifestMember.id)).where(
                    WinnerEvidenceManifestMember.manifest_id.in_(manifest_ids)
                )
            )
            or 0
        )
        assert manifest_members > 0
        repeated = CohortMaterializationService().materialize_slice(
            db,
            generation=generation,
            outcome_definition=definition,
            config=config,
            lease_guard=lambda: None,
            should_cancel=lambda: False,
            operation_at=publication_at + timedelta(seconds=1),
        )
        assert repeated.publication_status == GenerationPublicationStatus.ALREADY_ACTIVE
        return {
            "evidence_rows": int(generation.evidence_row_count),
            "episodes": int(db.scalar(select(func.count(WinnerPredictionEpisode.id))) or 0),
            "published_generations": int(
                db.scalar(
                    select(func.count(WinnerCohortGeneration.id)).where(
                        WinnerCohortGeneration.status == CohortGenerationStatus.PUBLISHED,
                        WinnerCohortGeneration.refresh_state_id == state.id,
                    )
                )
                or 0
            ),
            "serving_estimates": int(
                db.scalar(
                    select(func.count(WinnerProbabilityEstimate.id)).where(
                        WinnerProbabilityEstimate.prediction_id == prediction.id,
                        WinnerProbabilityEstimate.estimate_kind == "DECISION_TIME",
                    )
                )
                or 0
            ),
        }
