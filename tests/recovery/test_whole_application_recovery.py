from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import psutil
import pytest
from alembic.config import Config
from native_mutation_support import bound_pipeline, seed_price_frame
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from test_technical_work import _synthetic_frame

import app.services.technical_score_service as technical_score_service
from alembic import command
from app.models.tables import (
    AcquisitionPlanRecord,
    BackgroundJob,
    CombinedResult,
    IBFetchItem,
    IBFetchRun,
    MarketCalculationContext,
    PipelineRun,
    PipelineStep,
    PriceBar,
    RawCompanyRow,
    RefreshCycleRecord,
    TechnicalScore,
    TechnicalSourceManifest,
    UploadRun,
    WorkScopeMember,
    WorkScopeRecord,
)
from app.services.background_job_service import (
    fence_stalled_jobs,
    heartbeat_job,
    is_cancel_requested,
    record_job_progress,
    request_job_cancel,
)
from app.services.bar_cache_service import price_bar_data_hash
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.configuration_delivery import resolve_pipeline_configurations
from app.services.domain_write_fence import assert_current_execution_ownership
from app.services.ib_fetch_job_service import FetchJobOptions, _admit_fetch_authority
from app.services.ib_fetch_plan_service import FetchPlan, fetch_plan_to_dict
from app.services.operational_metrics import operational_metrics
from app.services.pipeline_executor import (
    PipelineExecutionDependencies,
    execute_full_pipeline,
)
from app.services.pipeline_service import PipelineStatus, PipelineStepStatus
from app.services.price_bar_repository import (
    _pipeline_acquisition_visibility,
    load_price_bars_frame,
)
from app.services.ranking_profile_service import RankingPipelineResult
from app.services.sector_rotation_dtos import SectorRotationSnapshotDto
from app.services.technical_score_service import (
    TechnicalScoringError,
    _canonical_technical_source_manifest,
    _technical_checkpoint,
    score_run_technicals,
)
from app.settings import get_settings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]

CUTOFF = datetime(2026, 9, 24, 13, 19, 59, tzinfo=UTC)
SESSION = date(2026, 9, 23)
FIRST_FETCH_SYMBOLS = ("BHE", "BLLN", "KLIC", "LSCC", "PDFS")


@pytest.fixture
def recovery_engine(disposable_postgres_database_factory):
    with disposable_postgres_database_factory() as database_url:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
        command.upgrade(config, "head")
        command.check(config)
        engine = create_engine(database_url)
        try:
            yield engine
        finally:
            engine.dispose()


def test_pipeline_owned_first_fetch_is_visible(recovery_engine):
    with Session(recovery_engine) as db:
        context, fetch_run, items = _seed_acquisition(db, FIRST_FETCH_SYMBOLS)
        for index, ticker in enumerate(FIRST_FETCH_SYMBOLS):
            _bar(
                db,
                ticker=ticker,
                bar_date=SESSION - timedelta(days=index),
                created_at=CUTOFF + timedelta(minutes=5),
                fetch_run_id=fetch_run.id,
                fetch_item_id=items[ticker].id,
            )
        db.commit()

        visible = {
            ticker: len(
                load_price_bars_frame(
                    db,
                    ticker,
                    "TRADES",
                    max_session=SESSION,
                    as_of=CUTOFF,
                    calculation_context_id=context.id,
                )
            )
            for ticker in FIRST_FETCH_SYMBOLS
        }
        assert visible == {ticker: 1 for ticker in FIRST_FETCH_SYMBOLS}
        db.rollback()


def test_unrelated_post_cutoff_fetch_remains_invisible(recovery_engine):
    with Session(recovery_engine) as db:
        context, _, _ = _seed_acquisition(db, ("NEG",))
        _bar(
            db,
            ticker="NEG",
            bar_date=SESSION,
            created_at=CUTOFF + timedelta(minutes=2),
            fetch_run_id=None,
            fetch_item_id=None,
        )
        db.commit()
        frame = load_price_bars_frame(
            db,
            "NEG",
            "TRADES",
            max_session=SESSION,
            as_of=CUTOFF,
            calculation_context_id=context.id,
        )
        assert frame.empty
        db.rollback()


