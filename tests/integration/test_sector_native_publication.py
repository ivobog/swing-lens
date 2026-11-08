"""Real financial pipeline and manual sector refresh in the same owned database."""

import csv
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from alembic.config import Config
from evidence_output import output_directory
from fastapi import UploadFile
from sector_native_helpers import native_worker
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import BackgroundJob, IBContract, PipelineRun, PriceBar
from app.settings import get_settings


@pytest.mark.slow
@pytest.mark.destructive
def test_native_financial_publication_during_sector_refresh(
    disposable_postgres_database, monkeypatch
):
    sys.path.insert(0, str(Path(__file__).parents[1] / "e2e"))
    from single_run_certification.fixtures import (
        _seed_price_bars,
        _seed_setup_lifecycle_baseline,
        certification_as_of_session,
        seed_prerequisites,
        write_canonical_csv,
    )
    from single_run_certification.test_real_writer_cardinality import (
        EXACT_TEN,
        _seed_retained_provider_history,
    )
    from single_run_certification.test_single_run_certification import (
        _activate_disposable_sec_processor,
        _seed_disposable_sec_readiness,
    )

    from app.services import market_calculation_context_service as contexts
    from app.services import market_sector_refresh_service as refresh
    from app.services.upload_service import create_upload_run

    root = output_directory("nsp")
    root.mkdir(parents=True, exist_ok=True)
    profile = yaml.safe_load(Path("config/winner_probability.yaml").read_text())
    profile["engine"]["enabled"] = True
    profile_path = root / "winner_probability.yaml"
    profile_path.write_text(yaml.safe_dump(profile))
    source_root = Path.cwd()
    runtime_root = root / "runtime"
    shutil.copytree(source_root / "config", runtime_root / "config")
    shutil.copyfile(profile_path, runtime_root / "config/winner_probability.yaml")
    environment = {
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
        "DB_MONITOR_ENABLED": "false",
        "RUNTIME_MODE": "NORMAL",
        "JOB_ACTIVATION_MIN_ID": "0",
        "PIPELINE_ACTIVATION_MIN_ID": "0",
        "JOB_WORKER_ENABLED": "false",
        "MARKET_DATA_PREWARM_ENABLED": "false",
        "WINNER_PROBABILITY_ENABLED": "true",
        "WINNER_PROBABILITY_CAPTURE_IN_PIPELINE": "true",
        "WINNER_PROBABILITY_AUTO_MATURATION_ENABLED": "false",
        "WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED": "false",
        "SETUP_LIFECYCLE_ENABLED": "true",
        "SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED": "true",
        "SETUP_CAPTURE_HANDOFF_ENABLED": "true",
        "CERI_ENABLED": "true",
        "CERI_RUN_CAPTURE_ENABLED": "true",
        "CERI_PROVIDER_INGEST_ENABLED": "true",
        "CERI_LEGACY_PIPELINE_SCHEDULING_ENABLED": "false",
        "CERI_BATCHED_WORKFLOW_ENABLED": "true",
        "SEC_DOCUMENT_INCREMENTAL_MODE": "OFF",
        "IB_REQUESTS_PER_MINUTE": "60",
        "IB_INTELLIGENCE_HISTORICAL_REQUESTS_PER_MINUTE": "60",
        "IB_MIN_SECONDS_BETWEEN_REQUESTS": "0",
        "IB_INTELLIGENCE_HISTORICAL_MIN_SPACING_SECONDS": "0",
        "IB_MAX_RETRIES": "1",
        "IB_REQUEST_DELAY_SECONDS": "0",
        "IB_BACKOFF_SECONDS": "0",
        "OBSERVABILITY_METRICS_ENABLED": "false",
        "TECHNICAL_PROCESS_POOL_ENABLED": "false",
        "TECHNICAL_PURE_BOUNDARY_ENABLED": "true",
        "TECHNICAL_ARTIFACT_CACHE_ENABLED": "true",
        "TECHNICAL_ARTIFACT_CACHE_WRITE_ENABLED": "true",
        "TECHNICAL_SERIES_VERSION_MAINTENANCE_ENABLED": "true",
        "WORKER_MEMORY_WARNING_MB": "8192",
        "WORKER_MEMORY_CRITICAL_MB": "12288",
        "UPLOAD_DIR": str(root / "uploads"),
        "NATIVE_WINNER_CONFIG": str(profile_path),
        "NATIVE_RUNTIME_DIR": str(runtime_root),
        "NATIVE_PUBLICATION": "true",
        "PYTHONPATH": os.pathsep.join(
            (str(Path.cwd()), str(Path.cwd() / "tests"), str(Path.cwd() / "tests/e2e"))
        ),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database)
    monkeypatch.chdir(runtime_root)
    seed = seed_prerequisites(disposable_postgres_database, winner_configuration_path=profile_path)
    _activate_disposable_sec_processor(disposable_postgres_database)
    _seed_disposable_sec_readiness(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    symbols = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
    with Session(engine) as db:
        db.add_all(
            IBContract(
                ticker=symbol,
                ib_conid=980000 + index,
                symbol=symbol,
                exchange="SMART",
                primary_exchange="NYSE",
                currency="USD",
                sec_type="STK",
                resolution_status="RESOLVED",
            )
            for index, symbol in enumerate(symbols)
        )
        db.add_all(
            IBContract(
                ticker=ticker,
                ib_conid=9500000 + index,
                symbol=ticker,
                exchange="SMART",
                primary_exchange="NASDAQ",
                currency="USD",
                sec_type="STK",
                resolution_status="RESOLVED",
            )
            for index, ticker in enumerate(EXACT_TEN)
        )
        _seed_setup_lifecycle_baseline(
            db,
            run_id=seed.decoy_run_id,
            as_of_session=certification_as_of_session(),
            tickers=EXACT_TEN,
        )
        _seed_price_bars(db, tickers=EXACT_TEN[1:], as_of_session=certification_as_of_session())
        _seed_price_bars(
            db, tickers=EXACT_TEN[:1], as_of_session=certification_as_of_session(), bar_count=180
        )
        db.commit()
    csv_path = root / "universe.csv"
    write_canonical_csv(csv_path)
    with csv_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        exemplar = next(reader)
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for ticker in EXACT_TEN:
            writer.writerow(
                {**exemplar, "Symbol": ticker, "Description": ticker + " native fixture"}
            )
    with csv_path.open("rb") as stream, Session(engine) as db:
        upload = create_upload_run(db, UploadFile(filename="native.csv", file=stream))
        run_id = upload.id
    _seed_retained_provider_history(engine, tickers=EXACT_TEN)
    # Every case starts from this exact, already-received input/sequence state.
    # These are disposable database snapshots, never financial expectations.
    from single_run_certification.fixtures import _ohlcv

    from app.services.bar_cache_service import cache_bars
    from app.services.ib_data_fetcher import HistoricalBar

    with Session(engine) as db:
        for symbol in (*symbols, "SPY", "QQQ"):
            for basis in ("ADJUSTED_LAST", "TRADES"):
                bars = [
                    HistoricalBar(
                        ticker=symbol,
                        bar_date=row["date"],
                        timeframe="1 day",
                        **{key: row[key] for key in ("open", "high", "low", "close", "volume")},
                        source="QA_NATIVE_COHORT",
                        what_to_show=basis,
                        adjustment_type="adjusted" if basis == "ADJUSTED_LAST" else "raw",
                    )
                    for row in _ohlcv(symbol, 1000, as_of_session=certification_as_of_session())
                ]
                cache_bars(db, bars)
        db.commit()
    frozen_at = datetime.now(UTC)
    engine.dispose()
    seed_dump = root / "frozen-inputs.dump"
    _database_snapshot(disposable_postgres_database, seed_dump)
    import hashlib

    monkeypatch.setenv(
        "MSI_NATIVE_PERF_INPUT_SHA256", hashlib.sha256(seed_dump.read_bytes()).hexdigest()
    )
    monkeypatch.setenv("MSI_NATIVE_PERF_EXECUTION_ID", root.parents[2].name)
    (root / "fixture-input.json").write_text(
        json.dumps(
            {
                "database": engine.url.database,
                "frozen_at": frozen_at,
                "snapshot_sha256": os.environ["MSI_NATIVE_PERF_INPUT_SHA256"],
                "symbols": [*symbols, "SPY", "QQQ"],
                "bases": ["ADJUSTED_LAST", "TRADES"],
                "sessions": 1000,
                "native_stock_fixture": "EXACT_TEN",
                "runtime_flags": environment,
            },
            default=str,
            indent=2,
        )
    )
    from sector_native_evidence import capture_result

    native_context = contexts.create_pipeline_market_context
    monkeypatch.setattr(
        contexts,
        "create_pipeline_market_context",
        lambda db, pipeline: native_context(db, pipeline, cutoff_at=frozen_at),
    )
    control = None
    orders = [
        ("base", "sent", "candidate"),
        ("sent", "candidate", "base"),
        ("candidate", "base", "sent"),
    ]
    rounds = int(os.environ.get("MSI_NATIVE_PERF_ROUNDS", "0"))
    assert rounds in (0, 3)
    if rounds:
        _measure_frozen_inputs(disposable_postgres_database, frozen_at, orders, root)
    # Nine fresh read processes fit inside the real financial workflow. Rotate
    # variant order across three rounds without holding the financial worker.
    loaded_order = tuple(variant for order in orders for variant in order) if rounds else None
    cases = [("control", None), ("treatment", loaded_order)]
    for label, order in cases:
        _database_snapshot(disposable_postgres_database, seed_dump, restore=True)
        case_root = root / label
        case_root.mkdir()
        receipt = _run_native_case(
            disposable_postgres_database,
            run_id,
            frozen_at,
            environment,
            case_root,
            refresh,
            with_refresh=label != "control",
            perf_order=order,
        )
        engine = create_engine(disposable_postgres_database)
        try:
            result = capture_result(
                engine, run_id, receipt["pipeline_id"], case_root / "financial-evidence.json"
            )
            if control is None:
                control = result
            else:
                assert result == control, "native control equivalence failed; retained SQL: " + str(
                    case_root
                )
        finally:
            engine.dispose()
    get_settings.cache_clear()


def _database_snapshot(url, path, *, restore=False):
    import subprocess

    from sqlalchemy.engine import make_url

    from app.database_safety import assert_disposable_database

    assert_disposable_database(url)
    parsed = make_url(url)
    assert (parsed.database or "").startswith("swinglens_pytest_")
    environment = {**os.environ, "PGPASSWORD": parsed.password or ""}
    common = [
        "--host",
        parsed.host,
        "--port",
        str(parsed.port or 5432),
        "--username",
        parsed.username,
        "--dbname",
        parsed.database,
        "--no-owner",
    ]
    command = (
        ["pg_restore", *common, "--clean", "--if-exists", "--exit-on-error", str(path)]
        if restore
        else ["pg_dump", *common, "--format=custom", "--file", str(path)]
    )
    process = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=120)
    assert process.returncode == 0, process.stderr.replace(
        parsed.password or "<absent>", "[redacted]"
    )


