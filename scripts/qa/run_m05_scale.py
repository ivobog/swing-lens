from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
from fastapi import UploadFile
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"
DATABASE_PREFIX = "swinglens_qa_m05_"
DEFAULT_SIZES = (50, 250, 1000)


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("page_fault_count", wintypes.DWORD),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
    ]


if os.name == "nt":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PSAPI = ctypes.WinDLL("psapi", use_last_error=True)
    _KERNEL32.GetCurrentProcess.restype = wintypes.HANDLE
    _PSAPI.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    _PSAPI.GetProcessMemoryInfo.restype = wintypes.BOOL


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run SwingLens M-05 scale profiles against a disposable PostgreSQL database."
    )
    parser.add_argument("--sizes", nargs="+", type=int, default=list(DEFAULT_SIZES))
    parser.add_argument("--bars", type=int, default=756)
    parser.add_argument("--http-iterations", type=int, default=3)
    parser.add_argument(
        "--admin-url",
        default=os.environ.get("SWINGLENS_TEST_POSTGRES_ADMIN_URL", DEFAULT_ADMIN_URL),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "test-results" / "m05-scale.json",
    )
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if not args.sizes or any(size < 1 or size > 5000 for size in args.sizes):
        raise ValueError("sizes must contain values from 1 through 5000")
    if args.sizes != sorted(set(args.sizes)):
        raise ValueError("sizes must be unique and supplied in ascending order")
    if args.bars < 252 or args.bars > 1000:
        raise ValueError("bars must be between 252 and 1000")
    if not 1 <= args.http_iterations <= 20:
        raise ValueError("http iterations must be between 1 and 20")
    parsed = make_url(_sqlalchemy_url(args.admin_url))
    if parsed.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("M-05 destructive checks require a localhost PostgreSQL admin URL")


def _native_postgres_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _sqlalchemy_url(value: str) -> str:
    return value.replace("postgresql://", "postgresql+psycopg://", 1)


def _database_url(admin_url: str, database_name: str) -> str:
    return make_url(_sqlalchemy_url(admin_url)).set(database=database_name).render_as_string(
        hide_password=False
    )


@contextmanager
def _disposable_database(admin_url: str) -> Iterator[tuple[str, str]]:
    database_name = f"{DATABASE_PREFIX}{uuid.uuid4().hex[:12]}"
    if not database_name.startswith(DATABASE_PREFIX):
        raise RuntimeError("refusing to create an unscoped M-05 database")
    native_admin_url = _native_postgres_url(admin_url)
    with psycopg.connect(native_admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        try:
            yield database_name, _database_url(admin_url, database_name)
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )


def _migrate(database_url: str, runtime_root: Path) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "UPLOAD_DIR": str(runtime_root / "uploads"),
        "EXPORT_DIR": str(runtime_root / "exports"),
        "CACHE_DIR": str(runtime_root / "cache"),
        "JOB_WORKER_ENABLED": "false",
        "TECHNICAL_PROCESS_POOL_ENABLED": "false",
        "CERI_ENABLED": "false",
        "SETUP_LIFECYCLE_ENABLED": "false",
        "WINNER_PROBABILITY_ENABLED": "false",
    }
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stdout + completed.stderr)


def _configure_environment(database_url: str, runtime_root: Path) -> None:
    values = {
        "DATABASE_URL": database_url,
        "UPLOAD_DIR": str(runtime_root / "uploads"),
        "EXPORT_DIR": str(runtime_root / "exports"),
        "CACHE_DIR": str(runtime_root / "cache"),
        "JOB_WORKER_ENABLED": "false",
        "TECHNICAL_PROCESS_POOL_ENABLED": "false",
        "TECHNICAL_PURE_BOUNDARY_ENABLED": "false",
        "TECHNICAL_ARTIFACT_CACHE_ENABLED": "false",
        "FETCH_TECHNICAL_OVERLAP_ENABLED": "false",
        "CERI_ENABLED": "false",
        "CERI_RUN_CAPTURE_ENABLED": "false",
        "SETUP_LIFECYCLE_ENABLED": "false",
        "SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED": "false",
        "WINNER_PROBABILITY_ENABLED": "false",
        "WINNER_PROBABILITY_CAPTURE_IN_PIPELINE": "false",
    }
    os.environ.update(values)
    for key in ("UPLOAD_DIR", "EXPORT_DIR", "CACHE_DIR"):
        Path(values[key]).mkdir(parents=True, exist_ok=True)