def test_technical_evidence_is_linear_and_bounded():
    shared = _source_manifest("SPY", range(3))
    scores = []
    for index in range(100):
        own = _source_manifest(f"T{index:03d}", range(3))
        scores.append(
            TechnicalScore(
                run_id=1,
                ticker=f"T{index:03d}",
                debug_json={
                    "temporal_lineage": {
                        "source_manifests": {"price": own, "benchmark": shared}
                    }
                },
            )
        )
    configuration = SimpleNamespace(
        snapshot=SimpleNamespace(
            identity=SimpleNamespace(as_dict=lambda: {"namespace": "core.technical", "v": "1"})
        )
    )
    cutoff = SimpleNamespace(
        context_id=7,
        cutoff_at=CUTOFF,
        latest_completed_session=SESSION,
    )
    manifest = _canonical_technical_source_manifest(
        scores,
        run_id=1,
        pipeline_run_id=2,
        market_cutoff=cutoff,
        effective_configuration=configuration,
    )
    assert manifest["source_count"] == 101
    assert manifest["state_count"] == 303
    assert len({item["digest"] for item in manifest["sources"]}) == 101
    assert len(Canonical.dumps(manifest)) < 100_000
    old_state_visits = 100 * sum(
        len(item["manifest"]["states"]) for item in manifest["sources"]
    )
    assert old_state_visits == 30_300
    assert manifest["state_count"] * 2 < old_state_visits


def test_technical_heartbeat_survives_long_work(recovery_engine):
    with Session(recovery_engine) as setup:
        job = _job(setup, token="owner-a")
        setup.commit()
        job_id = job.id
    with Session(recovery_engine) as business, Session(recovery_engine) as control:
        business.execute(text("SELECT 1"))
        control.execute(text("SET LOCAL statement_timeout = '2s'"))
        control_job = control.get(BackgroundJob, job_id)
        before = control_job.lease_expires_at
        heartbeat_job(control, control_job, lease_seconds=900, execution_token="owner-a")
        record_job_progress(
            control,
            job_id=job_id,
            execution_token="owner-a",
            stage="SCORING_TECHNICALS",
            current_item="CALCULATING:T010",
            last_completed_item="T009",
            processed=10,
            total=100,
            checkpoint_version="technical-recovery-v1",
        )
        control.commit()
        refreshed = control.get(BackgroundJob, job_id)
        assert refreshed.lease_expires_at > before
        assert refreshed.progress_processed == 10
        assert refreshed.progress_total == 100
        business.rollback()


def test_technical_cancel_is_nonblocking_and_rolls_back(recovery_engine):
    with Session(recovery_engine) as setup:
        run = UploadRun(
            filename="cancel-recovery.csv", row_count=1, status="COMPLETED"
        )
        setup.add(run)
        setup.flush()
        job = _job(setup, token="cancel-owner")
        setup.commit()
        job_id = job.id
        run_id = run.id
    with Session(recovery_engine) as business, Session(recovery_engine) as control:
        business.add(TechnicalScore(run_id=run_id, ticker="CANCEL", insufficient_data=False))
        business.flush()
        control.execute(text("SET LOCAL statement_timeout = '2s'"))
        request_job_cancel(control, job_id)
        control.commit()
        with pytest.raises(TechnicalScoringError, match="cancelled"):
            _technical_checkpoint(
                None,
                lambda: is_cancel_requested(business, job_id),
                phase="CALCULATING",
                processed=10,
                total=100,
            )
        business.rollback()
    with Session(recovery_engine) as verify:
        assert is_cancel_requested(verify, job_id) is True
        assert verify.scalar(select(TechnicalScore.id).limit(1)) is None


def test_stale_owner_cannot_publish(recovery_engine):
    with Session(recovery_engine) as setup:
        job = _job(setup, token="old-token")
        setup.commit()
        job_id = job.id
    with Session(recovery_engine) as supersede:
        row = supersede.get(BackgroundJob, job_id)
        row.execution_token = "new-token"
        supersede.commit()
    with Session(recovery_engine) as stale:
        with pytest.raises(Exception, match="lease is no longer held"):
            assert_current_execution_ownership(
                stale, job_id=job_id, execution_token="old-token"
            )
        stale.rollback()
    with Session(recovery_engine) as verify:
        assert verify.scalar(select(TechnicalScore.id).limit(1)) is None


