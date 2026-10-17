"""Native PostgreSQL publication, replay, worker retry and watchdog regression."""

from __future__ import annotations

import cProfile
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from time import perf_counter, sleep

import pandas as pd
import pytest
from native_mutation_support import bound_pipeline, seed_price_frame
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, sessionmaker
from test_core_effective_configuration import core_configurations
from test_fundamental_ranker_v2 import _quality_values
from test_technical_work import _synthetic_frame
from test_worker_progress_reliability_postgresql import _migrate

from app.models.tables import (
    BackgroundJob,
    IBContract,
    PipelineDependency,
    PipelineRun,
    PipelineStep,
    RankingResult,
    RawCompanyRow,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SetupSignalSnapshotCurrentSelection,
    SignalAlertEvent,
    SignalChangeEvent,
    TechnicalScore,
    UploadRun,
)
from app.services.background_job_service import enqueue_job, fence_stalled_jobs, heartbeat_job
from app.services.background_worker import run_worker_once
from app.services.combined_decision import refresh_combined_results
from app.services.configuration_delivery import resolve_pipeline_configurations
from app.services.contextual_effective_configuration import (
    resolve_regime_configuration,
    resolve_sector_configuration,
)
from app.services.core_effective_configuration import resolve_technical_configuration
from app.services.decision_effective_configuration import (
    resolve_alert_configuration,
    resolve_lifecycle_configuration,
    resolve_setup_configuration,
    resolve_winner_configuration,
)
from app.services.domain_write_fence import ControlPlaneLockTimeout, control_plane_transaction
from app.services.fundamental_score_service import recalculate_run_fundamentals
from app.services.market_regime_command_center import MarketRegimeCommandCenterService
from app.services.pipeline_execution_authority import claimable_pipeline_job_ids
from app.services.pipeline_executor import _pipeline_step
from app.services.pipeline_service import cancel_pipeline
from app.services.pipeline_state_machine import transition_pipeline
from app.services.ranking_profile_service import refresh_ranking_profile
from app.services.runtime_mutation_authority import RecoveryAuthority
from app.services.sector_rotation_service import SectorRotationService
from app.services.setup_lifecycle.evaluation_service import (
    SetupLifecycleEvaluationCancelled,
    SetupLifecycleEvaluationService,
)
from app.services.setup_lifecycle.snapshot_builder import (
    SetupLifecycleSnapshotCaptureService,
)
from app.services.technical_indicators import load_pine_defaults
from app.services.technical_score_service import score_run_technicals
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.services.transition_preflight_plan_service import (
    freeze_transition_decision_handoff_manifest,
)
from app.services.winner_probability.capture_service import WinnerPredictionCaptureService
from app.services.winner_probability.config import load_winner_probability_config
from app.settings import get_settings


@pytest.fixture
def run40_engine(disposable_postgres_database):
    _migrate(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database, pool_size=8)
    try:
        yield engine
    finally:
        engine.dispose()


def _seed_universe(
    db,
    *,
    run_id,
    tickers,
    cutoff_at,
    durable=False,
    winner_config=None,
    lifecycle_config=None,
    alert_configuration=None,
    technical_checkpoint_callback=None,
    ranking_checkpoint_callback=None,
):
    if not durable and alert_configuration is None:
        from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService

        # Standalone native fixtures must freeze the same selected rule values
        # that their alert writer will consume, before binding the authority.
        alerts = SetupLifecycleAlertService(config=lifecycle_config)
        alerts.seed_builtin_rules(db)
        alerts.rules_for_evaluation(db)
        alert_configuration = alerts.effective_configuration
    configs = list(core_configurations())
    configs[1] = resolve_technical_configuration(
        pine=load_pine_defaults(),
        v4=load_technical_scoring_v4_config(),
        v5=load_technical_scoring_v5_config(),
        settings=get_settings(),
    )
    regime = resolve_regime_configuration()
    sector = resolve_sector_configuration()
    extra = [
        resolve_setup_configuration(lifecycle_config),
        resolve_lifecycle_configuration(lifecycle_config),
        resolve_alert_configuration(lifecycle_config),
        resolve_winner_configuration(),
        regime,
        sector,
    ]
    if alert_configuration is not None:
        extra = [
            item for item in extra if item.snapshot.family.namespace != "decision.alerts.setup"
        ]
        extra.append(alert_configuration)
    if winner_config is not None:
        extra = [
            item for item in extra if item.snapshot.family.namespace != "decision.winner.prediction"
        ]
        extra.extend(
            resolve_winner_configuration(winner_config, family=family)
            for family in ("prediction", "outcome", "cohort", "generation")
        )
    db.add(
        UploadRun(
            id=run_id,
            filename=f"run40-fixture-{run_id}.csv",
            status="COMPLETED",
            row_count=len(tickers),
        )
    )
    db.flush()
    for index, ticker in enumerate(tickers, 1):
        raw = {"Symbol": ticker, **_quality_values()}
        if winner_config is not None:
            from test_t14c_decision_writer_postgresql import _native_liquidity_source

            from app.services.earnings_date_parser import parse_earnings_date

            raw.update(_native_liquidity_source())
            raw["upcoming_earnings_date"] = "2026-11-10"
            db.add(
                IBContract(
                    ticker=ticker,
                    ib_conid=1_400_000 + index,
                    symbol=ticker,
                    exchange="SMART",
                    primary_exchange="NASDAQ",
                    currency="USD",
                    sec_type="STK",
                    resolution_status="RESOLVED",
                )
            )
        db.add(
            RawCompanyRow(
                run_id=run_id,
                row_number=index,
                ticker=ticker,
                sector="Technology",
                sector_canonical="Information Technology",
                upcoming_earnings_date=(
                    parse_earnings_date(raw["upcoming_earnings_date"])
                    if winner_config is not None
                    else None
                ),
                raw_json=raw,
            )
        )
    db.flush()
    frozen = list(resolve_pipeline_configurations(db).values()) if durable else [*configs, *extra]
    cutoff, pipeline_id = bound_pipeline(db, run_id, frozen, cutoff_at=cutoff_at)
    frame = _synthetic_frame()
    frame["date"] = pd.bdate_range(end=cutoff.latest_completed_session, periods=len(frame))
    if run_id == 702:
        for column in ("open", "high", "low", "close"):
            frame[column] *= 1.2
        frame["volume"] *= 2
    for ticker in (*tickers, "SPY", "QQQ", "XLK"):
        seed_price_frame(db, ticker, frame, cutoff.cutoff_at)
    args = dict(market_cutoff=cutoff, pipeline_run_id=pipeline_id)
    fundamentals = recalculate_run_fundamentals(
        db, run_id, **args, effective_configuration=configs[0]
    )
    technicals = score_run_technicals(
        db,
        run_id,
        tickers=list(tickers),
        **args,
        effective_configuration=configs[1],
        checkpoint_callback=technical_checkpoint_callback,
    )
    f_by_ticker = {row.ticker: row for row in fundamentals}
    t_by_ticker = {row.ticker: row for row in technicals}
    refresh_combined_results(
        db,
        run_id,
        **args,
        effective_configuration=configs[2],
        source_evidence={
            ticker: {
                "fundamental": f_by_ticker[ticker].evidence_id,
                "technical": t_by_ticker[ticker].evidence_id,
            }
            for ticker in tickers
        },
    )
    refresh_ranking_profile(
        db,
        run_id,
        "momentum_swing",
        **args,
        effective_configuration=configs[3],
        checkpoint_callback=ranking_checkpoint_callback,
    )
    MarketRegimeCommandCenterService().build_snapshot(
        db, run_id=run_id, market_cutoff=cutoff, effective_configuration=regime
    )
    SectorRotationService().build_sector_rotation_snapshot(
        db, run_id, market_cutoff=cutoff, effective_configuration=sector
    )
    db.commit()
    return cutoff, pipeline_id


