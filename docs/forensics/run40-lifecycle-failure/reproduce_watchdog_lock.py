"""Run 40 lock witness: production functions, isolated disposable PostgreSQL.

Run from the repository root with .venv/Scripts/python.exe and this file's path.
Creates only a randomly named swinglens_pytest_* database, then drops it.
The schema intentionally omits unrelated foreign keys; no production data is copied.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.database_safety import assert_disposable_database, resolve_test_postgres_admin_url
from app.models.tables import BackgroundJob, PipelineDependency, PipelineRun
from app.services.background_job_service import fence_stalled_jobs, heartbeat_job
from app.services.domain_write_fence import ControlPlaneLockTimeout, control_plane_transaction
from app.services.pipeline_execution_authority import job_execution_authority_predicate
from app.services.runtime_mutation_authority import RecoveryAuthority


def run() -> dict:
    database_name = "swinglens_pytest_run40_" + uuid.uuid4().hex[:12]
    admin_url = resolve_test_postgres_admin_url()
    database_url = make_url(admin_url).set(
        drivername="postgresql+psycopg", database=database_name
    ).render_as_string(hide_password=False)
    result = {"database": database_name, "production_run_mutated": False}
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        engine = create_engine(database_url, pool_size=4)
        try:
            assert_disposable_database(database_url)
            with engine.begin() as connection:
                for model in (PipelineRun, PipelineDependency, BackgroundJob):
                    connection.execute(CreateTable(model.__table__, include_foreign_key_constraints=[]))
            now = datetime.now(UTC)
            with Session(engine) as db:
                db.add(PipelineRun(id=1, upload_run_id=1, status="EVALUATING_SETUP_LIFECYCLES"))
                db.add(BackgroundJob(
                    id=1, job_type="FULL_PIPELINE", related_run_id=1,
                    payload_json={"pipeline_run_id": 1}, status="RUNNING",
                    execution_token="disposable-witness-token", worker_id="witness-worker",
                    last_progress_at=now, progress_sequence=246,
                    progress_stage="EVALUATING_SETUP_LIFECYCLES", heartbeat_at=now,
                    lease_expires_at=now + timedelta(minutes=15), operational_metadata_json={},
                ))
                db.commit()

            ready: Queue[int] = Queue()

            def heartbeat_attempt() -> dict:
                with Session(engine) as db:
                    pid = db.scalar(text("select pg_backend_pid()"))
                    ready.put(pid)
                    began = time.perf_counter()
                    try:
                        with control_plane_transaction(db, lock_timeout_seconds=3):
                            heartbeat_job(db, BackgroundJob(id=1, status="RUNNING",
                                execution_token="disposable-witness-token"),
                                execution_token="disposable-witness-token")
                    except ControlPlaneLockTimeout as exc:
                        return {"outcome": type(exc).__name__, "message": str(exc),
                                "elapsed_seconds": round(time.perf_counter() - began, 3)}
                    return {"outcome": "success"}

            with Session(engine) as watchdog, ThreadPoolExecutor(max_workers=1) as pool:
                blocker_pid = watchdog.scalar(text("select pg_backend_pid()"))
                fenced = fence_stalled_jobs(
                    watchdog, authority=RecoveryAuthority.normal("forensic.run40.witness"),
                    worker_id="witness-worker", now=now,
                    default_timeout_seconds=120, market_data_timeout_seconds=300,
                    long_stage_timeout_seconds=1800,
                )
                assert fenced == [], "Witness must be healthy, not stalled."
                result["watchdog_fenced_jobs"] = fenced
                attempt = pool.submit(heartbeat_attempt)
                waiter_pid = ready.get(timeout=10)
                deadline = time.monotonic() + 2
                graph = None
                with engine.connect() as observer:
                    while time.monotonic() < deadline:
                        graph = observer.execute(text(
                            "select pid, pg_blocking_pids(pid) blockers, wait_event_type, wait_event "
                            "from pg_stat_activity where pid=:pid"
                        ), {"pid": waiter_pid}).mappings().one()
                        if blocker_pid in graph["blockers"]:
                            break
                        observer.commit()
                        time.sleep(0.02)
                assert graph is not None and blocker_pid in graph["blockers"]
                result["lock_graph"] = dict(graph)
                result["watchdog_backend_pid"] = blocker_pid
                result["heartbeat_while_watchdog_uncommitted"] = attempt.result(timeout=10)
                assert result["heartbeat_while_watchdog_uncommitted"]["outcome"] == "ControlPlaneLockTimeout"
                watchdog.commit()
                result["heartbeat_after_watchdog_commit"] = heartbeat_attempt()
                assert result["heartbeat_after_watchdog_commit"]["outcome"] == "success"

            # A/B witness for the terminal pipeline + retryable queued job mismatch.
            with Session(engine) as db:
                db.execute(update(BackgroundJob).where(BackgroundJob.id == 1).values(status="QUEUED"))
                query = select(BackgroundJob.id).where(
                    BackgroundJob.id == 1, job_execution_authority_predicate()
                )
                result["queued_job_authorized_with_active_pipeline"] = db.scalar(query) == 1
                db.execute(update(PipelineRun).where(PipelineRun.id == 1).values(status="FAILED"))
                result["queued_job_authorized_with_failed_pipeline"] = db.scalar(query) == 1
                assert result["queued_job_authorized_with_active_pipeline"]
                assert not result["queued_job_authorized_with_failed_pipeline"]
                db.rollback()
        finally:
            engine.dispose()
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
            result["disposable_database_dropped"] = True
    return result


if __name__ == "__main__":
    evidence = run()
    Path(__file__).with_name("reproduction-results.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2))
