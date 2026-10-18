"""Prepare and exercise an isolated production-data clone; never resume production.

The clone name and every mutating connection are guarded. The retained dump is
stored under .codex-tmp, outside the reviewable remediation evidence directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs/remediation/run41-lifecycle/evidence"
WORK = ROOT / ".codex-tmp/run41-recovery"


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def pg_environment(url):
    result = dict(os.environ)
    result.update(
        PGHOST=url.host,
        PGPORT=str(url.port or 5432),
        PGUSER=url.username,
        PGPASSWORD=url.password or "",
        PGDATABASE=url.database,
    )
    return result


def prepare():
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    from app.database_safety import assert_disposable_database, resolve_test_postgres_admin_url
    from app.settings import get_settings
    from scripts.ops.postgres_tooling import resolve_tool, server_major

    source = make_url(get_settings().database_url)
    assert source.database == "swinglens", "This witness is scoped to the diagnosed source database"
    WORK.mkdir(parents=True, exist_ok=True)
    dump = WORK / "run41-production-snapshot.dump"
    if dump.exists() or (OUT / "production-clone.json").exists():
        raise FileExistsError("Retained Run 41 clone evidence must not be overwritten")
    tool, _ = resolve_tool("pg_dump", server_major(source.render_as_string(hide_password=False)))
    emit("clone.dump_started", source_database=source.database)
    with (WORK / "pg_dump.stderr.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            [
                str(tool),
                "--format=custom",
                "--compress=1",
                "--no-owner",
                "--no-acl",
                "--no-password",
                "--file",
                str(dump),
            ],
            env=pg_environment(source),
            stderr=log,
            check=True,
        )
    name = "swinglens_pytest_run41_" + uuid.uuid4().hex[:12]
    target = source.set(database=name)
    metadata = {
        "database": name,
        "dump": str(dump),
        "source_database": source.database,
        "dump_bytes": dump.stat().st_size,
        "production_written": False,
    }
    digest = hashlib.sha256()
    with dump.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    metadata["dump_sha256"] = digest.hexdigest()
    save("production-clone.json", metadata)
    with psycopg.connect(resolve_test_postgres_admin_url(), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    assert_disposable_database(target, active_database_url=source)
    tool, _ = resolve_tool("pg_restore", server_major(source.render_as_string(hide_password=False)))
    emit("clone.restore_started", database=name, dump_bytes=metadata["dump_bytes"])
    with (WORK / "pg_restore.stderr.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            [
                str(tool),
                "--no-owner",
                "--no-acl",
                "--no-password",
                "--exit-on-error",
                "--dbname",
                name,
                str(dump),
            ],
            env=pg_environment(target),
            stderr=log,
            check=True,
        )
    metadata["restored"] = True
    save("production-clone.json", metadata)
    emit("clone.ready", database=name)


def verify():
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session, sessionmaker

    from app.database_safety import assert_disposable_database
    from app.settings import get_settings

    metadata = json.loads((OUT / "production-clone.json").read_text())
    assert metadata.get("restored")
    source = make_url(get_settings().database_url)
    target = source.set(database=metadata["database"])
    assert_disposable_database(target, active_database_url=source)
    os.environ["DATABASE_URL"] = target.render_as_string(hide_password=False)
    os.environ["DB_MONITOR_LOG_DIR"] = str(OUT / "clone-sql")
    os.environ["DB_MONITOR_TEST_LOG_DIR"] = str(OUT / "clone-sql")
    get_settings.cache_clear()

    # Import the application only after its global DB factory is scoped to the clone.
    from app.models.tables import (
        BackgroundJob,
        PipelineRun,
        PipelineStep,
        TransitionDecisionHandoffManifest,
    )
    from app.observability.logging import configure_json_logging
    from app.services.background_worker import run_worker_once
    from app.services.configuration_delivery import binding_reference, load_configuration_delivery
    from app.services.pipeline_service import resume_pipeline
    from scripts.forensics.review_resumed_pipeline_evidence import review_resumed_pipeline_evidence

    configure_json_logging("run41-clone-worker")
    engine = create_engine(target, pool_size=6)
    result = {"database": target.database, "production_written": False}
    try:
        with Session(engine) as db:
            assert db.scalar(text("select current_database()")) == metadata["database"]
            pipeline = db.get(PipelineRun, 41)
            assert (
                pipeline.status == "FAILED"
                and pipeline.current_step == "EVALUATING_SETUP_LIFECYCLES"
            )
            delivery = load_configuration_delivery(db, binding_reference(db, pipeline_run_id=41))
            flags = delivery.configurations["execution.settings"].values
            assert flags["setup_capture_handoff_enabled"] is False
            result["frozen_handoff_enabled"] = False
            result["frozen_anchor"] = delivery.anchor["anchor_id"]
            handoff = db.scalar(
                select(TransitionDecisionHandoffManifest).where(
                    TransitionDecisionHandoffManifest.pipeline_run_id == 41
                )
            )
            expected_handoff = handoff.manifest_fingerprint
            pins = db.execute(
                text(
                    "select s.id, s.evaluation_run_id, s.evidence_id, e.payload_fingerprint "
                    "from setup_signal_snapshots s "
                    "join core_calculation_evidence e on e.id=s.evidence_id "
                    "where s.run_id=41 order by s.id"
                )
            ).all()
            assert len(pins) == 88 and {row.evaluation_run_id for row in pins} == {58}
            before = [tuple(row) for row in pins]
            # Quiesce copied operational backlog only in the disposable clone.
            # The real worker then claims and executes the supported resume.
            db.execute(
                text(
                    "update background_jobs set status='CANCELLED', execution_token=null, "
                    "requested_cancel=true "
                    "where status in ('RUNNING','QUEUED','STALLED','RECOVERING')"
                )
            )
            db.commit()
            pipeline = resume_pipeline(db, 41, resume_from_step="EVALUATING_SETUP_LIFECYCLES")
            job_id = pipeline.result_json["background_job_id"]
            result["evidence_review"] = review_resumed_pipeline_evidence(
                db,
                41,
                expected_handoff_fingerprint=expected_handoff,
                incident_reference="run41-lifecycle-remediation:production-clone",
            )
            db.commit()
            result["resume_job_id"] = job_id
        emit("clone.resume_started", job_id=job_id)
        started = time.perf_counter()
        executed = run_worker_once(
            worker_id="run41-clone-" + uuid.uuid4().hex[:10],
            queues=("interactive",),
            included_job_types=("FULL_PIPELINE",),
            stale_after_seconds=900,
            heartbeat_interval_seconds=5,
            session_factory=sessionmaker(bind=engine, autoflush=False, autocommit=False),
        )
        result["execution_seconds"] = round(time.perf_counter() - started, 3)
        result["worker_executed"] = executed
        with Session(engine) as db:
            pipeline = db.get(PipelineRun, 41)
            job = db.get(BackgroundJob, job_id)
            result.update(
                pipeline_status=pipeline.status,
                job_status=job.status,
                pipeline_error=pipeline.error_message,
                job_error=job.error_message,
            )
            result["steps"] = [
                dict(row._mapping)
                for row in db.execute(
                    select(PipelineStep.step_name, PipelineStep.status, PipelineStep.error_message)
                    .where(PipelineStep.pipeline_run_id == 41)
                    .order_by(PipelineStep.step_order)
                )
            ]
            after = [
                tuple(row)
                for row in db.execute(
                    text(
                        "select s.id, s.evaluation_run_id, s.evidence_id, e.payload_fingerprint "
                        "from setup_signal_snapshots s "
                        "join core_calculation_evidence e on e.id=s.evidence_id "
                        "where s.run_id=41 order by s.id"
                    )
                )
            ]
            result["snapshot_creator_and_evidence_pins_preserved"] = before == after
            result["winner_predictions"] = db.scalar(
                text("select count(*) from winner_prediction_snapshots where run_id=41")
            )
            result["lifecycle_evaluations"] = [
                dict(row._mapping)
                for row in db.execute(
                    text(
                        "select id,status,current_phase,captured_count,"
                        "canonical_count,changed_count,"
                        "transitioned_count,alerted_count "
                        "from setup_lifecycle_evaluation_runs "
                        "where source_run_id=41 order by id"
                    )
                )
            ]
        save("production-clone-result.json", result)
        emit("clone.resume_finished", **result)
        assert executed and result["job_status"] == "COMPLETED" and before == after
        validate_and_preserve(engine, result)
    finally:
        engine.dispose()


def validate_and_preserve(engine, result):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.models.tables import BackgroundJob, PipelineRun
    from scripts.forensics.run41_recovery_contract import validate_recovery

    baseline = json.loads((OUT / "preexisting-coverage-gaps.json").read_text())
    with Session(engine) as db:
        db.execute(text("set transaction read only"))
        pipeline = db.get(PipelineRun, 41)
        job = db.get(BackgroundJob, result["resume_job_id"])
        assert result["worker_executed"] and job.status == "COMPLETED"
        result["recovery_contract"] = validate_recovery(
            db, pipeline, baseline["all_combined_row_sha256"]
        )
        pins = [
            tuple(row)
            for row in db.execute(
                text(
                    "select s.id,s.evaluation_run_id,s.evidence_id,e.payload_fingerprint "
                    "from setup_signal_snapshots s join core_calculation_evidence e "
                    "on e.id=s.evidence_id where s.run_id=41 order by s.id"
                )
            )
        ]
        assert len(pins) == 88 and {row[1] for row in pins} == {58}
        assert {row[0] for row in pins} == set(range(3498, 3586))
        assert result["snapshot_creator_and_evidence_pins_preserved"]
        save("clone-preserved-snapshot-pins.json", pins)
        result["terminal_contract_verified_read_only"] = True
        save("production-clone-result.json", result)
    emit("clone.verified_terminal_outcome", **result["recovery_contract"])


def finalize():
    """Verify an already-completed rehearsal without enqueueing or executing again."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from app.database_safety import assert_disposable_database
    from app.settings import get_settings

    meta = json.loads((OUT / "production-clone.json").read_text())
    source = make_url(get_settings().database_url)
    target = source.set(database=meta["database"])
    assert_disposable_database(target, active_database_url=source)
    engine = create_engine(target)
    try:
        validate_and_preserve(
            engine, json.loads((OUT / "production-clone-result.json").read_text())
        )
    finally:
        engine.dispose()


def cleanup():
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    from app.database_safety import assert_disposable_database, resolve_test_postgres_admin_url
    from app.settings import get_settings

    metadata = json.loads((OUT / "production-clone.json").read_text())
    source = make_url(get_settings().database_url)
    target = source.set(database=metadata["database"])
    assert_disposable_database(target, active_database_url=source)
    with psycopg.connect(resolve_test_postgres_admin_url(), autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(target.database))
        )
    metadata["dropped"] = True
    save("production-clone.json", metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "finalize", "cleanup"))
    action = parser.parse_args().action
    from app import database_safety
    from app.models import tables

    if not (
        hasattr(database_safety, "resolve_test_postgres_admin_url")
        and hasattr(tables, "WinnerAcquisitionDecisionRecord")
    ):
        parser.error(
            "Historical recovery witness requires the certified Run 41 readiness "
            "baseline and acquisition-ledger schema. See "
            "docs/remediation/run41-main-publication/README.md. No action was taken."
        )
    {"prepare": prepare, "verify": verify, "finalize": finalize, "cleanup": cleanup}[action]()