def _business_dates(count: int, end: date | None = None) -> list[date]:
    current = end or date.today()
    dates: list[date] = []
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
    return list(reversed(dates))


def _tickers(count: int) -> list[str]:
    return [f"QA{index:04d}" for index in range(1, count + 1)]


def _seed_price_bars(
    database_url: str,
    tickers: list[str],
    bar_count: int,
    what_to_show_values: tuple[str, ...] = ("TRADES",),
) -> int:
    dates = _business_dates(bar_count)
    symbols = [*tickers, "SPY", "QQQ"]
    inserted = 0
    with psycopg.connect(_native_postgres_url(database_url)) as connection:
        with connection.cursor().copy(
            """
            COPY price_bars (
                ticker, bar_date, timeframe, open, high, low, close, volume,
                source, what_to_show, adjustment_type, revision_count, data_hash
            ) FROM STDIN
            """
        ) as copy:
            for ticker_index, ticker in enumerate(symbols):
                start_close = 70.0 + (ticker_index % 80)
                daily_step = 0.025 + (ticker_index % 7) * 0.002
                for what_to_show in what_to_show_values:
                    for bar_index, bar_date in enumerate(dates):
                        close = round(start_close + bar_index * daily_step, 4)
                        payload = (
                            f"{ticker}|{bar_date.isoformat()}|{close}|{bar_index}|{what_to_show}"
                        )
                        copy.write_row(
                            (
                                ticker,
                                bar_date,
                                "1 day",
                                close - 0.2,
                                close + 0.8,
                                close - 0.9,
                                close,
                                1_000_000 + ticker_index * 100 + bar_index,
                                "qa-deterministic",
                                what_to_show,
                                None,
                                0,
                                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                            )
                        )
                        inserted += 1
        connection.commit()
    return inserted


def _load_csv_template() -> dict[str, str]:
    fixture_path = REPO_ROOT / "tests" / "fixtures" / "golden_pipeline.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in fixture["raw_rows"][0]["raw_json"].items()}


def _write_scale_csv(path: Path, tickers: list[str]) -> None:
    template = _load_csv_template()
    sectors = (
        "Technology",
        "Financials",
        "Health Care",
        "Consumer Discretionary",
        "Industrials",
        "Energy",
        "Utilities",
        "Real Estate",
        "Materials",
        "Communication Services",
        "Consumer Staples",
    )
    headers = list(template)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for index, ticker in enumerate(tickers):
            row = dict(template)
            row["Symbol"] = ticker
            row["Description"] = f"QA Company {index + 1:04d}"
            row["Sector"] = sectors[index % len(sectors)]
            row["Market capitalization"] = str(1_000_000_000 + index * 10_000_000)
            writer.writerow(row)


def _current_rss_bytes() -> int:
    if os.name == "nt":
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = _KERNEL32.GetCurrentProcess()
        ok = _PSAPI.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.working_set_size) if ok else 0
    statm = Path("/proc/self/statm")
    if statm.exists():
        resident_pages = int(statm.read_text(encoding="ascii").split()[1])
        return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
    return 0


class _RssSampler:
    def __init__(self) -> None:
        self.start_bytes = _current_rss_bytes()
        self.peak_bytes = self.start_bytes
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def __enter__(self) -> _RssSampler:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self.peak_bytes = max(self.peak_bytes, _current_rss_bytes())

    def _sample(self) -> None:
        while not self._stop.wait(0.02):
            self.peak_bytes = max(self.peak_bytes, _current_rss_bytes())