def _run_native_case(
    url, run_id, frozen_at, environment, root, refresh, *, with_refresh, perf_order
):
    from sqlalchemy import func

    from app.models.tables import IBFetchRun
    from app.services.pipeline_service import start_pipeline

    engine = create_engine(url)
    with Session(engine) as db:
        pipeline = start_pipeline(db, run_id, requested_by="native-sector-followup")
        db.commit()
        pipeline_id = pipeline.id
        before = {
            symbol: float(
                db.scalar(
                    select(PriceBar.close)
                    .where(PriceBar.ticker == symbol, PriceBar.what_to_show == "ADJUSTED_LAST")
                    .order_by(PriceBar.bar_date.desc())
                )
            )
            for symbol in ("SPY", "QQQ")
        }
    ctx = get_context("spawn")
    events = {
        name: ctx.Event() for name in ("acquired", "release", "calculating", "sector_provider")
    }
    if not with_refresh:
        for event in events.values():
            event.set()
    general = ctx.Process(
        target=native_worker, args=(url, "general", environment, events, str(root))
    )
    sector = ctx.Process(
        target=native_worker, args=(url, "sector_refresh", environment, events, str(root))
    )
    general.start()
    admission = None
    try:
        if with_refresh:
            deadline = time.monotonic() + 240
            while not events["acquired"].wait(0.25):
                assert general.is_alive(), "native worker exited; see " + str(root)
                assert time.monotonic() < deadline, "native acquisition boundary missing"
            # Only external health is deterministic; admission and handlers are native.
            native_health = refresh.check_status
            refresh.check_status = lambda **_: SimpleNamespace(
                status="IB_API_READY",
                api_connected=True,
                api_ready=True,
                smoke_response_received=True,
            )
            try:
                refresh._last_probe = float("-inf")
                with Session(engine) as db:
                    admission = refresh.enqueue_sector_refresh(
                        db, settings=get_settings(), request_id="native-" + root.name
                    )
                    db.commit()
            finally:
                refresh.check_status = native_health
            sector.start()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                with Session(engine) as db:
                    job = db.get(BackgroundJob, admission["job_id"])
                    gate = (
                        db.execute(text("SELECT * FROM ib_historical_gate WHERE id=1"))
                        .mappings()
                        .one()
                    )
                    if job.progress_stage == "WAITING_FOR_IB_ACQUISITION" and job.worker_id:
                        assert gate["job_id"] != job.id and gate["owner"]
                        (root / "broker-wait.json").write_text(
                            json.dumps(
                                {
                                    "sector_job_id": job.id,
                                    "sector_worker": job.worker_id,
                                    "gate": dict(gate),
                                    "observed_at": datetime.now(UTC),
                                },
                                default=str,
                                indent=2,
                            )
                        )
                        activity = (
                            db.execute(
                                text(
                                    "SELECT application_name,state,wait_event_type,wait_event,"
                                    "clock_timestamp()-xact_start AS transaction_age,"
                                    "left(query,240) AS query "
                                    "FROM pg_stat_activity WHERE datname=current_database() "
                                    "AND application_name LIKE 'sector-native-%'"
                                )
                            )
                            .mappings()
                            .all()
                        )
                        (root / "acquisition-barrier-transactions.json").write_text(
                            json.dumps([dict(row) for row in activity], default=str, indent=2)
                        )
                        break
                time.sleep(0.05)
            else:
                pytest.fail("sector did not wait behind the native general acquisition")
            events["release"].set()
            assert events["sector_provider"].wait(180), "released broker slot was not handed off"
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                with Session(engine) as db:
                    pipeline = db.get(PipelineRun, pipeline_id)
                    if pipeline.current_step in (
                        "SCORING_TECHNICALS",
                        "SCORING_COMBINED",
                        "RANKING_PROFILES",
                    ):
                        gate = (
                            db.execute(text("SELECT * FROM ib_historical_gate WHERE id=1"))
                            .mappings()
                            .one()
                        )
                        assert gate["job_id"] == admission["job_id"]
                        (root / "calculation-overlap-start.json").write_text(
                            json.dumps(
                                {
                                    "observed_at": datetime.now(UTC),
                                    "pipeline_id": pipeline_id,
                                    "pipeline_stage": pipeline.current_step,
                                    "gate": dict(gate),
                                },
                                default=str,
                                indent=2,
                            )
                        )
                        events["calculating"].set()
                        break
                time.sleep(0.05)
            else:
                pytest.fail("sector did not overlap native calculation")
            if perf_order:
                _measure_native_load(url, frozen_at, pipeline_id, perf_order, root, engine)
        deadline = time.monotonic() + 1200
        while general.is_alive() or (sector.pid and sector.is_alive()):
            assert time.monotonic() < deadline, "native publication deadline; see " + str(root)
            general.join(0.25)
            if sector.pid:
                sector.join(0.25)
        assert general.exitcode == 0 and (not sector.pid or sector.exitcode == 0), str(root)
        with Session(engine) as db:
            pipeline = db.get(PipelineRun, pipeline_id)
            receipt = {
                "pipeline_id": pipeline_id,
                "run_id": run_id,
                "pipeline_status": pipeline.status,
                "pipeline_error": pipeline.error_message,
                "pipeline_result": pipeline.result_json,
                "frozen_at": frozen_at,
                "database": engine.url.database,
            }
            from app.models.tables import BackgroundWorker, IBFetchItem, PipelineStep

            receipt["jobs"] = [
                dict(row)
                for row in db.execute(
                    select(BackgroundJob.__table__).order_by(BackgroundJob.id)
                ).mappings()
            ]
            receipt["workers"] = [
                dict(row) for row in db.execute(select(BackgroundWorker.__table__)).mappings()
            ]
            receipt["pipeline_steps"] = [
                dict(row)
                for row in db.execute(
                    select(PipelineStep.__table__)
                    .where(PipelineStep.pipeline_run_id == pipeline_id)
                    .order_by(PipelineStep.step_order)
                ).mappings()
            ]
            receipt["fetch_items"] = [
                dict(row)
                for row in db.execute(
                    select(IBFetchItem.__table__).order_by(IBFetchItem.id)
                ).mappings()
            ]
            assert pipeline.status in ("COMPLETED", "PARTIAL"), receipt
            # Preserve the engine's business-quality PARTIAL classification.
            # A financial/provider failure cannot satisfy this positive fixture.
            for name in (
                "ib_failure_count",
                "technical_error_count",
                "ceri_failed",
                "setup_lifecycle_failed",
                "winner_prediction_failed",
                "incomplete_rows",
            ):
                assert not pipeline.result_json.get(name), receipt
            for name in (
                "fundamental_scores",
                "technical_scores",
                "combined_results",
                "winner_prediction_inserted",
            ):
                assert pipeline.result_json[name] == 10, receipt
            # A resumed pipeline can retain a zero local-step counter even
            # though the separately certified CERI capture completed in full.
            from app.models.ceri_tables import CeriScoreSnapshot

            ceri_rows = db.scalar(
                select(func.count())
                .select_from(CeriScoreSnapshot)
                .where(
                    CeriScoreSnapshot.run_id == run_id,
                    CeriScoreSnapshot.calculation_context_id
                    == pipeline.result_json["ceri_calculation_context_id"],
                )
            )
            receipt["actual_ceri_capture_rows"] = ceri_rows
            assert pipeline.result_json["ceri_completion_state"] == "CERTIFIED", receipt
            assert pipeline.result_json["ceri_certified_capture_count"] == ceri_rows == 10, receipt
            if admission:
                acquisition = db.get(IBFetchRun, admission["fetch_run_id"])
                receipt["refresh"] = {
                    key: getattr(acquisition, key)
                    for key in (
                        "id",
                        "status",
                        "success_count",
                        "failure_count",
                        "started_at",
                        "completed_at",
                    )
                }
                assert acquisition.status == "COMPLETED" and acquisition.success_count == 26, (
                    receipt
                )
                from sector_native_evidence import capture_refresh_effects

                receipt.update(capture_refresh_effects(db, pipeline, acquisition.id, frozen_at))
                receipt["initial_shared_close"] = before
            (root / "result.json").write_text(json.dumps(receipt, default=str, indent=2))
            return receipt
    finally:
        for event in events.values():
            event.set()
        for process in (general, sector):
            if process.pid and process.is_alive():
                process.terminate()
                process.join(10)
        engine.dispose()
        _database_snapshot(url, root / "terminal.dump")