def _capture(db, run_id, cutoff, config=None):
    from app.services.setup_lifecycle.repository import SetupLifecycleRepository

    result = SetupLifecycleSnapshotCaptureService(
        config=config, repository=SetupLifecycleRepository(config)
    ).capture_snapshots_for_run(db, run_id, market_cutoff=cutoff)
    assert result.failed == 0 and result.captured > 0, result
    db.commit()
    return result


def _counts(db):
    return {
        model.__tablename__: db.scalar(select(func.count()).select_from(model))
        for model in (
            SetupSignalSnapshotCurrentSelection,
            SignalChangeEvent,
            SetupLifecycleEpisode,
            SetupLifecycleEvent,
            SignalAlertEvent,
        )
    }


@pytest.mark.parametrize("stage", ["technical", "ranking"])
def test_upstream_evidence_checkpoint_interruption_rolls_back_native_writes(run40_engine, stage):
    checkpoints = []
    artifact = TechnicalScore if stage == "technical" else RankingResult
    with Session(run40_engine) as db:

        def interrupt(**progress):
            checkpoints.append(progress)
            if progress["phase"] == "PERSISTING_EVIDENCE":
                assert progress["processed"] == progress["total"] == 2
                assert progress["last_completed_item"]
                # The producer has flushed actual evidence; no partial result
                # is published to another connection before the owner commits.
                assert db.scalar(select(func.count()).select_from(RawCompanyRow)) == 2
                assert db.scalar(select(func.count()).select_from(artifact)) == 2
                with Session(run40_engine) as observer:
                    assert observer.scalar(select(func.count()).select_from(artifact)) == 0
                raise ControlPlaneLockTimeout(f"injected {stage} evidence interruption")

        with pytest.raises(ControlPlaneLockTimeout, match=f"injected {stage}"):
            _seed_universe(
                db,
                run_id=702,
                tickers=("R000", "R001"),
                cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
                **{f"{stage}_checkpoint_callback": interrupt},
            )
        db.rollback()
        with Session(run40_engine) as observer:
            # Technical's existing input/calculation boundary was already
            # committed; only the interrupted publication must be absent.
            assert observer.get(UploadRun, 702) is not None
            assert observer.scalar(select(func.count()).select_from(artifact)) == 0
        assert any(item["phase"] == "PERSISTING_EVIDENCE" for item in checkpoints)


def test_failed_sql_session_cannot_publish_staged_business_rows_during_step_reporting(run40_engine):
    with Session(run40_engine) as db:
        db.add(UploadRun(id=702, filename="failed-transaction.csv", status="COMPLETED"))
        db.flush()
        _, pipeline_id = bound_pipeline(db, 702, list(resolve_pipeline_configurations(db).values()))
        pipeline = db.get(PipelineRun, pipeline_id)
        db.add(
            PipelineStep(
                pipeline_run_id=pipeline_id,
                step_order=1,
                step_name="EVALUATING_SETUP_LIFECYCLES",
                status="PENDING",
            )
        )
        db.commit()
        with pytest.raises(DataError, match="division by zero"):
            with _pipeline_step(db, pipeline, "EVALUATING_SETUP_LIFECYCLES"):
                db.add(
                    RawCompanyRow(
                        run_id=702,
                        row_number=1,
                        ticker="ROLLBACK",
                        raw_json={},
                    )
                )
                db.flush()
                db.execute(text("SELECT 1/0"))
        with Session(run40_engine) as observer:
            assert observer.scalar(select(func.count()).select_from(RawCompanyRow)) == 0
            step = observer.scalar(
                select(PipelineStep).where(PipelineStep.pipeline_run_id == pipeline_id)
            )
            assert step.status == "FAILED"
            assert "division by zero" in step.error_message