def test_watchdog_reports_locked_candidate(recovery_engine, caplog):
    with Session(recovery_engine) as setup:
        job = _job(setup, token="locked-owner")
        job.last_progress_at = datetime.now(UTC) - timedelta(minutes=40)
        job.progress_stage = "SCORING_TECHNICALS"
        setup.commit()
        job_id = job.id
    with Session(recovery_engine) as locker, Session(recovery_engine) as watchdog:
        locker.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.id == job_id)
            .with_for_update()
        )
        with caplog.at_level("WARNING"):
            fenced = fence_stalled_jobs(
                watchdog,
                default_timeout_seconds=60,
                market_data_timeout_seconds=60,
                long_stage_timeout_seconds=60,
                now=datetime.now(UTC),
                worker_id="recovery-test",
            )
        assert fenced == []
        assert "LOCKED_CANDIDATE_SKIPPED" in caplog.text
        watchdog.rollback()
        locker.rollback()


def test_deterministic_pipeline_10_25_100(disposable_postgres_database_factory):
    """Production Technical/PG path plus deterministic downstream orchestration."""

    with disposable_postgres_database_factory() as database_url:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
        command.upgrade(config, "head")
        command.check(config)
        engine = create_engine(database_url)
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        metrics = []
        expected_classification = None
        try:
            for run_id, size in enumerate((10, 25, 100), start=1010):
                result, measurement = _run_deterministic_pipeline_gate(
                    sessions, engine, run_id=run_id, size=size
                )
                metrics.append(measurement)
                print("RECOVERY_GATE=" + json.dumps(measurement, sort_keys=True))
                assert result.status == PipelineStatus.COMPLETED
                assert measurement["requested"] == size
                assert measurement["technical_scores"] == size
                assert measurement["visible"] == size
                assert measurement["source_validation_queries"] < size * 8 + 50
                assert measurement["max_score_payload_bytes"] < 50_000
                assert measurement["canonical_manifest_bytes"] < 20_000_000
                assert measurement["technical_seconds"] < 900
                assert measurement["max_checkpoint_gap_seconds"] < 60
                assert measurement["lease_expired"] is False
                assert measurement["stages"] == [
                    "Fundamental",
                    "Market handoff",
                    "Technical",
                    "Combined",
                    "Ranking",
                    "CERI",
                    "Setup",
                    "Lifecycle",
                    "Alerts",
                    "Winner",
                ]
                assert measurement["anchor_score"]["technical_composite_score"] != "None"
                if expected_classification is None:
                    expected_classification = measurement["anchor_score"]["classification"]
                else:
                    assert (
                        measurement["anchor_score"]["classification"]
                        == expected_classification
                    )
            assert metrics[-1]["retry_score_count"] == 100
            assert metrics[-1]["technical_seconds"] < 300
            assert metrics[-1]["technical_worker_seconds"] < 300
            assert metrics[-1]["pure_kernel_seconds"] < 300
            print("RECOVERY_METRICS=" + json.dumps(metrics, sort_keys=True))
        finally:
            engine.dispose()


def _seed_acquisition(db: Session, tickers: tuple[str, ...]):
    suffix = tickers[0]
    plan_id = ("p" + suffix.lower()).ljust(64, "0")[:64]
    db.add(
        AcquisitionPlanRecord(
            plan_id=plan_id,
            plan_kind="RECOVERY_TEST",
            plan_version="v1",
            payload_json={"tickers": list(tickers)},
        )
    )
    run = UploadRun(filename=f"{suffix}.csv", row_count=len(tickers), status="COMPLETED")
    db.add(run)
    db.flush()
    pipeline = PipelineRun(
        upload_run_id=run.id,
        acquisition_plan_id=plan_id,
        status="SCORING_TECHNICALS",
    )
    db.add(pipeline)
    db.flush()
    context = MarketCalculationContext(
        pipeline_run_id=pipeline.id,
        upload_run_id=run.id,
        cutoff_at=CUTOFF,
        exchange_timezone="America/New_York",
        latest_completed_session=SESSION,
        daily_bar_ready_at=None,
        calendar_version="test-v1",
        bar_readiness_version="test-v1",
        cutoff_reason="RECOVERY_TEST",
    )
    fetch_run = IBFetchRun(
        acquisition_plan_id=plan_id,
        run_id=run.id,
        requested_tickers=list(tickers),
        symbols_including_benchmarks=list(tickers),
        status="COMPLETED",
        completed_at=CUTOFF + timedelta(minutes=10),
    )
    db.add_all([context, fetch_run])
    db.flush()
    items = {}
    for ticker in tickers:
        item = IBFetchItem(
            fetch_run_id=fetch_run.id,
            ticker=ticker,
            what_to_show="TRADES",
            bar_size="1 day",
            status="SUCCESS",
            inserted=1,
            completed_at=CUTOFF + timedelta(minutes=10),
        )
        db.add(item)
        db.flush()
        items[ticker] = item
    return context, fetch_run, items


