from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.qa.run_m05_scale import (  # noqa: E402
    DATABASE_PREFIX,
    DEFAULT_ADMIN_URL,
    _configure_environment,
    _current_rss_bytes,
    _disposable_database,
    _migrate,
    _native_postgres_url,
    _run_profiles,
    _sqlalchemy_url,
)

RELEASE_SOAK_HOURS = 8.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeated daily-style SwingLens pipelines for the M-05 soak check."
    )
    parser.add_argument("--duration-hours", type=float, default=RELEASE_SOAK_HOURS)
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument("--tickers", type=int, default=50)
    parser.add_argument("--bars", type=int, default=756)
    parser.add_argument("--http-iterations", type=int, default=1)
    parser.add_argument(
        "--admin-url",
        default=os.environ.get("SWINGLENS_TEST_POSTGRES_ADMIN_URL", DEFAULT_ADMIN_URL),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "test-results" / "m05-soak.json",
    )
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if not 0.01 <= args.duration_hours <= 24:
        raise ValueError("duration-hours must be between 0.01 and 24")
    if not 0 <= args.interval_seconds <= 3600:
        raise ValueError("interval-seconds must be between 0 and 3600")
    if args.max_cycles is not None and not 1 <= args.max_cycles <= 1000:
        raise ValueError("max-cycles must be between 1 and 1000")
    if not 1 <= args.tickers <= 250:
        raise ValueError("soak tickers must be between 1 and 250")
    if not 252 <= args.bars <= 1000:
        raise ValueError("bars must be between 252 and 1000")
    if not 1 <= args.http_iterations <= 5:
        raise ValueError("http-iterations must be between 1 and 5")
    parsed = make_url(_sqlalchemy_url(args.admin_url))
    if parsed.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("M-05 soak requires a localhost PostgreSQL admin URL")


def _database_observation(database_url: str) -> dict[str, int]:
    with psycopg.connect(_native_postgres_url(database_url), connect_timeout=3) as db:
        row = db.execute(
            """
            SELECT
                pg_database_size(current_database()),
                (SELECT count(*) FROM upload_runs),
                (SELECT count(*) FROM background_jobs WHERE status IN ('QUEUED', 'RUNNING')),
                (SELECT count(*) FROM background_jobs
                    WHERE status = 'RUNNING' AND lease_expires_at < now()),
                (SELECT count(*) FROM technical_scores),
                (SELECT count(*) FROM combined_results),
                (SELECT count(*) FROM ranking_results)
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("soak database observation returned no row")
    return {
        "database_bytes": int(row[0]),
        "upload_runs": int(row[1]),
        "active_jobs": int(row[2]),
        "stale_jobs": int(row[3]),
        "technical_scores": int(row[4]),
        "combined_results": int(row[5]),
        "ranking_results": int(row[6]),
    }


def _soak_mode(args: argparse.Namespace) -> str:
    if args.duration_hours >= RELEASE_SOAK_HOURS and args.max_cycles is None:
        return "RELEASE_SOAK"
    return "SHAKEDOWN"


def main() -> int:
    args = _parse_args()
    _validate_args(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    mode = _soak_mode(args)
    target_seconds = args.duration_hours * 3600
    started_wall = time.time()
    started_monotonic = time.monotonic()
    cycles: list[dict[str, Any]] = []
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="swinglens-m05-soak-") as temporary:
        runtime_root = Path(temporary)
        with _disposable_database(args.admin_url) as (database_name, database_url):
            print(f"Created disposable soak database {database_name}.", flush=True)
            _migrate(database_url, runtime_root)
            _configure_environment(database_url, runtime_root)
            initial = _database_observation(database_url)

            while True:
                cycle_number = len(cycles) + 1
                cycle_started = time.monotonic()
                print(f"Starting soak cycle {cycle_number}...", flush=True)
                execution = _run_profiles(
                    database_url,
                    runtime_root,
                    [args.tickers],
                    args.bars,
                    args.http_iterations,
                    seed_price_bars=cycle_number == 1,
                    execute_via_worker=True,
                )
                observation = _database_observation(database_url)
                profile = execution["profiles"][0]
                cycle = {
                    "cycle": cycle_number,
                    "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
                    "cycle_seconds": round(time.monotonic() - cycle_started, 3),
                    "rss_bytes": _current_rss_bytes(),
                    "pipeline_status": profile["pipeline_status"],
                    "pipeline_wall_ms": profile["pipeline"]["wall_ms"],
                    "pipeline_sql_statements": profile["pipeline"]["sql_statements"],
                    "run_detail_p95_ms": profile["http"]["run_detail"]["timing"]["p95_ms"],
                    "combined_export_p95_ms": profile["http"]["combined_export"]["timing"][
                        "p95_ms"
                    ],
                    "database": observation,
                }
                cycles.append(cycle)
                expected_per_run = args.tickers
                expected_rankings = args.tickers * 5 * cycle_number
                if observation["technical_scores"] != expected_per_run * cycle_number:
                    failures.append(f"cycle {cycle_number}: technical evidence count mismatch")
                if observation["combined_results"] != expected_per_run * cycle_number:
                    failures.append(f"cycle {cycle_number}: combined evidence count mismatch")
                if observation["ranking_results"] != expected_rankings:
                    failures.append(f"cycle {cycle_number}: ranking evidence count mismatch")
                if observation["active_jobs"] or observation["stale_jobs"]:
                    failures.append(f"cycle {cycle_number}: active or stale durable jobs remain")
                print(
                    f"Completed soak cycle {cycle_number}: "
                    f"pipeline={profile['pipeline_status']}, "
                    f"elapsed={cycle['elapsed_seconds']:.1f}s.",
                    flush=True,
                )

                elapsed = time.monotonic() - started_monotonic
                if failures:
                    break
                if args.max_cycles is not None:
                    if cycle_number >= args.max_cycles:
                        break
                elif elapsed >= target_seconds:
                    break
                remaining = target_seconds - elapsed
                sleep_seconds = (
                    args.interval_seconds
                    if args.max_cycles is not None
                    else min(args.interval_seconds, remaining)
                )
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)

            elapsed_seconds = time.monotonic() - started_monotonic
            completed_duration = elapsed_seconds >= target_seconds
            if failures:
                status = "FAIL"
            elif mode == "RELEASE_SOAK" and completed_duration:
                status = "PASS"
            else:
                status = "SHAKEDOWN_PASS"
            report = {
                "procedure": "M-05-soak",
                "mode": mode,
                "status": status,
                "started_at_epoch": started_wall,
                "completed_at_epoch": time.time(),
                "target_hours": args.duration_hours,
                "elapsed_hours": round(elapsed_seconds / 3600, 6),
                "completed_target_duration": completed_duration,
                "cycle_count": len(cycles),
                "interval_seconds": args.interval_seconds,
                "commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
                ).strip(),
                "environment": {
                    "os": platform.platform(),
                    "python": platform.python_version(),
                    "database_name": database_name,
                    "database_disposable": database_name.startswith(DATABASE_PREFIX),
                    "ib_mode": "not_connected; deterministic cached bars",
                    "ticker_count": args.tickers,
                    "bars_per_ticker": args.bars,
                },
                "initial_database": initial,
                "final_database": cycles[-1]["database"],
                "peak_rss_bytes": max(cycle["rss_bytes"] for cycle in cycles),
                "failures": failures,
                "cycles": cycles,
            }
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            print(f"M-05 soak report written to {args.output}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
