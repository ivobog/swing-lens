from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from playwright.sync_api import Browser, Page, expect
from psycopg import sql
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database_safety import assert_disposable_database
from app.models.ceri_tables import CeriScoreSnapshot
from app.models.tables import PriceBar
from app.services.bar_cache_service import cache_bars
from app.services.ceri.config import load_ceri_config
from app.services.ceri.evidence_eligibility import eligible_snapshot_select
from app.services.ceri.sec.processor_lifecycle import (
    certify_processor,
    promote_processor,
    register_deployed_processor,
)
from app.services.ib_data_fetcher import HistoricalBar
from app.services.runtime_certification_observer import observe_certification_runtime
from app.services.runtime_mutation_authority import RuntimeMutationAuthority
from app.services.winner_probability.market_data_obligation_service import (
    MarketDataObligationService,
)
from app.services.winner_probability.trading_session_service import next_regular_session
from single_run_certification.evidence import (
    build_run_evidence_graph,
    database_integrity_checks,
    query_rows,
)
from single_run_certification.fixtures import (
    CANONICAL_TICKERS,
    DECOY_TICKER,
    FIXTURE_VERSION,
    SeedResult,
    seed_prerequisites,
    write_canonical_csv,
)
from single_run_certification.reporting import (
    CertificationRecorder,
    write_database_html,
    write_report,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
POSTGRES_ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"
ALEMBIC_HEAD = ScriptDirectory.from_config(
    Config(str(REPO_ROOT / "alembic.ini"))
).get_current_head()
TERMINAL_PIPELINE_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "CANCELLED"}
LIVE_CANARY_TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN", "META")


@dataclass(frozen=True)
class CertificationEnvironment:
    base_url: str
    database_url: str
    database_name: str
    artifact_dir: Path
    csv_path: Path
    seed: SeedResult
    execution_id: str
    server_log: Path
    ib_log: Path
    provider_mode: str
    tickers: tuple[str, ...]


@pytest.fixture
def certification_provider_ingest_enabled() -> bool:
    # Production runtime certification must exercise the durable asynchronous
    # provider -> normalization -> feature -> finalizer graph.  External
    # payloads are frozen by the allowlisted adapter; orchestration is not.
    return True


@pytest.fixture
def certification_provider_mode() -> str:
    mode = os.environ.get("SWINGLENS_CERTIFICATION_PROVIDER_MODE", "deterministic")
    if mode not in {"deterministic", "live"}:
        pytest.fail(f"Unsupported certification provider mode: {mode}")
    return mode