def _run_deterministic_pipeline_gate(sessions, engine, *, run_id: int, size: int):
    incident_tickers = (
        list(FIRST_FETCH_SYMBOLS)
        if size == 100
        else [f"{ticker}{size}" for ticker in FIRST_FETCH_SYMBOLS]
    )
    tickers = incident_tickers + [
        f"R{size:03d}{index:03d}" for index in range(size - len(FIRST_FETCH_SYMBOLS))
    ]
    stage_calls: list[str] = []
    checkpoint_times: list[float] = []
    settings = get_settings()
    cutoff_at = CUTOFF
    frame = _synthetic_frame()
    frame["date"] = pd.bdate_range(end=SESSION, periods=len(frame))
    plan = FetchPlan(
        run_id=run_id,
        requested_tickers=tickers,
        symbols_including_benchmarks=[*tickers, "SPY", "QQQ", "XLK"],
        items=[],
        estimated_request_count=0,
        estimated_full_backfills=0,
        estimated_top_ups=0,
        estimated_refreshes=0,
        estimated_skips=size,
        warnings=[],
    )

    with sessions() as db:
        db.add(
            UploadRun(
                id=run_id,
                filename=f"recovery-{size}.csv",
                row_count=size,
                status="COMPLETED",
            )
        )
        db.flush()
        for index, ticker in enumerate(tickers, start=1):
            db.add(
                RawCompanyRow(
                    run_id=run_id,
                    row_number=index,
                    ticker=ticker,
                    sector="Technology",
                    sector_canonical="Information Technology",
                    raw_json={"Symbol": ticker},
                )
            )
        configurations = resolve_pipeline_configurations(db)
        cutoff, pipeline_id = bound_pipeline(
            db,
            run_id,
            configurations.values(),
            cutoff_at=cutoff_at,
        )
        pipeline = db.get(PipelineRun, pipeline_id)
        plan_id = Canonical.fingerprint({"recovery_plan": run_id})
        scope_id = Canonical.fingerprint({"recovery_scope": run_id})
        refresh_id = Canonical.fingerprint({"recovery_refresh": run_id})
        db.add(
            AcquisitionPlanRecord(
                plan_id=plan_id,
                plan_kind="RECOVERY_TEST",
                plan_version="v1",
                payload_json={"tickers": tickers},
            )
        )
        db.flush()
        membership_fingerprint = Canonical.fingerprint(tickers)
        db.add(
            WorkScopeRecord(
                scope_id=scope_id,
                scope_kind="RECOVERY_PIPELINE",
                subject_kind="TICKER",
                membership_policy="FROZEN",
                membership_fingerprint=membership_fingerprint,
                acquisition_plan_id=plan_id,
                payload_json={"tickers": tickers},
            )
        )
        db.flush()
        db.add_all(
            [
                WorkScopeMember(
                    scope_id=scope_id,
                    subject_type="TICKER",
                    subject_id=ticker,
                    ordinal=index,
                    membership_fingerprint=Canonical.fingerprint(
                        {"ticker": ticker, "ordinal": index}
                    ),
                )
                for index, ticker in enumerate(tickers)
            ]
        )
        db.add(
            RefreshCycleRecord(
                refresh_cycle_id=refresh_id,
                refresh_kind="RECOVERY_PIPELINE",
                scope_id=scope_id,
                observation_cycle_key=f"recovery:{run_id}",
                payload_json={"cutoff": cutoff_at.isoformat()},
            )
        )
        db.flush()
        pipeline.scope_id = scope_id
        pipeline.refresh_cycle_id = refresh_id
        pipeline.acquisition_plan_id = plan_id
        fetch_authority = _admit_fetch_authority(
            db,
            plan,
            FetchJobOptions(include_benchmarks=True),
            parent_scope_id=scope_id,
        )
        pipeline.result_json = {
            "market_data_policy": "REQUIRE_IB",
            "ib_fetch_plan": fetch_plan_to_dict(plan),
            "ib_fetch_authority": fetch_authority.as_dict(),
        }
        step_names = (
            "VALIDATING_RUN",
            "SCORING_FUNDAMENTALS",
            "FETCHING_MARKET_DATA",
            "SCORING_TECHNICALS",
            "MARKET_REGIME_SNAPSHOT",
            "COMBINING_RESULTS",
            "RANKING_PROFILES",
            "SECTOR_ROTATION_SNAPSHOT",
            "CERI_PROVIDER_INGEST",
            "CAPTURING_SETUP_SIGNALS",
            "EVALUATING_SETUP_LIFECYCLES",
            "CAPTURING_WINNER_PREDICTIONS",
        )
        for order, name in enumerate(step_names, start=1):
            db.add(
                PipelineStep(
                    pipeline_run_id=pipeline_id,
                    step_name=name,
                    step_order=order,
                    status=PipelineStepStatus.PENDING,
                    retry_count=0,
                )
            )
        for symbol in [*tickers[5:], "SPY", "QQQ", "XLK"]:
            seed_price_frame(db, symbol, frame, cutoff.cutoff_at)
        _seed_pipeline_first_fetch_frames(
            db,
            run_id=run_id,
            plan_id=fetch_authority.acquisition_plan_id,
            scope_id=fetch_authority.scope_id,
            refresh_id=fetch_authority.refresh_cycle_id,
            tickers=tickers[:5],
            frame=frame,
            cutoff_at=cutoff.cutoff_at,
        )
        now = datetime.now(UTC)
        job = BackgroundJob(
            job_type="FULL_PIPELINE",
            status="RUNNING",
            payload_json={"pipeline_run_id": pipeline_id},
            related_run_id=run_id,
            execution_token=f"recovery-owner-{size}",
            worker_id="recovery-test",
            locked_at=now,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=900),
            last_progress_at=now,
            run_after=now,
        )
        db.add(job)
        db.commit()
        job_id = job.id

    with sessions() as authority_check:
        resolved_authority = _pipeline_acquisition_visibility(
            authority_check,
            calculation_context_id=cutoff.context_id,
            ticker=tickers[0],
            what_to_show="TRADES",
            timeframe="1 day",
        )
        if resolved_authority is None:
            retained_pipeline = authority_check.get(PipelineRun, pipeline_id)
            fetch_rows = list(
                authority_check.execute(
                    select(
                        IBFetchRun.id,
                        IBFetchRun.run_id,
                        IBFetchRun.scope_id,
                        IBFetchRun.refresh_cycle_id,
                        IBFetchRun.acquisition_plan_id,
                    ).where(IBFetchRun.run_id == run_id)
                )
            )
            pytest.fail(
                "fresh-fetch authority did not resolve: "
                + repr(
                    {
                        "retained": retained_pipeline.result_json.get(
                            "ib_fetch_authority"
                        ),
                        "fetch_rows": [tuple(row) for row in fetch_rows],
                    }
                )
            )

    def progress(_db, **values):
        checkpoint_times.append(time.perf_counter())
        with sessions() as control:
            control_job = control.get(BackgroundJob, job_id)
            heartbeat_job(
                control,
                control_job,
                lease_seconds=900,
                execution_token=control_job.execution_token,
            )
            record_job_progress(
                control,
                job_id=job_id,
                execution_token=control_job.execution_token,
                **values,
            )
            control.commit()

    technical_span = {"start": None, "end": None}

    def technicals(db, current_run_id, *, effective_configuration=None, **kwargs):
        # Reaching this adapter proves the retained frozen Market handoff step
        # completed; a zero-request plan intentionally has no fetch callback.
        stage_calls.append("Market handoff")
        stage_calls.append("Technical")
        technical_span["start"] = time.perf_counter()
        rows = score_run_technicals(
            db,
            current_run_id,
            effective_configuration=effective_configuration,
            **kwargs,
        )
        technical_span["end"] = time.perf_counter()
        return rows

    technicals.__name__ = "score_run_technicals"

    dependencies = PipelineExecutionDependencies(
        market_cutoff=cutoff,
        validate_pipeline_preflight=lambda *_args: {"complete": True},
        recalculate_fundamentals=lambda *_args, **_kwargs: stage_calls.append(
            "Fundamental"
        )
        or [SimpleNamespace(ticker=ticker) for ticker in tickers],
        build_fetch_plan=lambda **_kwargs: stage_calls.append("Market handoff") or plan,
        score_technicals=technicals,
        build_market_regime_snapshot=lambda *_args, **_kwargs: SimpleNamespace(
            regime="Confirmed Uptrend",
            risk_state="green",
            confidence="normal",
            warnings=[],
        ),
        refresh_combined=lambda *_args, **_kwargs: stage_calls.append("Combined")
        or [
            CombinedResult(
                run_id=run_id, ticker=ticker, is_complete=True, has_warning=False
            )
            for ticker in tickers
        ],
        refresh_rankings=lambda *_args, **_kwargs: stage_calls.append("Ranking")
        or RankingPipelineResult(
            status="COMPLETED",
            profile_count=5,
            result_count=size * 5,
            reason=None,
            results=(),
        ),
        build_sector_rotation_snapshot=lambda *_args, **_kwargs: SectorRotationSnapshotDto(
            run_id=run_id,
            as_of_date=SESSION.isoformat(),
            mode="universe_only",
            calculation_version="recovery-v1",
            config_version="recovery-v1",
            config_hash="recovery",
            default_ranking_profile="momentum_swing",
            rows=[],
            summary={"sector_count": 1, "ticker_count": size},
            warnings=[],
            debug={},
        ),
        ceri_run_capture_enabled=settings.ceri_enabled
        and settings.ceri_run_capture_enabled,
        ceri_provider_ingest_enabled=settings.ceri_enabled
        and settings.ceri_provider_ingest_enabled,
        schedule_ceri_provider_ingest=lambda *_args, **_kwargs: stage_calls.append("CERI")
        or 1,
        capture_ceri_snapshot=lambda *_args, **_kwargs: stage_calls.append("CERI") or {},
        capture_setup_signals=lambda *_args, **_kwargs: stage_calls.append("Setup") or {},
        evaluate_setup_lifecycles=lambda *_args, **_kwargs: stage_calls.extend(
            ["Lifecycle", "Alerts"]
        )
        or {},
        setup_lifecycle_pipeline_step_enabled=(
            settings.setup_lifecycle_pipeline_step_enabled
        ),
        setup_capture_handoff_enabled=settings.setup_capture_handoff_enabled,
        capture_winner_predictions=lambda *_args, **_kwargs: stage_calls.append("Winner")
        or {
            "inserted": size,
            "duplicate": 0,
            "excluded": 0,
            "failed": 0,
            "pending_outcomes": size,
            "decision_time_estimates": size,
        },
        winner_probability_capture_enabled=(
            settings.winner_probability_enabled
            and settings.winner_probability_capture_in_pipeline
        ),
    )
    sql_metrics = {"queries": 0, "source_validation_queries": 0, "score_insert_seconds": 0.0}
    duration_before = {
        name: operational_metrics.total(f"swinglens_technical_{name}_seconds")
        for name in ("input_load", "worker_span", "finalize")
    }

    def before_sql(_conn, _cursor, statement, _parameters, context, _many):
        context._recovery_started_at = time.perf_counter()

    def after_sql(_conn, _cursor, statement, _parameters, context, _many):
        elapsed = time.perf_counter() - context._recovery_started_at
        sql_metrics["queries"] += 1
        normalized = " ".join(statement.lower().split())
        if "from price_bars" in normalized and "price_bars.id in" in normalized:
            sql_metrics["source_validation_queries"] += 1
        if "insert into technical_scores" in normalized:
            sql_metrics["score_insert_seconds"] += elapsed

    event.listen(engine, "before_cursor_execute", before_sql)
    event.listen(engine, "after_cursor_execute", after_sql)
    process = psutil.Process()
    rss_before = process.memory_info().rss
    started = time.perf_counter()
    hard_deadline = started + 900
    try:
        with sessions() as db:
            result = execute_full_pipeline(
                db,
                pipeline_id,
                dependencies=dependencies,
                should_cancel=lambda: time.perf_counter() > hard_deadline,
                progress_callback=progress,
            )
            db.commit()
    finally:
        wall_seconds = time.perf_counter() - started
        rss_after = process.memory_info().rss
        event.remove(engine, "before_cursor_execute", before_sql)
        event.remove(engine, "after_cursor_execute", after_sql)

    with sessions() as verify:
        duration_after = {
            name: operational_metrics.total(f"swinglens_technical_{name}_seconds")
            for name in ("input_load", "worker_span", "finalize")
        }
        scores = list(
            verify.scalars(
                select(TechnicalScore)
                .where(TechnicalScore.run_id == run_id)
                .order_by(TechnicalScore.ticker)
            )
        )
        manifest = verify.scalar(
            select(TechnicalSourceManifest).where(
                TechnicalSourceManifest.run_id == run_id
            )
        )
        job = verify.get(BackgroundJob, job_id)
        visible = sum(
            not load_price_bars_frame(
                verify,
                ticker,
                "TRADES",
                max_session=SESSION,
                as_of=cutoff.cutoff_at,
                calculation_context_id=cutoff.context_id,
            ).empty
            for ticker in tickers
        )
        score_payloads = [len(Canonical.dumps(score.debug_json)) for score in scores]
        measurement = {
            "size": size,
            "requested": size,
            "visible": visible,
            "technical_scores": len(scores),
            "technical_error_count": result.performance.get("technical_error_count"),
            "low_confidence_count": sum(
                score.technical_confidence in {"low", "error"} for score in scores
            ),
            "insufficient_count": sum(bool(score.insufficient_data) for score in scores),
            "pipeline_status": result.status,
            "technical_seconds": round(
                technical_span["end"] - technical_span["start"], 6
            ),
            "technical_input_seconds": round(
                duration_after["input_load"] - duration_before["input_load"],
                6,
            ),
            "technical_worker_seconds": round(
                duration_after["worker_span"] - duration_before["worker_span"],
                6,
            ),
            "technical_finalize_seconds": round(
                duration_after["finalize"] - duration_before["finalize"],
                6,
            ),
            "pipeline_seconds": round(wall_seconds, 6),
            "source_validation_queries": sql_metrics["source_validation_queries"],
            "all_sql_queries": sql_metrics["queries"],
            "score_insert_seconds": round(sql_metrics["score_insert_seconds"], 6),
            "validated_price_states": manifest.state_count,
            "canonical_manifest_bytes": len(Canonical.dumps(manifest.manifest_json)),
            "total_score_payload_bytes": sum(score_payloads),
            "average_score_payload_bytes": round(sum(score_payloads) / len(scores), 2),
            "max_score_payload_bytes": max(score_payloads),
            "process_rss_before_bytes": rss_before,
            "process_rss_after_bytes": rss_after,
            "process_rss_growth_bytes": max(0, rss_after - rss_before),
            "checkpoint_count": len(checkpoint_times),
            "max_checkpoint_gap_seconds": round(
                max(
                    (
                        right - left
                        for left, right in zip(
                            checkpoint_times, checkpoint_times[1:], strict=False
                        )
                    ),
                    default=0.0,
                ),
                6,
            ),
            "lease_expired": job.lease_expires_at <= datetime.now(UTC),
            "anchor_score": {
                "classification": scores[0].classification,
                "technical_composite_score": str(scores[0].technical_composite_score),
                "confidence_adjusted_score": str(scores[0].confidence_adjusted_score),
            },
            "stages": stage_calls,
            "retry_score_count": None,
        }
    if size == 100:
        # Exact retry is idempotent across execution modes. Its worker-span
        # metric is also the deterministic post-remediation pure-kernel sample.
        retry_deadline = time.perf_counter() + 900
        pure_before = operational_metrics.total(
            "swinglens_technical_worker_span_seconds"
        )
        original_get_settings = technical_score_service.get_settings
        production_settings = original_get_settings()
        technical_score_service.get_settings = lambda: production_settings.model_copy(
            update={
                "technical_process_pool_enabled": False,
                "technical_pure_boundary_enabled": True,
                "technical_pure_boundary_shadow_compare_enabled": False,
            }
        )
        try:
            with sessions() as retry:
                score_run_technicals(
                    retry,
                    run_id,
                    tickers=tickers,
                    market_cutoff=cutoff,
                    pipeline_run_id=pipeline_id,
                    effective_configuration=configurations["core.technical"],
                    should_cancel=lambda: time.perf_counter() > retry_deadline,
                )
                retry.commit()
        finally:
            technical_score_service.get_settings = original_get_settings
        measurement["pure_kernel_seconds"] = round(
            operational_metrics.total("swinglens_technical_worker_span_seconds")
            - pure_before,
            6,
        )
        with sessions() as verify:
            measurement["retry_score_count"] = verify.scalar(
                select(func.count())
                .select_from(TechnicalScore)
                .where(TechnicalScore.run_id == run_id)
            )
    return result, measurement


