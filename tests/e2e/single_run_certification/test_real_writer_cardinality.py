"""Disposable full-pipeline cardinality gates with real internal writers."""

import csv
import json
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.models.tables import (
    IBContract,
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
from single_run_certification.frozen_ceri import frozen_ceri_registry
from single_run_certification.reporting import CertificationRecorder
from single_run_certification.test_single_run_certification import (
    _launch_run_through_gui,
    _run_pipeline_through_gui,
    certification_environment,  # noqa: F401
    certification_page,  # noqa: F401
)

EXACT_TEN = ("BHE", "BLLN", "KLIC", "LSCC", "PDFS", "ACMR", "RDVT", "AVT", "DVN", "JNJ")


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
            db.commit()
            db.commit()

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
                pipeline_diagnostics = diagnostic_db.execute(
                    text(
                        "select status, error_message, "
                        "result_json->>'ceri_completion_state' as ceri_completion_state, "
                        "result_json->>'ceri_score_snapshots' as ceri_score_snapshots "
                        "from pipeline_runs where id=:pipeline_id"
                    ),
                    {"pipeline_id": pipeline_id},
                ).mappings().one()
                job_diagnostics = diagnostic_db.execute(
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
                ).mappings().all()
                job_summaries = diagnostic_db.execute(
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
                ).mappings().all()
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
            pipeline_metrics = db.execute(
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
            ).mappings().one()
            ceri_metrics = db.execute(
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
            ).mappings().one()
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
                        "total_pipeline_seconds": float(
                            pipeline_metrics["total_seconds"] or 0
                        ),
                        "technical_ms": float(pipeline_metrics["technical_ms"] or 0),
                        "ceri_workflow_seconds": float(
                            ceri_metrics["workflow_seconds"] or 0
                        ),
                        "ceri_max_job_seconds": float(
                            ceri_metrics["max_job_seconds"] or 0
                        ),
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


def _seed_retained_provider_history(engine, *, tickers: tuple[str, ...]) -> None:
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