def _measure(engine: Engine, operation: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
    query_count = 0

    def count_query(*_args: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(engine, "before_cursor_execute", count_query)
    started = time.perf_counter()
    cpu_started = time.process_time()
    try:
        with _RssSampler() as memory:
            result = operation()
    finally:
        event.remove(engine, "before_cursor_execute", count_query)
    wall_ms = (time.perf_counter() - started) * 1000
    cpu_ms = (time.process_time() - cpu_started) * 1000
    return result, {
        "wall_ms": round(wall_ms, 3),
        "cpu_ms": round(cpu_ms, 3),
        "cpu_to_wall_pct": round(cpu_ms / wall_ms * 100, 2) if wall_ms else 0.0,
        "sql_statements": query_count,
        "rss_start_bytes": memory.start_bytes,
        "rss_peak_bytes": memory.peak_bytes,
        "rss_growth_bytes": max(0, memory.peak_bytes - memory.start_bytes),
    }


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {"min_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}

    def percentile(fraction: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * weight

    return {
        "min_ms": round(ordered[0], 3),
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(percentile(0.95), 3),
        "max_ms": round(ordered[-1], 3),
    }


def _measure_http(
    engine: Engine,
    client: TestClient,
    path: str,
    iterations: int,
) -> dict[str, Any]:
    timings: list[float] = []
    query_counts: list[int] = []
    response_size = 0
    status_code = 0
    peak_rss = 0
    for _ in range(iterations):
        response, measurement = _measure(engine, lambda: client.get(path))
        status_code = response.status_code
        if status_code != 200:
            raise RuntimeError(f"GET {path} returned {status_code}: {response.text[:500]}")
        response_size = len(response.content)
        timings.append(float(measurement["wall_ms"]))
        query_counts.append(int(measurement["sql_statements"]))
        peak_rss = max(peak_rss, int(measurement["rss_peak_bytes"]))
    return {
        "path": path,
        "status_code": status_code,
        "iterations": iterations,
        "response_bytes": response_size,
        "timing": _percentiles(timings),
        "sql_statements_min": min(query_counts),
        "sql_statements_max": max(query_counts),
        "rss_peak_bytes": peak_rss,
    }


def _database_size(session: Session) -> int:
    return int(session.scalar(text("SELECT pg_database_size(current_database())")) or 0)


def _evidence_counts(session: Session, run_id: int) -> dict[str, int]:
    from app.models.tables import (
        CombinedResult,
        FundamentalScore,
        RankingResult,
        RawCompanyRow,
        TechnicalScore,
    )

    models = {
        "raw_rows": RawCompanyRow,
        "fundamental_scores": FundamentalScore,
        "technical_scores": TechnicalScore,
        "combined_results": CombinedResult,
        "ranking_results": RankingResult,
    }
    return {
        name: int(session.scalar(select(func.count(model.id)).where(model.run_id == run_id)) or 0)
        for name, model in models.items()
    }


def _evaluate_thresholds(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def record(
        requirement: str,
        profile: dict[str, Any],
        observed_ms: float,
        budget_ms: float,
    ) -> None:
        checks.append(
            {
                "requirement": requirement,
                "ticker_count": profile["ticker_count"],
                "observed_ms": observed_ms,
                "budget_ms": budget_ms,
                "passed": observed_ms <= budget_ms,
            }
        )

    for profile in profiles:
        record(
            "history_p95_at_most_500_ms",
            profile,
            profile["http"]["history"]["timing"]["p95_ms"],
            500.0,
        )
        record(
            "run_detail_p95_at_most_1500_ms",
            profile,
            profile["http"]["run_detail"]["timing"]["p95_ms"],
            1500.0,
        )
        record(
            "combined_export_p95_at_most_2000_ms",
            profile,
            profile["http"]["combined_export"]["timing"]["p95_ms"],
            2000.0,
        )
        if profile["ticker_count"] == 250:
            record(
                "technical_scoring_250_at_most_60000_ms",
                profile,
                profile["pipeline_step_durations_ms"]["SCORING_TECHNICALS"],
                60000.0,
            )

    failed = [check for check in checks if not check["passed"]]
    return {
        "status": "PASS" if not failed else "FAIL",
        "checks": checks,
        "passed": len(checks) - len(failed),
        "failed": len(failed),
    }


def _run_profiles(
    database_url: str,
    runtime_root: Path,
    sizes: list[int],
    bar_count: int,
    http_iterations: int,
    *,
    seed_price_bars: bool = True,
    execute_via_worker: bool = False,
) -> dict[str, Any]:
    from app.db import get_db
    from app.main import create_app
    from app.services.ib_fetch_plan_service import FetchPlan
    from app.services.pipeline_executor import (
        PipelineExecutionDependencies,
        execute_full_pipeline,
    )
    from app.services.pipeline_service import start_pipeline
    from app.services.ranking_profile_service import (
        get_ranking_profiles,
        refresh_all_ranking_profiles,
    )
    from app.services.upload_service import create_upload_run
    from app.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    engine = create_engine(database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    all_tickers = _tickers(max(sizes))

    if seed_price_bars:
        print(
            f"Seeding {len(all_tickers) + 2} symbols x {bar_count} bars...",
            flush=True,
        )
        seed_result, seed_measurement = _measure(
            engine,
            lambda: _seed_price_bars(database_url, all_tickers, bar_count),
        )
        print(f"Seeded {seed_result} price bars.", flush=True)
    else:
        seed_result = 0
        current_rss = _current_rss_bytes()
        seed_measurement = {
            "wall_ms": 0.0,
            "cpu_ms": 0.0,
            "cpu_to_wall_pct": 0.0,
            "sql_statements": 0,
            "rss_start_bytes": current_rss,
            "rss_peak_bytes": current_rss,
            "rss_growth_bytes": 0,
        }

    def override_db() -> Iterator[Session]:
        with session_factory() as route_session:
            yield route_session

    app = create_app(settings)
    app.dependency_overrides[get_db] = override_db
    profiles: list[dict[str, Any]] = []

    with TestClient(app) as client:
        for size in sizes:
            print(f"Running {size}-ticker profile...", flush=True)
            csv_path = runtime_root / f"m05-{size}.csv"
            _write_scale_csv(csv_path, all_tickers[:size])
            with session_factory() as session:
                database_before = _database_size(session)

                def upload_operation(path: Path = csv_path) -> Any:
                    with path.open("rb") as handle:
                        return create_upload_run(
                            session,
                            UploadFile(filename=path.name, file=handle),
                        )

                upload_run, upload_measurement = _measure(engine, upload_operation)
                if upload_run.status != "COMPLETED" or upload_run.row_count != size:
                    raise RuntimeError(
                        f"{size}-ticker upload failed: {upload_run.status} "
                        f"rows={upload_run.row_count} error={upload_run.error_message}"
                    )

                pipeline = start_pipeline(
                    session,
                    upload_run.id,
                    requested_by="m05-scale",
                    ceri_run_capture_enabled=False,
                    setup_lifecycle_pipeline_step_enabled=False,
                )
                session.commit()

                def cached_plan(**kwargs: Any) -> FetchPlan:
                    tickers = list(kwargs["tickers"])
                    return FetchPlan(
                        run_id=kwargs.get("run_id"),
                        requested_tickers=tickers,
                        symbols_including_benchmarks=[*tickers, "SPY", "QQQ"],
                        items=[],
                        estimated_request_count=0,
                        estimated_full_backfills=0,
                        estimated_top_ups=0,
                        estimated_refreshes=0,
                        estimated_skips=len(tickers) + 2,
                        warnings=[],
                    )

                dependencies = PipelineExecutionDependencies(build_fetch_plan=cached_plan)
                pipeline_id = pipeline.id
                if execute_via_worker:
                    from app.services.background_worker import run_worker_once

                    result_holder: dict[str, Any] = {}

                    def execute_pipeline_job(
                        worker_db: Session,
                        _job: Any,
                        selected_pipeline_id: int = pipeline_id,
                        selected_dependencies: PipelineExecutionDependencies = dependencies,
                        selected_holder: dict[str, Any] = result_holder,
                    ) -> dict[str, Any]:
                        result = execute_full_pipeline(
                            worker_db,
                            selected_pipeline_id,
                            dependencies=selected_dependencies,
                        )
                        selected_holder["result"] = result
                        return result.__dict__

                    def execute_worker() -> Any:
                        return run_worker_once(
                            worker_id="m05-soak-worker",
                            stale_after_seconds=900,
                            session_factory=session_factory,
                            handlers={"FULL_PIPELINE": execute_pipeline_job},
                        )

                    worker_ran, pipeline_measurement = _measure(engine, execute_worker)
                    if not worker_ran or "result" not in result_holder:
                        raise RuntimeError("M-05 soak worker did not execute the queued pipeline")
                    pipeline_result = result_holder["result"]
                    session.expire_all()
                else:

                    def execute_pipeline(
                        selected_pipeline_id: int = pipeline_id,
                        selected_dependencies: PipelineExecutionDependencies = dependencies,
                    ) -> Any:
                        return execute_full_pipeline(
                            session,
                            selected_pipeline_id,
                            dependencies=selected_dependencies,
                        )

                    pipeline_result, pipeline_measurement = _measure(
                        engine,
                        execute_pipeline,
                    )

                upload_run_id = upload_run.id

                def refresh_rankings(selected_run_id: int = upload_run_id) -> Any:
                    return refresh_all_ranking_profiles(session, selected_run_id)

                ranking_results, ranking_measurement = _measure(
                    engine,
                    refresh_rankings,
                )
                session.commit()

                counts = _evidence_counts(session, upload_run.id)
                expected_ranking_count = size * len(get_ranking_profiles())
                expected_counts = {
                    "raw_rows": size,
                    "fundamental_scores": size,
                    "technical_scores": size,
                    "combined_results": size,
                    "ranking_results": expected_ranking_count,
                }
                if counts != expected_counts:
                    raise RuntimeError(
                        f"durable evidence mismatch for {size}: {counts} != {expected_counts}"
                    )

                run_detail = _measure_http(
                    engine,
                    client,
                    f"/runs/{upload_run.id}",
                    http_iterations,
                )
                combined_export = _measure_http(
                    engine,
                    client,
                    f"/runs/{upload_run.id}/exports/combined.csv",
                    http_iterations,
                )
                first_profile = get_ranking_profiles()[0].name
                ranking_view = _measure_http(
                    engine,
                    client,
                    f"/runs/{upload_run.id}/rankings/{first_profile}",
                    http_iterations,
                )
                history_view = _measure_http(
                    engine,
                    client,
                    "/history?page=1&page_size=50",
                    http_iterations,
                )
                database_after = _database_size(session)

                profiles.append(
                    {
                        "ticker_count": size,
                        "bar_count_per_ticker": bar_count,
                        "run_id": upload_run.id,
                        "pipeline_id": pipeline.id,
                        "pipeline_status": pipeline_result.status,
                        "upload": upload_measurement,
                        "pipeline": pipeline_measurement,
                        "pipeline_step_durations_ms": pipeline_result.performance.get(
                            "step_durations_ms", {}
                        ),
                        "ranking": {
                            **ranking_measurement,
                            "result_count": len(ranking_results),
                        },
                        "http": {
                            "run_detail": run_detail,
                            "combined_export": combined_export,
                            "ranking_view": ranking_view,
                            "history": history_view,
                        },
                        "evidence_counts": counts,
                        "database_bytes_before": database_before,
                        "database_bytes_after": database_after,
                        "database_growth_bytes": database_after - database_before,
                    }
                )
                print(
                    f"Completed {size}-ticker profile: "
                    f"pipeline={pipeline_result.status}, "
                    f"wall={pipeline_measurement['wall_ms'] / 1000:.3f}s.",
                    flush=True,
                )

    engine.dispose()
    return {
        "seed": {
            **seed_measurement,
            "inserted_price_bars": seed_result,
            "ticker_count": len(all_tickers) + 2,
            "bars_per_ticker": bar_count,
        },
        "profiles": profiles,
    }


def main() -> int:
    args = _parse_args()
    _validate_args(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    with tempfile.TemporaryDirectory(prefix="swinglens-m05-") as temporary:
        runtime_root = Path(temporary)
        with _disposable_database(args.admin_url) as (database_name, database_url):
            print(f"Created disposable database {database_name}.", flush=True)
            print("Migrating disposable database to head...", flush=True)
            _migrate(database_url, runtime_root)
            print("Migration complete.", flush=True)
            _configure_environment(database_url, runtime_root)
            execution = _run_profiles(
                database_url,
                runtime_root,
                list(args.sizes),
                args.bars,
                args.http_iterations,
            )
            threshold_evaluation = _evaluate_thresholds(execution["profiles"])
            report = {
                "procedure": "M-05-scale",
                "status": threshold_evaluation["status"],
                "started_at": started_at,
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
                ).strip(),
                "environment": {
                    "os": platform.platform(),
                    "python": platform.python_version(),
                    "processor": platform.processor(),
                    "database_name": database_name,
                    "database_disposable": database_name.startswith(DATABASE_PREFIX),
                    "ib_mode": "not_connected; deterministic cached bars",
                },
                "threshold_evaluation": threshold_evaluation,
                **execution,
            }
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            print(f"M-05 report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