def _seed_pipeline_first_fetch_frames(
    db: Session,
    *,
    run_id: int,
    plan_id: str,
    scope_id: str,
    refresh_id: str,
    tickers: list[str],
    frame: pd.DataFrame,
    cutoff_at: datetime,
) -> None:
    fetch_run = IBFetchRun(
        acquisition_plan_id=plan_id,
        scope_id=scope_id,
        refresh_cycle_id=refresh_id,
        run_id=run_id,
        requested_tickers=tickers,
        symbols_including_benchmarks=tickers,
        status="COMPLETED",
        completed_at=cutoff_at + timedelta(minutes=10),
    )
    db.add(fetch_run)
    db.flush()
    observed_at = cutoff_at + timedelta(minutes=5)
    for ticker in tickers:
        for feed in ("ADJUSTED_LAST", "TRADES"):
            item = IBFetchItem(
                fetch_run_id=fetch_run.id,
                ticker=ticker,
                what_to_show=feed,
                bar_size="1 day",
                status="SUCCESS",
                inserted=len(frame),
                completed_at=cutoff_at + timedelta(minutes=10),
            )
            db.add(item)
            db.flush()
            rows = []
            for value in frame.to_dict(orient="records"):
                row = PriceBar(
                    ticker=ticker,
                    bar_date=value["date"].date(),
                    timeframe="1 day",
                    open=Decimal(str(value["open"])),
                    high=Decimal(str(value["high"])),
                    low=Decimal(str(value["low"])),
                    close=Decimal(str(value["close"])),
                    volume=Decimal(str(value["volume"])),
                    source="IBKR",
                    what_to_show=feed,
                    created_at=observed_at,
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                    first_fetch_run_id=fetch_run.id,
                    first_fetch_item_id=item.id,
                    revision_count=0,
                )
                row.data_hash = price_bar_data_hash(row)
                rows.append(row)
            db.add_all(rows)
    db.flush()