def test_healthy_watchdog_delay_cannot_block_heartbeat_and_true_stall_still_fences(run40_engine):
    now = datetime.now(UTC)
    with Session(run40_engine) as db:
        db.add(
            BackgroundJob(
                id=1,
                job_type="FULL_PIPELINE",
                status="RUNNING",
                execution_token="witness",
                worker_id="run40-worker",
                payload_json={},
                last_progress_at=now,
                progress_sequence=246,
                progress_stage="EVALUATING_SETUP_LIFECYCLES",
                heartbeat_at=now,
                lease_expires_at=now + timedelta(minutes=15),
            )
        )
        db.commit()
    paused, release = Event(), Event()

    def supervisor():
        with Session(run40_engine) as db:
            assert (
                fence_stalled_jobs(
                    db,
                    authority=RecoveryAuthority.normal("run40.healthy.watchdog"),
                    default_timeout_seconds=60,
                    market_data_timeout_seconds=120,
                    long_stage_timeout_seconds=1800,
                    now=now,
                )
                == []
            )
            assert not db.dirty
            paused.set()
            assert release.wait(10)
            db.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(supervisor)
        assert paused.wait(10)
        try:
            with Session(run40_engine) as db:
                # Wait beyond a deliberately short heartbeat lock budget while
                # the healthy supervisor transaction remains uncommitted.
                with control_plane_transaction(db, lock_timeout_seconds=0.1):
                    heartbeat_job(
                        db,
                        BackgroundJob(id=1, status="RUNNING", execution_token="witness"),
                        execution_token="witness",
                    )
        finally:
            release.set()
        task.result(timeout=10)
    with Session(run40_engine) as db:
        job = db.get(BackgroundJob, 1)
        assert job.status == "RUNNING" and job.execution_token == "witness"
        job.last_progress_at = now - timedelta(minutes=31)
        db.commit()
        assert fence_stalled_jobs(
            db,
            authority=RecoveryAuthority.normal("run40.stalled.watchdog"),
            default_timeout_seconds=60,
            market_data_timeout_seconds=120,
            long_stage_timeout_seconds=1800,
            now=now,
        ) == [1]
        db.commit()
        assert db.get(BackgroundJob, 1).status == "STALLED"
        assert db.get(BackgroundJob, 1).execution_token is None


@pytest.mark.parametrize("population", [2, 88])
def test_native_lifecycle_interruption_replay_and_scale(run40_engine, population, record_property):
    tickers = tuple(f"R{index:03d}" for index in range(population))
    with Session(run40_engine) as db:
        history_lengths = [1] * population
        sessions = [16]
        if population == 88:
            # Run 40 retained one through seven prior observations per ticker.
            # This distribution gives 321 native, certified historical snapshots.
            history_lengths = (
                [1] * 13 + [2] * 11 + [3] * 20 + [4] * 13 + [5] * 15 + [6] * 13 + [7] * 3
            )
            sessions = [9, 10, 11, 14, 15, 16, 17]
        for index, day in enumerate(sessions):
            prior_id = 801 + index
            members = tuple(
                ticker
                for ticker, length in zip(tickers, history_lengths, strict=True)
                if length >= len(sessions) - index
            )
            prior_cutoff, prior_pipeline = _seed_universe(
                db,
                run_id=prior_id,
                tickers=members,
                cutoff_at=datetime(2026, 9, day, 21, tzinfo=UTC),
            )
            prior_capture = _capture(db, prior_id, prior_cutoff)
            prior = SetupLifecycleEvaluationService().evaluate_run(
                db,
                prior_id,
                capture_result=prior_capture,
                market_cutoff=prior_cutoff,
                pipeline_run_id=prior_pipeline,
            )
            assert prior.failed == 0
            db.commit()
        winner_config = None
        if population == 88:
            winner_config = load_winner_probability_config()
            winner_config = replace(
                winner_config, engine=replace(winner_config.engine, enabled=True)
            )
        cutoff, pipeline_id = _seed_universe(
            db,
            run_id=702,
            tickers=tickers,
            cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
            winner_config=winner_config,
        )
        capture = _capture(db, 702, cutoff)
        _certify_native_publication(
            db,
            run40_engine,
            population=population,
            history_lengths=history_lengths,
            cutoff=cutoff,
            pipeline_id=pipeline_id,
            capture=capture,
            winner_config=winner_config,
            record_property=record_property,
        )


