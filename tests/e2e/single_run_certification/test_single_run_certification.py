from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from playwright.sync_api import Page, sync_playwright
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.tables import PriceBar
from app.services.bar_cache_service import cache_bars
from app.services.ib_data_fetcher import HistoricalBar
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
ALEMBIC_HEAD = "0030_fix_ceri_estimate_snapshot_identity"
TERMINAL_PIPELINE_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}


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


@pytest.fixture(scope="module")
def certification_environment(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[CertificationEnvironment]:
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

    database_url = f"postgresql+psycopg://postgres:postgres@127.0.0.1:5432/{database_name}"
    runtime_root = tmp_path_factory.mktemp("swinglens-single-run-certification")
    artifact_dir = REPO_ROOT / "test-results" / "single-run-certification" / execution_id
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
    csv_hash = write_canonical_csv(csv_path)

    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "APP_HOST": "127.0.0.1",
        "DEBUG": "false",
        "ALLOW_PUBLIC_BIND": "false",
        "USE_DURABLE_PIPELINE": "true",
        "JOB_WORKER_ENABLED": "true",
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
        "WINNER_PROBABILITY_ENABLED": "true",
        "WINNER_PROBABILITY_CAPTURE_IN_PIPELINE": "true",
        "WINNER_PROBABILITY_ADMIN_ENABLED": "true",
        "SETUP_LIFECYCLE_ENABLED": "true",
        "SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED": "true",
        "SETUP_CAPTURE_HANDOFF_ENABLED": "true",
        "SETUP_LIFECYCLE_ALERTS_ENABLED": "true",
        "CERI_ENABLED": "true",
        "CERI_PROVIDER_INGEST_ENABLED": "false",
        "CERI_RUN_CAPTURE_ENABLED": "true",
        "CERI_ALERTS_ENABLED": "true",
        "CERI_UI_ENABLED": "true",
        "CERI_ADMIN_ENABLED": "true",
        "CERTIFICATION_IB_LOG": str(ib_log),
        "CERTIFICATION_OUTCOME_NOW": "2027-01-15T22:00:00+00:00",
        "PYTHONPATH": str(REPO_ROOT),
    }

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

    seed = seed_prerequisites(database_url)
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    log_handle = server_log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "certification_server:app",
            "--app-dir",
            str(REPO_ROOT / "tests" / "e2e" / "single_run_certification"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_healthy(process, base_url, server_log)
        environment_payload = {
            "execution_id": execution_id,
            "git_commit": _git_commit(),
            "alembic_revision": ALEMBIC_HEAD,
            "fixture_version": FIXTURE_VERSION,
            "fixture_hash": seed.fixture_hash,
            "csv_hash": csv_hash,
            "database_name": database_name,
            "database_engine": "PostgreSQL",
            "python": sys.version,
            "feature_flags": {key: value for key, value in env.items() if _is_feature_flag(key)},
            "canonical_tickers": list(CANONICAL_TICKERS),
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
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_handle.close()
        _drop_database(admin, database_name)
        admin.close()


@pytest.fixture
def certification_page() -> Iterator[Page]:
    """Own the browser lifecycle; do not depend on the optional pytest plugin."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True, viewport={"width": 1600, "height": 1000}
        )
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


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
        pipeline_steps = query_rows(
            engine,
            """
            select step_order, step_name, status, started_at, completed_at,
                   message, error_message, retry_count
            from pipeline_steps where pipeline_run_id = :pipeline_id order by step_order
            """,
            {"pipeline_id": pipeline_id},
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
            any(
                entry["table"] == "winner_processing_runs"
                and "maturation" in entry["relationship_to_run"]
                for entry in graph["tables"]
            ),
            "run evidence graph includes Winner Evidence maturation processing lineage",
            area="Winner Evidence",
            expected=True,
            actual=[
                entry["table"]
                for entry in graph["tables"]
                if "winner" in entry["table"]
            ],
        )
        recorder.check(
            any(
                entry["table"] == "background_jobs"
                and "matured outcome" in entry["relationship_to_run"]
                for entry in graph["tables"]
            ),
            "run evidence graph includes the browser-queued maturation background job",
            area="Pipeline/Jobs",
            expected=True,
            actual=[
                entry["relationship_to_run"]
                for entry in graph["tables"]
                if entry["table"] == "background_jobs"
            ],
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
) -> tuple[int, str]:
    page.get_by_role("button", name="Run full pipeline").click()
    confirm = page.locator("[data-confirm-panel]")
    confirm.wait_for(state="visible", timeout=5_000)
    confirm.locator("[data-confirm-continue]").click()
    page.wait_for_url(re.compile(rf"/runs/{run_id}/pipeline/\d+$"), timeout=30_000)
    pipeline_id = int(page.url.rsplit("/", 1)[-1])
    deadline = time.monotonic() + 300
    status = ""
    while time.monotonic() < deadline:
        status = (page.locator("[data-pipeline-status]").text_content() or "").strip()
        if status in TERMINAL_PIPELINE_STATUSES:
            break
        page.wait_for_timeout(500)
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
        select ticker, sector, final_rank, final_score, combined_decision,
               fundamental_score, dual_score, days_until_earnings,
               is_complete, has_warning
        from combined_results where run_id=:run_id order by final_rank nulls last, ticker
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
    db_rows = query_rows(
        engine,
        """
        select ticker, opportunity_score, event_risk_score, data_confidence, posture
        from ceri_score_snapshots where run_id=:run_id order by ticker
        """,
        {"run_id": run_id},
    )
    recorder.check(
        len(gui_rows) == len(db_rows),
        "CERI visible row count matches DB",
        area="CERI",
        expected=len(db_rows),
        actual=len(gui_rows),
    )
    recorder.check(
        len(db_rows) == len(CANONICAL_TICKERS),
        "CERI produced one run-owned score snapshot per canonical ticker",
        area="CERI",
        expected=len(CANONICAL_TICKERS),
        actual=len(db_rows),
    )
    db_by = {row["ticker"]: row for row in db_rows}
    for gui in gui_rows:
        ticker = _ticker_from_cell(gui["Ticker"], db_by)
        db = db_by.get(ticker)
        if not db:
            recorder.check(False, f"CERI {ticker} belongs to run", area="CERI")
            continue
        comparisons = {
            "Opportunity": _display_value(db["opportunity_score"]),
            "Risk": _display_value(db["event_risk_score"]),
            "Confidence": str(db["data_confidence"]),
            "Posture": str(db["posture"]),
        }
        for field, expected in comparisons.items():
            recorder.check(
                gui[field].splitlines()[0].strip() == expected,
                f"CERI {ticker} {field} GUI↔DB",
                area="CERI",
                expected=expected,
                actual=gui[field],
            )


def _compare_ceri_alerts(page: Page, engine, recorder: CertificationRecorder, run_id: int) -> None:
    gui_rows = _table_rows(page, {"Ticker", "Severity", "Status", "Evidence", "Action"})
    db_rows = query_rows(
        engine,
        """
        select a.ticker, a.severity, a.status, a.evidence_json
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
    db_by_ticker = {str(row["ticker"]): row for row in db_rows}
    for gui in gui_rows:
        db = db_by_ticker.get(gui["Ticker"].strip())
        recorder.check(
            db is not None,
            f"CERI alert {gui['Ticker']} belongs to canonical run",
            area="CERI",
            expected=True,
            actual=db is not None,
        )
        if db is None:
            continue
        for field, expected in {
            "Ticker": str(db["ticker"]),
            "Severity": str(db["severity"]),
            "Status": str(db["status"]),
        }.items():
            recorder.check(
                gui[field].strip() == expected,
                f"CERI alert {db['ticker']} {field} GUI↔DB",
                area="CERI",
                expected=expected,
                actual=gui[field],
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
            str(db["evidence_grade"])
            if db["evidence_grade"] is not None
            else "Missing"
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
    page.locator(
        'form[action="/api/winner-probability/outcomes/process"] output'
    ).wait_for(state="visible", timeout=10_000)

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
        if int(job_result.get("failed", 0)) or int(job_result.get("warnings", 0))
        else "COMPLETED"
    )
    recorder.check(
        job.get("status") == expected_job_status,
        "browser-queued Winner Evidence maturation job reached its count-derived status",
        area="Winner Evidence",
        expected=expected_job_status,
        actual=job.get("status"),
    )
    recorder.check(
        int(job_result.get("failed", -1)) == 0,
        "Winner Evidence maturation completed without failed outcomes",
        area="Winner Evidence",
        expected=0,
        actual=job_result.get("failed"),
    )
    if int(job_result.get("warnings", 0)):
        recorder.warnings.append(
            "Winner outcome maturation emitted deterministic missing-comparison warnings "
            f"for sparse/Unknown sector cases ({job_result['warnings']})."
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
    maturation_row = next(
        (row for row in operations_rows if row["Type"] == "WINNER_OUTCOME_MATURATION"),
        None,
    )
    recorder.check(
        maturation_row is not None and maturation_row["Status"] == job.get("status"),
        "Winner operations GUI status matches the background job",
        area="Winner Evidence",
        expected=job.get("status"),
        actual=maturation_row["Status"] if maturation_row else "missing",
    )
    processing_run = query_rows(
        engine,
        """
        select id, status, counts_json from winner_processing_runs
        where background_job_id=:job_id order by id desc limit 1
        """,
        {"job_id": job.get("id")},
    )[0]
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
        order by t.id
        """,
        {"prediction_id": prediction_id},
    )
    recorder.check(
        bool(matured_forward) and all(row["status"] == "MATURED" for row in matured_forward),
        "all ALFA forward horizons matured from later bars",
        area="Winner Evidence",
        expected="all MATURED",
        actual=[row["status"] for row in matured_forward],
    )
    recorder.check(
        bool(matured_target_stop)
        and all(row["status"] == "MATURED" for row in matured_target_stop),
        "ALFA primary target/stop outcome matured",
        area="Winner Evidence",
        expected="MATURED",
        actual=[row["status"] for row in matured_target_stop],
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
        for ticker, fallback in (("ALFA", 100.0), ("SPY", 500.0), ("XLK", 250.0)):
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
    def sort_key(row: dict[str, str]) -> tuple[str, str, str]:
        return tuple(row[key] for key in ("Type", "Entry", "Due/Event"))
    gui_sorted = sorted(gui_rows, key=sort_key)
    expected_sorted = sorted(expected, key=sort_key)
    recorder.check(
        len(gui_sorted) == len(expected_sorted),
        "matured Winner Evidence GUI outcome cardinality matches DB",
        area="Winner Evidence",
        expected=len(expected_sorted),
        actual=len(gui_sorted),
    )
    for index, (gui, db) in enumerate(zip(gui_sorted, expected_sorted, strict=False), start=1):
        for field, expected_value in db.items():
            recorder.check(
                gui[field].strip() == expected_value,
                f"matured outcome row {index} {field} GUI↔DB",
                area="Winner Evidence",
                expected=expected_value,
                actual=gui[field].strip(),
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
    lifecycle_count = int(query_rows(
        engine,
        """
        select count(*) as value from setup_lifecycle_events
        where evaluation_run_id in (
          select id from setup_lifecycle_evaluation_runs where source_run_id=:run_id
        ) and is_current_version is true
          and event_type in ('EPISODE_OPENED','STATE_TRANSITION','PHASE_TRANSITION')
        """,
        {"run_id": run_id},
    )[0]["value"])
    signal_count = int(query_rows(
        engine,
        """
        select count(*) as value from signal_change_events e
        where evaluation_run_id in (
          select id from setup_lifecycle_evaluation_runs where source_run_id=:run_id
        ) and exists (
          select 1 from setup_signal_snapshots s
          where s.id=e.current_snapshot_id and s.is_canonical is true
        )
        """,
        {"run_id": run_id},
    )[0]["value"])
    response = page.request.get(
        f"{page.url.split('/runs/', 1)[0]}/api/setup-lifecycle/changes?run_id={run_id}&limit=500"
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
        if item["state_age_sessions"] is not None:
            expected_age = f"Age {item['state_age_sessions']} sessions"
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
    for gui, db in zip(gui_rows, db_rows, strict=False):
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
    page.wait_for_function(
        """
        id => document.querySelector(
          `#alert-${id} [data-slse-alert-status]`
        )?.textContent.includes('ACKNOWLEDGED')
        """,
        arg=alert_id,
    )
    db_status = query_rows(
        engine,
        "select status, acknowledged_at from signal_alert_events where id=:id",
        {"id": alert_id},
    )[0]
    recorder.check(
        db_status["status"] == "ACKNOWLEDGED" and db_status["acknowledged_at"] is not None,
        "GUI lifecycle alert acknowledgement persisted",
        area="Alerts",
        expected="ACKNOWLEDGED with timestamp",
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
    row.locator("[data-ceri-alert-status]").filter(has_text="ACKNOWLEDGED").wait_for()
    db_status = query_rows(
        engine,
        "select status, acknowledged_at from ceri_alert_events where id=:id",
        {"id": alert_id},
    )[0]
    recorder.check(
        db_status["status"] == "ACKNOWLEDGED" and db_status["acknowledged_at"] is not None,
        "GUI CERI alert acknowledgement persisted",
        area="CERI",
        expected="ACKNOWLEDGED with timestamp",
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
            " and exists (select 1 from setup_signal_snapshots s "
            "where s.id=e.current_snapshot_id and s.is_canonical is true)"
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
    page.get_by_role("button", name="Refresh rankings").first.click()
    page.wait_for_load_state("networkidle")
    after_first = _idempotency_counts(engine, run_id)
    recorder.check(
        after_first["ranking_count"] > 0,
        "ranking refresh materialized all configured profiles",
        area="Rankings",
        expected=">0",
        actual=after_first["ranking_count"],
    )
    return {"before": before, "after_first": after_first}


def _verify_idempotency(page: Page, engine, env, recorder, run_id: int, *, state: dict) -> dict:
    page.get_by_role("button", name="Refresh rankings").first.click()
    page.wait_for_load_state("networkidle")
    after_second = _idempotency_counts(engine, run_id)
    recorder.check(
        state["after_first"] == after_second,
        "second ranking refresh is idempotent and does not duplicate related evidence",
        area="Isolation/Integrity",
        expected=state["after_first"],
        actual=after_second,
    )
    return {
        "operation": "ranking refresh twice through GUI",
        **state,
        "after_second": after_second,
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


def _is_feature_flag(key: str) -> bool:
    return key.startswith(("WINNER_", "SETUP_", "CERI_", "TECHNICAL_")) or key in {
        "USE_DURABLE_PIPELINE",
        "JOB_WORKER_ENABLED",
    }