@pytest.fixture
def certification_environment(
    tmp_path_factory: pytest.TempPathFactory,
    certification_provider_ingest_enabled: bool,
    certification_provider_mode: str,
) -> Iterator[CertificationEnvironment]:
    live_mode = certification_provider_mode == "live"
    execution_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    database_name = f"swinglens_pytest_cert_{uuid.uuid4().hex[:12]}"
    if not database_name.startswith("swinglens_pytest_cert_"):
        raise RuntimeError("unsafe certification database name")
    admin_url = os.environ.get("SWINGLENS_TEST_POSTGRES_ADMIN_URL", POSTGRES_ADMIN_URL)
    try:
        admin = psycopg.connect(admin_url, autocommit=True)
    except psycopg.Error as exc:
        pytest.fail(f"BLOCKED: certification requires disposable PostgreSQL: {exc}")
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

    database_url = (
        make_url(admin_url.replace("postgresql://", "postgresql+psycopg://", 1))
        .set(database=database_name)
        .render_as_string(hide_password=False)
    )
    assert_disposable_database(database_url)
    runtime_root = tmp_path_factory.mktemp("swinglens-single-run-certification")
    artifact_root = Path(
        os.environ.get(
            "SWINGLENS_CERTIFICATION_ARTIFACT_ROOT",
            str(REPO_ROOT / "test-results" / "single-run-certification"),
        )
    ).resolve()
    artifact_dir = artifact_root / execution_id
    for relative in (
        "screenshots/gui",
        "screenshots/database",
        "sql",
        "db-results",
        "comparisons",
        "exports",
        "logs",
    ):
        (artifact_dir / relative).mkdir(parents=True, exist_ok=True)
    server_log = artifact_dir / "logs" / "uvicorn.log"
    ib_log = artifact_dir / "logs" / "deterministic-ib.jsonl"
    csv_path = runtime_root / "single-run-certification.csv"
    if live_mode:
        csv_hash = _write_live_canary_csv(csv_path)
        certification_tickers = LIVE_CANARY_TICKERS
    else:
        csv_hash = write_canonical_csv(csv_path)
        certification_tickers = CANONICAL_TICKERS
    # This positive path freezes an enabled native profile, alongside the flags.
    winner_profile = yaml.safe_load((REPO_ROOT / "config/winner_probability.yaml").read_text())
    winner_profile["engine"]["enabled"] = True
    winner_configuration_path = runtime_root / "winner_probability.yaml"
    winner_configuration_path.write_text(yaml.safe_dump(winner_profile), encoding="utf-8")

    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
        "APP_HOST": "127.0.0.1",
        "DEBUG": "false",
        "ALLOW_PUBLIC_BIND": "false",
        "USE_DURABLE_PIPELINE": "true",
        "RUNTIME_MODE": "CERTIFICATION",
        "SWINGLENS_RUNTIME_INSTANCE_ID": execution_id,
        "DURABLE_WORKER_PROCESS_ENABLED": "true",
        "EMBEDDED_JOB_WORKER_ENABLED": "false",
        "JOB_WORKER_ENABLED": "false",
        "MARKET_DATA_PREWARM_ENABLED": "false",
        "WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED": "false",
        "WINNER_PROBABILITY_AUTO_MATURATION_ENABLED": "false",
        "JOB_POLL_INTERVAL_SECONDS": "0.05",
        "JOB_STALE_AFTER_SECONDS": "120",
        "JOB_WORKER_ID": f"certification-{execution_id}",
        "UPLOAD_DIR": str(runtime_root / "uploads"),
        "EXPORT_DIR": str(runtime_root / "exports"),
        "CACHE_DIR": str(runtime_root / "cache"),
        "IB_REQUEST_DELAY_SECONDS": "0",
        "IB_MIN_SECONDS_BETWEEN_REQUESTS": "0",
        "IB_REQUESTS_PER_MINUTE": "10000",
        "IB_BACKOFF_SECONDS": "0",
        "IB_MAX_RETRIES": "1",
        "TECHNICAL_ARTIFACT_CACHE_ENABLED": "true",
        "TECHNICAL_ARTIFACT_CACHE_WRITE_ENABLED": "true",
        "TECHNICAL_SERIES_VERSION_MAINTENANCE_ENABLED": "true",
        "WINNER_PROBABILITY_ENABLED": "true",
        "WINNER_PROBABILITY_CAPTURE_IN_PIPELINE": "true",
        "WINNER_PROBABILITY_ADMIN_ENABLED": "true",
        "SETUP_LIFECYCLE_ENABLED": "true",
        "SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED": "true",
        "SETUP_CAPTURE_HANDOFF_ENABLED": "true",
        "SETUP_LIFECYCLE_ALERTS_ENABLED": "true",
        "CERI_ENABLED": "true",
        "CERI_PROVIDER_INGEST_ENABLED": str(certification_provider_ingest_enabled).lower(),
        "CERI_LEGACY_PIPELINE_SCHEDULING_ENABLED": (
            "false" if certification_provider_ingest_enabled else "true"
        ),
        "CERI_BATCHED_WORKFLOW_ENABLED": (
            "true" if certification_provider_ingest_enabled else "false"
        ),
        "SEC_DOCUMENT_INCREMENTAL_MODE": (
            "OFF"
            if certification_provider_ingest_enabled
            else os.environ.get("SEC_DOCUMENT_INCREMENTAL_MODE", "OFF")
        ),
        "CERI_RUN_CAPTURE_ENABLED": "true",
        "CERI_ALERTS_ENABLED": "true",
        "CERI_UI_ENABLED": "true",
        "CERI_ADMIN_ENABLED": "true",
        "CERTIFICATION_IB_LOG": str(ib_log),
        "CERTIFICATION_WINNER_CONFIGURATION": str(winner_configuration_path),
        "CERTIFICATION_OUTCOME_NOW": "2027-01-15T22:00:00+00:00",
        "CERTIFICATION_FROZEN_CERI_PROVIDERS": str(certification_provider_ingest_enabled).lower(),
        "SWINGLENS_CERTIFICATION_ADAPTER_MODULE": "certification_server",
        "SWINGLENS_CERTIFICATION_PROVIDER_MODE": certification_provider_mode,
        "SWINGLENS_RUNTIME_CHILD_LOG_DIR": str(artifact_dir / "logs"),
        "PYTHONPATH": os.pathsep.join(
            [str(REPO_ROOT), str(REPO_ROOT / "tests" / "e2e" / "single_run_certification")]
        ),
    }
    if live_mode:
        # Only the provider boundary changes. The same supervisor, web
        # process, durable worker, queue, leases and observer remain active.
        env.pop("SWINGLENS_CERTIFICATION_ADAPTER_MODULE", None)
        env["CERTIFICATION_FROZEN_CERI_PROVIDERS"] = "false"
        for key in (
            "IB_REQUEST_DELAY_SECONDS",
            "IB_MIN_SECONDS_BETWEEN_REQUESTS",
            "IB_REQUESTS_PER_MINUTE",
            "IB_BACKOFF_SECONDS",
            "IB_MAX_RETRIES",
        ):
            if key in os.environ:
                env[key] = os.environ[key]
            else:
                env.pop(key, None)

    migration_log = artifact_dir / "logs" / "alembic-upgrade.log"
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    migration_log.write_text(migration.stdout + migration.stderr, encoding="utf-8")
    if migration.returncode != 0:
        _drop_database(admin, database_name)
        admin.close()
        pytest.fail(f"BLOCKED: Alembic migration failed; see {migration_log}")

    seed = seed_prerequisites(
        database_url,
        winner_configuration_path=winner_configuration_path,
        live_transition_tickers=certification_tickers if live_mode else (),
    )
    _activate_disposable_sec_processor(database_url)
    if certification_provider_ingest_enabled and not live_mode:
        _seed_disposable_sec_readiness(database_url)
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    log_handle = server_log.open("w", encoding="utf-8")
    supervisor_env = {**env, "PROCESS_ROLE": "SUPERVISOR"}
    supervisor_creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.worker_supervisor",
            "--worker-id",
            env["JOB_WORKER_ID"],
            "--queues",
            "interactive,broker,background",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--runtime-instance-id",
            execution_id,
            "--repo-root",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        env=supervisor_env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=supervisor_creationflags,
    )
    worker_log = artifact_dir / "logs" / "lifecycle-worker.log"
    try:
        _wait_healthy(process, base_url, server_log)
        _wait_worker_ready(
            process,
            database_url,
            env["JOB_WORKER_ID"],
            worker_log,
        )
        environment_payload = {
            "execution_id": execution_id,
            "git_commit": _git_commit(),
            "working_tree_fingerprint": env.get("SWINGLENS_CERTIFICATION_CODE_FINGERPRINT"),
            "alembic_revision": ALEMBIC_HEAD,
            "fixture_version": FIXTURE_VERSION,
            "fixture_hash": seed.fixture_hash,
            "csv_hash": csv_hash,
            "database_name": database_name,
            "database_engine": "PostgreSQL",
            "python": sys.version,
            "feature_flags": {key: value for key, value in env.items() if _is_feature_flag(key)},
            "canonical_tickers": list(certification_tickers),
            "provider_mode": certification_provider_mode,
            "decoy_run_id": seed.decoy_run_id,
            "decoy_ticker": DECOY_TICKER,
        }
        (artifact_dir / "environment.json").write_text(
            json.dumps(environment_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        yield CertificationEnvironment(
            base_url=base_url,
            database_url=database_url,
            database_name=database_name,
            artifact_dir=artifact_dir,
            csv_path=csv_path,
            seed=seed,
            execution_id=execution_id,
            server_log=server_log,
            ib_log=ib_log,
            provider_mode=certification_provider_mode,
            tickers=certification_tickers,
        )
    finally:
        if process.poll() is None and sys.platform == "win32":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        elif process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_handle.close()
        _drop_database(admin, database_name)
        admin.close()


@pytest.fixture
def certification_page(
    browser: Browser,
    certification_environment: CertificationEnvironment,
) -> Iterator[Page]:
    """Use the shared browser runtime with an isolated certification context."""
    context = browser.new_context(
        accept_downloads=True,
        viewport={"width": 1600, "height": 1000},
        extra_http_headers={
            "x-swinglens-certification-session": certification_environment.execution_id
        },
    )
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.destructive
def test_single_run_comprehensive_e2e_certification(
    certification_page: Page,
    certification_environment: CertificationEnvironment,
) -> None:
    page = certification_page
    env = certification_environment
    recorder = CertificationRecorder(execution_id=env.execution_id)
    engine = create_engine(env.database_url)
    graph: dict = {}
    pipeline_steps: list[dict] = []
    idempotency: dict = {}
    exports: list[dict] = []
    runtime_identity: dict = {}
    page.context.tracing.start(screenshots=True, snapshots=True, sources=True)

    try:
        run_id = _launch_run_through_gui(page, env, recorder)
        recorder.run_id = run_id
        pipeline_id, terminal_status = _run_pipeline_through_gui(page, env, recorder, run_id)
        recorder.check(
            terminal_status in {"COMPLETED", "PARTIAL"},
            "durable pipeline reached a successful terminal state",
            area="Pipeline/Jobs",
            expected="COMPLETED or PARTIAL",
            actual=terminal_status,
        )
        pipeline_identity = query_rows(
            engine,
            """
            select id as pipeline_id, created_at as pipeline_started_at,
                   completed_at as pipeline_finished_at
            from pipeline_runs where id=:pipeline_id
            """,
            {"pipeline_id": pipeline_id},
        )
        root_identity = query_rows(
            engine,
            """
            select id as root_job_id, root_correlation_id
            from background_jobs
            where related_run_id=:run_id and job_type='FULL_PIPELINE'
              and parent_job_id is null
              and (payload_json->>'pipeline_run_id')::bigint=:pipeline_id
            order by id limit 1
            """,
            {"run_id": run_id, "pipeline_id": pipeline_id},
        )
        worker_identity = query_rows(
            engine,
            """
            select worker_id, instance_id as worker_instance_id,
                   generation as worker_generation,
                   process_id as worker_process_id, started_at as worker_started_at
            from background_workers where worker_id=:worker_id
            """,
            {"worker_id": f"certification-{env.execution_id}"},
        )
        runtime_identity = {
            **(pipeline_identity[0] if pipeline_identity else {}),
            **(root_identity[0] if root_identity else {}),
            **(worker_identity[0] if worker_identity else {}),
            "runtime_instance_id": env.execution_id,
        }
        required_identity = {
            "pipeline_id",
            "root_job_id",
            "root_correlation_id",
            "worker_id",
            "worker_instance_id",
            "worker_generation",
            "pipeline_started_at",
            "pipeline_finished_at",
        }
        recorder.check(
            required_identity.issubset(runtime_identity)
            and all(runtime_identity.get(key) is not None for key in required_identity),
            "runtime identity records pipeline, root job, and worker generation",
            area="Pipeline/Jobs",
            expected=sorted(required_identity),
            actual=runtime_identity,
        )
        pipeline_steps = query_rows(
            engine,
            """
            select step_order, step_name, status, started_at, completed_at,
                   message, error_message, retry_count, result_json
            from pipeline_steps where pipeline_run_id = :pipeline_id order by step_order
            """,
            {"pipeline_id": pipeline_id},
        )
        (env.artifact_dir / "pipeline-results.json").write_text(
            json.dumps(
                query_rows(
                    engine,
                    "select step_name, status, result_json from pipeline_steps "
                    "where pipeline_run_id=:pipeline_id order by step_order",
                    {"pipeline_id": pipeline_id},
                ),
                default=str,
                indent=2,
            ),
            encoding="utf-8",
        )
        _compare_pipeline_page(page, recorder, pipeline_steps)
        idempotency = _materialize_rankings_once(page, engine, env, recorder, run_id)
        _capture_surface(
            page,
            env,
            recorder,
            name="run-detail",
            path=f"/runs/{run_id}",
            ordinal=10,
            heading=f"Run {run_id}",
        )
        _compare_run_detail(page, engine, recorder, run_id)
        _assert_ceri_upgrade_baseline(
            engine,
            recorder,
            run_id,
            baseline_snapshot_id=env.seed.ceri_baseline_snapshot_id,
        )

        dynamic = _dynamic_routes(engine, run_id)
        surfaces = [
            (30, "column-mapping", f"/runs/{run_id}/mapping", "Column Mapping"),
            (40, "coverage", f"/runs/{run_id}/coverage", "OHLCV Coverage"),
            (50, "market-regime", f"/runs/{run_id}/market-regime", "Market Regime"),
            (60, "sector-rotation", f"/runs/{run_id}/sector-rotation", "Sector Rotation"),
            (70, "market-changes", f"/runs/{run_id}/setup-lifecycle", "Market Changes"),
            (80, "alerts", "/setup-lifecycle/alerts", "Alert Center"),
            (90, "winner-evidence", f"/runs/{run_id}/winner-probability", "Winner Evidence"),
            (100, "ceri", f"/runs/{run_id}/ceri", "CERI Dashboard"),
            (105, "ceri-changes", "/ceri/changes", "CERI Changes and Alerts"),
            (110, "runs", "/runs", "Runs"),
            (120, "history", f"/history?run_id={run_id}", "History"),
            (130, "ticker-chart", f"/runs/{run_id}/tickers/ALFA/chart", "ALFA"),
            *dynamic,
        ]
        for ordinal, name, path, heading in surfaces:
            _capture_surface(
                page,
                env,
                recorder,
                name=name,
                path=path,
                ordinal=ordinal,
                heading=heading,
            )
            _compare_current_surface(page, engine, recorder, run_id, name)

        maturation = _mature_winner_evidence(page, engine, env, recorder, run_id)
        idempotency["winner_maturation"] = maturation

        _acknowledge_one_alert(page, engine, env, recorder, run_id)
        _acknowledge_one_ceri_alert(page, engine, env, recorder)
        exports = _capture_exports(page, engine, env, recorder, run_id)
        idempotency = _verify_idempotency(page, engine, env, recorder, run_id, state=idempotency)
        _verify_ib_boundary(env, recorder)
        _verify_no_restricted_leaks(env, recorder)

        graph_result = build_run_evidence_graph(
            engine,
            run_id=run_id,
            artifact_dir=env.artifact_dir,
            tickers=CANONICAL_TICKERS,
        )
        graph = graph_result.manifest
        recorder.check(
            graph_result.relationship_count >= 20,
            "run evidence graph covers direct and indirect relationships",
            area="Isolation/Integrity",
            expected=">=20",
            actual=graph_result.relationship_count,
        )
        recorder.check(
            any(entry["table"] == "winner_prediction_snapshots" for entry in graph["tables"]),
            "run evidence graph includes pipeline-owned Winner prediction lineage",
            area="Winner Evidence",
            expected=True,
            actual=[entry["table"] for entry in graph["tables"] if "winner" in entry["table"]],
        )
        recorder.check(
            idempotency.get("winner_maturation", {}).get("mutation_status") == "REJECTED",
            "certification evidence records the fenced unrelated Winner mutation",
            area="Isolation/Integrity",
            expected="REJECTED",
            actual=idempotency.get("winner_maturation", {}).get("mutation_status"),
        )
        recorder.integrity_checks = database_integrity_checks(engine, run_id)
        for check in recorder.integrity_checks:
            recorder.check(
                check["passed"],
                check["name"],
                area="Isolation/Integrity",
                expected=check["expected"],
                actual=check["actual"],
            )
        _capture_database_screenshots(page, env, graph)
    except Exception as exc:  # keep the mandatory evidence package on harness/product failure
        recorder.failures.append(f"Harness execution: {type(exc).__name__}: {exc}")
        (env.artifact_dir / "logs" / "harness-traceback.log").write_text(
            traceback.format_exc(), encoding="utf-8"
        )
    finally:
        environment = json.loads(
            (env.artifact_dir / "environment.json").read_text(encoding="utf-8")
        )
        if recorder.run_id is not None:
            run_rows = query_rows(
                engine,
                "select status, row_count from upload_runs where id=:run_id",
                {"run_id": recorder.run_id},
            )
            if run_rows:
                environment["run_status"] = run_rows[0]["status"]
                environment["ticker_count"] = run_rows[0]["row_count"]
        environment["provider_mode"] = "deterministic"
        environment["runtime_identity"] = runtime_identity
        write_report(
            env.artifact_dir,
            recorder=recorder,
            environment=environment,
            graph=graph,
            pipeline_steps=pipeline_steps,
            idempotency=idempotency,
            exports=exports,
        )
        if recorder.failures:
            page.context.tracing.stop(path=env.artifact_dir / "logs" / "playwright-trace.zip")
        else:
            page.context.tracing.stop()
        engine.dispose()

    assert not recorder.failures, f"Certification FAIL; evidence: {env.artifact_dir}\n" + "\n".join(
        recorder.failures
    )


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.destructive
@pytest.mark.live_provider
def test_live_provider_canary_uses_production_runtime(
    certification_page: Page,
    certification_environment: CertificationEnvironment,
) -> None:
    """Exercise real providers through the same isolated production topology."""

    page = certification_page
    env = certification_environment
    recorder = CertificationRecorder(execution_id=env.execution_id)
    engine = create_engine(env.database_url)
    pipeline_steps: list[dict] = []
    metrics: dict[str, object] = {}
    runtime_identity: dict[str, object] = {}
    page.context.tracing.start(screenshots=True, snapshots=True, sources=True)
    try:
        recorder.check(
            env.provider_mode == "live",
            "live certification provider boundary is active",
            area="Provider Boundary",
            expected="live",
            actual=env.provider_mode,
        )
        run_id = _launch_run_through_gui(page, env, recorder)
        recorder.run_id = run_id
        pipeline_id, terminal_status = _run_pipeline_through_gui(
            page,
            env,
            recorder,
            run_id,
            absolute_deadline_seconds=1800,
            progress_deadline_seconds=180,
        )
        pipeline_steps = query_rows(
            engine,
            "select step_order,step_name,status,started_at,completed_at,message,"
            "error_message,retry_count,result_json from pipeline_steps "
            "where pipeline_run_id=:pipeline_id order by step_order",
            {"pipeline_id": pipeline_id},
        )
        root_rows = query_rows(
            engine,
            "select id as root_job_id,root_correlation_id,worker_instance_id,"
            "started_at,completed_at from background_jobs where related_run_id=:run_id "
            "and job_type='FULL_PIPELINE' and parent_job_id is null "
            "and (payload_json->>'pipeline_run_id')::bigint=:pipeline_id order by id limit 1",
            {"run_id": run_id, "pipeline_id": pipeline_id},
        )
        worker_rows = query_rows(
            engine,
            "select worker_id,instance_id as worker_instance_id,generation as worker_generation "
            "from background_workers where worker_id=:worker_id",
            {"worker_id": f"certification-{env.execution_id}"},
        )
        runtime_identity = {
            "pipeline_id": pipeline_id,
            "runtime_instance_id": env.execution_id,
            **(root_rows[0] if root_rows else {}),
            **(worker_rows[0] if worker_rows else {}),
        }
        jobs = query_rows(
            engine,
            "select id,job_type,status,parent_job_id,pipeline_dependency_id,created_at,"
            "started_at,completed_at,"
            "heartbeat_at,lease_expires_at,progress_sequence,progress_processed,progress_total,"
            "retry_count,recovery_count,payload_json,operational_metadata_json "
            "from background_jobs where related_run_id=:run_id "
            "and (payload_json->>'pipeline_run_id')::bigint=:pipeline_id order by id",
            {"run_id": run_id, "pipeline_id": pipeline_id},
        )
        provider_jobs = [row for row in jobs if row["job_type"] == "CERI_PROVIDER_INGEST_BATCH"]
        multi_symbol = [
            row
            for row in provider_jobs
            if len((row.get("payload_json") or {}).get("tickers") or []) >= 2
        ]
        checkpointed = [
            row
            for row in multi_symbol
            if int(row.get("progress_sequence") or 0) >= 2
            and len(
                ((row.get("operational_metadata_json") or {}).get("ceri_batch") or {}).get(
                    "completed_tickers", []
                )
            )
            >= 2
        ]
        continuations = [
            row
            for row in jobs
            if row["job_type"] == "FULL_PIPELINE" and row.get("parent_job_id") is not None
        ]
        dependencies = query_rows(
            engine,
            "select id,dependency_type from pipeline_dependencies "
            "where pipeline_run_id=:pipeline_id order by id",
            {"pipeline_id": pipeline_id},
        )
        dependency_types = {row["id"]: row["dependency_type"] for row in dependencies}
        ceri_continuations = [
            row
            for row in continuations
            if dependency_types.get(row.get("pipeline_dependency_id")) == "CERI_WORKFLOW"
        ]
        sec_continuations = [
            row
            for row in continuations
            if dependency_types.get(row.get("pipeline_dependency_id")) == "SEC_READINESS"
        ]
        telemetry = query_rows(
            engine,
            "select provider,count(*) as calls,coalesce(sum(retry_count),0) as retries,"
            "max(latency_ms) as max_latency_ms,count(*) filter (where error_code is not null) "
            "as errors from ceri_provider_request_telemetry where root_correlation_id=:root "
            "group by provider order by provider",
            {"root": runtime_identity.get("root_correlation_id")},
        )
        telemetry_by_provider = {row["provider"]: row for row in telemetry}
        price_counts = query_rows(
            engine,
            "select ticker,count(*) as bars from price_bars where ticker=any(:tickers) "
            "group by ticker order by ticker",
            {"tickers": list(env.tickers)},
        )
        step_by_name = {row["step_name"]: row for row in pipeline_steps}
        recorder.check(
            terminal_status in {"COMPLETED", "PARTIAL"},
            "live pipeline reached successful terminal state",
            area="Pipeline/Jobs",
            expected="COMPLETED or PARTIAL",
            actual=terminal_status,
        )
        recorder.check(
            all(row["status"] in {"COMPLETED", "PARTIAL"} for row in pipeline_steps),
            "every live pipeline stage is terminal-successful",
            area="Pipeline/Jobs",
            expected="all COMPLETED/PARTIAL",
            actual={row["step_name"]: row["status"] for row in pipeline_steps},
        )
        recorder.check(
            bool(checkpointed),
            "multi-symbol live EODHD batch persisted per-symbol checkpoints",
            area="Live CERI",
            expected=">=2 completed symbols and progress_sequence >=2",
            actual=checkpointed,
        )
        recorder.check(
            len(ceri_continuations) == 1 and ceri_continuations[0]["status"] == "COMPLETED",
            "CERI continuation was claimed exactly once",
            area="Live Continuation",
            expected="one completed continuation",
            actual=ceri_continuations,
        )
        recorder.check(
            len(sec_continuations) == 1 and sec_continuations[0]["status"] == "COMPLETED",
            "SEC readiness continuation was claimed exactly once",
            area="Live Continuation",
            expected="one completed SEC continuation",
            actual=sec_continuations,
        )
        for provider in ("eodhd", "sec"):
            provider_row = telemetry_by_provider.get(provider)
            recorder.check(
                provider_row is not None
                and int(provider_row.get("calls") or 0) > 0
                and int(provider_row.get("errors") or 0) == 0,
                f"live {provider.upper()} provider requests completed without errors",
                area=f"Live {provider.upper()}",
                expected="calls > 0 and errors = 0",
                actual=provider_row,
            )
        recorder.check(
            {row["ticker"] for row in price_counts} == set(env.tickers),
            "IB market-data path persisted bars for the complete live cohort",
            area="Live IB",
            expected=sorted(env.tickers),
            actual=price_counts,
        )
        for stage in (
            "CAPTURING_SETUP_SIGNALS",
            "EVALUATING_SETUP_LIFECYCLES",
            "CAPTURING_WINNER_PREDICTIONS",
        ):
            recorder.check(
                stage in step_by_name and step_by_name[stage]["status"] in {"COMPLETED", "PARTIAL"},
                f"{stage} completed through live pipeline",
                area=stage,
                expected="COMPLETED/PARTIAL",
                actual=step_by_name.get(stage),
            )
        metrics = {
            "ticker_cohort": list(env.tickers),
            "job_graph": jobs,
            "provider_telemetry": telemetry,
            "ib_price_bar_counts": price_counts,
            "retry_count": sum(int(row.get("retry_count") or 0) for row in jobs),
            "recovery_count": sum(int(row.get("recovery_count") or 0) for row in jobs),
            "provider_child_count": len(provider_jobs),
            "continuation_count": len(continuations),
        }
        (env.artifact_dir / "live-provider-metrics.json").write_text(
            json.dumps(metrics, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
    except Exception as exc:
        recorder.failures.append(f"Live harness execution: {type(exc).__name__}: {exc}")
        (env.artifact_dir / "logs" / "live-harness-traceback.log").write_text(
            traceback.format_exc(), encoding="utf-8"
        )
    finally:
        environment = json.loads(
            (env.artifact_dir / "environment.json").read_text(encoding="utf-8")
        )
        environment["provider_mode"] = "live"
        environment["runtime_identity"] = runtime_identity
        environment["run_status"] = next(
            (row["status"] for row in pipeline_steps if row["step_order"] == 1), None
        )
        environment["ticker_count"] = len(env.tickers)
        write_report(
            env.artifact_dir,
            recorder=recorder,
            environment=environment,
            graph={},
            pipeline_steps=pipeline_steps,
            idempotency={"live_provider_metrics": metrics},
            exports=[],
        )
        if recorder.failures:
            page.context.tracing.stop(path=env.artifact_dir / "logs" / "playwright-trace.zip")
        else:
            page.context.tracing.stop()
        engine.dispose()
    assert not recorder.failures, f"Live canary FAIL; evidence: {env.artifact_dir}\n" + "\n".join(
        recorder.failures
    )


def _launch_run_through_gui(
    page: Page,
    env: CertificationEnvironment,
    recorder: CertificationRecorder,
) -> int:
    response = page.goto(env.base_url)
    recorder.check(
        response is not None and response.status == 200,
        "upload page rendered",
        area="Upload",
        expected=200,
        actual=response.status if response else None,
    )
    page.locator("#csv-file").set_input_files(env.csv_path)
    page.get_by_role("button", name="Process").click()
    page.wait_for_url(re.compile(r"/runs/\d+$"), timeout=30_000)
    run_id = int(page.url.rsplit("/", 1)[-1])
    recorder.check(
        run_id != env.seed.decoy_run_id,
        "GUI upload created exactly one new canonical run",
        area="Upload",
        expected=f"not {env.seed.decoy_run_id}",
        actual=run_id,
    )
    recorder.check(
        page.locator("[data-cockpit-row]").count() == 0,
        "new run has no stale combined rows before pipeline",
        area="Isolation/Integrity",
        expected=0,
        actual=page.locator("[data-cockpit-row]").count(),
    )
    return run_id


def _run_pipeline_through_gui(
    page: Page,
    env: CertificationEnvironment,
    recorder: CertificationRecorder,
    run_id: int,
    *,
    absolute_deadline_seconds: int = 900,
    progress_deadline_seconds: int = 180,
) -> tuple[int, str]:
    page.get_by_role("button", name="Run full pipeline").click()
    ib_preflight = page.locator("[data-ib-preflight-panel]")
    ib_preflight.wait_for(state="visible", timeout=30_000)
    ib_preflight.locator("[data-ib-run-ready]").wait_for(state="visible", timeout=30_000)
    ib_preflight.locator("[data-ib-run-ready]").click()
    confirm = page.locator("[data-confirm-panel]")
    confirm.wait_for(state="visible", timeout=30_000)
    # Admission reconstructs and fingerprints the complete transition candidate
    # set.  On a cold certification database that can legitimately outlive
    # Playwright's 30-second action-navigation timeout even though the request
    # is still making bounded progress.
    confirm.locator("[data-confirm-continue]").click(no_wait_after=True)
    page.wait_for_url(re.compile(rf"/runs/{run_id}/pipeline(?:/\d+)?$"), timeout=120_000)
    if not re.search(rf"/runs/{run_id}/pipeline/\d+$", page.url):
        raise RuntimeError(
            "PIPELINE_ADMISSION_REJECTED: "
            + (page.locator("body").text_content() or "empty response body")
        )
    pipeline_id = int(page.url.rsplit("/", 1)[-1])
    absolute_deadline = time.monotonic() + absolute_deadline_seconds
    progress_deadline = time.monotonic() + progress_deadline_seconds
    previous_progress = None
    status = ""
    observer_engine = create_engine(env.database_url)
    observer_log = env.artifact_dir / "runtime-invariants.jsonl"
    try:
        while time.monotonic() < absolute_deadline and time.monotonic() < progress_deadline:
            status = (page.locator("[data-pipeline-status]").text_content() or "").strip()
            with Session(observer_engine) as observer_db:
                observation = observe_certification_runtime(
                    observer_db,
                    pipeline_id=pipeline_id,
                    lock_wait_seconds=5,
                    transaction_age_seconds=120,
                    no_progress_seconds=progress_deadline_seconds,
                )
            with observer_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(observation.as_dict(), default=str, sort_keys=True) + "\n")
            if observation.failures:
                raise RuntimeError(
                    "PRODUCTION_RUNTIME_INVARIANT_FAILED: "
                    + json.dumps(observation.as_dict(), default=str, sort_keys=True)
                )
            if status in TERMINAL_PIPELINE_STATUSES:
                break
            progress = (
                status,
                (page.locator("[data-pipeline-last-progress]").text_content() or "").strip(),
                (page.locator("[data-pipeline-progress-stage]").text_content() or "").strip(),
                (page.locator("[data-pipeline-items-processed]").text_content() or "").strip(),
                (page.locator("[data-pipeline-current-item]").text_content() or "").strip(),
            )
            if progress != previous_progress:
                previous_progress = progress
                progress_deadline = time.monotonic() + progress_deadline_seconds
            page.wait_for_timeout(500)
    finally:
        observer_engine.dispose()
    recorder.check(
        status in TERMINAL_PIPELINE_STATUSES,
        "pipeline progress page reached terminal state",
        area="Pipeline/Jobs",
        expected=sorted(TERMINAL_PIPELINE_STATUSES),
        actual=status,
    )
    screenshot = env.artifact_dir / "screenshots" / "gui" / "020-pipeline-progress.png"
    page.screenshot(path=screenshot, full_page=True)
    recorder.surfaces.append(
        {
            "name": "pipeline-progress",
            "path": f"/runs/{run_id}/pipeline/{pipeline_id}",
            "screenshot": screenshot.relative_to(env.artifact_dir).as_posix(),
            "status": 200,
        }
    )
    return pipeline_id, status


def _capture_surface(
    page: Page,
    env: CertificationEnvironment,
    recorder: CertificationRecorder,
    *,
    name: str,
    path: str,
    ordinal: int,
    heading: str,
) -> None:
    response = page.goto(f"{env.base_url}{path}")
    status = response.status if response else None
    recorder.check(
        status == 200,
        f"{name} rendered successfully",
        area=_surface_area(name),
        expected=200,
        actual=status,
    )
    body = page.locator("body").inner_text()
    recorder.check(
        heading.lower() in body.lower(),
        f"{name} exposes expected heading/content",
        area=_surface_area(name),
        expected=heading,
        actual=body[:200],
    )
    recorder.check(
        DECOY_TICKER not in body,
        f"{name} excludes the decoy canary",
        area="Isolation/Integrity",
        expected="decoy absent",
        actual="present" if DECOY_TICKER in body else "absent",
    )
    screenshot = env.artifact_dir / "screenshots" / "gui" / f"{ordinal:03d}-{name}.png"
    page.screenshot(path=screenshot, full_page=True)
    recorder.surfaces.append(
        {
            "name": name,
            "path": path,
            "screenshot": screenshot.relative_to(env.artifact_dir).as_posix(),
            "status": status,
        }
    )


def _compare_pipeline_page(
    page: Page,
    recorder: CertificationRecorder,
    db_steps: list[dict],
) -> None:
    gui_steps = page.locator("[data-pipeline-step-row]").evaluate_all(
        """
        rows => rows.map(row => ({
          name: row.dataset.stepName,
          status: row.querySelector('[data-step-status]')?.textContent.trim() || '',
          order: Number(row.cells[0]?.textContent.trim()),
          error: row.querySelector('[data-step-error]')?.textContent.trim() || ''
        }))
        """
    )
    recorder.check(
        len(gui_steps) == len(db_steps),
        "pipeline GUI step cardinality matches DB",
        area="Pipeline/Jobs",
        expected=len(db_steps),
        actual=len(gui_steps),
    )
    by_name = {item["step_name"]: item for item in db_steps}
    for gui in gui_steps:
        db = by_name.get(gui["name"])
        recorder.check(
            db is not None,
            f"pipeline step {gui['name']} exists in DB",
            area="Pipeline/Jobs",
            expected=True,
            actual=db is not None,
        )
        if db:
            for field in ("status", "order"):
                db_field = "step_order" if field == "order" else field
                recorder.check(
                    str(gui[field]) == str(db[db_field]),
                    f"pipeline {gui['name']} {field} GUI↔DB",
                    area="Pipeline/Jobs",
                    expected=str(db[db_field]),
                    actual=str(gui[field]),
                )


def _compare_run_detail(
    page: Page,
    engine,
    recorder: CertificationRecorder,
    run_id: int,
) -> None:
    db_rows = query_rows(
        engine,
        """
        select c.ticker, c.sector, c.final_rank, c.final_score, c.combined_decision,
               c.fundamental_score, coalesce(c.dual_score, t.dual_score) as dual_score,
               c.days_until_earnings,
               c.is_complete, c.has_warning
        from combined_results c
        left join technical_scores t on t.run_id=c.run_id and t.ticker=c.ticker
        where c.run_id=:run_id order by c.final_rank nulls last, c.ticker
        """,
        {"run_id": run_id},
    )
    gui_rows = page.locator("[data-cockpit-row]").evaluate_all(
        "rows => rows.map(row => ({...row.dataset}))"
    )
    recorder.check(
        len(gui_rows) == len(db_rows) == len(CANONICAL_TICKERS),
        "combined cockpit shows the complete canonical universe",
        area="Fundamentals",
        expected=len(CANONICAL_TICKERS),
        actual=len(gui_rows),
    )
    db_by_ticker = {row["ticker"]: row for row in db_rows}
    mapping = {
        "rank": "final_rank",
        "sector": "sector",
        "decision": "combined_decision",
        "finalScore": "final_score",
        "fundamentalScore": "fundamental_score",
        "technicalScore": "dual_score",
        "daysUntilEarnings": "days_until_earnings",
    }
    for gui in gui_rows:
        ticker = gui["ticker"]
        db = db_by_ticker.get(ticker)
        recorder.check(
            db is not None,
            f"cockpit ticker {ticker} exists in run DB",
            area="Isolation/Integrity",
            expected=True,
            actual=db is not None,
        )
        if db is None:
            continue
        for gui_field, expected in (
            ("incomplete", "false" if db["is_complete"] else "true"),
            ("hasWarning", "true" if db["has_warning"] else "false"),
        ):
            recorder.check(
                gui.get(gui_field) == expected,
                f"{ticker} {gui_field} GUI↔DB",
                area="Isolation/Integrity",
                expected=expected,
                actual=gui.get(gui_field),
            )
        for gui_field, db_field in mapping.items():
            expected = _normalized_scalar(db[db_field])
            actual = _normalized_scalar(gui.get(gui_field))
            recorder.check(
                actual == expected,
                f"{ticker} {gui_field} GUI↔DB",
                area=_field_area(gui_field),
                expected=expected,
                actual=actual,
            )
    ranking_summary = query_rows(
        engine,
        """
        select count(*) as result_count, count(distinct ranking_profile) as profile_count
        from ranking_results where run_id=:run_id
        """,
        {"run_id": run_id},
    )[0]
    expected_summary = (
        f"{ranking_summary['result_count']} profile rows across "
        f"{ranking_summary['profile_count']} profiles."
    )
    recorder.check(
        expected_summary in page.locator("body").inner_text(),
        "run detail ranking summary GUI↔DB",
        area="Rankings",
        expected=expected_summary,
        actual="present" if expected_summary in page.locator("body").inner_text() else "absent",
    )
    _compare_expanded_evidence(page, engine, recorder, run_id)


def _compare_expanded_evidence(
    page: Page, engine, recorder: CertificationRecorder, run_id: int
) -> None:
    gui = page.locator("[data-cockpit-row]").evaluate_all(
        """
        rows => rows.map(row => {
          const detail = row.nextElementSibling;
          const sections = {};
          for (const section of detail?.querySelectorAll('.detail-grid > div') || []) {
            const name = section.querySelector('h3')?.textContent.trim();
            const values = {};
            for (const dt of section.querySelectorAll('dt')) {
              values[dt.textContent.trim()] = dt.nextElementSibling?.textContent.trim() || '';
            }
            if (name) sections[name] = values;
          }
          return {ticker: row.dataset.ticker, sections};
        })
        """
    )
    fundamentals = query_rows(
        engine,
        """
        select ticker, scoring_model_version, fundamental_score, data_coverage_score,
          growth_quality_score, profitability_quality_score, fcf_quality_score,
          earnings_quality_score, capital_efficiency_score, balance_sheet_quality_score,
          valuation_quality_score, forward_quality_score, shareholder_quality_score,
          liquidity_risk_score, missing_data_penalty
        from fundamental_scores where run_id=:run_id
        """,
        {"run_id": run_id},
    )
    technicals = query_rows(
        engine,
        """
        select ticker, dual_score, trend_score, momentum_score, setup_score, risk_score,
          market_score, combined_relative_strength_score, htf_score, technical_confidence
        from technical_scores where run_id=:run_id
        """,
        {"run_id": run_id},
    )
    fund_by = {row["ticker"]: row for row in fundamentals}
    tech_by = {row["ticker"]: row for row in technicals}
    fund_map = {
        "Score": "fundamental_score",
        "Coverage": "data_coverage_score",
        "Growth": "growth_quality_score",
        "Profitability": "profitability_quality_score",
        "FCF": "fcf_quality_score",
        "Earnings": "earnings_quality_score",
        "Capital": "capital_efficiency_score",
        "Balance": "balance_sheet_quality_score",
        "Valuation": "valuation_quality_score",
        "Forward": "forward_quality_score",
        "Shareholder": "shareholder_quality_score",
        "Liquidity": "liquidity_risk_score",
        "Penalty": "missing_data_penalty",
    }
    tech_map = {
        "Score": "dual_score",
        "Trend": "trend_score",
        "Momentum": "momentum_score",
        "Setup": "setup_score",
        "Risk": "risk_score",
        "Market": "market_score",
        "RS": "combined_relative_strength_score",
        "HTF": "htf_score",
        "Confidence": "technical_confidence",
    }
    for item in gui:
        ticker = item["ticker"]
        for section_name, mapping, db_by, area in (
            ("Fundamentals", fund_map, fund_by, "Fundamentals"),
            ("Technicals", tech_map, tech_by, "Technicals"),
        ):
            values = item["sections"].get(section_name, {})
            db = db_by.get(ticker)
            if db is None:
                continue
            for label, field in mapping.items():
                expected = _display_value(db[field])
                actual = values.get(label, "").splitlines()[0].strip()
                recorder.check(
                    actual == expected,
                    f"{ticker} expanded {section_name} {label} GUI↔DB",
                    area=area,
                    expected=expected,
                    actual=actual,
                )


def _compare_current_surface(
    page: Page, engine, recorder: CertificationRecorder, run_id: int, name: str
) -> None:
    if name == "ceri":
        _compare_ceri(page, engine, recorder, run_id)
    elif name == "ceri-changes":
        _compare_ceri_alerts(page, engine, recorder, run_id)
    elif name == "winner-evidence":
        _compare_winner(page, engine, recorder, run_id)
    elif name == "market-changes":
        _compare_lifecycle(page, engine, recorder, run_id)
    elif name == "alerts":
        _compare_lifecycle_alerts(page, engine, recorder)
    elif name == "sector-rotation":
        _compare_sector_rotation(page, engine, recorder, run_id)
    elif name == "ranking-profile":
        _compare_ranking_profile(page, engine, recorder, run_id)
    elif name in {"runs", "history"}:
        body = page.locator("body").inner_text()
        recorder.check(
            str(run_id) in body,
            f"{name} exposes canonical run",
            area="History/Exports",
            expected=str(run_id),
            actual="present" if str(run_id) in body else "absent",
        )


def _compare_ceri(page: Page, engine, recorder: CertificationRecorder, run_id: int) -> None:
    gui_rows = _table_rows(page, {"Ticker", "Opportunity", "Risk", "Confidence", "Posture"})
    produced_rows = query_rows(
        engine,
        """
        select ticker, opportunity_score, event_risk_score, data_confidence, posture
        from ceri_score_snapshots where run_id=:run_id order by ticker
        """,
        {"run_id": run_id},
    )
    with Session(engine) as db:
        eligible_rows = list(
            db.scalars(eligible_snapshot_select().where(CeriScoreSnapshot.run_id == run_id))
        )
    db_rows = [
        {
            "ticker": row.ticker,
            "opportunity_score": row.opportunity_score,
            "event_risk_score": row.event_risk_score,
            "data_confidence": row.data_confidence,
            "posture": row.posture,
        }
        for row in eligible_rows
    ]
    recorder.check(
        len(gui_rows) == len(db_rows),
        "CERI visible row count matches DB",
        area="CERI",
        expected=len(db_rows),
        actual=len(gui_rows),
    )
    recorder.check(
        len(produced_rows) == len(CANONICAL_TICKERS),
        "CERI produced one run-owned score snapshot per canonical ticker",
        area="CERI",
        expected=len(CANONICAL_TICKERS),
        actual=len(produced_rows),
    )
    db_by = {row["ticker"]: row for row in db_rows}
    for gui in gui_rows:
        ticker = _ticker_from_cell(gui["Ticker"], db_by)
        db = db_by.get(ticker)
        if not db:
            recorder.check(False, f"CERI {ticker} belongs to run", area="CERI")
            continue
        comparisons = {
            "Opportunity": (
                _display_value(db["opportunity_score"])
                if db["opportunity_score"] is not None
                else "Unrated"
            ),
            "Risk": _display_value(db["event_risk_score"]),
            "Confidence": str(db["data_confidence"]),
            "Posture": str(db["posture"]),
        }
        for field, expected in comparisons.items():
            actual = gui[field].splitlines()[0].strip()
            matches = actual.startswith(expected) if field == "Risk" else actual == expected
            recorder.check(
                matches,
                f"CERI {ticker} {field} GUI↔DB",
                area="CERI",
                expected=expected,
                actual=gui[field],
            )


def _assert_ceri_upgrade_baseline(
    engine,
    recorder: CertificationRecorder,
    run_id: int,
    *,
    baseline_snapshot_id: int,
) -> None:
    config = load_ceri_config()
    upgrade_threshold = float(config.change_thresholds["opportunity_upgrade_threshold"])
    minimum_coverage = float(config.revision.minimum_component_coverage_pct)
    rows = query_rows(
        engine,
        """
        select cur.id current_id, cur.opportunity_score current_score,
               cur.opportunity_coverage_pct current_coverage,
               cur.data_confidence current_confidence,
               cur.comparison_state, cur.comparison_snapshot_id,
               prior.id prior_id, prior.opportunity_score prior_score
        from ceri_score_snapshots cur
        left join ceri_score_snapshots prior on prior.id=cur.comparison_snapshot_id
        where cur.run_id=:run_id and cur.ticker='ALFA'
        """,
        {"run_id": run_id},
    )
    recorder.check(
        bool(rows),
        "CERI ALFA current snapshot exists for baseline comparison",
        area="CERI",
        expected=True,
        actual=bool(rows),
    )
    if not rows:
        return
    row = rows[0]
    recorder.check(
        row["prior_id"] == baseline_snapshot_id,
        "CERI ALFA uses the intended pre-run baseline snapshot",
        area="CERI",
        expected=baseline_snapshot_id,
        actual=row["prior_id"],
    )
    recorder.check(
        row["comparison_state"] == "COMPARABLE"
        and row["comparison_snapshot_id"] == baseline_snapshot_id,
        "CERI ALFA current snapshot is comparable to the pre-run baseline",
        area="CERI",
        expected={"state": "COMPARABLE", "snapshot_id": baseline_snapshot_id},
        actual={
            "state": row["comparison_state"],
            "snapshot_id": row["comparison_snapshot_id"],
        },
    )
    recorder.check(
        row["current_coverage"] is not None
        and float(row["current_coverage"]) >= minimum_coverage
        and row["current_confidence"] != "Insufficient",
        "CERI ALFA current score satisfies production coverage and confidence gates",
        area="CERI",
        expected={"minimum_coverage": minimum_coverage, "confidence": "rated"},
        actual={
            "coverage": row["current_coverage"],
            "confidence": row["current_confidence"],
        },
    )
    recorder.check(
        row["prior_score"] is not None
        and row["current_score"] is not None
        and float(row["prior_score"]) < upgrade_threshold <= float(row["current_score"]),
        "CERI ALFA crosses the configured opportunity upgrade threshold",
        area="CERI",
        expected=f"prior < {upgrade_threshold} <= current",
        actual={"prior": row["prior_score"], "current": row["current_score"]},
    )
    upgrade_changes = query_rows(
        engine,
        """
        select id, from_snapshot_id, to_snapshot_id, comparison_state
        from ceri_change_events
        where company_id=(select company_id from ceri_score_snapshots where id=:current_id)
          and from_snapshot_id=:baseline_id and to_snapshot_id=:current_id
          and change_type='OPPORTUNITY_UPGRADED'
        """,
        {"baseline_id": baseline_snapshot_id, "current_id": row["current_id"]},
    )
    recorder.check(
        bool(upgrade_changes)
        and all(change["comparison_state"] == "COMPARABLE" for change in upgrade_changes),
        "CERI emitted the expected comparable OPPORTUNITY_UPGRADED transition",
        area="CERI",
        expected="OPPORTUNITY_UPGRADED",
        actual=upgrade_changes,
    )


def _compare_ceri_alerts(page: Page, engine, recorder: CertificationRecorder, run_id: int) -> None:
    gui_rows = _table_rows(
        page,
        {
            "Ticker",
            "Alert",
            "Importance",
            "Change",
            "Risk",
            "Confidence",
            "When",
            "Status",
            "Action",
        },
    )
    db_rows = query_rows(
        engine,
        """
        select a.ticker, a.importance, a.status, c.change_type
        from ceri_alert_events a
        join ceri_change_events c on c.id=a.source_change_event_id
        join ceri_score_snapshots s on s.id=c.to_snapshot_id
        where s.run_id=:run_id order by a.created_at desc, a.id desc
        """,
        {"run_id": run_id},
    )
    recorder.check(
        bool(db_rows),
        "CERI run produced user-visible alerts",
        area="CERI",
        expected=">0",
        actual=len(db_rows),
    )
    recorder.check(
        len(gui_rows) == len(db_rows),
        "CERI visible alert count matches DB",
        area="CERI",
        expected=len(db_rows),
        actual=len(gui_rows),
    )
    expected_rows = Counter(
        (
            str(row["ticker"]),
            str(row["change_type"]).replace("_", " ").title(),
            str(row["importance"]).title(),
            str(row["status"]),
        )
        for row in db_rows
    )
    actual_rows = Counter(
        (
            row["Ticker"].strip(),
            row["Alert"].strip(),
            row["Importance"].strip(),
            row["Status"].strip(),
        )
        for row in gui_rows
    )
    recorder.check(
        actual_rows == expected_rows,
        "CERI visible alert identities match DB including multiple alerts per ticker",
        area="CERI",
        expected=sorted(expected_rows.elements()),
        actual=sorted(actual_rows.elements()),
    )


def _compare_winner(page: Page, engine, recorder: CertificationRecorder, run_id: int) -> None:
    gui_rows = _table_rows(page, {"Ticker", "Probability", "Grade", "n"})
    db_rows = query_rows(
        engine,
        """
        select p.ticker, e.point_probability, e.evidence_grade, e.sample_n, e.effective_n
        from winner_prediction_snapshots p
        left join winner_probability_estimates e on e.prediction_id=p.id
        where p.run_id=:run_id order by p.ticker
        """,
        {"run_id": run_id},
    )
    recorder.check(
        len(gui_rows) == len(db_rows),
        "Winner Evidence visible row count matches DB",
        area="Winner Evidence",
        expected=len(db_rows),
        actual=len(gui_rows),
    )
    db_by = {row["ticker"]: row for row in db_rows}
    for gui in gui_rows:
        ticker = _ticker_from_cell(gui["Ticker"], db_by)
        db = db_by.get(ticker)
        if not db:
            recorder.check(False, f"Winner {ticker} belongs to run", area="Winner Evidence")
            continue
        probability = (
            f"{float(db['point_probability']) * 100:.0f}%"
            if db["point_probability"] is not None
            else "Insufficient"
        )
        recorder.check(
            gui["Probability"].splitlines()[0].strip() == probability,
            f"Winner {ticker} probability GUI↔DB",
            area="Winner Evidence",
            expected=probability,
            actual=gui["Probability"],
        )
        evidence_grade = (
            str(db["evidence_grade"]) if db["evidence_grade"] is not None else "Missing"
        )
        recorder.check(
            gui["Grade"].strip() == evidence_grade,
            f"Winner {ticker} evidence grade GUI↔DB",
            area="Winner Evidence",
            expected=evidence_grade,
            actual=gui["Grade"],
        )


def _mature_winner_evidence(
    page: Page,
    engine,
    env: CertificationEnvironment,
    recorder: CertificationRecorder,
    run_id: int,
) -> dict:
    prediction = query_rows(
        engine,
        """
        select id, run_id, ticker, prediction_as_of_date, source_data_cutoff_at,
               captured_at, planned_entry_session, eligibility_status, setup_family,
               setup_classification, ranking_profile, fundamental_score, technical_score,
               combined_score, market_regime, market_risk_state, sector_state, sector_rank,
               feature_schema_version, feature_vector_hash, config_hash,
               calculation_version, revision, feature_json, source_ids_json,
               warning_flags_json, lineage_json
        from winner_prediction_snapshots
        where run_id=:run_id and ticker='ALFA'
        order by id limit 1
        """,
        {"run_id": run_id},
    )[0]
    prediction_id = int(prediction["id"])
    immutable_hash_before = _payload_hash(prediction)
    pending_before = query_rows(
        engine,
        """
        select count(*) as value from winner_forward_outcomes
        where prediction_id=:prediction_id and status='PENDING' and is_current_revision
        """,
        {"prediction_id": prediction_id},
    )[0]["value"]
    recorder.check(
        pending_before > 0,
        "canonical prediction begins with pending forward outcomes",
        area="Winner Evidence",
        expected=">0",
        actual=pending_before,
    )

    # Certification executes the production capture path but may not create a
    # second, unrelated mutation root.  Exercise the real browser endpoint and
    # prove that the certification mutation fence leaves durable state intact.
    before_job_id = query_rows(
        engine,
        "select coalesce(max(id), 0) as value from background_jobs",
    )[0]["value"]
    response = page.goto(f"{env.base_url}/winner-probability/operations")
    recorder.check(
        response is not None and response.status == 200,
        "Winner Evidence operations rendered before fenced maturation request",
        area="Winner Evidence",
        expected=200,
        actual=response.status if response else None,
    )
    page.locator(
        'form[action="/api/winner-probability/outcomes/process"] button[type="submit"]'
    ).click()
    output = page.locator('form[action="/api/winner-probability/outcomes/process"] output')
    output.wait_for(state="visible", timeout=10_000)
    expect(output).to_contain_text(
        re.compile(r"CERTIFICATION_MUTATION_FORBIDDEN|not authorized during certification"),
        timeout=10_000,
    )
    rejection = (output.text_content() or "").strip()
    after_job_id = query_rows(
        engine,
        "select coalesce(max(id), 0) as value from background_jobs",
    )[0]["value"]
    prediction_after = query_rows(
        engine,
        """
        select id, run_id, ticker, prediction_as_of_date, source_data_cutoff_at,
               captured_at, planned_entry_session, eligibility_status, setup_family,
               setup_classification, ranking_profile, fundamental_score, technical_score,
               combined_score, market_regime, market_risk_state, sector_state, sector_rank,
               feature_schema_version, feature_vector_hash, config_hash,
               calculation_version, revision, feature_json, source_ids_json,
               warning_flags_json, lineage_json
        from winner_prediction_snapshots where id=:prediction_id
        """,
        {"prediction_id": prediction_id},
    )[0]
    pending_after = query_rows(
        engine,
        """
        select count(*) as value from winner_forward_outcomes
        where prediction_id=:prediction_id and status='PENDING' and is_current_revision
        """,
        {"prediction_id": prediction_id},
    )[0]["value"]
    immutable_hash_after = _payload_hash(prediction_after)
    recorder.check(
        (
            "CERTIFICATION_MUTATION_FORBIDDEN" in rejection
            or "not authorized during certification" in rejection
        ),
        "certification rejects unrelated Winner maturation through the production endpoint",
        area="Isolation/Integrity",
        expected="certification mutation rejection",
        actual=rejection,
    )
    recorder.check(
        after_job_id == before_job_id,
        "rejected Winner maturation creates no durable job",
        area="Isolation/Integrity",
        expected=before_job_id,
        actual=after_job_id,
    )
    recorder.check(
        immutable_hash_after == immutable_hash_before and pending_after == pending_before,
        "rejected Winner maturation leaves prediction and outcomes unchanged",
        area="Winner Evidence",
        expected={"hash": immutable_hash_before, "pending": pending_before},
        actual={"hash": immutable_hash_after, "pending": pending_after},
    )
    _capture_surface(
        page,
        env,
        recorder,
        name="winner-operations",
        path="/winner-probability/operations",
        ordinal=180,
        heading="Winner Probability Operations",
    )
    return {
        "prediction_id": prediction_id,
        "ticker": prediction["ticker"],
        "mutation_status": "REJECTED",
        "reason": rejection,
        "immutable_hash_before": immutable_hash_before,
        "immutable_hash_after": immutable_hash_after,
        "pending_before": pending_before,
        "pending_after": pending_after,
    }

    seeded = _seed_later_market_bars(engine, prediction_id)
    recorder.check(
        seeded > 0,
        "later deterministic market bars were persisted after prediction capture",
        area="Winner Evidence",
        expected=">0",
        actual=seeded,
    )

    before_job_id = query_rows(
        engine,
        "select coalesce(max(id), 0) as value from background_jobs",
    )[0]["value"]
    response = page.goto(f"{env.base_url}/winner-probability/operations")
    recorder.check(
        response is not None and response.status == 200,
        "Winner Evidence operations rendered before maturation",
        area="Winner Evidence",
        expected=200,
        actual=response.status if response else None,
    )
    page.locator(
        'form[action="/api/winner-probability/outcomes/process"] button[type="submit"]'
    ).click()
    page.locator('form[action="/api/winner-probability/outcomes/process"] output').wait_for(
        state="visible", timeout=10_000
    )

    deadline = time.monotonic() + 60
    job: dict = {}
    while time.monotonic() < deadline:
        rows = query_rows(
            engine,
            """
            select id, status, result_json, error_message
            from background_jobs
            where id > :before_job_id and job_type='WINNER_OUTCOME_MATURATION'
            order by id desc limit 1
            """,
            {"before_job_id": before_job_id},
        )
        if rows:
            job = rows[0]
            if job["status"] in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}:
                break
        page.wait_for_timeout(100)
    job_result = job.get("result_json") or {}
    expected_job_status = (
        "PARTIAL"
        if int(job_result.get("failed_h5", 0)) or int(job_result.get("pending_h5_after_cycle", 0))
        else "COMPLETED"
    )
    recorder.check(
        bool(job),
        "browser action created a WINNER_OUTCOME_MATURATION background job",
        area="Winner Evidence",
        expected=True,
        actual=bool(job),
    )
    recorder.check(
        job.get("status") == expected_job_status,
        "browser-queued Winner Evidence maturation job reached its count-derived status",
        area="Winner Evidence",
        expected=expected_job_status,
        actual=job.get("status"),
    )
    recorder.check(
        int(job_result.get("failed_h5", -1)) == 0,
        "Winner Evidence primary H5 maturation completed without failed outcomes",
        area="Winner Evidence",
        expected=0,
        actual=job_result.get("failed_h5"),
    )
    recorder.check(
        int(job_result.get("pending_h5_after_cycle", -1)) == 0,
        "Winner Evidence primary H5 queue drained completely",
        area="Winner Evidence",
        expected=0,
        actual=job_result.get("pending_h5_after_cycle"),
    )
    recorder.check(
        int(job_result.get("matured_h5", 0)) > 0,
        "Winner Evidence matured at least one primary H5 outcome",
        area="Winner Evidence",
        expected=">0",
        actual=job_result.get("matured_h5"),
    )
    recorder.check(
        int(job_result.get("target_stop_matured", 0)) > 0,
        "Winner Evidence matured at least one primary target/stop outcome",
        area="Winner Evidence",
        expected=">0",
        actual=job_result.get("target_stop_matured"),
    )

    _capture_surface(
        page,
        env,
        recorder,
        name="winner-operations",
        path="/winner-probability/operations",
        ordinal=180,
        heading="Winner Probability Operations",
    )
    operations_rows = _table_rows(
        page, {"ID", "Type", "Status", "Run", "Started", "Completed", "Counts", "Error"}
    )
    processing_runs = query_rows(
        engine,
        """
        select id, status, counts_json from winner_processing_runs
        where background_job_id=:job_id order by id desc limit 1
        """,
        {"job_id": job.get("id")},
    )
    recorder.check(
        bool(processing_runs),
        "Winner Evidence maturation persisted its processing run",
        area="Winner Evidence",
        expected=True,
        actual=bool(processing_runs),
    )
    processing_run = processing_runs[0] if processing_runs else {"id": None, "counts_json": {}}
    maturation_row = next(
        (
            row
            for row in operations_rows
            if row["Type"] == "WINNER_OUTCOME_MATURATION" and row["ID"] == str(processing_run["id"])
        ),
        None,
    )
    recorder.check(
        maturation_row is not None and maturation_row["Status"] == job.get("status"),
        "Winner operations GUI status matches the background job",
        area="Winner Evidence",
        expected=job.get("status"),
        actual=maturation_row["Status"] if maturation_row else "missing",
    )
    gui_counts = json.loads(maturation_row["Counts"]) if maturation_row else {}
    recorder.check(
        gui_counts == processing_run["counts_json"],
        "Winner operations GUI counts match the processing-run DB record",
        area="Winner Evidence",
        expected=processing_run["counts_json"],
        actual=gui_counts,
    )

    prediction_after = query_rows(
        engine,
        """
        select id, run_id, ticker, prediction_as_of_date, source_data_cutoff_at,
               captured_at, planned_entry_session, eligibility_status, setup_family,
               setup_classification, ranking_profile, fundamental_score, technical_score,
               combined_score, market_regime, market_risk_state, sector_state, sector_rank,
               feature_schema_version, feature_vector_hash, config_hash,
               calculation_version, revision, feature_json, source_ids_json,
               warning_flags_json, lineage_json
        from winner_prediction_snapshots where id=:prediction_id
        """,
        {"prediction_id": prediction_id},
    )[0]
    immutable_hash_after = _payload_hash(prediction_after)
    recorder.check(
        immutable_hash_after == immutable_hash_before,
        "original point-in-time prediction remains immutable after outcome maturation",
        area="Winner Evidence",
        expected=immutable_hash_before,
        actual=immutable_hash_after,
    )

    matured_forward = query_rows(
        engine,
        """
        select entry_model, horizon_sessions, entry_session, due_session, status,
               close_return_pct, mfe_pct, mae_pct, positive_return, revision,
               source_bar_lineage_hash, matured_at
        from winner_forward_outcomes
        where prediction_id=:prediction_id and is_current_revision
          and entry_model='NEXT_OPEN' and horizon_sessions=5
        order by entry_model, horizon_sessions
        """,
        {"prediction_id": prediction_id},
    )
    matured_target_stop = query_rows(
        engine,
        """
        select t.entry_model, t.status, t.first_event, t.evaluated_at,
               t.primary_winner, t.revision, t.source_bar_lineage_hash
        from winner_target_stop_outcomes t
        join winner_outcome_definitions d on d.id=t.outcome_definition_id
        where t.prediction_id=:prediction_id and t.is_current_revision and d.is_primary
          and t.entry_model='NEXT_OPEN' and t.horizon_sessions=5
        order by t.id
        """,
        {"prediction_id": prediction_id},
    )
    recorder.check(
        bool(matured_forward)
        and all(
            row["status"] == "MATURED" and row["matured_at"] is not None for row in matured_forward
        ),
        "ALFA primary NEXT_OPEN H5 outcome matured from later bars",
        area="Winner Evidence",
        expected="MATURED with timestamp",
        actual=[
            {"status": row["status"], "matured_at": row["matured_at"]} for row in matured_forward
        ],
    )
    recorder.check(
        bool(matured_target_stop)
        and all(
            row["status"] == "MATURED" and row["evaluated_at"] is not None
            for row in matured_target_stop
        ),
        "ALFA primary target/stop outcome matured",
        area="Winner Evidence",
        expected="MATURED with timestamp",
        actual=[
            {"status": row["status"], "evaluated_at": row["evaluated_at"]}
            for row in matured_target_stop
        ],
    )

    _capture_surface(
        page,
        env,
        recorder,
        name="winner-matured-prediction",
        path=f"/winner-probability/predictions/{prediction_id}",
        ordinal=190,
        heading="ALFA Winner Evidence",
    )
    _compare_matured_outcomes(page, recorder, matured_forward, matured_target_stop)
    _capture_surface(
        page,
        env,
        recorder,
        name="winner-outcomes",
        path="/winner-probability/outcomes?min_sample=0",
        ordinal=200,
        heading="Outcome Explorer",
    )
    return {
        "prediction_id": prediction_id,
        "ticker": prediction["ticker"],
        "later_bars_seeded": seeded,
        "matured_forward_outcomes": len(matured_forward),
        "matured_target_stop_outcomes": len(matured_target_stop),
        "immutable_hash_before": immutable_hash_before,
        "immutable_hash_after": immutable_hash_after,
        "job_id": job.get("id"),
        "job_status": job.get("status"),
        "job_counts": job_result,
        "processing_run_id": processing_run["id"],
    }


def _seed_later_market_bars(engine, prediction_id: int) -> int:
    bounds = query_rows(
        engine,
        """
        select min(entry_session) as start_date, max(due_session) as end_date
        from winner_forward_outcomes where prediction_id=:prediction_id
        """,
        {"prediction_id": prediction_id},
    )[0]
    start_date = bounds["start_date"]
    end_date = bounds["end_date"]
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise AssertionError("winner outcome session bounds were not materialized")

    inserted = 0
    with Session(engine) as db:
        for ticker, fallback in (
            *((ticker, 100.0) for ticker in CANONICAL_TICKERS),
            ("SPY", 500.0),
            ("XLK", 250.0),
        ):
            latest = db.scalar(
                select(PriceBar)
                .where(PriceBar.ticker == ticker)
                .where(PriceBar.what_to_show == "ADJUSTED_LAST")
                .order_by(PriceBar.bar_date.desc())
                .limit(1)
            )
            cursor = start_date
            base = fallback
            if latest is not None:
                base = float(latest.close or fallback)
                if latest.bar_date >= cursor:
                    cursor = next_regular_session(latest.bar_date)
            bars: list[HistoricalBar] = []
            index = 0
            while cursor <= end_date:
                open_price = base * (1.0 + 0.01 * (index + 1))
                close_price = open_price * 1.015
                bars.append(
                    HistoricalBar(
                        ticker=ticker,
                        bar_date=cursor,
                        timeframe="1 day",
                        open=open_price,
                        high=open_price * 1.04,
                        low=open_price * 0.995,
                        close=close_price,
                        volume=1_500_000 + index * 10_000,
                        source="QA_LATER_MARKET_BARS",
                        what_to_show="ADJUSTED_LAST",
                        adjustment_type="adjusted",
                    )
                )
                cursor = next_regular_session(cursor)
                index += 1
            summary = cache_bars(db, bars)
            inserted += summary.inserted
        MarketDataObligationService().evaluate(db, now=datetime(2027, 1, 15, 22, tzinfo=UTC))
        db.commit()
    return inserted


def _compare_matured_outcomes(
    page: Page,
    recorder: CertificationRecorder,
    forward_rows: list[dict],
    target_rows: list[dict],
) -> None:
    gui_rows = _table_rows(
        page,
        {"Type", "Status", "Entry", "Due/Event", "Return", "MFE", "MAE", "Winner", "Revision"},
    )
    expected: list[dict[str, str]] = []
    for row in forward_rows:
        expected.append(
            {
                "Type": "Forward",
                "Status": str(row["status"]),
                "Entry": str(row["entry_session"] or ""),
                "Due/Event": str(row["due_session"] or ""),
                "Return": _one_decimal(row["close_return_pct"]),
                "MFE": _one_decimal(row["mfe_pct"]),
                "MAE": _one_decimal(row["mae_pct"]),
                "Winner": "Positive" if row["positive_return"] else "",
                "Revision": str(row["revision"]),
            }
        )
    for row in target_rows:
        expected.append(
            {
                "Type": "Target/Stop",
                "Status": str(row["status"]),
                "Entry": str(row["entry_model"]),
                "Due/Event": str(row["first_event"] or row["evaluated_at"] or ""),
                "Return": "",
                "MFE": "",
                "MAE": "",
                "Winner": (
                    "Yes"
                    if row["primary_winner"] is True
                    else "No"
                    if row["primary_winner"] is False
                    else ""
                ),
                "Revision": str(row["revision"]),
            }
        )
    normalized_gui = [{field: value.strip() for field, value in row.items()} for row in gui_rows]
    for index, expected_row in enumerate(expected, start=1):
        recorder.check(
            expected_row in normalized_gui,
            f"primary matured outcome row {index} GUI↔DB",
            area="Winner Evidence",
            expected=expected_row,
            actual=normalized_gui,
        )


def _one_decimal(value) -> str:
    return f"{float(value):.1f}" if value is not None else ""


def _payload_hash(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _compare_lifecycle(page: Page, engine, recorder: CertificationRecorder, run_id: int) -> None:
    gui_rows = _table_rows(
        page,
        {"Ticker", "Date", "Family", "Source", "State", "Actionability", "Confidence"},
    )
    lifecycle_count = int(
        query_rows(
            engine,
            """
        select count(*) as value from setup_lifecycle_events
        where evaluation_run_id in (
          select id from setup_lifecycle_evaluation_runs where source_run_id=:run_id
        ) and is_current_version is true
          and event_type in ('EPISODE_OPENED','STATE_TRANSITION','PHASE_TRANSITION')
        """,
            {"run_id": run_id},
        )[0]["value"]
    )
    signal_count = int(
        query_rows(
            engine,
            """
        select count(*) as value from signal_change_events e
        where evaluation_run_id in (
          select id from setup_lifecycle_evaluation_runs where source_run_id=:run_id
        ) and exists (
          select 1 from setup_signal_snapshot_current_selections selection
          where selection.selected_snapshot_id=e.current_snapshot_id
        )
        """,
            {"run_id": run_id},
        )[0]["value"]
    )
    response = page.request.get(
        f"{page.url.split('/runs/', 1)[0]}/api/setup-lifecycle/changes"
        f"?view_scope=HISTORICAL_RUN&run_id={run_id}&limit=500"
    )
    api = response.json()
    expected_count = lifecycle_count + signal_count
    recorder.check(
        response.ok and api["total"] == expected_count,
        "Market Changes API combines lifecycle and material signal events",
        area="Setup Lifecycle",
        expected=expected_count,
        actual=api.get("total"),
    )
    recorder.check(
        len(gui_rows) == len(api["items"]),
        "Market Changes visible row count matches API",
        area="Setup Lifecycle",
        expected=len(api["items"]),
        actual=len(gui_rows),
    )
    for gui, item in zip(gui_rows, api["items"], strict=False):
        expected_ticker = str(item["ticker"])
        recorder.check(
            gui["Ticker"].startswith(expected_ticker),
            f"Market Changes ticker {expected_ticker} GUI/API",
            area="Setup Lifecycle",
            expected=expected_ticker,
            actual=gui["Ticker"],
        )
        expected_state = f"{item['previous_state'] or 'New'} to {item['current_state'] or '—'}"
        recorder.check(
            gui["State"].strip().startswith(expected_state),
            f"Market Changes {expected_ticker} state GUI/API",
            area="Setup Lifecycle",
            expected=expected_state,
            actual=gui["State"],
        )
        age = item["state_age_sessions"]
        expected_age = f"Age {age if age is not None else '—'} sessions"
        recorder.check(
            expected_age in gui["State"],
            f"Market Changes {expected_ticker} state age GUI/API",
            area="Setup Lifecycle",
            expected=expected_age,
            actual=gui["State"],
        )
        recorder.check(
            str(item["source_type"]) in gui["Source"],
            f"Market Changes {expected_ticker} source type GUI/API",
            area="Setup Lifecycle",
            expected=item["source_type"],
            actual=gui["Source"],
        )


def _compare_lifecycle_alerts(page: Page, engine, recorder: CertificationRecorder) -> None:
    gui_rows = _table_rows(
        page,
        {"Ticker", "Date", "Alert Type", "Severity", "Source Type", "Review Status"},
    )
    db_count = int(
        query_rows(engine, "select count(*) as value from signal_alert_events", {})[0]["value"]
    )
    response = page.request.get(
        f"{page.url.split('/setup-lifecycle/', 1)[0]}/api/setup-lifecycle/alerts?limit=500"
    )
    api = response.json()
    recorder.check(
        response.ok and api["total"] == db_count,
        "Alert API total matches persisted full scope",
        area="Alerts",
        expected=db_count,
        actual=api.get("total"),
    )
    recorder.check(
        len(gui_rows) == len(api["items"]),
        "Alert Center visible row count matches API",
        area="Alerts",
        expected=len(api["items"]),
        actual=len(gui_rows),
    )
    for gui, item in zip(gui_rows, api["items"], strict=False):
        expected = {
            "Alert Type": item["alert_type"],
            "Severity": item["severity"],
            "Source Type": item["source_type"],
            "Review Status": item["review_status"],
        }
        for field, value in expected.items():
            recorder.check(
                gui[field].strip() == str(value),
                f"Alert {item['id']} {field} GUI/API",
                area="Alerts",
                expected=value,
                actual=gui[field],
            )


def _compare_sector_rotation(
    page: Page, engine, recorder: CertificationRecorder, run_id: int
) -> None:
    gui_rows = _table_rows(page, {"Rank", "Sector", "State", "Permission", "Final"})
    db_rows = query_rows(
        engine,
        """
        select r.current_rank, r.sector, r.rotation_state, r.sector_permission,
               r.sector_final_score
        from sector_rotation_rows r join sector_rotation_snapshots s on s.id=r.snapshot_id
        where s.run_id=:run_id order by r.current_rank nulls last, r.sector
        """,
        {"run_id": run_id},
    )
    recorder.check(
        len(gui_rows) == len(db_rows),
        "Sector Rotation visible row count matches DB",
        area="Sector Rotation",
        expected=len(db_rows),
        actual=len(gui_rows),
    )
    for gui, db in zip(gui_rows, db_rows, strict=False):
        expected = {
            "Rank": str(db["current_rank"] or ""),
            "Sector": str(db["sector"]),
            "State": str(db["rotation_state"]),
            "Permission": str(db["sector_permission"]).replace("_", " "),
            "Final": _display_value(db["sector_final_score"]),
        }
        for field, value in expected.items():
            recorder.check(
                gui[field].strip() == value,
                f"sector {db['sector']} {field} GUI↔DB",
                area="Sector Rotation",
                expected=value,
                actual=gui[field],
            )


def _compare_ranking_profile(
    page: Page, engine, recorder: CertificationRecorder, run_id: int
) -> None:
    payload = page.evaluate("() => JSON.parse(document.body.innerText)")
    profile = str(payload["profile"]["name"])
    gui_rows = payload["results"]
    db_rows = query_rows(
        engine,
        """
        select profile_rank, ticker, company_name, sector, ranking_profile,
               ranking_label, profile_score, technical_profile_score,
               fundamental_score, base_technical_score, technical_classification,
               fundamental_label, decision_label, position_size_hint,
               days_until_earnings, earnings_risk_level, is_complete, has_warning
        from ranking_results
        where run_id=:run_id and ranking_profile=:profile
        order by profile_rank nulls last, ticker
        """,
        {"run_id": run_id, "profile": profile},
    )
    recorder.check(
        len(gui_rows) == len(db_rows) == len(CANONICAL_TICKERS),
        f"ranking profile {profile} returns complete run universe",
        area="Rankings",
        expected=len(CANONICAL_TICKERS),
        actual=len(gui_rows),
    )
    field_map = {
        "rank": "profile_rank",
        "ticker": "ticker",
        "company_name": "company_name",
        "sector": "sector",
        "profile_name": "ranking_profile",
        "profile_label": "ranking_label",
        "profile_score": "profile_score",
        "technical_profile_score": "technical_profile_score",
        "fundamental_score": "fundamental_score",
        "base_technical_score": "base_technical_score",
        "technical_classification": "technical_classification",
        "fundamental_label": "fundamental_label",
        "decision": "decision_label",
        "position_size_hint": "position_size_hint",
        "days_until_earnings": "days_until_earnings",
        "earnings_risk": "earnings_risk_level",
        "is_complete": "is_complete",
        "has_warning": "has_warning",
    }
    gui_by_ticker = {row["ticker"]: row for row in gui_rows}
    recorder.check(
        len(gui_by_ticker) == len(gui_rows)
        and set(gui_by_ticker) == {row["ticker"] for row in db_rows},
        f"ranking profile {profile} returns the exact unique run target set",
        area="Rankings",
        expected=sorted(row["ticker"] for row in db_rows),
        actual=sorted(gui_by_ticker),
    )
    for db in db_rows:
        gui = gui_by_ticker.get(db["ticker"], {})
        for gui_field, db_field in field_map.items():
            expected = _normalized_scalar(db[db_field])
            actual = _normalized_scalar(gui.get(gui_field))
            recorder.check(
                actual == expected,
                f"ranking {profile} {db['ticker']} {gui_field} route↔DB",
                area="Rankings",
                expected=expected,
                actual=actual,
            )


def _acknowledge_one_alert(page: Page, engine, env, recorder, run_id: int) -> None:
    page.goto(f"{env.base_url}/setup-lifecycle/alerts?status=UNREAD")
    button = page.locator("[data-slse-alert-action$='/acknowledge']").first
    if button.count() == 0:
        recorder.warnings.append("No unread lifecycle alert was available for GUI acknowledgement.")
        return
    row = button.locator("xpath=ancestor::tr")
    alert_id = int(str(row.get_attribute("id")).split("-")[-1])
    button.click()
    row.locator("[data-slse-alert-status]").filter(has_text="Update failed").wait_for()
    db_status = query_rows(
        engine,
        "select status, acknowledged_at from signal_alert_events where id=:id",
        {"id": alert_id},
    )[0]
    recorder.check(
        db_status["status"] == "UNREAD" and db_status["acknowledged_at"] is None,
        "certification fences lifecycle alert acknowledgement",
        area="Isolation/Integrity",
        expected="UNREAD without timestamp",
        actual=db_status,
    )
    page.screenshot(
        path=env.artifact_dir / "screenshots" / "gui" / "085-alert-acknowledged.png",
        full_page=True,
    )


def _acknowledge_one_ceri_alert(page: Page, engine, env, recorder) -> None:
    page.goto(f"{env.base_url}/ceri/changes?status=UNREAD")
    button = page.locator("[data-ceri-alert-action$='/acknowledge']").first
    recorder.check(
        button.count() == 1,
        "CERI exposes an unread alert acknowledgement action",
        area="CERI",
        expected=1,
        actual=button.count(),
    )
    if button.count() == 0:
        return
    action = str(button.get_attribute("data-ceri-alert-action"))
    alert_id = int(action.split("/")[-2])
    row = button.locator("xpath=ancestor::tr")
    button.click()
    row.locator("[data-ceri-alert-status]").filter(has_text="Update failed").wait_for()
    db_status = query_rows(
        engine,
        "select status, acknowledged_at from ceri_alert_events where id=:id",
        {"id": alert_id},
    )[0]
    recorder.check(
        db_status["status"] == "UNREAD" and db_status["acknowledged_at"] is None,
        "certification fences CERI alert acknowledgement",
        area="Isolation/Integrity",
        expected="UNREAD without timestamp",
        actual=db_status,
    )
    page.screenshot(
        path=env.artifact_dir / "screenshots" / "gui" / "107-ceri-alert-acknowledged.png",
        full_page=True,
    )


def _capture_exports(page: Page, engine, env, recorder, run_id: int) -> list[dict]:
    export_specs = (
        ("combined.csv", f"/runs/{run_id}/exports/combined.csv"),
        ("fundamentals.csv", f"/runs/{run_id}/exports/fundamentals.csv"),
        ("technicals.csv", f"/runs/{run_id}/exports/technicals.csv"),
        ("raw.csv", f"/runs/{run_id}/exports/raw.csv"),
        ("ranking-profiles.csv", f"/runs/{run_id}/rankings/export.csv"),
        ("market-regime.csv", f"/runs/{run_id}/market-regime/export.csv"),
        ("sector-rotation.csv", f"/runs/{run_id}/sector-rotation/export.csv"),
        ("setup-lifecycle.csv", "/setup-lifecycle/export.csv"),
        ("winner-evidence.csv", f"/api/winner-probability/run/{run_id}/export.csv"),
        ("ceri.csv", f"/ceri/export.csv?run_id={run_id}"),
    )
    expected_count_sql = {
        "combined.csv": "select count(*) as value from combined_results where run_id=:run_id",
        "fundamentals.csv": "select count(*) as value from fundamental_scores where run_id=:run_id",
        "technicals.csv": "select count(*) as value from technical_scores where run_id=:run_id",
        "raw.csv": "select count(*) as value from raw_company_rows where run_id=:run_id",
        "ranking-profiles.csv": (
            "select count(*) as value from ranking_results where run_id=:run_id"
        ),
        "market-regime.csv": (
            "select count(*) as value from market_regime_snapshots where run_id=:run_id"
        ),
        "sector-rotation.csv": (
            "select count(*) as value from sector_rotation_rows where snapshot_id in "
            "(select id from sector_rotation_snapshots where run_id=:run_id)"
        ),
        "setup-lifecycle.csv": (
            "select ("
            "select count(*) from setup_lifecycle_events where evaluation_run_id in "
            "(select id from setup_lifecycle_evaluation_runs where source_run_id=:run_id)"
            " and is_current_version is true and event_type in "
            "('EPISODE_OPENED','STATE_TRANSITION','PHASE_TRANSITION')"
            ") + ("
            "select count(*) from signal_change_events e where evaluation_run_id in "
            "(select id from setup_lifecycle_evaluation_runs where source_run_id=:run_id)"
            " and exists (select 1 from setup_signal_snapshot_current_selections selection "
            "where selection.selected_snapshot_id=e.current_snapshot_id)"
            ") as value"
        ),
        "winner-evidence.csv": (
            "select count(*) as value from winner_prediction_snapshots where run_id=:run_id"
        ),
        "ceri.csv": "select count(*) as value from ceri_score_snapshots where run_id=:run_id",
    }
    results: list[dict] = []
    for name, path in export_specs:
        destination = env.artifact_dir / "exports" / name
        try:
            page.goto(f"{env.base_url}/runs/{run_id}")
            page.evaluate(
                """
                path => {
                  const a=document.createElement('a');
                  a.href=path;
                  a.textContent='certification export';
                  a.id='cert-export';
                  document.body.appendChild(a);
                }
                """,
                path,
            )
            with page.expect_download(timeout=30_000) as download_info:
                page.locator("#cert-export").click()
            download_info.value.save_as(destination)
            content = destination.read_text(encoding="utf-8-sig")
            row_count = max(0, len(list(csv.reader(content.splitlines()))) - 1)
            if name == "ceri.csv":
                with Session(engine) as db:
                    expected_count = len(
                        list(
                            db.scalars(
                                eligible_snapshot_select(CeriScoreSnapshot.id).where(
                                    CeriScoreSnapshot.run_id == run_id
                                )
                            )
                        )
                    )
            else:
                expected_count = int(
                    query_rows(engine, expected_count_sql[name], {"run_id": run_id})[0]["value"]
                )
            recorder.check(
                row_count == expected_count,
                f"{name} row count reconciles to DB",
                area="History/Exports",
                expected=expected_count,
                actual=row_count,
            )
            recorder.check(
                DECOY_TICKER not in content,
                f"{name} excludes decoy run data",
                area="History/Exports",
                expected="decoy absent",
                actual="present" if DECOY_TICKER in content else "absent",
            )
            results.append({"name": name, "status": "PASS", "row_count": row_count, "path": path})
        except Exception as exc:
            recorder.check(
                False,
                f"{name} downloaded through browser",
                area="History/Exports",
                expected="download",
                actual=str(exc),
            )
            results.append({"name": name, "status": "FAIL", "error": str(exc), "path": path})
    structured_specs = (
        ("market-regime.json", f"/runs/{run_id}/market-regime/export.json", "json"),
        ("sector-rotation.json", f"/runs/{run_id}/sector-rotation/export.json", "json"),
        ("sector-rotation.md", f"/runs/{run_id}/sector-rotation/brief.md", "markdown"),
        ("winner-evidence.json", f"/api/winner-probability/run/{run_id}/export.json", "json"),
        ("ceri.json", f"/ceri/export.json?run_id={run_id}", "json"),
    )
    for name, path, kind in structured_specs:
        destination = env.artifact_dir / "exports" / name
        try:
            page.goto(f"{env.base_url}/runs/{run_id}")
            page.evaluate(
                """
                path => {
                  const a=document.createElement('a');
                  a.href=path;
                  a.textContent='certification structured export';
                  a.id='cert-structured-export';
                  document.body.appendChild(a);
                }
                """,
                path,
            )
            with page.expect_download(timeout=30_000) as download_info:
                page.locator("#cert-structured-export").click()
            download_info.value.save_as(destination)
            content = destination.read_text(encoding="utf-8-sig")
            if kind == "json":
                json.loads(content)
            recorder.check(
                bool(content.strip()) and DECOY_TICKER not in content,
                f"{name} is valid and excludes decoy run data",
                area="History/Exports",
                expected="non-empty, parseable, decoy absent",
                actual=f"{len(content)} bytes",
            )
            results.append({"name": name, "status": "PASS", "path": path})
        except Exception as exc:
            recorder.check(
                False,
                f"{name} downloaded through browser",
                area="History/Exports",
                expected="download",
                actual=str(exc),
            )
            results.append({"name": name, "status": "FAIL", "error": str(exc), "path": path})
    return results


def _materialize_rankings_once(page: Page, engine, env, recorder, run_id: int) -> dict:
    before = _idempotency_counts(engine, run_id)
    page.goto(f"{env.base_url}/runs/{run_id}")
    _assert_standalone_rankings_retired(page, env, run_id)
    page.wait_for_load_state("networkidle")
    after_first = _idempotency_counts(engine, run_id)
    recorder.check(
        after_first["ranking_count"] > 0,
        "canonical pipeline materialized configured ranking profiles",
        area="Rankings",
        expected=">0",
        actual=after_first["ranking_count"],
    )
    return {"before": before, "after_first": after_first}


def _verify_idempotency(page: Page, engine, env, recorder, run_id: int, *, state: dict) -> dict:
    _assert_standalone_rankings_retired(page, env, run_id)
    page.wait_for_load_state("networkidle")
    after_second = _idempotency_counts(engine, run_id)
    recorder.check(
        state["after_first"] == after_second,
        "repeated rejected ranking refresh preserves canonical evidence",
        area="Isolation/Integrity",
        expected=state["after_first"],
        actual=after_second,
    )
    return {
        "operation": "canonical ranking read and repeated rejected standalone refresh",
        **state,
        "after_second": after_second,
    }


def _assert_standalone_rankings_retired(page: Page, env, run_id: int) -> None:
    expect(page.get_by_role("button", name="Refresh rankings").first).to_be_disabled()
    response = page.request.post(f"{env.base_url}/runs/{run_id}/rankings/refresh")
    assert response.status == 409
    assert response.json()["detail"]["code"] in {
        "STANDALONE_MUTATION_RETIRED",
        "CERTIFICATION_MUTATION_FORBIDDEN",
    }


def _idempotency_counts(engine, run_id: int) -> dict:
    return query_rows(
        engine,
        """
        select
          (select count(*) from ranking_results where run_id=:run_id) ranking_count,
          (select count(*) from winner_prediction_snapshots
           where run_id=:run_id) prediction_count,
          (select count(*) from signal_alert_events where evaluation_run_id in
             (select id from setup_lifecycle_evaluation_runs
              where source_run_id=:run_id)) alert_count,
          (select count(*) from ceri_alert_events a
             join ceri_change_events c on c.id=a.source_change_event_id
             join ceri_score_snapshots s on s.id=c.to_snapshot_id
             where s.run_id=:run_id) ceri_alert_count
        """,
        {"run_id": run_id},
    )[0]


def _verify_ib_boundary(env, recorder: CertificationRecorder) -> None:
    lines = env.ib_log.read_text(encoding="utf-8").splitlines() if env.ib_log.exists() else []
    events = [json.loads(line) for line in lines]
    historical = [event for event in events if event["event"] == "historical_data"]
    forbidden = [event for event in events if event["event"] == "forbidden_order_api"]
    recorder.check(
        bool(historical),
        "real fetch executor consumed deterministic IB historical data",
        area="Technicals",
        expected=">=1 request",
        actual=len(historical),
    )
    recorder.check(
        not forbidden,
        "no broker order API was invoked",
        area="Isolation/Integrity",
        expected=0,
        actual=len(forbidden),
    )


def _verify_no_restricted_leaks(env, recorder: CertificationRecorder) -> None:
    forbidden = ("provider_secret", "postgres:postgres", "authorization: bearer")
    leaks: list[str] = []
    for path in env.artifact_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".png", ".zip"}:
            continue
        text_value = path.read_text(encoding="utf-8", errors="ignore").lower()
        for sentinel in forbidden:
            if sentinel in text_value:
                leaks.append(f"{path.relative_to(env.artifact_dir)}:{sentinel}")
    recorder.check(
        not leaks,
        "evidence package contains no restricted/provider/database secret sentinels",
        area="CERI",
        expected=[],
        actual=leaks,
    )


def _capture_database_screenshots(page: Page, env, graph: dict) -> None:
    pages = write_database_html(env.artifact_dir, graph)
    for index, html_path in enumerate(pages, start=1):
        page.goto(html_path.as_uri())
        page.screenshot(
            path=env.artifact_dir
            / "screenshots"
            / "database"
            / f"{index * 10:03d}-{graph['tables'][index - 1]['table']}.png",
            full_page=True,
        )


def _dynamic_routes(engine, run_id: int) -> list[tuple[int, str, str, str]]:
    routes: list[tuple[int, str, str, str]] = []
    ranking = query_rows(
        engine,
        """
        select ranking_profile from ranking_results
        where run_id=:run_id order by ranking_profile limit 1
        """,
        {"run_id": run_id},
    )
    if ranking:
        profile = ranking[0]["ranking_profile"]
        routes.append((140, "ranking-profile", f"/runs/{run_id}/rankings/{profile}", str(profile)))
    ceri = query_rows(
        engine,
        "select ticker from ceri_score_snapshots where run_id=:run_id order by ticker limit 1",
        {"run_id": run_id},
    )
    if ceri:
        routes.append((150, "ceri-ticker", f"/ceri/ticker/{ceri[0]['ticker']}", ceri[0]["ticker"]))
    lifecycle = query_rows(
        engine,
        "select ticker from setup_signal_snapshots where run_id=:run_id order by ticker limit 1",
        {"run_id": run_id},
    )
    if lifecycle:
        routes.append(
            (
                160,
                "lifecycle-ticker",
                f"/setup-lifecycle/ticker/{lifecycle[0]['ticker']}",
                lifecycle[0]["ticker"],
            )
        )
    winner = query_rows(
        engine,
        """
        select id, ticker from winner_prediction_snapshots
        where run_id=:run_id order by ticker limit 1
        """,
        {"run_id": run_id},
    )
    if winner:
        routes.append(
            (
                170,
                "winner-prediction",
                f"/winner-probability/predictions/{winner[0]['id']}",
                winner[0]["ticker"],
            )
        )
    return routes


def _table_rows(page: Page, required_headers: set[str]) -> list[dict[str, str]]:
    return page.locator("table").evaluate_all(
        """
        (tables, required) => {
          for (const table of tables) {
            const headers = Array.from(table.querySelectorAll('thead th')).map(
              th => th.textContent.trim()
            );
            if (!required.every(header => headers.includes(header))) continue;
            return Array.from(table.querySelectorAll('tbody tr:not([hidden])')).map(row => {
              const result = {};
              Array.from(row.cells).forEach(
                (cell, index) => result[headers[index]] = cell.textContent.trim()
              );
              return result;
            });
          }
          return [];
        }
        """,
        sorted(required_headers),
    )


def _ticker_from_cell(value: str, known_tickers) -> str:
    """Extract a ticker even when a badge is rendered without whitespace beside it."""
    return next(
        (
            ticker
            for ticker in sorted(known_tickers, key=len, reverse=True)
            if value.startswith(ticker)
        ),
        value.split()[0],
    )


def _display_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _normalized_scalar(value) -> str:
    if value in (None, ""):
        return ""
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except Exception:
        return str(value).strip()


def _field_area(field: str) -> str:
    if field == "fundamentalScore":
        return "Fundamentals"
    if field == "technicalScore":
        return "Technicals"
    if field == "rank":
        return "Rankings"
    return "Upload"


def _surface_area(name: str) -> str:
    mapping = {
        "market-regime": "Market Regime",
        "sector-rotation": "Sector Rotation",
        "market-changes": "Setup Lifecycle",
        "alerts": "Alerts",
        "winner-evidence": "Winner Evidence",
        "winner-prediction": "Winner Evidence",
        "ceri": "CERI",
        "ceri-ticker": "CERI",
        "runs": "History/Exports",
        "history": "History/Exports",
        "ranking-profile": "Rankings",
        "pipeline-progress": "Pipeline/Jobs",
    }
    return mapping.get(name, "Upload")


def _write_live_canary_csv(path: Path) -> str:
    """Reuse the certified input shape with a small real-provider cohort."""

    write_canonical_csv(path)
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fieldnames = tuple(reader.fieldnames or ())
        rows = list(reader)[: len(LIVE_CANARY_TICKERS)]
    descriptions = {
        "AAPL": "Apple Inc.",
        "MSFT": "Microsoft Corporation",
        "NVDA": "NVIDIA Corporation",
        "AMZN": "Amazon.com, Inc.",
        "META": "Meta Platforms, Inc.",
    }
    for row, ticker in zip(rows, LIVE_CANARY_TICKERS, strict=True):
        row["Symbol"] = ticker
        row["Description"] = descriptions[ticker]
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_healthy(process: subprocess.Popen, base_url: str, log_path: Path) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"certification server stopped early; see {log_path}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    raise RuntimeError(f"certification server did not become healthy; see {log_path}")


def _wait_worker_ready(
    process: subprocess.Popen,
    database_url: str,
    worker_id: str,
    log_path: Path,
) -> None:
    engine = create_engine(database_url)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"certification worker stopped early; see {log_path}")
            with engine.connect() as connection:
                heartbeat = connection.scalar(
                    text(
                        "select heartbeat_at from background_workers "
                        "where worker_id=:worker_id and stopping_at is null"
                    ),
                    {"worker_id": worker_id},
                )
            if heartbeat is not None:
                return
            time.sleep(0.1)
    finally:
        engine.dispose()
    raise RuntimeError(f"certification worker did not become ready; see {log_path}")


def _drop_database(admin, database_name: str) -> None:
    if not database_name.startswith("swinglens_pytest_cert_"):
        raise RuntimeError(f"refusing to drop unsafe database {database_name}")
    admin.execute(
        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database_name))
    )


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _activate_disposable_sec_processor(database_url: str) -> None:
    """Promote the deployed processor only inside this fresh disposable database."""

    engine = create_engine(database_url)
    try:
        with Session(engine) as db:
            release = register_deployed_processor(
                db,
                authority=RuntimeMutationAuthority.normal("test.sec_processor.register"),
                git_sha=_git_commit(),
            )
            certify_processor(
                db,
                processor_signature=release.processor_signature,
                evidence={"source": "single-run-disposable-certification"},
                actor="single-run-certification",
            )
            promote_processor(
                db,
                processor_signature=release.processor_signature,
                actor="single-run-certification",
            )
            db.commit()
    finally:
        engine.dispose()