def _certify_native_publication(
    db,
    run40_engine,
    *,
    population,
    history_lengths,
    cutoff,
    pipeline_id,
    capture,
    winner_config,
    record_property,
):
    before = _counts(db)
    db.commit()

    def interrupt_canonicalization(**progress):
        if progress["phase"] == "CANONICALIZATION_BATCH_COMPLETED":
            assert (
                _counts(db)["setup_signal_snapshot_current_selections"]
                > before["setup_signal_snapshot_current_selections"]
            )
            with Session(run40_engine) as observer:
                assert _counts(observer) == before
            raise ControlPlaneLockTimeout("run40 injected canonicalization interruption")

    with pytest.raises(ControlPlaneLockTimeout, match="canonicalization interruption"):
        SetupLifecycleEvaluationService().evaluate_run(
            db,
            702,
            capture_result=capture,
            market_cutoff=cutoff,
            pipeline_run_id=pipeline_id,
            progress_callback=interrupt_canonicalization,
        )
    assert _counts(db) == before
    db.rollback()

    def interrupt(**progress):
        if progress["phase"] == "CHANGE_DETECTION_COMPLETED":
            assert (
                db.scalar(select(func.count()).select_from(SignalChangeEvent))
                > before["signal_change_events"]
            )
            with Session(run40_engine) as observer:
                assert _counts(observer) == before
            raise ControlPlaneLockTimeout("run40 injected post-detector interruption")

    with pytest.raises(ControlPlaneLockTimeout, match="injected"):
        SetupLifecycleEvaluationService().evaluate_run(
            db,
            702,
            capture_result=capture,
            market_cutoff=cutoff,
            pipeline_run_id=pipeline_id,
            progress_callback=interrupt,
        )
    assert _counts(db) == before
    assert (
        db.scalar(
            select(SetupLifecycleEvaluationRun.status).where(
                SetupLifecycleEvaluationRun.source_run_id == 702,
                SetupLifecycleEvaluationRun.current_phase == "failed",
            )
        )
        == "FAILED"
    )
    db.rollback()
    sql = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        sql.append(statement)

    progress = []
    event.listen(run40_engine, "before_cursor_execute", observe)
    profile = cProfile.Profile() if os.environ.get("RUN40_PROFILE") == "1" else None
    started = perf_counter()
    try:
        if profile is not None:
            profile.enable()
        result = SetupLifecycleEvaluationService().evaluate_run(
            db,
            702,
            capture_result=capture,
            market_cutoff=cutoff,
            pipeline_run_id=pipeline_id,
            progress_callback=lambda **p: progress.append(p),
        )
        db.commit()
    finally:
        if profile is not None:
            profile.disable()
        elapsed = perf_counter() - started
        event.remove(run40_engine, "before_cursor_execute", observe)
    assert result.failed == 0 and result.canonical_snapshots == population
    assert result.change_events > 0
    published = _counts(db)
    retry = SetupLifecycleEvaluationService().evaluate_run(
        db,
        702,
        capture_result=capture,
        market_cutoff=cutoff,
        pipeline_run_id=pipeline_id,
    )
    db.commit()
    assert retry.failed == 0 and retry.change_events == 0
    assert _counts(db) == published
    winner = None
    if population == 88:
        handoff = freeze_transition_decision_handoff_manifest(
            db, upload_run_id=702, market_cutoff=cutoff
        )
        handoff_id = handoff.id
        db.commit()
        winner = WinnerPredictionCaptureService().capture_run(
            db,
            run_id=702,
            market_cutoff=cutoff,
            config=winner_config,
            decision_handoff_manifest_id=handoff_id,
        )
        db.commit()
        assert winner.failed == 0 and winner.inserted == population, winner
    measurement = {
        "population": population,
        "evaluation_seconds": round(elapsed, 3),
        "sql_operations": len(sql),
        "changes": result.change_events,
        "history_lengths": history_lengths,
        "published_counts": published,
        "winner": str(winner) if winner is not None else None,
        "progress_phases": [item["phase"] for item in progress],
    }
    record_property("run40_measurement", json.dumps(measurement))
    directory = Path(
        os.environ.get("RUN40_ARTIFACT_DIR", "docs/remediation/run40-lifecycle/evidence")
    )
    directory.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get("RUN40_BENCHMARK_LABEL", "after")
    if profile is not None:
        profile.dump_stats(str(directory / f"{mode}-{population}.prof"))
    (directory / f"{mode}-{population}.json").write_text(json.dumps(measurement, indent=2))


