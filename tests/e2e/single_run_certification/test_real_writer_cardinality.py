"""Disposable full-pipeline cardinality gates with real internal writers."""

import csv
import hashlib
import json
import os
import threading
import time as time_module
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter

import psutil
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.ceri_tables import CeriCompany, CeriIngestionRunSourceRecord
from app.models.tables import (
    IBContract,
    RawCompanyRow,
    UploadRun,
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
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.enums import CeriDataset
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.processing_run_service import CeriProcessingRunService
from app.services.ib_data_fetcher import HistoricalBar
from app.services.us_market_calendar import previous_us_trading_day
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
from app.services.work_scope_identity import ScopeMember, membership_fingerprint
from app.settings import SecDocumentIncrementalMode, Settings
from single_run_certification.fixtures import (
    FIXTURE_VERSION,
    _seed_price_bars,
    _seed_setup_lifecycle_baseline,
    certification_as_of_session,
    seed_prerequisites,
)
from single_run_certification.frozen_ceri import frozen_ceri_registry
from single_run_certification.reporting import CertificationRecorder
from single_run_certification.test_single_run_certification import (
    _launch_run_through_gui,
    _run_pipeline_through_gui,
    certification_environment,  # noqa: F401
    certification_page,  # noqa: F401
    certification_provider_mode,  # noqa: F401
)
from single_run_certification.transaction_observer import PostgresTransactionObserver

EXACT_TEN = ("BHE", "BLLN", "KLIC", "LSCC", "PDFS", "ACMR", "RDVT", "AVT", "DVN", "JNJ")


def _assert_admission_harness_ready(env) -> dict[str, object]:
    status_paths = sorted((env.artifact_dir / "logs").glob("adapter-status-*.json"))
    statuses = [json.loads(path.read_text(encoding="utf-8")) for path in status_paths]
    web_statuses = [row for row in statuses if row.get("process_role") == "WEB"]
    assert len(web_statuses) == 1, statuses
    status = web_statuses[0]
    expected = {
        "adapter_module": "certification_server",
        "execution_id": env.execution_id,
        "runtime_mode": "CERTIFICATION",
        "database_safety_context": "DISPOSABLE_TEST",
        "database_name": env.database_name,
        "trace_installed": True,
        "trace_directory": str((env.artifact_dir / "logs").resolve()),
        "trace_directory_writable": True,
        "admission_mode": "intercept_before_enqueue",
        "intercept_installed": True,
    }
    assert {key: status.get(key) for key in expected} == expected
    assert psutil.pid_exists(int(status["process_id"]))
    trace_path = env.artifact_dir / "logs" / f"transaction-ownership-{status['process_id']}.jsonl"
    assert trace_path.is_file()
    trace_events = [
        json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(row["event"] == "trace_installed" for row in trace_events)
    assert any(row["event"] == "admission_intercept_installed" for row in trace_events)
    write_probe = env.artifact_dir / "logs" / ".controller-write-probe"
    write_probe.write_text("writable", encoding="utf-8")
    write_probe.unlink()
    return status


@pytest.fixture
def certification_provider_ingest_enabled() -> bool:
    return True


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.destructive
@pytest.mark.parametrize("size", (10, 100))
def test_frozen_real_writer_cardinality(
    certification_environment,  # noqa: F811
    certification_page,  # noqa: F811
    size,
):
    env = certification_environment
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

    engine = create_engine(env.database_url)
    try:
        _prepare_real_writer_pre_pipeline_baseline(
            engine,
            decoy_run_id=env.seed.decoy_run_id,
            tickers=tickers,
        )

        recorder = CertificationRecorder(execution_id=env.execution_id)
        run_id = _launch_run_through_gui(certification_page, env, recorder)
        _seed_retained_provider_history(engine, tickers=tickers)
        pipeline_id, status = _run_pipeline_through_gui(
            certification_page,
            env,
            recorder,
            run_id,
            absolute_deadline_seconds=3600 if size == 100 else 900,
            progress_deadline_seconds=900 if size == 100 else 180,
        )
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
                            "result_json->'failure_classification' as failure_classification, "
                            "operational_metadata_json->'failure_classification' "
                            "as operational_failure_classification, "
                            "operational_metadata_json#>'{ceri_feature_rebuild,rows_loaded}' "
                            "as rows_loaded from background_jobs "
                            "where related_run_id=:run_id order by id"
                        ),
                        {"run_id": run_id},
                    )
                    .mappings()
                    .all()
                )
            exported_failure = _export_task_2_9_12o_failure_evidence(
                engine,
                artifact_dir=env.artifact_dir,
                execution_id=env.execution_id,
                run_id=run_id,
                pipeline_id=pipeline_id,
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
                        "failure_evidence": str(exported_failure),
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
                        "provider_source_records": provider_source_count,
                        "job_counts": job_counts,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        if size == 10:
            publication = _mature_and_publish_one_generation(engine, run_id=run_id)
            assert publication == {
                "evidence_rows": 1,
                "episodes": size,
                "published_generations": 1,
                "serving_estimates": 1,
            }
    finally:
        engine.dispose()


def _export_task_2_9_12o_failure_evidence(
    engine,
    *,
    artifact_dir: Path,
    execution_id: str,
    run_id: int,
    pipeline_id: int,
) -> Path:
    """Export replayable deterministic anchor evidence before disposable-DB teardown."""

    from app.models.tables import (
        MarketCalculationContext,
        TransitionDecisionHandoffManifest,
        TransitionPreflightPlan,
    )
    from app.services.market_calculation_context_service import cutoff_from_row
    from app.services.setup_lifecycle.repository import SetupLifecycleRepository
    from app.services.setup_lifecycle.snapshot_builder import (
        SetupLifecycleSnapshotBuilder,
        build_run_context_snapshots,
    )
    from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader
    from app.services.transition_preflight_plan_service import (
        _canonical_structural_differences,
        _semantic_model_payload,
        build_run_start_anchor_manifest,
        reconstruct_transition_decision_manifests,
    )

    evidence_dir = artifact_dir / "task-2.9.12o-failure-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    with Session(engine) as db:
        plan = db.scalar(
            select(TransitionPreflightPlan).where(
                TransitionPreflightPlan.upload_run_id == run_id,
                TransitionPreflightPlan.pipeline_run_id == pipeline_id,
            )
        )
        if plan is None:
            raise AssertionError("failed certification has no pipeline-bound transition plan")
        context_row = db.get(MarketCalculationContext, plan.market_calculation_context_id)
        if context_row is None:
            raise AssertionError("failed certification has no frozen market context")
        cutoff = cutoff_from_row(context_row)
        repository = SetupLifecycleRepository()
        run_context = SetupLifecycleSourceLoader().load_run_context(
            db,
            run_id,
            market_cutoff=cutoff,
            tickers=set(plan.tickers_json or ()),
        )
        built_rows = build_run_context_snapshots(
            db,
            run_context,
            builder=SetupLifecycleSnapshotBuilder(),
            repository=repository,
        )
        decision_manifests = reconstruct_transition_decision_manifests(
            db,
            market_cutoff=cutoff,
            built_rows=built_rows,
            repository=repository,
            tickers=set(plan.tickers_json or ()),
        )
        reconstructed = build_run_start_anchor_manifest(
            db,
            upload_run_id=run_id,
            market_cutoff=cutoff,
            decision_manifests=decision_manifests,
        )
        stored = dict(plan.run_start_anchor_json or {})
        handoff = db.scalar(
            select(TransitionDecisionHandoffManifest).where(
                TransitionDecisionHandoffManifest.preflight_plan_id == plan.id
            )
        )
        raw_rows = list(
            db.scalars(
                select(RawCompanyRow)
                .where(RawCompanyRow.run_id == run_id)
                .order_by(RawCompanyRow.row_number, RawCompanyRow.id)
            )
        )
        jobs = [
            dict(row)
            for row in db.execute(
                text(
                    "select id, job_type, status, parent_job_id, root_correlation_id, "
                    "trigger_source, request_key, workflow_key, retry_count, recovery_count, "
                    "result_json->'failure_classification' as failure_classification, "
                    "operational_metadata_json->'failure_classification' "
                    "as operational_failure_classification "
                    "from background_jobs where related_run_id=:run_id order by id"
                ),
                {"run_id": run_id},
            ).mappings()
        ]
        ceri_counts = [
            dict(row)
            for row in db.execute(
                text(
                    "select calculation_context_id, count(*) as rows, "
                    "count(*) filter (where evidence_id is not null) as certified_rows, "
                    "count(distinct ticker) as tickers "
                    "from ceri_score_snapshots where run_id=:run_id "
                    "group by calculation_context_id order by calculation_context_id"
                ),
                {"run_id": run_id},
            ).mappings()
        ]
        pipeline = (
            db.execute(
                text(
                    "select status, current_step, result_json, error_message "
                    "from pipeline_runs where id=:pipeline_id"
                ),
                {"pipeline_id": pipeline_id},
            )
            .mappings()
            .one()
        )
        steps = [
            dict(row)
            for row in db.execute(
                text(
                    "select step_order, step_name, status, message, error_message, result_json "
                    "from pipeline_steps where pipeline_run_id=:pipeline_id order by step_order"
                ),
                {"pipeline_id": pipeline_id},
            ).mappings()
        ]

        raw_projection = [
            {
                "id": row.id,
                "row_number": row.row_number,
                "ticker": row.ticker,
                "semantic_digest": CanonicalEvidenceSerializer.fingerprint(
                    _semantic_model_payload(row)
                ),
            }
            for row in raw_rows
        ]
        payload = {
            "schema": "swinglens.task-2.9.12o-failure-evidence.v1",
            "execution_id": execution_id,
            "fixture_version": FIXTURE_VERSION,
            "alembic_head": "0092_phase2_eligibility_ledgers",
            "run_id": run_id,
            "pipeline_id": pipeline_id,
            "preflight_plan_id": plan.id,
            "market_calculation_context_id": plan.market_calculation_context_id,
            "stored_anchor": stored,
            "stored_anchor_fingerprint": plan.run_start_anchor_fingerprint,
            "reconstructed_anchor": reconstructed,
            "reconstructed_anchor_fingerprint": CanonicalEvidenceSerializer.fingerprint(
                reconstructed
            ),
            "comparison": _canonical_structural_differences(stored, reconstructed),
            "raw_source_projection": raw_projection,
            "decision_manifest_fingerprints": {
                ticker: wrapper.get("decision_manifest_fingerprint")
                for ticker, wrapper in sorted(decision_manifests.items())
            },
            "handoff": (
                {
                    "id": handoff.id,
                    "run_start_anchor_fingerprint": handoff.run_start_anchor_fingerprint,
                    "manifest_fingerprint": handoff.manifest_fingerprint,
                }
                if handoff is not None
                else None
            ),
            "ceri_counts_by_context": ceri_counts,
            "pipeline": dict(pipeline),
            "steps": steps,
            "jobs": jobs,
        }
    retained_dir = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "remediation"
        / "readiness-phase2"
        / "evidence"
        / "task-2.9.12o"
        / execution_id
    )
    retained_dir.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True, default=str)
    outputs = (
        evidence_dir / "anchor-and-lineage.json",
        retained_dir / "anchor-and-lineage.json",
    )
    for output in outputs:
        output.write_text(serialized, encoding="utf-8")
        assert output.stat().st_size > 0
        json.loads(output.read_text(encoding="utf-8"))
    return outputs[-1]