def _bar(
    db: Session,
    *,
    ticker: str,
    bar_date: date,
    created_at: datetime,
    fetch_run_id: int | None,
    fetch_item_id: int | None,
) -> PriceBar:
    row = PriceBar(
        ticker=ticker,
        bar_date=bar_date,
        timeframe="1 day",
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10.5"),
        volume=Decimal("1000"),
        source="IB",
        what_to_show="TRADES",
        created_at=created_at,
        first_seen_at=created_at,
        last_seen_at=created_at,
        first_fetch_run_id=fetch_run_id,
        first_fetch_item_id=fetch_item_id,
        revision_count=0,
        data_hash=f"hash-{ticker}-{bar_date}",
    )
    db.add(row)
    return row


def _source_manifest(ticker: str, ids) -> dict:
    return {
        "ticker": ticker,
        "what_to_show": "TRADES",
        "timeframe": "1 day",
        "as_of": CUTOFF,
        "max_session": SESSION,
        "acquisition_authority": None,
        "states": [{"id": int(value) + 1, "fingerprint": f"{ticker}-{value}"} for value in ids],
    }


def _job(db: Session, *, token: str) -> BackgroundJob:
    now = datetime.now(UTC)
    job = BackgroundJob(
        job_type="FULL_PIPELINE",
        status="RUNNING",
        payload_json={"pipeline_run_id": 1},
        execution_token=token,
        worker_id="recovery-test",
        locked_at=now,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=30),
        run_after=now,
    )
    db.add(job)
    db.flush()
    return job