@pytest.mark.skipif(
    not os.environ.get("RUN40_NATIVE_FIXTURE_DUMP"),
    reason="Explicit retained native fixture required for final-runtime recheck",
)
def test_retained_native_88_fixture_on_final_runtime(disposable_postgres_database, record_property):
    from sqlalchemy.engine import make_url

    from app.database_safety import assert_disposable_database
    from app.services.configuration_delivery import binding_reference, load_configuration_delivery
    from app.services.market_calculation_context_service import market_context_for_pipeline

    dump = Path(os.environ["RUN40_NATIVE_FIXTURE_DUMP"]).resolve()
    metadata = json.loads(dump.with_suffix(".json").read_text())
    assert hashlib.sha256(dump.read_bytes()).hexdigest() == metadata["sha256"]
    assert metadata["database"] == "swinglens_pytest_69284b2e2a2c"
    assert_disposable_database(disposable_postgres_database)
    target = make_url(disposable_postgres_database)
    environment = dict(os.environ)
    environment.update(
        PGHOST=target.host,
        PGPORT=str(target.port or 5432),
        PGUSER=target.username,
        PGPASSWORD=target.password or "",
        PGDATABASE=target.database,
    )
    restored = subprocess.run(
        [
            r"C:\Program Files\PostgreSQL\18\bin\pg_restore.exe",
            "--no-owner",
            "--no-acl",
            "--no-password",
            "--dbname",
            target.database,
            "--clean",
            "--if-exists",
            "--exit-on-error",
            str(dump),
        ],
        env=environment,
        capture_output=True,
    )
    assert restored.returncode == 0, "Restoring the disposable native fixture failed"
    engine = create_engine(disposable_postgres_database, pool_size=8)
    try:
        with Session(engine) as db:
            history_lengths = (
                [1] * 13 + [2] * 11 + [3] * 20 + [4] * 13 + [5] * 15 + [6] * 13 + [7] * 3
            )
            history = dict(
                db.execute(
                    select(SetupSignalSnapshot.ticker, func.count())
                    .where(SetupSignalSnapshot.run_id.between(801, 807))
                    .group_by(SetupSignalSnapshot.ticker)
                ).all()
            )
            assert history == {
                f"R{index:03d}": length for index, length in enumerate(history_lengths)
            }
            assert sum(history.values()) == 321
            pipeline = db.scalar(select(PipelineRun).where(PipelineRun.upload_run_id == 702))
            assert pipeline is not None
            assert pipeline.execution_authority_state == "ACTIVE"
            cutoff = market_context_for_pipeline(db, pipeline)
            delivery = load_configuration_delivery(
                db, binding_reference(db, pipeline_run_id=pipeline.id)
            )
            winner_config = delivery.configurations["decision.winner.prediction"].winner_config()
            assert winner_config.engine.enabled
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(SetupSignalSnapshot)
                    .where(SetupSignalSnapshot.run_id == 702)
                )
                == 0
            )
            capture = _capture(db, 702, cutoff)
            _certify_native_publication(
                db,
                engine,
                population=88,
                history_lengths=history_lengths,
                cutoff=cutoff,
                pipeline_id=pipeline.id,
                capture=capture,
                winner_config=winner_config,
                record_property=record_property,
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("legacy_history", [False, True])
def test_native_winner_continuation_fixture_contract(
    run40_engine, legacy_history, tmp_path, monkeypatch
):
    import io
    import logging

    import yaml

    profile = yaml.safe_load(Path("config/winner_probability.yaml").read_text())
    profile["engine"]["enabled"] = True
    profile_path = tmp_path / "winner_probability.yaml"
    profile_path.write_text(yaml.safe_dump(profile))
    config = load_winner_probability_config(profile_path)
    with Session(run40_engine) as db:
        if legacy_history:
            monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
            from single_run_certification.fixtures import _seed_winner_history

            db.add(UploadRun(id=701, filename="retained-winner-history.csv", status="COMPLETED"))
            db.flush()
            _seed_winner_history(db, 701, configuration_path=profile_path)
            db.commit()
        cutoff, pipeline_id = _seed_universe(
            db,
            run_id=702,
            tickers=("WINNER",),
            cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
            winner_config=config,
        )
        capture = _capture(db, 702, cutoff)
        evaluated = SetupLifecycleEvaluationService().evaluate_run(
            db, 702, capture_result=capture, market_cutoff=cutoff, pipeline_run_id=pipeline_id
        )
        db.commit()
        assert evaluated.failed == 0
        from app.models.tables import CoreCalculationEvidence, SectorRotationSnapshot
        from app.services.core_mutation_authority import validate_source_values

        sector = db.scalar(
            select(SectorRotationSnapshot).where(SectorRotationSnapshot.run_id == 702)
        )
        detached_sector = SectorRotationSnapshot(
            **{column.key: getattr(sector, column.key) for column in sector.__table__.columns}
        )
        validate_source_values(
            detached_sector, db.get(CoreCalculationEvidence, sector.evidence_id), db=db
        )
        with pytest.raises(ValueError, match="MUTATION_SOURCE_SECTOR_SESSION_REQUIRED"):
            validate_source_values(
                detached_sector, db.get(CoreCalculationEvidence, sector.evidence_id)
            )
        assert "sector_final_score" not in {
            column.key for column in SectorRotationSnapshot.__table__.columns
        }
        sector_evidence_id = sector.evidence_id
        db.connection().exec_driver_sql(
            "UPDATE sector_rotation_rows "
            "SET sector_final_score=coalesce(sector_final_score,0)+1 WHERE snapshot_id=%s",
            (sector.id,),
        )
        db.expire_all()
        with pytest.raises(ValueError, match="MUTATION_SOURCE_FINANCIAL_VALUE_MISMATCH"):
            validate_source_values(
                detached_sector, db.get(CoreCalculationEvidence, sector_evidence_id), db=db
            )
        db.rollback()
        handoff = freeze_transition_decision_handoff_manifest(
            db, upload_run_id=702, market_cutoff=cutoff
        )
        handoff_id = handoff.id
        db.commit()
        errors = io.StringIO()
        handler = logging.StreamHandler(errors)
        logger = logging.getLogger("app.services.winner_probability.capture_service")
        logger.addHandler(handler)
        try:
            winner = WinnerPredictionCaptureService().capture_run(
                db,
                run_id=702,
                market_cutoff=cutoff,
                config=config,
                decision_handoff_manifest_id=handoff_id,
            )
        finally:
            logger.removeHandler(handler)
        db.commit()
        if winner.failed:
            artifact = Path("docs/remediation/run40-lifecycle/evidence/winner-history-stack.txt")
            artifact.write_text(errors.getvalue())
        assert winner.failed == 0 and winner.inserted == 1, errors.getvalue() or {
            "exclusions": winner.as_dict()["exclusion_reasons"],
            "failures": winner.as_dict()["representative_failures"],
        }


@pytest.mark.parametrize("outcome", ["retry", "continuation", "exhausted", "cancelled", "terminal"])
def test_real_worker_infrastructure_retry_claim_exhaustion_and_cancel(
    run40_engine, outcome, monkeypatch
):
    monkeypatch.setattr(
        "app.services.background_job_service.default_retry_delay", lambda _attempt: timedelta(0)
    )
    with Session(run40_engine) as db:
        capture = None
        cutoff = None
        if outcome in {"retry", "continuation"}:
            prior_cutoff, prior_pipeline = _seed_universe(
                db,
                run_id=701,
                tickers=("RETRY",),
                cutoff_at=datetime(2026, 9, 16, 21, tzinfo=UTC),
            )
            prior_capture = _capture(db, 701, prior_cutoff)
            SetupLifecycleEvaluationService().evaluate_run(
                db,
                701,
                capture_result=prior_capture,
                market_cutoff=prior_cutoff,
                pipeline_run_id=prior_pipeline,
            )
            db.commit()
            cutoff, pipeline_id = _seed_universe(
                db,
                run_id=702,
                tickers=("RETRY",),
                cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
                durable=True,
            )
            capture = _capture(db, 702, cutoff)
        else:
            db.add(UploadRun(id=702, filename="retry-control.csv", status="COMPLETED"))
            db.flush()
            cutoff, pipeline_id = bound_pipeline(
                db, 702, list(resolve_pipeline_configurations(db).values())
            )
        pipeline = db.get(PipelineRun, pipeline_id)
        pipeline.status = "QUEUED"
        pipeline.current_step = "EVALUATING_SETUP_LIFECYCLES"
        db.add(
            PipelineStep(
                pipeline_run_id=pipeline_id,
                step_order=1,
                step_name="EVALUATING_SETUP_LIFECYCLES",
                status="PENDING",
            )
        )
        job = enqueue_job(
            db,
            "FULL_PIPELINE",
            {"pipeline_run_id": pipeline_id},
            related_run_id=702,
            max_retries=0 if outcome == "exhausted" else 2,
        )
        pipeline.result_json = {"background_job_id": job.id}
        if outcome == "continuation":
            root = job
            root.status = "COMPLETED"
            dependency = PipelineDependency(
                pipeline_run_id=pipeline_id,
                dependency_type="CERI_COMPLETION",
                dependency_key="run40-dependency",
                state="COMPLETED",
                required_subjects_json=["RETRY"],
                continuation_step="EVALUATING_SETUP_LIFECYCLES",
                continuation_identity="run40-continuation",
                root_job_id=root.id,
            )
            db.add(dependency)
            db.flush()
            job = enqueue_job(
                db,
                "FULL_PIPELINE",
                {"pipeline_run_id": pipeline_id, "resume_from_step": "EVALUATING_SETUP_LIFECYCLES"},
                related_run_id=702,
                parent_job_id=root.id,
                max_retries=2,
                pipeline_dependency_id=dependency.id,
            )
            dependency.continuation_job_id = job.id
            pipeline.result_json = {"background_job_id": root.id, "continuation_job_id": job.id}
        db.commit()
        job_id = job.id
        before = _counts(db)
    failed_once = False

    def handler(db, job):
        nonlocal failed_once
        pipeline = db.get(PipelineRun, pipeline_id)

        def checkpoint(**progress):
            nonlocal failed_once
            if progress["phase"] == "CHANGE_DETECTION_COMPLETED" and not failed_once:
                assert (
                    db.scalar(select(func.count()).select_from(SignalChangeEvent))
                    > before["signal_change_events"]
                )
                failed_once = True
                raise ControlPlaneLockTimeout("run40 native worker post-detector interruption")

        with _pipeline_step(db, pipeline, "EVALUATING_SETUP_LIFECYCLES"):
            if outcome == "retry" and not failed_once:
                _assert_healthy_worker_survives_delayed_watchdog(run40_engine, job.id)
            if capture is not None:
                result = SetupLifecycleEvaluationService().evaluate_run(
                    db,
                    702,
                    capture_result=capture,
                    market_cutoff=cutoff,
                    pipeline_run_id=pipeline_id,
                    progress_callback=checkpoint,
                )
                assert result.failed == 0
            else:
                if outcome in {"cancelled", "terminal"}:

                    def external_cancel():
                        with Session(run40_engine) as control:
                            cancel_pipeline(control, pipeline_id)
                            control.commit()
                            if outcome == "terminal":
                                parent = control.get(PipelineRun, pipeline_id)
                                transition_pipeline(
                                    control, parent, "CANCELLED", actor="pipeline_orchestrator"
                                )
                                control.commit()

                    # HTTP cancellation executes outside the worker's ambient
                    # domain ownership ContextVars.
                    with ThreadPoolExecutor(max_workers=1) as controller:
                        controller.submit(external_cancel).result(timeout=10)
                raise ControlPlaneLockTimeout("run40 control interruption")
        transition_pipeline(db, pipeline, "COMPLETED", actor="pipeline_orchestrator")
        db.commit()
        return {"native_lifecycle_completed": True}

    kwargs = dict(
        stale_after_seconds=900,
        session_factory=sessionmaker(run40_engine),
        handlers={"FULL_PIPELINE": handler},
        included_job_types=("FULL_PIPELINE",),
        heartbeat_interval_seconds=0.2,
    )
    with pytest.raises(ControlPlaneLockTimeout):
        run_worker_once(
            worker_id="run40-first", worker_instance_id="run40-first-instance", **kwargs
        )
    with Session(run40_engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        job = db.get(BackgroundJob, job_id)
        assert _counts(db) == before
        if outcome in {"retry", "continuation"}:
            assert pipeline.status == "QUEUED" and job.status == "QUEUED"
            assert claimable_pipeline_job_ids(db, upload_run_id=702) == (job_id,)
            assert job.payload_json["resume_from_step"] == "EVALUATING_SETUP_LIFECYCLES"
        elif outcome == "exhausted":
            assert pipeline.status == "FAILED" and job.status == "FAILED"
            assert pipeline.error_message == "CONTROL_PLANE_RETRIES_EXHAUSTED"
        else:
            assert pipeline.status == "CANCELLED" and job.status == "CANCELLED"
        if outcome not in {"retry", "continuation"}:
            assert claimable_pipeline_job_ids(db, upload_run_id=702) == ()
            return
    assert run_worker_once(
        worker_id="run40-second", worker_instance_id="run40-second-instance", **kwargs
    )
    with Session(run40_engine) as db:
        assert db.get(PipelineRun, pipeline_id).status == "COMPLETED"
        completed = db.get(BackgroundJob, job_id)
        assert completed.status == "COMPLETED" and completed.retry_count == 1
        assert claimable_pipeline_job_ids(db, upload_run_id=702) == ()
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == (
            2 if outcome == "continuation" else 1
        )
        step = db.scalar(select(PipelineStep).where(PipelineStep.pipeline_run_id == pipeline_id))
        assert step.status == "COMPLETED" and step.retry_count == 1
        assert step.result_json["attempt_history"][0]["status"] == "INTERRUPTED"


def _assert_healthy_worker_survives_delayed_watchdog(engine, job_id):
    paused, release = Event(), Event()
    with Session(engine) as observer:
        before = observer.get(BackgroundJob, job_id).heartbeat_at

    def supervisor():
        with Session(engine) as control:
            assert (
                fence_stalled_jobs(
                    control,
                    authority=RecoveryAuthority.normal("run40.delayed-supervisor"),
                    default_timeout_seconds=60,
                    market_data_timeout_seconds=120,
                    long_stage_timeout_seconds=1800,
                )
                == []
            )
            assert not control.dirty
            paused.set()
            assert release.wait(15)
            control.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        delayed = pool.submit(supervisor)
        assert paused.wait(10)
        try:
            # Exceed the unchanged production control-plane lock timeout (3s).
            sleep(3.4)
            with Session(engine) as observer:
                running = observer.get(BackgroundJob, job_id)
                assert running.status == "RUNNING" and running.execution_token
                assert running.heartbeat_at > before
        finally:
            release.set()
        delayed.result(timeout=10)


def test_legacy_partial_publication_reuses_original_proof_and_processes_alerts(run40_engine):
    from copy import deepcopy

    from app.models.tables import SetupSignalSnapshot
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.setup_lifecycle.change_authority import (
        change_source_validation_scope,
        validate_signal_change,
    )
    from app.services.setup_lifecycle.config import (
        AlertRuleConfig,
        load_setup_lifecycle_config,
        setup_lifecycle_config_hash,
    )
    from app.services.setup_lifecycle.enums import EventSeverity
    from app.services.setup_lifecycle.repository import (
        SetupLifecycleRepository,
        current_canonical_snapshot_predicate,
        published_lifecycle_select,
    )

    config = load_setup_lifecycle_config()
    rule = AlertRuleConfig(
        rule_id="NATIVE_REPLAY_TEST",
        enabled=True,
        severity=EventSeverity.NOTABLE,
        source="signal_change",
        cooldown_sessions=1,
        minimum_confidence=0,
        filters={"signal_keys": ["close_price", "technical_score", "setup_score"]},
    )
    config = replace(
        config, alerts=replace(config.alerts, rules={**config.alerts.rules, rule.rule_id: rule})
    )
    config = replace(config, config_hash=setup_lifecycle_config_hash(config))
    repository = SetupLifecycleRepository(config)
    service = SetupLifecycleEvaluationService(config=config, repository=repository)
    with Session(run40_engine) as db:
        rules = service.alert_service.seed_builtin_rules(db)
        service.alert_service._prepare_rules(db, rules)
        frozen_alerts = service.alert_service.effective_configuration
        db.commit()
        prior_cutoff, prior_pipeline = _seed_universe(
            db,
            run_id=701,
            tickers=("LEGACY",),
            cutoff_at=datetime(2026, 9, 16, 21, tzinfo=UTC),
            lifecycle_config=config,
            alert_configuration=frozen_alerts,
        )
        prior_capture = _capture(db, 701, prior_cutoff, config)
        service.evaluate_run(
            db,
            701,
            capture_result=prior_capture,
            market_cutoff=prior_cutoff,
            pipeline_run_id=prior_pipeline,
        )
        db.commit()
        cutoff, pipeline_id = _seed_universe(
            db,
            run_id=702,
            tickers=("LEGACY",),
            cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
            lifecycle_config=config,
            alert_configuration=frozen_alerts,
        )
        capture = _capture(db, 702, cutoff, config)

        def old_failure_boundary(**progress):
            if progress["phase"] == "CHANGE_DETECTION_COMPLETED":
                db.commit()
                raise ControlPlaneLockTimeout("legacy committed detector boundary")

        # Reconstruct the old commit boundary only in this disposable fixture.
        # Production always enters through evaluate_run's owning transaction.
        with pytest.raises(ControlPlaneLockTimeout):
            service._evaluate_run.__wrapped__(
                service,
                db,
                702,
                capture_result=capture,
                market_cutoff=cutoff,
                pipeline_run_id=pipeline_id,
                progress_callback=old_failure_boundary,
            )
        failed = db.scalar(
            select(SetupLifecycleEvaluationRun).where(
                SetupLifecycleEvaluationRun.source_run_id == 702,
                SetupLifecycleEvaluationRun.status == "RUNNING",
            )
        )
        repository.complete_evaluation_run(
            db,
            failed,
            status="FAILED",
            current_phase="failed",
            counts={"failed": 1},
            errors={"system": "retained old failure"},
        )
        failed_id = failed.id
        db.commit()
        retained = list(
            db.scalars(
                select(SignalChangeEvent).where(SignalChangeEvent.evaluation_run_id == failed_id)
            )
        )
        assert retained
        original = {
            row.id: Canonical.dumps({"body": row.evidence_json, "creator": row.evaluation_run_id})
            for row in retained
        }
        with Session(run40_engine) as observer:
            assert not list(
                observer.scalars(
                    published_lifecycle_select(SignalChangeEvent).where(
                        SignalChangeEvent.evaluation_run_id == failed_id
                    )
                )
            )
            assert not list(
                observer.scalars(
                    select(SetupSignalSnapshot).where(
                        SetupSignalSnapshot.run_id == 702, current_canonical_snapshot_predicate()
                    )
                )
            )

        previous = db.get(SetupSignalSnapshot, prior_capture.snapshot_ids[0])
        current = db.get(SetupSignalSnapshot, capture.snapshot_ids[0])
        # A cached source cannot survive an untracked JSON edit or transaction loss.
        saved_features = deepcopy(current.signals_json)
        with pytest.raises(ValueError, match="MUTATION|SOURCE"):
            with change_source_validation_scope(
                db,
                previous=previous,
                current=current,
                history=(previous,),
                configuration=service.effective_configuration,
            ):
                current.signals_json["forged"] = True
        current.signals_json = saved_features
        db.rollback()
        with pytest.raises(ValueError, match="MUTATION_CHANGE_PROJECTION_PIN_MISMATCH"):
            with change_source_validation_scope(
                db,
                previous=previous,
                current=current,
                history=(previous,),
                configuration=service.effective_configuration,
            ) as manifest:
                manifest["snapshot_projections"][str(current.id)]["setup_evidence_id"] += 1
                validate_signal_change(
                    db,
                    retained[0],
                    configuration=service.effective_configuration,
                    source_manifest=manifest,
                )
        db.rollback()
        with pytest.raises(ValueError, match="MUTATION|SOURCE"):
            with change_source_validation_scope(
                db,
                previous=previous,
                current=current,
                history=(previous,),
                configuration=service.effective_configuration,
            ):
                db.connection().exec_driver_sql(
                    "UPDATE setup_signal_snapshots SET close_price=close_price+1 WHERE id=%s",
                    (current.id,),
                )
        db.rollback()
        with pytest.raises(ValueError, match="TRANSACTION_CHANGED"):
            with change_source_validation_scope(
                db,
                previous=previous,
                current=current,
                history=(previous,),
                configuration=service.effective_configuration,
            ):
                db.rollback()

        result = service.evaluate_run(
            db, 702, capture_result=capture, market_cutoff=cutoff, pipeline_run_id=pipeline_id
        )
        db.commit()
        assert result.failed == 0 and result.change_events == 0
        assert db.get(SetupLifecycleEvaluationRun, failed_id).status == "FAILED"
        assert {
            row.id: Canonical.dumps({"body": row.evidence_json, "creator": row.evaluation_run_id})
            for row in db.scalars(
                select(SignalChangeEvent).where(SignalChangeEvent.evaluation_run_id == failed_id)
            )
        } == original
        alerts = list(
            db.scalars(
                published_lifecycle_select(SignalAlertEvent).where(
                    SignalAlertEvent.evidence_json["rule_id"].astext == rule.rule_id
                )
            )
        )
        assert alerts, [(row.signal_key, row.evidence_json) for row in retained]
        assert {row.signal_change_event_id for row in alerts} <= set(original)
        published = _counts(db)
        for boundary in ("episode:", "alert:"):
            observed = []

            def cancel_completed_replay(*, _boundary=boundary, _observed=observed, **progress):
                if (progress.get("current_item") or "").startswith(_boundary):
                    _observed.append(progress["current_item"])
                    raise SetupLifecycleEvaluationCancelled(f"stop replay after {_boundary}")

            with pytest.raises(SetupLifecycleEvaluationCancelled, match="stop replay after"):
                service.evaluate_run(
                    db,
                    702,
                    capture_result=capture,
                    market_cutoff=cutoff,
                    pipeline_run_id=pipeline_id,
                    progress_callback=cancel_completed_replay,
                )
            assert observed, boundary
            assert _counts(db) == published
            assert (
                db.get(SetupLifecycleEvaluationRun, result.evaluation_run_id).status == "COMPLETED"
            )
            db.rollback()
        service.evaluate_run(
            db, 702, capture_result=capture, market_cutoff=cutoff, pipeline_run_id=pipeline_id
        )
        db.commit()
        assert _counts(db) == published
        # A newer native canonical revision cannot invalidate an earlier
        # completed publication's immutable selection/evidence references.
        newer_cutoff, _ = _seed_universe(
            db,
            run_id=703,
            tickers=("LEGACY",),
            cutoff_at=cutoff.cutoff_at,
            lifecycle_config=config,
            alert_configuration=frozen_alerts,
        )
        newer = _capture(db, 703, newer_cutoff, config)
        service.canonicalizer.canonicalize_run(db, run_id=703, snapshot_ids=newer.snapshot_ids)
        db.commit()
        pointer = db.scalar(
            select(SetupSignalSnapshotCurrentSelection).where(
                SetupSignalSnapshotCurrentSelection.ticker == "LEGACY",
                SetupSignalSnapshotCurrentSelection.data_as_of_date
                == cutoff.latest_completed_session,
            )
        )
        assert pointer.selected_snapshot_id == newer.snapshot_ids[0]
        superseded = _counts(db)
        replay = service.evaluate_run(
            db,
            702,
            capture_result=capture,
            market_cutoff=cutoff,
            pipeline_run_id=pipeline_id,
        )
        db.commit()
        assert replay.evaluation_run_id == result.evaluation_run_id
        assert _counts(db) == superseded
        # Proof-bearing retained events still reject an actual content mutation.
        changed = db.get(SignalChangeEvent, retained[0].id)
        changed.new_value_json = {"forged": True}
        with pytest.raises(ValueError, match="MUTATION"):
            service.evaluate_run(
                db, 702, capture_result=capture, market_cutoff=cutoff, pipeline_run_id=pipeline_id
            )