def _measure_native_load(url, frozen_at, pipeline_id, order, root, engine):
    import subprocess

    benchmark = os.environ.get("MSI_NATIVE_PERF_SCRIPT")
    assert benchmark and Path(benchmark).is_file()
    for measurement, variant in enumerate(order):
        round_id = measurement // 3 + 1
        environment = {
            **os.environ,
            "DATABASE_URL": url,
            "MSI_NATIVE_PERF_CUTOFF": frozen_at.isoformat(),
            "MSI_NATIVE_PERF_MODE": "load",
        }
        log = root / f"{variant}-native-load-{round_id}.log"
        observed = []
        with log.open("w") as stream:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    benchmark,
                    variant,
                    os.environ["MSI_NATIVE_PERF_EXECUTION_ID"] + f"-native-{root.name}-{round_id}",
                ],
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 240
            try:
                while process.poll() is None:
                    assert time.monotonic() < deadline, "native-load benchmark deadline"
                    with Session(engine) as db:
                        active = (
                            db.execute(
                                text(
                                    "SELECT id,job_type,progress_stage,worker_id,heartbeat_at "
                                    "FROM background_jobs WHERE status='RUNNING' "
                                    "AND (related_run_id=:run_id "
                                    "OR payload_json->>'pipeline_run_id'=:pipeline)"
                                ),
                                {
                                    "run_id": db.get(PipelineRun, pipeline_id).upload_run_id,
                                    "pipeline": str(pipeline_id),
                                },
                            )
                            .mappings()
                            .all()
                        )
                        observed.append(
                            {"at": datetime.now(UTC), "active": [dict(row) for row in active]}
                        )
                    time.sleep(0.5)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(10)
        (root / f"{variant}-load-observations-{round_id}.json").write_text(
            json.dumps(observed, default=str, indent=2)
        )
        assert process.returncode == 0, str(log)
        assert sum(bool(row["active"]) for row in observed) >= 2, (
            "no active native financial work overlapped measurement"
        )