def _seed_disposable_sec_readiness(database_url: str) -> None:
    """Freeze SEC mapping/readiness only; provider jobs still create all run evidence."""

    from app.models.ceri_tables import CeriCompany, CeriSecSyncState
    from app.services.ceri.sec.processor_lifecycle import require_deployed_processor_active

    exact_ten = ("BHE", "BLLN", "KLIC", "LSCC", "PDFS", "ACMR", "RDVT", "AVT", "DVN", "JNJ")
    tickers = tuple(
        dict.fromkeys((*CANONICAL_TICKERS, *exact_ten, *(f"F{index:04d}" for index in range(90))))
    )
    engine = create_engine(database_url)
    try:
        with Session(engine) as db:
            release = require_deployed_processor_active(db)
            signature = release.active_signature or release.deployed_signature
            existing = {
                row.ticker: row
                for row in db.scalars(select(CeriCompany).where(CeriCompany.ticker.in_(tickers)))
            }
            now = datetime.now(UTC)
            for index, ticker in enumerate(tickers, start=1):
                cik = str(900_000_000 + index).zfill(10)
                company = existing.get(ticker)
                if company is None:
                    company = CeriCompany(ticker=ticker, exchange="US")
                    db.add(company)
                company.cik = cik
                company.sec_applicability = "REQUIRED"
                company.current_provider_ids_json = {
                    "eodhd": f"{ticker}.US",
                    "sec": cik,
                }
                db.add(
                    CeriSecSyncState(
                        cik=cik,
                        dataset="guidance",
                        processor_signature=signature,
                        bootstrap_completed_at=now,
                        last_discovered_at=now,
                        latest_filing_date=now.date(),
                        document_count=1,
                    )
                )
            db.commit()
    finally:
        engine.dispose()


def _is_feature_flag(key: str) -> bool:
    return key.startswith(("WINNER_", "SETUP_", "CERI_", "TECHNICAL_")) or key in {
        "USE_DURABLE_PIPELINE",
        "JOB_WORKER_ENABLED",
    }