def _prepare_real_writer_pre_pipeline_baseline(
    engine,
    *,
    decoy_run_id: int,
    tickers: tuple[str, ...],
    transaction_phase_started_callback=None,
    transaction_boundary_callback=None,
) -> None:
    """Prepare the exact 100-member baseline without spanning unrelated units.

    Setup identities and current selections are one committed fixture unit. Price
    history is independent evidence and is materialized in a fresh Session, so
    its CPU-heavy ORM construction cannot retain the completed Setup transaction.
    """

    as_of_session = certification_as_of_session()
    setup_identity = None
    if transaction_phase_started_callback is not None:
        transaction_phase_started_callback("SETUP_IDENTITY", "setup-baseline-writer")
    with Session(engine) as db:
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
        _seed_retained_ceri_companies(db, tickers=tickers)
        _seed_setup_lifecycle_baseline(
            db,
            run_id=decoy_run_id,
            as_of_session=as_of_session,
            tickers=tickers,
        )
        if transaction_boundary_callback is not None:
            db.flush()
            setup_identity = _fixture_transaction_identity(db)
        db.commit()
    if transaction_boundary_callback is not None:
        transaction_boundary_callback("SETUP_IDENTITY", setup_identity)

    price_identity = None
    if transaction_phase_started_callback is not None:
        transaction_phase_started_callback("PRICE_HISTORY", "price-history-writer")
    with Session(engine) as db:
        _seed_price_bars(
            db,
            tickers=tickers,
            as_of_session=as_of_session,
        )
        if transaction_boundary_callback is not None:
            # All expensive Python materialization precedes the first database
            # flush in this fresh Session. Measure the actual writer transaction.
            db.flush()
            price_identity = _fixture_transaction_identity(db)
        db.commit()
    if transaction_boundary_callback is not None:
        transaction_boundary_callback("PRICE_HISTORY", price_identity)


def _fixture_transaction_identity(db: Session) -> dict[str, int | float]:
    connection = db.connection().connection.driver_connection
    return {
        "session_id": id(db),
        "backend_pid": int(connection.info.backend_pid),
        "transaction_age_seconds": float(
            db.scalar(
                text("select extract(epoch from clock_timestamp() - transaction_timestamp())")
            )
            or 0
        ),
    }


def _seed_retained_ceri_companies(db: Session, *, tickers: tuple[str, ...]) -> None:
    """Persist the exact identity prerequisite used by the production CERI workflow."""

    existing = {
        (str(ticker).upper(), str(exchange or "").upper())
        for ticker, exchange in db.execute(select(CeriCompany.ticker, CeriCompany.exchange))
    }
    for ticker in tickers:
        identity = (ticker.upper(), "US")
        if identity in existing:
            continue
        db.add(
            CeriCompany(
                ticker=identity[0],
                exchange=identity[1],
                current_provider_ids_json={"eodhd": f"{identity[0]}.US"},
            )
        )
        existing.add(identity)
    db.flush()