def _measure_frozen_inputs(url, frozen_at, orders, root):
    """Repeat idle/cold/warm/concurrent cases before starting native workers.

    The subsequent loaded cases restore this exact database snapshot and cutoff,
    allowing complete payload comparison across both workloads and all versions.
    """
    import subprocess

    benchmark = os.environ.get("MSI_NATIVE_PERF_SCRIPT")
    assert benchmark and Path(benchmark).is_file()
    for round_id, order in enumerate(orders, 1):
        for variant in order:
            environment = {
                **os.environ,
                "DATABASE_URL": url,
                "MSI_NATIVE_PERF_CUTOFF": frozen_at.isoformat(),
                "MSI_NATIVE_PERF_MODE": "ordinary",
            }
            log = root / f"{variant}-fixture-idle-{round_id}.log"
            with log.open("w") as stream:
                process = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        benchmark,
                        variant,
                        os.environ["MSI_NATIVE_PERF_EXECUTION_ID"] + f"-fixture-idle-{round_id}",
                    ],
                    env=environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                )
            assert process.returncode == 0, str(log)


@pytest.mark.slow
@pytest.mark.destructive
@pytest.mark.skipif(
    not os.environ.get("MSI_NATIVE_RECEIPTS"),
    reason="requires retained owned native execution receipts",
)
def test_retained_native_publication_receipts(disposable_postgres_database):
    """Revalidate executed handlers' receipts without repeating immutable inputs."""
    import hashlib

    from sector_native_evidence import capture_refresh_effects, capture_result

    from app.services.winner_probability.config import load_winner_probability_config

    inputs = json.loads(Path(os.environ["MSI_NATIVE_RECEIPTS"]).read_text())
    source = Path(__file__).resolve().parents[2]
    root = output_directory("nsp-check")
    config_path = Path(inputs["winner_config"])
    assert hashlib.sha256(config_path.read_bytes()).hexdigest() == inputs["winner_config_sha256"]
    config = load_winner_probability_config(config_path)
    frozen_at = datetime.fromisoformat(inputs["frozen_at"])
    control = None
    results = {}
    for label in ("control", "treatment"):
        case = Path(inputs[label])
        assert case.resolve().is_relative_to((source / ".qa_work").resolve())
        dump = case / "terminal.dump"
        assert hashlib.sha256(dump.read_bytes()).hexdigest() == inputs[label + "_sha256"]
        _database_snapshot(disposable_postgres_database, dump, restore=True)
        engine = create_engine(disposable_postgres_database)
        # Native proof validators lock retained source rows for consistency.
        # Permit those SELECTs, while rejecting any data/schema mutation before
        # execution. No native operation is still running in this audit clone.
        from sqlalchemy import event

        def reject_mutation(_connection, _cursor, statement, *_arguments):
            assert statement.lstrip().split()[0].upper() in ("SELECT", "WITH", "SHOW")

        event.listen(engine, "before_cursor_execute", reject_mutation)
        try:
            with Session(engine) as db:
                pipeline = db.scalar(
                    select(PipelineRun).where(PipelineRun.requested_by == "native-sector-followup")
                )
                assert pipeline.status == "COMPLETED"
                assert pipeline.result_json["ceri_completion_state"] == "CERTIFIED"
                assert pipeline.result_json["ceri_certified_capture_count"] == 10
                pipeline_id, run_id = pipeline.id, pipeline.upload_run_id
                if label == "treatment":
                    jobs = [
                        job
                        for job in db.scalars(
                            select(BackgroundJob).where(BackgroundJob.job_type == "IB_FETCH")
                        )
                        if (job.payload_json or {}).get("msi_refresh")
                    ]
                    assert len(jobs) == 1 and jobs[0].status == "COMPLETED"
                    results["effects"] = capture_refresh_effects(
                        db, pipeline, jobs[0].payload_json["fetch_run_id"], frozen_at
                    )
                    wait = json.loads((case / "broker-wait.json").read_text())
                    overlap = json.loads((case / "calculation-overlap-start.json").read_text())
                    assert wait["gate"]["job_id"] != wait["sector_job_id"]
                    assert overlap["gate"]["job_id"] == wait["sector_job_id"] == jobs[0].id
                    assert overlap["pipeline_id"] == pipeline_id
                    assert overlap["pipeline_stage"] in (
                        "SCORING_TECHNICALS",
                        "SCORING_COMBINED",
                        "CALCULATING_RANKINGS",
                    )
                    observations = list(case.glob("*-load-observations-*.json"))
                    assert len(observations) == 9
                    for path in observations:
                        samples = json.loads(path.read_text())
                        assert sum(bool(sample["active"]) for sample in samples) >= 2
                    results["broker_wait"] = wait
                    results["calculation_overlap"] = overlap
                results[label] = {
                    "pipeline_id": pipeline_id,
                    "run_id": run_id,
                    "status": pipeline.status,
                    "result": pipeline.result_json,
                }
            result = capture_result(
                engine, run_id, pipeline_id, root / (label + ".json"), winner_config=config
            )
            if control is None:
                control = result
            else:
                assert result == control, (
                    "retained native business/source/authority equivalence failed"
                )
        finally:
            engine.dispose()
    results["outcome"] = "PASS"
    results["inputs"] = inputs
    (root / "verification.json").write_text(json.dumps(results, default=str, indent=2))
