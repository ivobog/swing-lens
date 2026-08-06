from __future__ import annotations

import argparse
import atexit
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import psycopg
from fastapi import UploadFile
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.qa.run_m05_scale import (  # noqa: E402
    _configure_environment,
    _migrate,
    _native_postgres_url,
    _seed_price_bars,
    _tickers,
    _write_scale_csv,
)

CONTAINER_PREFIX = "swinglens-qa-m05-restart-"
DEFAULT_IMAGE = "postgres:16"
TERMINAL_JOB_STATUSES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "STALE"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run M-05 web, worker, and PostgreSQL restart checks in an isolated container."
    )
    parser.add_argument("--tickers", type=int, default=250)
    parser.add_argument("--bars", type=int, default=756)
    parser.add_argument("--postgres-image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "test-results" / "m05-restart.json",
    )
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if args.tickers != 250:
        raise ValueError("the release restart drill requires exactly 250 tickers")
    if not 252 <= args.bars <= 1000:
        raise ValueError("bars must be between 252 and 1000")
    if not args.postgres_image.startswith("postgres:"):
        raise ValueError("restart drill requires an explicit official postgres image tag")


def _docker(*arguments: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _require_docker_success(result: subprocess.CompletedProcess[str], action: str) -> str:
    if result.returncode:
        raise RuntimeError(f"docker {action} failed: {result.stdout}{result.stderr}")
    return result.stdout.strip()


@contextmanager
def _postgres_container(
    image: str,
    spawned_processes: list[tuple[subprocess.Popen[str], Any]],
) -> Any:
    container_name = f"{CONTAINER_PREFIX}{uuid.uuid4().hex[:10]}"
    if not container_name.startswith(CONTAINER_PREFIX):
        raise RuntimeError("refusing to create an unscoped restart container")
    created = False
    try:
        requested_host_port = _free_local_port()
        container_id = _require_docker_success(
            _docker(
                "run",
                "--detach",
                "--name",
                container_name,
                "--label",
                "swinglens.qa.procedure=M-05",
                "--env",
                "POSTGRES_DB=swinglens_qa_m05_restart",
                "--env",
                "POSTGRES_USER=postgres",
                "--env",
                "POSTGRES_PASSWORD=postgres",
                "--publish",
                f"127.0.0.1:{requested_host_port}:5432",
                image,
            ),
            "run",
        )
        created = True
        inspection = json.loads(
            _require_docker_success(_docker("inspect", container_name), "inspect")
        )[0]
        port_bindings = inspection["NetworkSettings"]["Ports"]["5432/tcp"]
        if not port_bindings:
            raise RuntimeError("disposable PostgreSQL port was not published")
        host_port = int(port_bindings[0]["HostPort"])
        if host_port != requested_host_port:
            raise RuntimeError(
                f"unexpected PostgreSQL port binding: {host_port} != {requested_host_port}"
            )
        database_url = (
            "postgresql+psycopg://postgres:postgres@127.0.0.1:"
            f"{host_port}/swinglens_qa_m05_restart"
        )
        _wait_for_postgres(database_url, timeout=90)
        yield container_name, container_id, database_url
    finally:
        _cleanup_spawned_processes(spawned_processes)
        if created:
            if not container_name.startswith(CONTAINER_PREFIX):
                raise RuntimeError("refusing to remove an unscoped restart container")
            _docker("rm", "--force", container_name, timeout=120)


def _wait_for_postgres(database_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(_native_postgres_url(database_url), connect_timeout=2) as db:
                db.execute("SELECT 1")
            return
        except psycopg.Error as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"PostgreSQL did not become ready: {last_error}")


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _process_environment(database_url: str, runtime_root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": database_url,
        "UPLOAD_DIR": str(runtime_root / "uploads"),
        "EXPORT_DIR": str(runtime_root / "exports"),
        "CACHE_DIR": str(runtime_root / "cache"),
        "JOB_POLL_INTERVAL_SECONDS": "0.2",
        "JOB_STALE_AFTER_SECONDS": "5",
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


def _start_process(
    arguments: list[str],
    environment: dict[str, str],
    log_path: Path,
) -> tuple[subprocess.Popen[str], Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        arguments,
        cwd=REPO_ROOT,
        env=environment,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creation_flags,
    )
    return process, log_handle


def _stop_process(process: subprocess.Popen[str] | None, log_handle: Any | None) -> int | None:
    exit_code: int | None = None
    if process is not None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        exit_code = process.returncode
    if log_handle is not None:
        log_handle.close()
    return exit_code


def _cleanup_spawned_processes(entries: list[tuple[subprocess.Popen[str], Any]]) -> None:
    for process, log_handle in reversed(entries):
        _stop_process(process, log_handle)


def _http_get(base_url: str, path: str, timeout: float = 10) -> tuple[int, str]:
    try:
        with urlopen(f"{base_url}{path}", timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _wait_for_http(
    base_url: str,
    path: str,
    expected_status: int,
    timeout: float,
    request_timeout: float = 2,
) -> str:
    deadline = time.monotonic() + timeout
    last_status: int | None = None
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            last_status, body = _http_get(base_url, path, timeout=request_timeout)
            if last_status == expected_status:
                return body
        except (TimeoutError, URLError, OSError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(
        f"GET {path} did not reach {expected_status}; "
        f"last_status={last_status}, error={last_error}"
    )


def _wait_for(
    description: str,
    observation: Callable[[], Any],
    predicate: Callable[[Any], bool],
    timeout: float,
) -> Any:
    deadline = time.monotonic() + timeout
    last_value: Any = None
    while time.monotonic() < deadline:
        try:
            last_value = observation()
            if predicate(last_value):
                return last_value
        except psycopg.Error:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {description}; last value={last_value!r}")


def _pipeline_state(database_url: str, pipeline_id: int) -> tuple[str, str | None]:
    with psycopg.connect(_native_postgres_url(database_url), connect_timeout=2) as db:
        row = db.execute(
            "SELECT status, current_step FROM pipeline_runs WHERE id = %s",
            (pipeline_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"pipeline {pipeline_id} disappeared")
    return str(row[0]), str(row[1]) if row[1] is not None else None


def _job_state(database_url: str, job_id: int) -> tuple[str, int]:
    with psycopg.connect(_native_postgres_url(database_url), connect_timeout=2) as db:
        row = db.execute(
            "SELECT status, retry_count FROM background_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"background job {job_id} disappeared")
    return str(row[0]), int(row[1])


def _job_lease(database_url: str, job_id: int) -> tuple[str, str | None]:
    with psycopg.connect(_native_postgres_url(database_url), connect_timeout=2) as db:
        row = db.execute(
            "SELECT status, lease_owner FROM background_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"background job {job_id} disappeared")
    return str(row[0]), str(row[1]) if row[1] is not None else None


def _create_pipeline(database_url: str, runtime_root: Path, ticker_count: int) -> dict[str, Any]:
    from app.services.ib_fetch_plan_service import build_fetch_plan
    from app.services.pipeline_service import start_pipeline
    from app.services.upload_service import create_upload_run

    engine = create_engine(database_url, pool_pre_ping=True)
    csv_path = runtime_root / "m05-restart-250.csv"
    _write_scale_csv(csv_path, _tickers(ticker_count))
    try:
        with Session(engine) as db, csv_path.open("rb") as handle:
            upload_run = create_upload_run(
                db,
                UploadFile(filename=csv_path.name, file=handle),
            )
            plan = build_fetch_plan(
                db,
                _tickers(ticker_count),
                run_id=upload_run.id,
                include_benchmarks=True,
            )
            if plan.estimated_request_count != 0:
                raise RuntimeError(
                    "offline restart fixture is incomplete: "
                    f"{plan.estimated_request_count} IB requests were planned"
                )
            first = start_pipeline(
                db,
                upload_run.id,
                requested_by="m05-restart",
                ceri_run_capture_enabled=False,
                setup_lifecycle_pipeline_step_enabled=False,
            )
            second = start_pipeline(
                db,
                upload_run.id,
                requested_by="m05-restart-duplicate",
                ceri_run_capture_enabled=False,
                setup_lifecycle_pipeline_step_enabled=False,
            )
            if first.id != second.id or not getattr(second, "_coalesced", False):
                raise RuntimeError("duplicate full-pipeline request was not coalesced")
            db.commit()
            return {
                "upload_run_id": upload_run.id,
                "pipeline_id": first.id,
                "background_job_id": int(first.result_json["background_job_id"]),
                "coalesced_pipeline_id": second.id,
                "planned_ib_requests": plan.estimated_request_count,
            }
    finally:
        engine.dispose()


def _final_evidence(database_url: str, ids: dict[str, Any], ticker_count: int) -> dict[str, Any]:
    from app.models.tables import (
        BackgroundJob,
        CombinedResult,
        FundamentalScore,
        MarketRegimeSnapshot,
        PipelineRun,
        PipelineStep,
        RawCompanyRow,
        SectorRotationSnapshot,
        TechnicalScore,
    )

    engine = create_engine(database_url, pool_pre_ping=True)
    run_id = int(ids["upload_run_id"])
    try:
        with Session(engine) as db:
            models = {
                "raw_rows": RawCompanyRow,
                "fundamental_scores": FundamentalScore,
                "technical_scores": TechnicalScore,
                "combined_results": CombinedResult,
                "market_regime_snapshots": MarketRegimeSnapshot,
                "sector_rotation_snapshots": SectorRotationSnapshot,
            }
            counts = {
                name: int(
                    db.scalar(select(func.count(model.id)).where(model.run_id == run_id)) or 0
                )
                for name, model in models.items()
            }
            pipeline = db.get(PipelineRun, ids["pipeline_id"])
            job = db.get(BackgroundJob, ids["background_job_id"])
            if pipeline is None or job is None:
                raise RuntimeError("pipeline or background job evidence disappeared")
            steps = db.scalars(
                select(PipelineStep)
                .where(PipelineStep.pipeline_run_id == pipeline.id)
                .order_by(PipelineStep.step_order)
            ).all()
            active_jobs = int(
                db.scalar(
                    select(func.count(BackgroundJob.id)).where(
                        BackgroundJob.status.in_(("QUEUED", "RUNNING"))
                    )
                )
                or 0
            )
            total_jobs = int(db.scalar(select(func.count(BackgroundJob.id))) or 0)
            expected = {
                "raw_rows": ticker_count,
                "fundamental_scores": ticker_count,
                "technical_scores": ticker_count,
                "combined_results": ticker_count,
                "market_regime_snapshots": 1,
                "sector_rotation_snapshots": 1,
            }
            if counts != expected:
                raise RuntimeError(f"restart evidence mismatch: {counts} != {expected}")
            if total_jobs != 1 or active_jobs != 0:
                raise RuntimeError(
                    f"unexpected durable job counts: total={total_jobs}, active={active_jobs}"
                )
            lease_events = list((job.operational_metadata_json or {}).get("lease_events") or [])
            recovered = [event for event in lease_events if event.get("event_type") == "RECOVERED"]
            if len(recovered) < 2:
                raise RuntimeError(f"expected two stale recoveries, observed {len(recovered)}")
            return {
                "counts": counts,
                "pipeline_status": pipeline.status,
                "job_status": job.status,
                "job_retry_count": job.retry_count,
                "total_jobs": total_jobs,
                "active_jobs": active_jobs,
                "lease_event_types": [event.get("event_type") for event in lease_events],
                "step_retry_counts": {step.step_name: step.retry_count for step in steps},
            }
    finally:
        engine.dispose()


def main() -> int:
    args = _parse_args()
    _validate_args(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    web_process: subprocess.Popen[str] | None = None
    web_log: Any | None = None
    worker_process: subprocess.Popen[str] | None = None
    worker_log: Any | None = None
    process_events: list[dict[str, Any]] = []
    spawned_processes: list[tuple[subprocess.Popen[str], Any]] = []
    atexit.register(_cleanup_spawned_processes, spawned_processes)

    with tempfile.TemporaryDirectory(prefix="swinglens-m05-restart-") as temporary:
        runtime_root = Path(temporary)
        with _postgres_container(args.postgres_image, spawned_processes) as (
            container_name,
            container_id,
            database_url,
        ):
            print(f"Created isolated PostgreSQL container {container_name}.", flush=True)
            _migrate(database_url, runtime_root)
            _configure_environment(database_url, runtime_root)
            tickers = _tickers(args.tickers)
            seeded = _seed_price_bars(
                database_url,
                tickers,
                args.bars,
                what_to_show_values=("ADJUSTED_LAST", "TRADES"),
            )
            ids = _create_pipeline(database_url, runtime_root, args.tickers)
            print(f"Queued coalesced pipeline {ids['pipeline_id']}.", flush=True)

            environment = _process_environment(database_url, runtime_root)
            web_environment = {**environment, "JOB_WORKER_ENABLED": "false"}
            worker_environment = {
                **environment,
                "JOB_WORKER_ENABLED": "true",
                "JOB_WORKER_ID": "m05-worker-1",
            }
            web_port = _free_local_port()
            base_url = f"http://127.0.0.1:{web_port}"
            web_process, web_log = _start_process(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                ],
                web_environment,
                args.output.with_name("m05-web-1.log"),
            )
            spawned_processes.append((web_process, web_log))
            _wait_for_http(base_url, "/health", 200, 30)
            _wait_for_http(base_url, "/ready", 200, 30)
            process_events.append({"event": "web_started", "pid": web_process.pid})

            worker_process, worker_log = _start_process(
                [sys.executable, "-m", "app.worker"],
                worker_environment,
                args.output.with_name("m05-worker-1.log"),
            )
            spawned_processes.append((worker_process, worker_log))
            _wait_for(
                "first worker technical step",
                lambda: _pipeline_state(database_url, ids["pipeline_id"]),
                lambda state: state[1] == "SCORING_TECHNICALS",
                120,
            )
            process_events.append({"event": "worker_1_claimed", "pid": worker_process.pid})

            web_exit = _stop_process(web_process, web_log)
            web_process = None
            web_log = None
            process_events.append({"event": "web_stopped", "exit_code": web_exit})
            web_process, web_log = _start_process(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                ],
                web_environment,
                args.output.with_name("m05-web-2.log"),
            )
            spawned_processes.append((web_process, web_log))
            _wait_for_http(base_url, "/ready", 200, 30)
            status_code, _ = _http_get(
                base_url,
                f"/runs/{ids['upload_run_id']}/pipeline/{ids['pipeline_id']}/status",
            )
            if status_code != 200:
                raise RuntimeError(f"pipeline status after web restart returned {status_code}")
            process_events.append({"event": "web_restarted", "pid": web_process.pid})

            worker_1_exit = _stop_process(worker_process, worker_log)
            worker_process = None
            worker_log = None
            process_events.append({"event": "worker_1_stopped", "exit_code": worker_1_exit})
            time.sleep(6)

            worker_environment["JOB_WORKER_ID"] = "m05-worker-2"
            worker_process, worker_log = _start_process(
                [sys.executable, "-m", "app.worker"],
                worker_environment,
                args.output.with_name("m05-worker-2.log"),
            )
            spawned_processes.append((worker_process, worker_log))
            _wait_for(
                "worker 2 durable lease",
                lambda: _job_lease(database_url, ids["background_job_id"]),
                lambda state: state == ("RUNNING", "m05-worker-2"),
                120,
            )
            process_events.append({"event": "worker_2_recovered", "pid": worker_process.pid})

            _require_docker_success(_docker("stop", "--time", "1", container_name), "stop")
            degraded_started = time.perf_counter()
            degraded_body = _wait_for_http(
                base_url,
                "/ready",
                200,
                15,
                request_timeout=8,
            )
            degraded_elapsed_ms = (time.perf_counter() - degraded_started) * 1000
            degraded_payload = json.loads(degraded_body)
            if degraded_payload.get("status") != "degraded":
                raise RuntimeError(f"readiness did not degrade: {degraded_payload}")
            if "postgres:postgres" in degraded_body or database_url in degraded_body:
                raise RuntimeError("readiness degradation leaked database credentials")
            process_events.append(
                {
                    "event": "postgres_stopped",
                    "readiness_http_status": 200,
                    "readiness_status": "degraded",
                    "readiness_elapsed_ms": round(degraded_elapsed_ms, 3),
                }
            )
            worker_2_exit = _stop_process(worker_process, worker_log)
            worker_process = None
            worker_log = None

            _require_docker_success(_docker("start", container_name), "start")
            _wait_for_postgres(database_url, 90)
            _wait_for_http(base_url, "/ready", 200, 60)
            process_events.append(
                {
                    "event": "postgres_restarted",
                    "readiness_http_status": 200,
                    "readiness_status": "ok",
                    "worker_2_exit": worker_2_exit,
                }
            )
            time.sleep(6)

            worker_environment["JOB_WORKER_ID"] = "m05-worker-3"
            worker_process, worker_log = _start_process(
                [sys.executable, "-m", "app.worker"],
                worker_environment,
                args.output.with_name("m05-worker-3.log"),
            )
            spawned_processes.append((worker_process, worker_log))
            final_job = _wait_for(
                "terminal recovered job",
                lambda: _job_state(database_url, ids["background_job_id"]),
                lambda state: state[0] in TERMINAL_JOB_STATUSES,
                900,
            )
            process_events.append(
                {"event": "worker_3_completed", "pid": worker_process.pid, "job": final_job}
            )
            worker_3_exit = _stop_process(worker_process, worker_log)
            worker_process = None
            worker_log = None
            process_events[-1]["exit_code"] = worker_3_exit

            status_code, _ = _http_get(base_url, f"/runs/{ids['upload_run_id']}", timeout=30)
            if status_code != 200:
                raise RuntimeError(f"run detail after recovery returned {status_code}")
            evidence = _final_evidence(database_url, ids, args.tickers)
            report = {
                "procedure": "M-05-restart",
                "status": "PASS",
                "started_at": started_at,
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
                ).strip(),
                "environment": {
                    "postgres_image": args.postgres_image,
                    "container_name": container_name,
                    "container_id": container_id,
                    "container_is_disposable": container_name.startswith(CONTAINER_PREFIX),
                    "ticker_count": args.tickers,
                    "bars_per_ticker": args.bars,
                    "seeded_price_bars": seeded,
                    "ib_mode": "not_connected; deterministic cached bars",
                },
                "ids": ids,
                "process_events": process_events,
                "evidence": evidence,
            }
            args.output.write_text(
                json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            print(f"M-05 restart report written to {args.output}")

            _stop_process(web_process, web_log)
            web_process = None
            web_log = None

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