def _seed_retained_provider_history(
    engine,
    *,
    tickers: tuple[str, ...],
    transaction_phase_started_callback=None,
    transaction_boundary_callback=None,
) -> None:
    """Create pre-cutoff retained history exclusively through production writers.

    Provider acquisition performed after a pipeline freezes its cutoff is
    correctly ineligible for that same AS_KNOWN calculation. A production-shaped
    clone therefore needs already-known provider history; the in-pipeline async
    DAG still performs a real refresh and deduplicates these frozen responses.
    """

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
    completed = 0
    for provider, datasets in provider_datasets.items():
        for dataset in datasets:
            for ticker in tickers:
                # Retained provider inputs are independently authoritative per
                # ticker. Commit the complete ingestion + normalization closure
                # for one ticker, then close its Session before preparing the
                # next member. The fixture must not turn 100 independent source
                # identities into one long pre-pipeline transaction.
                transaction_scope_started = perf_counter()
                if transaction_phase_started_callback is not None:
                    transaction_phase_started_callback(
                        "RETAINED_PROVIDER_HISTORY",
                        f"{provider}:{dataset.value}:{ticker}",
                    )
                with Session(engine) as db:
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
                    assert normalized.status == "COMPLETED", normalized
                    assert normalized.read == 4, normalized
                    assert normalized.normalized == 4, normalized
                    assert normalized.quarantined == 0, normalized
                    assert normalized.failed == 0, normalized
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
                    transaction_age_seconds = float(
                        db.scalar(
                            text(
                                "select extract(epoch from "
                                "clock_timestamp() - transaction_timestamp())"
                            )
                        )
                        or 0
                    )
                    backend_pid = int(db.scalar(text("select pg_backend_pid()")) or 0)
                    ingestion_run_id = int(result.ingestion_run_id)
                    processing_run_id = int(processing.id)
                    db.commit()
                completed += 1
                if transaction_boundary_callback is not None:
                    transaction_boundary_callback(
                        completed,
                        ticker,
                        {
                            "ingestion_run_id": ingestion_run_id,
                            "processing_run_id": processing_run_id,
                            "backend_pid": backend_pid,
                            "transaction_age_seconds": transaction_age_seconds,
                            "transaction_scope_seconds": perf_counter() - transaction_scope_started,
                        },
                    )


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.destructive
@pytest.mark.certification_harness(
    transaction_trace=True,
    admission_mode="intercept_before_enqueue",
)
def test_browser_admission_closes_preflight_creation_transaction(
    certification_environment,  # noqa: F811
    certification_page,  # noqa: F811
) -> None:
    """Exercise the real browser POST through the pre-enqueue ownership boundary."""

    env = certification_environment
    page = certification_page
    tickers = EXACT_TEN + tuple(f"F{index:04d}" for index in range(90))
    with env.csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        exemplar = next(reader)
    with env.csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for ticker in tickers:
            writer.writerow(
                {**exemplar, "Symbol": ticker, "Description": f"{ticker} admission fixture"}
            )

    engine = create_engine(
        env.database_url,
        connect_args={"application_name": "task-2.9.12n-admission-harness"},
    )
    observer_path = env.artifact_dir / "logs" / "browser-admission-observer.jsonl"
    observer = PostgresTransactionObserver(
        env.database_url,
        observer_path,
        application_name="task-2.9.12n-browser-observer",
    )
    monitor_stopped = threading.Event()
    monitor_samples: list[dict[str, object]] = []
    monitor_errors: list[str] = []

    def monitor() -> None:
        try:
            while not monitor_stopped.wait(0.1):
                monitor_samples.append(observer.sample())
        except Exception as exc:
            monitor_errors.append(f"{type(exc).__name__}: {exc}")

    monitor_thread = threading.Thread(target=monitor, name="task-2.9.12n-observer", daemon=True)
    try:
        harness_status = _assert_admission_harness_ready(env)
        _prepare_real_writer_pre_pipeline_baseline(
            engine,
            decoy_run_id=env.seed.decoy_run_id,
            tickers=tickers,
        )
        recorder = CertificationRecorder(execution_id=env.execution_id)
        run_id = _launch_run_through_gui(page, env, recorder)
        _seed_retained_provider_history(engine, tickers=tickers)

        assert _assert_admission_harness_ready(env) == harness_status
        monitor_thread.start()
        page.get_by_role("button", name="Run full pipeline").click()
        ib_preflight = page.locator("[data-ib-preflight-panel]")
        ib_preflight.wait_for(state="visible", timeout=30_000)
        ib_preflight.locator("[data-ib-run-ready]").wait_for(state="visible", timeout=30_000)
        ib_preflight.locator("[data-ib-run-ready]").click()
        confirm = page.locator("[data-confirm-panel]")
        confirm.wait_for(state="visible", timeout=30_000)
        with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.endswith(f"/runs/{run_id}/pipeline")
            ),
            timeout=300_000,
        ) as response_info:
            confirm.locator("[data-confirm-continue]").click(no_wait_after=True)
        response = response_info.value
        assert response.status == 409
        assert response.json()["detail"]["code"] == "CERTIFICATION_PREFLIGHT_BOUNDARY_REACHED"
    finally:
        monitor_stopped.set()
        if monitor_thread.is_alive():
            monitor_thread.join(timeout=10)

    assert monitor_errors == []
    trace_paths = sorted((env.artifact_dir / "logs").glob("transaction-ownership-*.jsonl"))
    assert trace_paths
    trace = [
        json.loads(line)
        for path in trace_paths
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    boundary = next(
        row
        for row in trace
        if row["event"] == "admission_boundary"
        and row["phase"] == "AFTER_INDEPENDENT_VERIFICATION_BEFORE_ADMISSION_MUTATION"
    )
    rollback_boundary = next(
        row
        for row in trace
        if row["event"] == "admission_boundary" and row["phase"] == "AFTER_INTERCEPT_ROLLBACK"
    )
    assert boundary["session_in_transaction"] is True
    assert rollback_boundary["session_in_transaction"] is False
    assert rollback_boundary["session_id"] == boundary["session_id"]
    backend_pid = int(boundary["backend_pid"])
    setup_reads = [
        row
        for row in trace
        if row["event"] == "setup_snapshot_sql_first"
        and int(row.get("backend_pid") or -1) == backend_pid
        and any("transition_preflight_plan_service.py" in frame for frame in row["query_stack"])
    ]
    assert setup_reads
    request_begins = [
        row
        for row in trace
        if row["event"] == "session_transaction_begin"
        and row["session_id"] == boundary["session_id"]
        and row.get("request_id") == boundary.get("request_id")
    ]
    request_ends = [
        row
        for row in trace
        if row["event"] == "session_transaction_end"
        and row["session_id"] == boundary["session_id"]
        and row.get("request_id") == boundary.get("request_id")
    ]
    assert [row["phase"] for row in request_begins] == [
        "PREFLIGHT_CREATION",
        "PRE_ENQUEUE_VERIFICATION",
    ]
    assert [row["phase"] for row in request_ends] == [
        "PREFLIGHT_CREATION",
        "PRE_ENQUEUE_VERIFICATION",
    ]
    creation_begin, verification_begin = request_begins
    creation_end, verification_end = request_ends
    assert int(verification_begin["backend_pid"]) == backend_pid
    assert creation_end["completion_action"] == "COMMIT"
    assert verification_end["completion_action"] == "ROLLBACK"
    commits = [
        row
        for row in trace
        if row["event"] == "session_commit"
        and row["session_id"] == boundary["session_id"]
        and creation_begin["timestamp"] < row["timestamp"] < creation_end["timestamp"]
    ]
    assert commits
    rollbacks = [
        row
        for row in trace
        if row["event"] == "session_rollback"
        and row["session_id"] == boundary["session_id"]
        and boundary["timestamp"] < row["timestamp"] < verification_end["timestamp"]
    ]
    assert rollbacks
    creation_seconds = (
        datetime.fromisoformat(creation_end["timestamp"])
        - datetime.fromisoformat(creation_begin["timestamp"])
    ).total_seconds()
    verification_seconds = (
        datetime.fromisoformat(verification_end["timestamp"])
        - datetime.fromisoformat(verification_begin["timestamp"])
    ).total_seconds()
    assert creation_seconds < 120
    assert verification_seconds < 120
    session_creations = [
        row
        for row in trace
        if row["event"] == "session_created" and row["session_id"] == boundary["session_id"]
    ]
    session_closes = [
        row
        for row in trace
        if row["event"] == "session_close_end" and row["session_id"] == boundary["session_id"]
    ]
    assert len(session_creations) == 1
    assert session_creations[0]["session_creation_stack"]
    assert len(session_closes) == 1
    assert session_closes[0]["session_in_transaction"] is False
    sql_summaries = [
        row
        for row in trace
        if row["event"] == "transaction_sql_summary"
        and row["session_id"] == boundary["session_id"]
        and row["phase"] in {"PREFLIGHT_CREATION", "PRE_ENQUEUE_VERIFICATION"}
    ]
    assert [row["phase"] for row in sql_summaries] == [
        "PREFLIGHT_CREATION",
        "PRE_ENQUEUE_VERIFICATION",
    ]
    assert all(row["sql_count"] > 0 for row in sql_summaries)

    final_sample = observer.sample()
    backend_rows = [row for row in final_sample["transactions"] if int(row["pid"]) == backend_pid]
    assert backend_rows == []
    with engine.connect() as connection:
        counts = dict(
            connection.execute(
                text(
                    "select (select count(*) from transition_preflight_plans "
                    "where upload_run_id=:run_id) as plans, "
                    "(select count(*) from transition_preflight_plans "
                    "where upload_run_id=:run_id and status='RESERVED') as reserved_plans, "
                    "(select count(*) from pipeline_runs "
                    "where upload_run_id=:run_id) as pipelines, "
                    "(select count(*) from background_jobs where related_run_id=:run_id) as jobs, "
                    "(select count(*) from raw_company_rows where run_id=:run_id) as raw_rows"
                ),
                {"run_id": run_id},
            )
            .mappings()
            .one()
        )
    assert counts == {
        "plans": 1,
        "reserved_plans": 1,
        "pipelines": 0,
        "jobs": 0,
        "raw_rows": 100,
    }

    ownership = {
        "classification": "API_DEPENDENCY_ADMISSION_SERVICE",
        "execution_id": env.execution_id,
        "historical_backend_pid": 15932,
        "correlated_backend_pid": backend_pid,
        "session_id": boundary["session_id"],
        "process_id": boundary["process_id"],
        "process_role": boundary["process_role"],
        "thread_id": boundary["thread_id"],
        "task": boundary["task"],
        "http_method": boundary.get("http_method"),
        "http_path": boundary.get("http_path"),
        "creation_backend_pid": creation_begin["backend_pid"],
        "transaction_began_at": creation_begin["timestamp"],
        "transaction_committed_at": creation_end["timestamp"],
        "creation_transaction_seconds": creation_seconds,
        "verification_transaction_began_at": verification_begin["timestamp"],
        "verification_transaction_rolled_back_at": verification_end["timestamp"],
        "verification_transaction_seconds": verification_seconds,
        "setup_query_count": sum(row["setup_snapshot_query_count"] for row in sql_summaries),
        "setup_query_stack": setup_reads[-1]["query_stack"],
        "creation_stack": creation_begin["begin_stack"],
        "verification_stack": verification_begin["begin_stack"],
        "boundary_stack": boundary["boundary_stack"],
        "post_boundary_session_in_transaction": rollback_boundary["session_in_transaction"],
        "harness_status": harness_status,
        "postgres_open_at_completion": backend_rows,
        "monitor_samples": len(monitor_samples) + 1,
        "monitor_errors": monitor_errors,
        "unauthorized_work": counts,
        "trace_files": [str(path) for path in trace_paths],
        "observer_log": str(observer_path),
    }
    output_root = Path(__file__).resolve().parents[3] / "docs/remediation/readiness-phase2"
    request_trace = [
        row
        for row in trace
        if row.get("request_id") == boundary.get("request_id")
        or row["event"] in {"trace_installed", "admission_intercept_installed"}
    ]
    session_trace = {
        "status": "PASS",
        "ownership": ownership,
        "events": request_trace,
    }
    (output_root / "TASK_2_9_12N_SESSION_TRACE.json").write_text(
        json.dumps(session_trace, indent=2, sort_keys=True), encoding="utf-8"
    )
    stage_timings = [
        row
        for row in trace
        if row["event"] == "admission_stage_timing"
        and row.get("request_id") == boundary.get("request_id")
    ]
    observed_ages = [
        float(row["age_seconds"])
        for sample in monitor_samples
        for row in sample["transactions"]
        if int(row["pid"])
        in {
            int(creation_begin["backend_pid"]),
            int(verification_begin["backend_pid"]),
        }
    ]
    performance = {
        "status": "PASS",
        "execution_id": env.execution_id,
        "session_id": boundary["session_id"],
        "transactions": [
            {
                "phase": creation_begin["phase"],
                "backend_pid": creation_begin["backend_pid"],
                "seconds": creation_seconds,
                "sql": sql_summaries[0],
            },
            {
                "phase": verification_begin["phase"],
                "backend_pid": verification_begin["backend_pid"],
                "seconds": verification_seconds,
                "sql": sql_summaries[1],
            },
        ],
        "stage_timings": stage_timings,
        "maximum_observed_postgresql_transaction_age_seconds": max(observed_ages, default=0.0),
        "hard_limit_seconds": 120,
        "objective_seconds": 60,
    }
    (output_root / "TASK_2_9_12N_ADMISSION_PERFORMANCE.json").write_text(
        json.dumps(performance, indent=2, sort_keys=True), encoding="utf-8"
    )
    observer.close()
    engine.dispose()


def test_task_2_9_12n_observer_reliability(disposable_postgres_database: str) -> None:
    """Prove supported mapping, autocommit isolation, ordered durable append, and gaps."""

    output_root = Path(__file__).resolve().parents[3]
    log_path = (
        output_root
        / "test-results/single-run-certification/task-2.9.12n/observer-reliability.jsonl"
    )
    if log_path.exists():
        log_path.unlink()
    observer = PostgresTransactionObserver(
        disposable_postgres_database,
        log_path,
        application_name="task-2.9.12n-reliability-observer",
    )
    writer = create_engine(
        disposable_postgres_database,
        connect_args={"application_name": "task-2.9.12n-reliability-writer"},
    )
    verifier = create_engine(disposable_postgres_database, isolation_level="AUTOCOMMIT")
    try:
        with writer.connect() as connection:
            transaction = connection.begin()
            writer_pid = int(connection.scalar(text("select pg_backend_pid()")))
            first = observer.sample()
            time_module.sleep(0.05)
            second = observer.sample()
            transaction.rollback()
        final = observer.sample()
        first_row = next(row for row in first["transactions"] if row["pid"] == writer_pid)
        second_row = next(row for row in second["transactions"] if row["pid"] == writer_pid)
        assert second_row["age_seconds"] > first_row["age_seconds"] >= 0
        assert not any(row["pid"] == writer_pid for row in final["transactions"])
        persisted = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert [row["sequence"] for row in persisted] == [1, 2, 3]
        assert all(row["gap_from_previous"] == 0 for row in persisted)
        with verifier.connect() as connection:
            observer_backends = list(
                connection.execute(
                    text(
                        "select pid,xact_start,state from pg_stat_activity "
                        "where datname=current_database() and application_name=:application"
                    ),
                    {"application": observer.application_name},
                ).mappings()
            )
        assert all(row["xact_start"] is None for row in observer_backends)
        result = {
            "status": "PASS",
            "samples": len(persisted),
            "sequences": [row["sequence"] for row in persisted],
            "sampling_gaps": sum(int(row["gap_from_previous"]) for row in persisted),
            "initial_sampling": True,
            "repeated_sampling": True,
            "ages_increase": True,
            "supported_row_mapping": "sqlalchemy.mappings",
            "isolation_level": "AUTOCOMMIT",
            "append_only_process_owner": os.getpid(),
            "flush": True,
            "fsync": True,
            "observer_open_transactions_after_sample": 0,
            "native_junit_is_diagnostic": False,
            "log": str(log_path),
        }
        result_path = (
            output_root / "docs/remediation/readiness-phase2/TASK_2_9_12N_OBSERVER_RELIABILITY.json"
        )
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    finally:
        observer.close()
        writer.dispose()
        verifier.dispose()


def test_pre_pipeline_preparation_closes_transactions_between_fixture_phases(
    disposable_postgres_database: str,
    record_property,
) -> None:
    """Certify the complete 100-member pre-pipeline preparation contract."""

    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")

    tickers = EXACT_TEN + tuple(f"F{index:04d}" for index in range(90))
    seeder_application = "task-2.9.12l-pre-pipeline-seeder"
    observer_application = "task-2.9.12l-autocommit-observer"
    diagnostic_path = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "remediation"
        / "readiness-phase2"
        / "TASK_2_9_12L_TRANSACTION_DIAGNOSTIC.json"
    )
    observer_engine = create_engine(
        disposable_postgres_database,
        isolation_level="AUTOCOMMIT",
        connect_args={"application_name": observer_application},
    )
    engine = None
    monitor_stopped = threading.Event()
    observation_lock = threading.Lock()
    phase_lock = threading.Lock()
    active_phase = {"phase": "SEED_PREREQUISITES", "owner": "seed-prerequisites"}
    monitor_observations: list[dict[str, object]] = []
    monitor_errors: list[str] = []
    boundary_observations: list[dict[str, object]] = []
    retained_observations: list[dict[str, object]] = []
    diagnostic_started_at = datetime.now(UTC)
    failure_details: dict[str, str] | None = None

    def set_active_phase(phase: str, owner: str) -> None:
        with phase_lock:
            active_phase.update({"phase": phase, "owner": owner})

    def monitor_transactions() -> None:
        try:
            with observer_engine.connect() as observer:
                while not monitor_stopped.wait(0.05):
                    rows = [
                        dict(row)
                        for row in observer.execute(
                            text(
                                "select pid, application_name, state, wait_event_type, "
                                "wait_event, extract(epoch from "
                                "(clock_timestamp()-xact_start)) as age_seconds "
                                "from pg_stat_activity where datname=current_database() "
                                "and pid <> pg_backend_pid() and xact_start is not null "
                                "and application_name <> :observer_application"
                            ),
                            {"observer_application": observer_application},
                        ).mappings()
                    ]
                    for row in rows:
                        row["age_seconds"] = float(row["age_seconds"] or 0)
                    with phase_lock:
                        phase = dict(active_phase)
                    observation = {
                        "observed_at": datetime.now(UTC).isoformat(),
                        **phase,
                        "open_transaction_count": len(rows),
                        "idle_in_transaction_count": sum(
                            row["state"] == "idle in transaction" for row in rows
                        ),
                        "transactions": rows,
                    }
                    with observation_lock:
                        monitor_observations.append(observation)
        except Exception as exc:  # retained as a focused assertion, never suppressed
            with observation_lock:
                monitor_errors.append(f"{type(exc).__name__}: {exc}")

    def read_open_fixture_transactions() -> list[dict[str, object]]:
        with observer_engine.connect() as observer:
            return [
                dict(row)
                for row in observer.execute(
                    text(
                        "select pid, application_name, state, xact_start, "
                        "extract(epoch from (clock_timestamp()-xact_start)) as age_seconds "
                        "from pg_stat_activity where datname=current_database() "
                        "and pid <> pg_backend_pid() and xact_start is not null "
                        "and application_name <> :observer_application"
                    ),
                    {"observer_application": observer_application},
                ).mappings()
            ]

    def assert_no_open_fixture_transaction() -> None:
        open_transactions = read_open_fixture_transactions()
        assert open_transactions == []

    def summarize_transactions() -> dict[str, object]:
        with observation_lock:
            monitor_snapshot = [dict(row) for row in monitor_observations]
            error_snapshot = list(monitor_errors)
        samples: list[dict[str, object]] = []
        for observation in monitor_snapshot:
            for transaction in observation["transactions"]:
                samples.append(
                    {
                        "source": "continuous_autocommit_monitor",
                        "observed_at": observation["observed_at"],
                        "phase": observation["phase"],
                        "owner": observation["owner"],
                        "open_transaction_count": observation["open_transaction_count"],
                        "idle_in_transaction_count": observation["idle_in_transaction_count"],
                        **transaction,
                    }
                )
        samples.extend(
            {
                "source": "committed_fixture_boundary",
                "phase": row["phase"],
                "owner": (
                    "setup-baseline-writer"
                    if row["phase"] == "SETUP_IDENTITY"
                    else "price-history-writer"
                ),
                "application_name": seeder_application,
                "backend_pid": row["backend_pid"],
                "age_seconds": float(row["transaction_age_seconds"]),
                "open_transaction_count": 1,
                "idle_in_transaction_count": 0,
            }
            for row in boundary_observations
        )
        samples.extend(
            {
                "source": "committed_fixture_boundary",
                "phase": "RETAINED_PROVIDER_HISTORY",
                "owner": f"eodhd:estimates:{row['ticker']}",
                "application_name": seeder_application,
                "backend_pid": row["backend_pid"],
                "age_seconds": float(row["transaction_age_seconds"]),
                "open_transaction_count": 1,
                "idle_in_transaction_count": 0,
            }
            for row in retained_observations
        )
        maximum = max(samples, key=lambda row: float(row["age_seconds"]), default=None)
        return {
            "observer_isolation_level": "AUTOCOMMIT",
            "observer_application_name": observer_application,
            "monitor_cycle_count": len(monitor_snapshot),
            "monitor_error_count": len(error_snapshot),
            "monitor_errors": error_snapshot,
            "cycles_with_open_transactions": sum(
                int(row["open_transaction_count"]) > 0 for row in monitor_snapshot
            ),
            "maximum_open_transaction_count": max(
                (int(row["open_transaction_count"]) for row in monitor_snapshot),
                default=0,
            ),
            "maximum_idle_in_transaction_count": max(
                (int(row["idle_in_transaction_count"]) for row in monitor_snapshot),
                default=0,
            ),
            "maximum_transaction": maximum,
            "transaction_samples": samples,
        }

    monitor_thread = threading.Thread(target=monitor_transactions, daemon=True)
    monitor_thread.start()
    try:
        set_active_phase("SEED_PREREQUISITES", "seed-prerequisites")
        seed = seed_prerequisites(disposable_postgres_database)
        assert_no_open_fixture_transaction()

        engine = create_engine(
            disposable_postgres_database,
            connect_args={"application_name": seeder_application},
        )

        def assert_baseline_boundary(
            phase: str,
            transaction: dict[str, int | float] | None,
        ) -> None:
            assert transaction is not None
            assert float(transaction["transaction_age_seconds"]) < 120
            with observer_engine.connect() as observer:
                backend = (
                    observer.execute(
                        text(
                            "select pid, state, xact_start, wait_event_type, wait_event "
                            "from pg_stat_activity where datname=current_database() and pid=:pid"
                        ),
                        {"pid": transaction["backend_pid"]},
                    )
                    .mappings()
                    .one()
                )
                assert backend["xact_start"] is None
                assert backend["state"] != "idle in transaction"
                if phase == "SETUP_IDENTITY":
                    committed = {
                        "setup_snapshots": int(
                            observer.scalar(
                                text(
                                    "select count(*) from setup_signal_snapshots "
                                    "where run_id=:run_id and ticker=any(:tickers)"
                                ),
                                {"run_id": seed.decoy_run_id, "tickers": list(tickers)},
                            )
                            or 0
                        ),
                        "current_selections": int(
                            observer.scalar(
                                text(
                                    "select count(*) "
                                    "from setup_signal_snapshot_current_selections "
                                    "where ticker=any(:tickers)"
                                ),
                                {"tickers": list(tickers)},
                            )
                            or 0
                        ),
                        "ceri_companies": int(
                            observer.scalar(
                                text(
                                    "select count(*) from ceri_companies where ticker=any(:tickers)"
                                ),
                                {"tickers": list(tickers)},
                            )
                            or 0
                        ),
                    }
                    assert committed == {
                        "setup_snapshots": 100,
                        "current_selections": 100,
                        "ceri_companies": 100,
                    }
                else:
                    committed = {
                        "price_bars": int(
                            observer.scalar(
                                text("select count(*) from price_bars where ticker=any(:tickers)"),
                                {"tickers": list(tickers)},
                            )
                            or 0
                        )
                    }
                    assert committed == {"price_bars": 64_000}
            boundary_observations.append({"phase": phase, **transaction, "committed": committed})

        _prepare_real_writer_pre_pipeline_baseline(
            engine,
            decoy_run_id=seed.decoy_run_id,
            tickers=tickers,
            transaction_phase_started_callback=set_active_phase,
            transaction_boundary_callback=assert_baseline_boundary,
        )

        set_active_phase("CURRENT_RUN_ROWS", "raw-company-row-writer")
        expected_raw_identities = [
            {
                "row_number": index,
                "ticker": ticker,
                "raw_json": {"ticker": ticker, "fixture": "task-2.9.12l"},
            }
            for index, ticker in enumerate(tickers, start=1)
        ]
        with Session(engine) as db:
            run = UploadRun(
                filename="task-2.9.12l-100.csv",
                status="COMPLETED",
                row_count=len(tickers),
            )
            db.add(run)
            db.flush()
            run_id = int(run.id)
            db.add_all(
                RawCompanyRow(
                    run_id=run_id,
                    row_number=row["row_number"],
                    ticker=row["ticker"],
                    raw_json=row["raw_json"],
                )
                for row in expected_raw_identities
            )
            db.commit()
        assert_no_open_fixture_transaction()

        def assert_retained_boundary(
            completed: int,
            ticker: str,
            transaction: dict[str, int | float],
        ) -> None:
            assert float(transaction["transaction_age_seconds"]) < 120
            with observer_engine.connect() as observer:
                backend = (
                    observer.execute(
                        text(
                            "select state, xact_start from pg_stat_activity "
                            "where datname=current_database() and pid=:pid"
                        ),
                        {"pid": transaction["backend_pid"]},
                    )
                    .mappings()
                    .one()
                )
                assert backend["xact_start"] is None
                assert backend["state"] != "idle in transaction"
                committed_members = int(
                    observer.scalar(
                        text(
                            "select count(*) from ceri_ingestion_run_source_records "
                            "where ingestion_run_id=:ingestion_run_id and ticker=:ticker "
                            "and normalization_state='NORMALIZED'"
                        ),
                        {
                            "ingestion_run_id": transaction["ingestion_run_id"],
                            "ticker": ticker,
                        },
                    )
                    or 0
                )
            assert committed_members == 4
            retained_observations.append(
                {
                    "completed": completed,
                    "ticker": ticker,
                    "committed_members": committed_members,
                    **transaction,
                }
            )

        _seed_retained_provider_history(
            engine,
            tickers=tickers,
            transaction_phase_started_callback=set_active_phase,
            transaction_boundary_callback=assert_retained_boundary,
        )
        assert_no_open_fixture_transaction()

        set_active_phase("FOCUSED_VERIFICATION", "autocommit-verifier")
        monitor_stopped.set()
        monitor_thread.join(timeout=10)
        assert not monitor_thread.is_alive()
        assert monitor_errors == []
        assert monitor_observations

        baseline_session = previous_us_trading_day(certification_as_of_session())
        setup_config_hash = hashlib.sha256(b"certification-setup-baseline").hexdigest()
        expected_setup_identity_rows = [
            {
                "run_id": seed.decoy_run_id,
                "ticker": ticker,
                "timeframe": "1d",
                "data_as_of_date": baseline_session,
                "engine_version": "certification-baseline-v1",
                "config_hash": setup_config_hash,
                "source_data_hash": hashlib.sha256(
                    f"{ticker}:{baseline_session.isoformat()}:baseline".encode()
                ).hexdigest(),
            }
            for ticker in tickers
        ]

        def setup_natural_key(row):
            return (
                int(row["run_id"]),
                str(row["ticker"]),
                str(row["timeframe"]),
                row["data_as_of_date"].isoformat(),
            )

        def setup_stable_identity(row):
            return (
                *setup_natural_key(row),
                str(row["engine_version"]),
                str(row["config_hash"]),
                str(row["source_data_hash"]),
            )

        expected_setup_identity_rows = sorted(
            expected_setup_identity_rows,
            key=setup_stable_identity,
        )
        expected_setup_selection_rows = [
            {
                "ticker": row["ticker"],
                "timeframe": row["timeframe"],
                "data_as_of_date": row["data_as_of_date"],
                "selected_run_id": row["run_id"],
                "revision": 1,
                "selection_reason": "CERTIFICATION_PRIOR_SESSION_BASELINE",
                "selection_decision_json": {"fixture": FIXTURE_VERSION},
                "engine_version": row["engine_version"],
                "config_hash": row["config_hash"],
                "source_data_hash": row["source_data_hash"],
            }
            for row in expected_setup_identity_rows
        ]

        def setup_selection_key(row):
            return (
                str(row["ticker"]),
                str(row["timeframe"]),
                row["data_as_of_date"].isoformat(),
            )

        expected_setup_selection_rows = sorted(
            expected_setup_selection_rows,
            key=setup_selection_key,
        )
        expected_winner_identities = [
            {
                "ticker": ticker,
                "prediction_as_of_date": (
                    datetime(2026, 5, 1, 20, 0, tzinfo=UTC) + timedelta(days=index)
                ).date(),
                "feature_vector_hash": hashlib.sha256(ticker.encode()).hexdigest(),
            }
            for index, ticker in enumerate(f"HIST{index:02d}" for index in range(20))
        ]
        expected_source_identities = sorted(
            [
                {
                    "provider": "eodhd",
                    "dataset": "estimates",
                    "provider_record_id": f"{ticker}-eps-{suffix}",
                }
                for ticker in tickers
                for suffix in ("90d", "30d", "7d", "current")
            ],
            key=lambda row: row["provider_record_id"],
        )
        expected_membership_identities = sorted(
            [
                {
                    **identity,
                    "ticker": ticker,
                    "ingestion_outcome": "INSERTED",
                    "normalization_state": "NORMALIZED",
                }
                for ticker in tickers
                for identity in (
                    {
                        "provider": "eodhd",
                        "dataset": "estimates",
                        "provider_record_id": f"{ticker}-eps-{suffix}",
                    }
                    for suffix in ("90d", "30d", "7d", "current")
                )
            ],
            key=lambda row: row["provider_record_id"],
        )

        with engine.connect() as verify:
            persisted_raw_identities = [
                dict(row)
                for row in verify.execute(
                    text(
                        "select row_number, ticker, raw_json from raw_company_rows "
                        "where run_id=:run_id order by row_number"
                    ),
                    {"run_id": run_id},
                ).mappings()
            ]
            persisted_setup_identity_rows = [
                dict(row)
                for row in verify.execute(
                    text(
                        "select run_id, ticker, timeframe, data_as_of_date, "
                        "engine_version, config_hash, source_data_hash "
                        "from setup_signal_snapshots where run_id=:run_id "
                        "and ticker=any(:tickers) "
                        "order by run_id, ticker, timeframe, data_as_of_date, "
                        "engine_version, config_hash, source_data_hash"
                    ),
                    {"run_id": seed.decoy_run_id, "tickers": list(tickers)},
                ).mappings()
            ]
            persisted_setup_selection_rows = [
                dict(row)
                for row in verify.execute(
                    text(
                        "select c.ticker, c.timeframe, c.data_as_of_date, "
                        "c.selected_run_id, c.revision, c.selection_reason, "
                        "c.selection_decision_json, s.engine_version, s.config_hash, "
                        "s.source_data_hash "
                        "from setup_signal_snapshot_current_selections c "
                        "join setup_signal_snapshots s on s.id=c.selected_snapshot_id "
                        "where c.ticker=any(:tickers) "
                        "order by c.ticker, c.timeframe, c.data_as_of_date"
                    ),
                    {"tickers": list(tickers)},
                ).mappings()
            ]
            persisted_winner_identities = [
                dict(row)
                for row in verify.execute(
                    text(
                        "select ticker, prediction_as_of_date, feature_vector_hash "
                        "from winner_prediction_snapshots where run_id=:run_id order by ticker"
                    ),
                    {"run_id": seed.decoy_run_id},
                ).mappings()
            ]
            persisted_source_identities = [
                dict(row)
                for row in verify.execute(
                    text(
                        "select provider, dataset, provider_record_id "
                        "from ceri_source_records "
                        "where restricted_normalized_json->>'ticker'=any(:tickers) "
                        "order by provider_record_id"
                    ),
                    {"tickers": list(tickers)},
                ).mappings()
            ]
            persisted_membership_identities = [
                dict(row)
                for row in verify.execute(
                    text(
                        "select m.provider, m.dataset, s.provider_record_id, m.ticker, "
                        "m.ingestion_outcome, m.normalization_state "
                        "from ceri_ingestion_run_source_records m "
                        "join ceri_source_records s on s.id=m.source_record_id "
                        "where m.ticker=any(:tickers) order by s.provider_record_id"
                    ),
                    {"tickers": list(tickers)},
                ).mappings()
            ]
            counts = {
                "raw_total": int(verify.scalar(text("select count(*) from raw_company_rows")) or 0),
                "raw_current": len(persisted_raw_identities),
                "setup_exact": len(persisted_setup_identity_rows),
                "setup_current_selections": int(
                    verify.scalar(
                        text(
                            "select count(*) from setup_signal_snapshot_current_selections "
                            "where ticker=any(:tickers)"
                        ),
                        {"tickers": list(tickers)},
                    )
                    or 0
                ),
                "winner_baseline": len(persisted_winner_identities),
                "price_bars_exact": int(
                    verify.scalar(
                        text("select count(*) from price_bars where ticker=any(:tickers)"),
                        {"tickers": list(tickers)},
                    )
                    or 0
                ),
                "ib_contracts_exact": int(
                    verify.scalar(
                        text("select count(*) from ib_contracts where ticker=any(:tickers)"),
                        {"tickers": list(tickers)},
                    )
                    or 0
                ),
                "ceri_companies_exact": int(
                    verify.scalar(
                        text("select count(*) from ceri_companies where ticker=any(:tickers)"),
                        {"tickers": list(tickers)},
                    )
                    or 0
                ),
                "ceri_sources_exact": len(persisted_source_identities),
                "ceri_memberships_exact": len(persisted_membership_identities),
                "ceri_estimates_exact": int(
                    verify.scalar(
                        text(
                            "select count(*) from ceri_estimate_snapshots e "
                            "join ceri_source_records s on s.id=e.source_record_id "
                            "where s.restricted_normalized_json->>'ticker'=any(:tickers)"
                        ),
                        {"tickers": list(tickers)},
                    )
                    or 0
                ),
            }
            integrity_failures = {
                "duplicate_raw": int(
                    verify.scalar(
                        text(
                            "select count(*) from (select ticker from raw_company_rows "
                            "where run_id=:run_id group by ticker having count(*)<>1) rows"
                        ),
                        {"run_id": run_id},
                    )
                    or 0
                ),
                "duplicate_setup": int(
                    verify.scalar(
                        text(
                            "select count(*) from (select ticker, timeframe, data_as_of_date "
                            "from setup_signal_snapshots where run_id=:run_id "
                            "and ticker=any(:tickers) group by ticker, timeframe, data_as_of_date "
                            "having count(*)<>1) rows"
                        ),
                        {"run_id": seed.decoy_run_id, "tickers": list(tickers)},
                    )
                    or 0
                ),
                "duplicate_memberships": int(
                    verify.scalar(
                        text(
                            "select count(*) from (select ingestion_run_id, source_record_id "
                            "from ceri_ingestion_run_source_records where ticker=any(:tickers) "
                            "group by ingestion_run_id, source_record_id having count(*)<>1) rows"
                        ),
                        {"tickers": list(tickers)},
                    )
                    or 0
                ),
                "orphan_setup_selection": int(
                    verify.scalar(
                        text(
                            "select count(*) from setup_signal_snapshot_current_selections c "
                            "left join setup_signal_snapshots s on s.id=c.selected_snapshot_id "
                            "where c.ticker=any(:tickers) and s.id is null"
                        ),
                        {"tickers": list(tickers)},
                    )
                    or 0
                ),
                "orphan_memberships": int(
                    verify.scalar(
                        text(
                            "select count(*) from ceri_ingestion_run_source_records m "
                            "left join ceri_ingestion_runs i on i.id=m.ingestion_run_id "
                            "left join ceri_source_records s on s.id=m.source_record_id "
                            "where m.ticker=any(:tickers) and (i.id is null or s.id is null)"
                        ),
                        {"tickers": list(tickers)},
                    )
                    or 0
                ),
                "cross_ticker_memberships": int(
                    verify.scalar(
                        text(
                            "select count(*) from ceri_ingestion_run_source_records m "
                            "join ceri_source_records s on s.id=m.source_record_id "
                            "where m.ticker=any(:tickers) "
                            "and m.ticker<>upper(s.company_hint_json->>'ticker')"
                        ),
                        {"tickers": list(tickers)},
                    )
                    or 0
                ),
                "cross_run_setup": int(
                    verify.scalar(
                        text(
                            "select count(*) from setup_signal_snapshots "
                            "where ticker=any(:tickers) and run_id<>:run_id"
                        ),
                        {"tickers": list(tickers), "run_id": seed.decoy_run_id},
                    )
                    or 0
                ),
                "pipeline_rows": int(
                    verify.scalar(text("select count(*) from pipeline_runs")) or 0
                ),
            }

        expected_setup_by_key = {
            setup_natural_key(row): row for row in expected_setup_identity_rows
        }
        persisted_setup_by_key = {
            setup_natural_key(row): row for row in persisted_setup_identity_rows
        }
        setup_missing_keys = sorted(set(expected_setup_by_key) - set(persisted_setup_by_key))
        setup_unexpected_keys = sorted(set(persisted_setup_by_key) - set(expected_setup_by_key))
        setup_field_differences = [
            {
                "identity": key,
                "expected": expected_setup_by_key[key],
                "persisted": persisted_setup_by_key[key],
            }
            for key in sorted(set(expected_setup_by_key) & set(persisted_setup_by_key))
            if expected_setup_by_key[key] != persisted_setup_by_key[key]
        ]
        expected_selection_by_key = {
            setup_selection_key(row): row for row in expected_setup_selection_rows
        }
        persisted_selection_by_key = {
            setup_selection_key(row): row for row in persisted_setup_selection_rows
        }
        selection_missing_keys = sorted(
            set(expected_selection_by_key) - set(persisted_selection_by_key)
        )
        selection_unexpected_keys = sorted(
            set(persisted_selection_by_key) - set(expected_selection_by_key)
        )
        selection_field_differences = [
            {
                "identity": key,
                "expected": expected_selection_by_key[key],
                "persisted": persisted_selection_by_key[key],
            }
            for key in sorted(set(expected_selection_by_key) & set(persisted_selection_by_key))
            if expected_selection_by_key[key] != persisted_selection_by_key[key]
        ]
        setup_semantic_diagnostic = {
            "expected_rows": len(expected_setup_identity_rows),
            "persisted_rows": len(persisted_setup_identity_rows),
            "expected_unique_stable_identities": len(
                {setup_stable_identity(row) for row in expected_setup_identity_rows}
            ),
            "persisted_unique_stable_identities": len(
                {setup_stable_identity(row) for row in persisted_setup_identity_rows}
            ),
            "missing_identities": len(setup_missing_keys),
            "unexpected_identities": len(setup_unexpected_keys),
            "field_differences": len(setup_field_differences),
            "selection_missing_identities": len(selection_missing_keys),
            "selection_unexpected_identities": len(selection_unexpected_keys),
            "selection_field_differences": len(selection_field_differences),
        }

        assert counts == {
            "raw_total": 101,
            "raw_current": 100,
            "setup_exact": 100,
            "setup_current_selections": 100,
            "winner_baseline": 20,
            "price_bars_exact": 64_000,
            "ib_contracts_exact": 100,
            "ceri_companies_exact": 100,
            "ceri_sources_exact": 400,
            "ceri_memberships_exact": 400,
            "ceri_estimates_exact": 400,
        }
        assert integrity_failures == {
            "duplicate_raw": 0,
            "duplicate_setup": 0,
            "duplicate_memberships": 0,
            "orphan_setup_selection": 0,
            "orphan_memberships": 0,
            "cross_ticker_memberships": 0,
            "cross_run_setup": 0,
            "pipeline_rows": 0,
        }
        assert setup_semantic_diagnostic == {
            "expected_rows": 100,
            "persisted_rows": 100,
            "expected_unique_stable_identities": 100,
            "persisted_unique_stable_identities": 100,
            "missing_identities": 0,
            "unexpected_identities": 0,
            "field_differences": 0,
            "selection_missing_identities": 0,
            "selection_unexpected_identities": 0,
            "selection_field_differences": 0,
        }, {
            "setup_missing": setup_missing_keys,
            "setup_unexpected": setup_unexpected_keys,
            "setup_field_differences": setup_field_differences,
            "selection_missing": selection_missing_keys,
            "selection_unexpected": selection_unexpected_keys,
            "selection_field_differences": selection_field_differences,
        }
        assert len(boundary_observations) == 2
        assert [row["phase"] for row in boundary_observations] == [
            "SETUP_IDENTITY",
            "PRICE_HISTORY",
        ]
        assert len(retained_observations) == 100
        assert [row["completed"] for row in retained_observations] == list(range(1, 101))
        assert membership_fingerprint(
            [ScopeMember("TICKER", row["ticker"]) for row in persisted_raw_identities]
        ) == membership_fingerprint([ScopeMember("TICKER", ticker) for ticker in tickers])
        assert CanonicalEvidenceSerializer.fingerprint(
            persisted_raw_identities
        ) == CanonicalEvidenceSerializer.fingerprint(expected_raw_identities)
        expected_setup_identities = [
            {
                "ticker": row["ticker"],
                "data_as_of_date": row["data_as_of_date"],
                "source_data_hash": row["source_data_hash"],
            }
            for row in expected_setup_identity_rows
        ]
        persisted_setup_identities = [
            {
                "ticker": row["ticker"],
                "data_as_of_date": row["data_as_of_date"],
                "source_data_hash": row["source_data_hash"],
            }
            for row in persisted_setup_identity_rows
        ]
        assert CanonicalEvidenceSerializer.fingerprint(
            persisted_setup_identities
        ) == CanonicalEvidenceSerializer.fingerprint(expected_setup_identities)
        assert CanonicalEvidenceSerializer.fingerprint(
            persisted_winner_identities
        ) == CanonicalEvidenceSerializer.fingerprint(expected_winner_identities)
        assert CanonicalEvidenceSerializer.fingerprint(
            persisted_source_identities
        ) == CanonicalEvidenceSerializer.fingerprint(expected_source_identities)
        assert CanonicalEvidenceSerializer.fingerprint(
            persisted_membership_identities
        ) == CanonicalEvidenceSerializer.fingerprint(expected_membership_identities)

        transaction_summary = summarize_transactions()
        maximum_transaction = transaction_summary["maximum_transaction"]
        assert maximum_transaction is not None
        maximum_transaction_age_seconds = float(maximum_transaction["age_seconds"])
        assert maximum_transaction_age_seconds < 120
        assert_no_open_fixture_transaction()
        assert engine.pool.checkedout() == 0
        assert observer_engine.pool.checkedout() == 0

        raw_fingerprint = CanonicalEvidenceSerializer.fingerprint(persisted_raw_identities)
        source_fingerprint = CanonicalEvidenceSerializer.fingerprint(persisted_source_identities)
    except BaseException as exc:
        failure_details = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        set_active_phase("FINALIZATION", "focused-certification")
        monitor_stopped.set()
        monitor_thread.join(timeout=10)
        final_open_transactions: list[dict[str, object]] = []
        final_observer_error = None
        try:
            final_open_transactions = read_open_fixture_transactions()
        except Exception as exc:  # preserve diagnostics without hiding the test failure
            final_observer_error = f"{type(exc).__name__}: {exc}"
        transaction_summary = summarize_transactions()
        maximum_transaction = transaction_summary["maximum_transaction"]
        maximum_transaction_age_seconds = (
            float(maximum_transaction["age_seconds"]) if maximum_transaction is not None else 0.0
        )
        diagnostic = {
            "task": "2.9.12l",
            "test": ("test_pre_pipeline_preparation_closes_transactions_between_fixture_phases"),
            "status": "PASSED" if failure_details is None else "FAILED",
            "started_at": diagnostic_started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "failure": failure_details,
            "transaction_age_limit_seconds": 120,
            "maximum_transaction_age_seconds": maximum_transaction_age_seconds,
            "maximum_transaction": maximum_transaction,
            "maximum_open_transaction_count": transaction_summary["maximum_open_transaction_count"],
            "maximum_idle_in_transaction_count": transaction_summary[
                "maximum_idle_in_transaction_count"
            ],
            "final_open_transaction_count": len(final_open_transactions),
            "final_idle_in_transaction_count": sum(
                row.get("state") == "idle in transaction" for row in final_open_transactions
            ),
            "final_open_transactions": final_open_transactions,
            "final_observer_error": final_observer_error,
            "fixture_boundaries": boundary_observations,
            "retained_boundary_count": len(retained_observations),
            "retained_boundaries": retained_observations,
            "setup_semantic_diagnostic": locals().get("setup_semantic_diagnostic"),
            "engine_checked_out_before_dispose": (
                engine.pool.checkedout() if engine is not None else None
            ),
            "observer_checked_out_before_dispose": observer_engine.pool.checkedout(),
            "monitor": transaction_summary,
        }
        diagnostic_path.write_text(
            json.dumps(diagnostic, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        record_property(
            "maximum_transaction_age_seconds",
            f"{maximum_transaction_age_seconds:.6f}",
        )
        if maximum_transaction is not None:
            record_property("maximum_transaction_phase", str(maximum_transaction["phase"]))
            record_property("maximum_transaction_owner", str(maximum_transaction["owner"]))
            record_property(
                "maximum_transaction_application_name",
                str(maximum_transaction.get("application_name", "")),
            )
        record_property(
            "maximum_open_transaction_count",
            str(transaction_summary["maximum_open_transaction_count"]),
        )
        record_property(
            "maximum_idle_in_transaction_count",
            str(transaction_summary["maximum_idle_in_transaction_count"]),
        )
        record_property("final_open_transaction_count", str(len(final_open_transactions)))
        record_property(
            "final_idle_in_transaction_count",
            str(sum(row.get("state") == "idle in transaction" for row in final_open_transactions)),
        )
        if boundary_observations:
            record_property("setup_session_id", str(boundary_observations[0]["session_id"]))
            record_property("setup_backend_pid", str(boundary_observations[0]["backend_pid"]))
        if len(boundary_observations) > 1:
            record_property("price_session_id", str(boundary_observations[1]["session_id"]))
            record_property("price_backend_pid", str(boundary_observations[1]["backend_pid"]))
        if "raw_fingerprint" in locals():
            record_property("raw_fingerprint", raw_fingerprint)
        if "source_fingerprint" in locals():
            record_property("source_fingerprint", source_fingerprint)
        record_property("fixture_boundary_count", str(len(boundary_observations)))
        record_property("retained_boundary_count", str(len(retained_observations)))
        if engine is not None:
            engine.dispose()
        observer_engine.dispose()


def test_retained_provider_history_closes_transactions_between_100_members(
    disposable_postgres_database: str,
) -> None:
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")

    tickers = EXACT_TEN + tuple(f"F{index:04d}" for index in range(90))
    seeder_application = "task-2.9.12h-retained-history-seeder"
    observer_application = "task-2.9.12h-boundary-observer"
    engine = create_engine(
        disposable_postgres_database,
        connect_args={"application_name": seeder_application},
    )
    observer_engine = create_engine(
        disposable_postgres_database,
        isolation_level="AUTOCOMMIT",
        connect_args={"application_name": observer_application},
    )
    boundary_observations: list[dict[str, object]] = []
    expected_scope_membership_fingerprint = membership_fingerprint(
        [ScopeMember("TICKER", ticker) for ticker in tickers]
    )
    expected_raw_identities = [
        {
            "row_number": index,
            "ticker": ticker,
            "raw_json": {"ticker": ticker, "fixture": "task-2.9.12h"},
        }
        for index, ticker in enumerate(tickers, start=1)
    ]
    expected_raw_fingerprint = CanonicalEvidenceSerializer.fingerprint(expected_raw_identities)
    expected_source_identities = sorted(
        [
            {
                "provider": "eodhd",
                "dataset": "estimates",
                "provider_record_id": f"{ticker}-eps-{suffix}",
            }
            for ticker in tickers
            for suffix in ("90d", "30d", "7d", "current")
        ],
        key=lambda row: row["provider_record_id"],
    )
    expected_source_fingerprint = CanonicalEvidenceSerializer.fingerprint(
        expected_source_identities
    )
    expected_membership_identities = sorted(
        [
            {
                **identity,
                "ticker": ticker,
                "ingestion_outcome": "INSERTED",
                "normalization_state": "NORMALIZED",
            }
            for ticker in tickers
            for identity in (
                {
                    "provider": "eodhd",
                    "dataset": "estimates",
                    "provider_record_id": f"{ticker}-eps-{suffix}",
                }
                for suffix in ("90d", "30d", "7d", "current")
            )
        ],
        key=lambda row: row["provider_record_id"],
    )
    expected_membership_evidence_fingerprint = CanonicalEvidenceSerializer.fingerprint(
        expected_membership_identities
    )

    try:
        with Session(engine) as db:
            run = UploadRun(
                filename="task-2.9.12h-100.csv",
                status="COMPLETED",
                row_count=len(tickers),
            )
            db.add(run)
            db.flush()
            run_id = int(run.id)
            db.add_all(
                RawCompanyRow(
                    run_id=run_id,
                    row_number=index,
                    ticker=ticker,
                    raw_json={"ticker": ticker, "fixture": "task-2.9.12h"},
                )
                for index, ticker in enumerate(tickers, start=1)
            )
            _seed_retained_ceri_companies(db, tickers=tickers)
            db.commit()

        def assert_closed_boundary(
            completed: int,
            ticker: str,
            transaction: dict[str, int | float],
        ) -> None:
            # This is the exact seam where the fixture may perform CPU work or
            # wait for browser/server readiness. Observe from an independent,
            # autocommit connection so the diagnostic retains no transaction.
            with observer_engine.connect() as observer:
                seeder_backends = list(
                    observer.execute(
                        text(
                            "select pid, state, xact_start, wait_event_type, wait_event "
                            "from pg_stat_activity where datname=current_database() "
                            "and application_name=:application_name"
                        ),
                        {"application_name": seeder_application},
                    ).mappings()
                )
                assert seeder_backends
                assert all(row["xact_start"] is None for row in seeder_backends)
                assert all(row["state"] != "idle in transaction" for row in seeder_backends)
                evidence = dict(
                    observer.execute(
                        text(
                            "select count(*) as memberships, "
                            "count(*) filter (where m.ticker=:ticker "
                            "and m.provider='eodhd' and m.dataset='estimates') as exact_scope, "
                            "count(*) filter (where m.normalization_state='NORMALIZED') "
                            "as normalized_memberships, "
                            "count(distinct s.id) as source_records, "
                            "count(distinct e.id) as normalized_evidence "
                            "from ceri_ingestion_run_source_records m "
                            "join ceri_source_records s on s.id=m.source_record_id "
                            "left join ceri_estimate_snapshots e "
                            "on e.source_record_id=s.id "
                            "where m.ingestion_run_id=:ingestion_run_id"
                        ),
                        {
                            "ticker": ticker,
                            "ingestion_run_id": transaction["ingestion_run_id"],
                        },
                    )
                    .mappings()
                    .one()
                )
                ingestion_complete = int(
                    observer.scalar(
                        text(
                            "select count(*) from ceri_ingestion_runs "
                            "where id=:ingestion_run_id and provider='eodhd' "
                            "and dataset='estimates' and status='COMPLETED' "
                            "and fetched_count=4 and failed_count=0 "
                            "and scope_json->>'ticker'=:ticker"
                        ),
                        {
                            "ticker": ticker,
                            "ingestion_run_id": transaction["ingestion_run_id"],
                        },
                    )
                    or 0
                )
                processing_complete = int(
                    observer.scalar(
                        text(
                            "select count(*) from ceri_processing_runs "
                            "where id=:processing_run_id "
                            "and deterministic_request_key=:request_key "
                            "and status='COMPLETED' and failed_count=0 "
                            "and read_count=4 and normalized_count=4"
                        ),
                        {
                            "processing_run_id": transaction["processing_run_id"],
                            "request_key": (
                                f"certification-retained:normalize:eodhd:estimates:{ticker}"
                            ),
                        },
                    )
                    or 0
                )
                duplicate_memberships = int(
                    observer.scalar(
                        text(
                            "select count(*) from ("
                            "select ingestion_run_id, source_record_id "
                            "from ceri_ingestion_run_source_records "
                            "where ingestion_run_id=:ingestion_run_id "
                            "group by ingestion_run_id, source_record_id having count(*) <> 1"
                            ") duplicates"
                        ),
                        {"ingestion_run_id": transaction["ingestion_run_id"]},
                    )
                    or 0
                )
                admitted_ingestion_runs = int(
                    observer.scalar(text("select count(*) from ceri_ingestion_runs")) or 0
                )
                next_ticker_memberships = (
                    int(
                        observer.scalar(
                            text(
                                "select count(*) from ceri_ingestion_run_source_records "
                                "where ticker=:ticker"
                            ),
                            {"ticker": tickers[completed]},
                        )
                        or 0
                    )
                    if completed < len(tickers)
                    else 0
                )
            assert transaction["transaction_age_seconds"] < 120
            assert evidence == {
                "memberships": 4,
                "exact_scope": 4,
                "normalized_memberships": 4,
                "source_records": 4,
                "normalized_evidence": 4,
            }
            assert ingestion_complete == 1
            assert processing_complete == 1
            assert duplicate_memberships == 0
            assert admitted_ingestion_runs == completed
            assert next_ticker_memberships == 0
            boundary_observations.append(
                {
                    "completed": completed,
                    "ticker": ticker,
                    "committed_members": evidence["memberships"],
                    **transaction,
                }
            )

        _seed_retained_provider_history(
            engine,
            tickers=tickers,
            transaction_boundary_callback=assert_closed_boundary,
        )

        with Session(engine) as db:
            persisted_raw_identities = [
                {"row_number": row.row_number, "ticker": row.ticker, "raw_json": row.raw_json}
                for row in db.scalars(
                    select(RawCompanyRow)
                    .where(RawCompanyRow.run_id == run_id)
                    .order_by(RawCompanyRow.row_number)
                )
            ]
            persisted_members = list(
                db.scalars(
                    select(CeriIngestionRunSourceRecord.ticker)
                    .distinct()
                    .order_by(CeriIngestionRunSourceRecord.ticker)
                )
            )
            persisted_source_identities = [
                dict(row)
                for row in db.execute(
                    text(
                        "select provider, dataset, provider_record_id "
                        "from ceri_source_records order by provider_record_id"
                    )
                ).mappings()
            ]
            persisted_membership_identities = [
                dict(row)
                for row in db.execute(
                    text(
                        "select m.provider, m.dataset, s.provider_record_id, m.ticker, "
                        "m.ingestion_outcome, m.normalization_state "
                        "from ceri_ingestion_run_source_records m "
                        "join ceri_source_records s on s.id=m.source_record_id "
                        "order by s.provider_record_id"
                    )
                ).mappings()
            ]
            counts = {
                name: int(db.scalar(text(f"select count(*) from {name}")) or 0)
                for name in (
                    "ceri_ingestion_runs",
                    "ceri_processing_runs",
                    "ceri_source_records",
                    "ceri_ingestion_run_source_records",
                    "ceri_estimate_snapshots",
                    "ceri_companies",
                )
            }
            incomplete_financial_evidence = int(
                db.scalar(
                    text(
                        "select count(*) from ceri_processing_runs "
                        "where status <> 'COMPLETED' or failed_count <> 0"
                    )
                )
                or 0
            )
            unrelated_authority = {
                "market_contexts": int(
                    db.scalar(text("select count(*) from market_calculation_contexts")) or 0
                ),
                "configuration_anchors": int(
                    db.scalar(text("select count(*) from execution_configuration_anchors")) or 0
                ),
                "pipeline_runs": int(db.scalar(text("select count(*) from pipeline_runs")) or 0),
            }
            integrity_failures = {
                "duplicate_raw_tickers": int(
                    db.scalar(
                        text(
                            "select count(*) from (select ticker from raw_company_rows "
                            "where run_id=:run_id group by ticker having count(*) <> 1) rows"
                        ),
                        {"run_id": run_id},
                    )
                    or 0
                ),
                "cross_ticker_memberships": int(
                    db.scalar(
                        text(
                            "select count(*) from ceri_ingestion_run_source_records m "
                            "join ceri_source_records s on s.id=m.source_record_id "
                            "where m.ticker <> upper(s.company_hint_json->>'ticker')"
                        )
                    )
                    or 0
                ),
                "orphan_memberships": int(
                    db.scalar(
                        text(
                            "select count(*) from ceri_ingestion_run_source_records m "
                            "left join ceri_ingestion_runs i on i.id=m.ingestion_run_id "
                            "left join ceri_source_records s on s.id=m.source_record_id "
                            "where i.id is null or s.id is null"
                        )
                    )
                    or 0
                ),
                "orphan_estimates": int(
                    db.scalar(
                        text(
                            "select count(*) from ceri_estimate_snapshots e "
                            "left join ceri_source_records s on s.id=e.source_record_id "
                            "where s.id is null"
                        )
                    )
                    or 0
                ),
            }

        assert len(boundary_observations) == 100
        assert [row["completed"] for row in boundary_observations] == list(range(1, 101))
        assert len(persisted_raw_identities) == 100
        assert set(persisted_members) == set(tickers)
        assert len(persisted_members) == 100
        assert (
            membership_fingerprint([ScopeMember("TICKER", ticker) for ticker in persisted_members])
            == expected_scope_membership_fingerprint
        )
        assert (
            CanonicalEvidenceSerializer.fingerprint(persisted_raw_identities)
            == expected_raw_fingerprint
        )
        assert (
            CanonicalEvidenceSerializer.fingerprint(persisted_source_identities)
            == expected_source_fingerprint
        )
        assert (
            CanonicalEvidenceSerializer.fingerprint(persisted_membership_identities)
            == expected_membership_evidence_fingerprint
        )
        assert counts == {
            "ceri_ingestion_runs": 100,
            "ceri_processing_runs": 100,
            "ceri_source_records": 400,
            "ceri_ingestion_run_source_records": 400,
            "ceri_estimate_snapshots": 400,
            "ceri_companies": 100,
        }
        assert incomplete_financial_evidence == 0
        assert integrity_failures == {
            "duplicate_raw_tickers": 0,
            "cross_ticker_memberships": 0,
            "orphan_memberships": 0,
            "orphan_estimates": 0,
        }
        assert unrelated_authority == {
            "market_contexts": 0,
            "configuration_anchors": 0,
            "pipeline_runs": 0,
        }
        # At 120 seconds an active transaction must prove fresh durable work.
        # Stalls, lock waits, cancellation, lost ownership, and the overall
        # workflow deadline remain independently enforced.
        from inspect import getsource

        assert "transaction_age_seconds=120" in getsource(_run_pipeline_through_gui)
        assert "transaction_progress_seconds=30" in getsource(_run_pipeline_through_gui)
        assert max(float(row["transaction_age_seconds"]) for row in boundary_observations) < 120
        assert engine.pool.checkedout() == 0
        assert observer_engine.pool.checkedout() == 0
    finally:
        observer_engine.dispose()
        engine.dispose()


def _mature_and_publish_one_generation(engine, *, run_id: int) -> dict[str, int]:
    """Continue the exact provider lineage through positive serving publication."""

    config = load_winner_probability_config()
    config = replace(config, engine=replace(config.engine, enabled=True))
    config = replace(config, config_hash=winner_probability_config_hash(config))
    with Session(engine) as db:
        prediction = db.scalar(
            select(WinnerPredictionSnapshot).where(
                WinnerPredictionSnapshot.run_id == run_id,
                WinnerPredictionSnapshot.ticker == EXACT_TEN[0],
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
