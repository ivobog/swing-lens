"""Bounded native recovery faults using the existing frozen pipeline runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.tables import BackgroundSupervisor, BackgroundWorker, IBContract
from app.services.background_job_service import (
    TERMINAL_JOB_STATUSES,
    JobLeaseLost,
    claim_next_job,
    default_retry_delay,
    request_job_cancel,
    requeue_stalled_jobs,
)
from app.services.domain_write_fence import assert_current_execution_ownership, fence_domain_commits
from app.services.runtime_mutation_authority import RecoveryAuthority
from single_run_certification.candidate_validation import VERSION
from single_run_certification.fixtures import (
    _seed_price_bars,
    _seed_setup_lifecycle_baseline,
    certification_as_of_session,
)
from single_run_certification.golden_assertions import assert_completed_oracle
from single_run_certification.golden_runtime import business_inputs
from single_run_certification.reporting import CertificationRecorder
from single_run_certification.test_real_writer_cardinality import (
    EXACT_TEN,
    _seed_retained_provider_history,
)
from single_run_certification.test_single_run_certification import (
    _launch_run_through_gui,
    certification_environment,  # noqa: F401
    certification_page,  # noqa: F401
    certification_provider_ingest_enabled,  # noqa: F401
    certification_provider_mode,  # noqa: F401
    certification_setup_capture_handoff_enabled,  # noqa: F401
)

GATE_KEY = 110028


@pytest.fixture
def certification_golden_version():
    return VERSION


@pytest.fixture(autouse=True)
def watchdog_fault_profile(request, monkeypatch):
    """The watchdog specimen observes the supported 60-second stall boundary."""
    if request.node.callspec.params.get("fault") == "watchdog":
        monkeypatch.setenv("JOB_PROGRESS_TIMEOUT_SECONDS", "60")
        monkeypatch.setenv("JOB_LONG_STAGE_PROGRESS_TIMEOUT_SECONDS", "60")
        monkeypatch.setenv("JOB_WATCHDOG_INTERVAL_SECONDS", "1")
        # The existing transient-lock check observes two seconds. Ambient
        # five-second heartbeats cannot guarantee a heartbeat in that window.
        monkeypatch.setenv("JOB_WORKER_HEARTBEAT_INTERVAL_SECONDS", "0.2")


def _rows(engine, statement, **parameters):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(statement), parameters).mappings()]


def _wait(description, operation, *, seconds=120):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = operation()
        if last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"{description} did not complete in {seconds}s; last={last!r}")


def _job(engine, job_id):
    return _rows(engine, "select * from background_jobs where id=:id", id=job_id)[0]


def _terminal_pipeline(engine, pipeline_id, run_id):
    row = _rows(engine, "select * from pipeline_runs where id=:id", id=pipeline_id)[0]
    if row["status"] not in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "BLOCKED"}:
        return None
    jobs = _rows(engine, "select status from background_jobs where related_run_id=:run", run=run_id)
    # A failed pipeline stage may be reopened by the same genuinely retrying
    # job. Observe the committed terminal obligation, rather than that interval.
    return row if jobs and all(j["status"] in TERMINAL_JOB_STATUSES for j in jobs) else None


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _verify_process(engine, env, *, role, job=None):
    worker_id = "certification-" + env.execution_id
    model = BackgroundWorker if role == "worker" else BackgroundSupervisor
    with Session(engine) as db:
        registration = db.get(model, worker_id)
        assert registration is not None and registration.process_id
        pid = registration.process_id
        started = registration.process_started_at
        instance = getattr(registration, "instance_id", None)
    process = psutil.Process(pid)
    command = process.cmdline()
    process_environment = process.environ()
    executable = Path(process.exe()).resolve()
    allowed = {Path(psutil.Process(os.getpid()).exe()).resolve(), Path(sys.executable).resolve()}
    if getattr(sys, "_base_executable", None):
        allowed.add(Path(sys._base_executable).resolve())
    assert executable in allowed
    assert process.username() == psutil.Process(os.getpid()).username()
    assert "-m" in command and command[command.index("-m") + 1] == (
        "app.worker" if role == "worker" else "app.worker_supervisor"
    )
    assert command[command.index("--worker-id") + 1] == worker_id
    assert Path(process.cwd()).resolve() == Path(env.environment["runtime_root"]).resolve()
    assert process_environment["SWINGLENS_RUNTIME_INSTANCE_ID"] == env.execution_id
    assert process_environment["DATABASE_URL"] == env.database_url
    assert env.database_name.startswith("swinglens_pytest_cert_")
    assert abs(process.create_time() - started.timestamp()) < 0.05
    if job is not None:
        assert job["worker_id"] == worker_id and job["worker_instance_id"] == instance
        assert job["status"] == "RUNNING" and job["execution_token"]
    identity = {
        "watchdog_environment": {
            key: process_environment.get(key)
            for key in (
                "JOB_WATCHDOG_INTERVAL_SECONDS", "JOB_PROGRESS_TIMEOUT_SECONDS",
                "JOB_LONG_STAGE_PROGRESS_TIMEOUT_SECONDS",
                "JOB_WORKER_HEARTBEAT_INTERVAL_SECONDS", "LOG_LEVEL",
            )
        },
        "pid": pid,
        "created_at": process.create_time(),
        "owner": process.username(),
        "executable": str(executable),
        "command": command,
        "instance_id": instance,
        "runtime_instance_id": env.execution_id,
        "database": env.database_name,
        "cwd": process.cwd(),
        "role": role,
    }
    return process, identity


def _install_gate(engine, *, run_id, mode):
    # A sequence survives rollback, so exactly the first attempted row faults.
    # Only this test-created database and its owned upload are affected.
    error = (
        "RAISE EXCEPTION 'A11_RECOVERABLE' USING ERRCODE = '40001';"
        if mode == "retry"
        else "RAISE EXCEPTION 'A11_PERMANENT' USING ERRCODE = '23514';"
        if mode == "permanent"
        else ""
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE SEQUENCE a11_checkpoint_hits"))
        connection.execute(
            text(f"""
            CREATE FUNCTION a11_technical_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.run_id = {int(run_id)} AND nextval('a11_checkpoint_hits') = 1 THEN
                    PERFORM pg_advisory_xact_lock({GATE_KEY});
                    {error}
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER a11_technical_checkpoint BEFORE INSERT ON technical_scores
            FOR EACH ROW EXECUTE FUNCTION a11_technical_checkpoint()
        """)
        )


def _start_pipeline(page, env, run_id):
    page.get_by_role("button", name="Run full pipeline").click()
    preflight = page.locator("[data-ib-preflight-panel]")
    preflight.wait_for(state="visible", timeout=30_000)
    preflight.locator("[data-ib-run-ready]").wait_for(state="visible", timeout=30_000)
    preflight.locator("[data-ib-run-ready]").click()
    confirm = page.locator("[data-confirm-panel]")
    confirm.wait_for(state="visible", timeout=30_000)
    confirm.locator("[data-confirm-continue]").click(no_wait_after=True)
    page.wait_for_url(re.compile(rf"/runs/{run_id}/pipeline/\d+$"), timeout=300_000)
    return int(page.url.rsplit("/", 1)[-1])


def _stale_publication_is_refused(engine, job_id, token, run_id):
    before = _rows(
        engine, "select * from fundamental_scores where run_id=:run order by id", run=run_id
    )
    assert len(before) == 10
    with Session(engine) as stale:
        with pytest.raises(JobLeaseLost, match="lease is no longer held"):
            with fence_domain_commits(stale, job_id=job_id, execution_token=token):
                # An actual attempted change to a previously committed business
                # row must roll back at the production commit-time lease fence.
                stale.execute(
                    text(
                        "update fundamental_scores set fundamental_score=fundamental_score+1 "
                        "where id=:id"
                    ),
                    {"id": before[0]["id"]},
                )
                stale.commit()
        stale.rollback()
    after = _rows(
        engine, "select * from fundamental_scores where run_id=:run order by id", run=run_id
    )
    assert after == before
    return {
        "attempted_row_id": before[0]["id"],
        "refusal": "JobLeaseLost",
        "before_sha256": _fingerprint(before),
        "after_sha256": _fingerprint(after),
    }


def _watchdog_interference(engine, env, job, receipt, save):
    profile = receipt["original_process"]["watchdog_environment"]
    assert {key: profile[key] for key in (
        "JOB_PROGRESS_TIMEOUT_SECONDS", "JOB_LONG_STAGE_PROGRESS_TIMEOUT_SECONDS",
        "JOB_WATCHDOG_INTERVAL_SECONDS", "JOB_WORKER_HEARTBEAT_INTERVAL_SECONDS",
    )} == {
        "JOB_PROGRESS_TIMEOUT_SECONDS": "60",
        "JOB_LONG_STAGE_PROGRESS_TIMEOUT_SECONDS": "60",
        "JOB_WATCHDOG_INTERVAL_SECONDS": "1",
        "JOB_WORKER_HEARTBEAT_INTERVAL_SECONDS": "0.2",
    }
    # First observe a genuine, shorter-than-control-timeout heartbeat lock wait.
    with engine.connect() as locker:
        locker.execute(
            text("select id from background_jobs where id=:id for update"), {"id": job["id"]}
        )
        waiting = _wait(
            "native heartbeat row-lock wait",
            lambda: _rows(
                engine,
                """
            select pid,wait_event,query from pg_stat_activity
            where datname=current_database() and wait_event_type='Lock'
              and query ilike '%background_jobs%' and query not ilike '%pg_stat_activity%'
        """,
            ),
            seconds=2,
        )
        receipt["transient_lock_wait"] = waiting
        locker.rollback()
    fresh = _wait(
        "heartbeat resumes without recovery",
        lambda: (
            row if (row := _job(engine, job["id"]))["heartbeat_at"] > job["heartbeat_at"] else None
        ),
        seconds=10,
    )
    assert fresh["execution_token"] == job["execution_token"]
    assert fresh["worker_instance_id"] == job["worker_instance_id"] and fresh["recovery_count"] == 0
    receipt["transient_kept_owner"] = True
    save()
    # The test profile uses the existing supported 60s long-stage minimum.
    # Observe real elapsed time rather than backdating the worker or job.
    _wait(
        "watchdog boundary approaches",
        lambda: (
            (datetime.now(UTC) - _job(engine, job["id"])["last_progress_at"]).total_seconds() >= 59
        ),
        seconds=70,
    )
    receipt["process_before_watchdog_fence"] = _verify_process(
        engine, env, role="worker", job=_job(engine, job["id"])
    )[1]
    with engine.connect() as locker:
        locker.execute(
            text("select id from background_jobs where id=:id for update"), {"id": job["id"]}
        )
        paths = (env.server_log, env.artifact_dir / "logs/lifecycle-worker.log")
        acquired_at = datetime.now(UTC)
        try:
            _wait(
                "native watchdog locked candidate refusal",
                lambda: any(
                    "LOCKED_OR_ADVANCED_CANDIDATE_SKIPPED"
                    in p.read_text(encoding="utf-8", errors="replace")
                    for p in paths
                    if p.exists()
                ),
                seconds=3,
            )
        except AssertionError:
            # Diagnostic reads happen only after the unchanged deadline fails.
            receipt["failed_lock_observation"] = {
                "acquired_at": acquired_at.isoformat(),
                "deadline_failed_at": datetime.now(UTC).isoformat(),
                "job_after_deadline": _job(engine, job["id"]),
                "observed_log_paths": [str(path) for path in paths],
                "required_seconds": 3,
            }
            save()
            raise
        locked = _job(engine, job["id"])
        assert locked["execution_token"] == job["execution_token"] and locked["status"] == "RUNNING"
        receipt["expired_locked_candidate_skipped"] = True
        receipt["elapsed_progress_seconds"] = (
            datetime.now(UTC) - locked["last_progress_at"]
        ).total_seconds()
        locker.rollback()
    save()


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.destructive
@pytest.mark.parametrize("fault", ("death", "watchdog", "cancel", "retry", "permanent"))
def test_original_pipeline_worker_fault_recovery(
    certification_environment,  # noqa: F811
    certification_page,  # noqa: F811
    fault,
):
    env, page = certification_environment, certification_page
    engine = create_engine(env.database_url)
    receipt = {
        "fault": fault,
        "execution_id": env.execution_id,
        "test_started_utc": datetime.now(UTC),
        "database": env.database_name,
        "checkpoints": [],
    }
    path = env.artifact_dir / "native-recovery.json"
    started = time.monotonic()

    def save():
        receipt["seconds"] = time.monotonic() - started
        path.write_text(json.dumps(receipt, indent=2, default=str) + "\n", encoding="utf-8")

    save()
    gate = engine.connect()
    try:
        assert tuple(env.tickers) == EXACT_TEN and env.provider_mode == "deterministic"
        with Session(engine) as db, business_inputs(env.database_url):
            db.add_all(
                IBContract(
                    ticker=ticker,
                    ib_conid=9_500_000 + index,
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
                run_id=env.seed.decoy_run_id,
                as_of_session=certification_as_of_session(),
                tickers=EXACT_TEN,
            )
            _seed_price_bars(db, tickers=EXACT_TEN, as_of_session=certification_as_of_session())
            db.commit()
        run_id = _launch_run_through_gui(page, env, CertificationRecorder(env.execution_id))
        with business_inputs(env.database_url):
            _seed_retained_provider_history(engine, tickers=EXACT_TEN)
        _install_gate(engine, run_id=run_id, mode=fault)
        gate.execute(text("select pg_advisory_lock(:key)"), {"key": GATE_KEY})
        pipeline_id = _start_pipeline(page, env, run_id)
        _wait(
            "original handler reaches native technical INSERT",
            lambda: _rows(
                engine,
                """
            select pid,granted from pg_locks
            where locktype='advisory' and objid=:key and not granted
        """,
                key=GATE_KEY,
            ),
            seconds=180,
        )
        root = _rows(
            engine,
            "select * from background_jobs where related_run_id=:run "
            "and job_type='FULL_PIPELINE' order by id",
            run=run_id,
        )[0]
        old_token, old_instance = root["execution_token"], root["worker_instance_id"]
        fundamental_before = _rows(
            engine, "select * from fundamental_scores where run_id=:run order by id", run=run_id
        )
        assert len(fundamental_before) == 10
        evidence_ids = [row["evidence_id"] for row in fundamental_before]
        assert all(evidence_ids)
        evidence_before = _rows(
            engine,
            "select * from core_calculation_evidence where id=ANY(:ids) order by id",
            ids=evidence_ids,
        )
        assert len(evidence_before) == 10
        assert not _rows(engine, "select id from technical_scores where run_id=:run", run=run_id)
        process, identity = _verify_process(engine, env, role="worker", job=root)
        _, supervisor_identity = _verify_process(engine, env, role="supervisor")
        assert supervisor_identity["pid"] in {p.pid for p in process.parents()}
        receipt.update(
            run_id=run_id,
            pipeline_id=pipeline_id,
            root_job_id=root["id"],
            original_process=identity,
            supervisor_process=supervisor_identity,
            original_token_sha256=hashlib.sha256(old_token.encode()).hexdigest(),
            original_fundamental_ids=[r["id"] for r in fundamental_before],
            original_fundamental_rows=fundamental_before,
            retained_fundamental_evidence=evidence_before,
            checkpoint=(
                "RUNNING original FULL_PIPELINE; committed fundamentals10; "
                "technical INSERT transaction held"
            ),
        )
        save()
        if fault == "death":
            # Revalidate immediately before terminating this exact owned PID.
            process, verified = _verify_process(
                engine, env, role="worker", job=_job(engine, root["id"])
            )
            assert verified == identity
            process.kill()
            process.wait(timeout=15)
            receipt["actual_owned_worker_terminated"] = True
            save()
        elif fault == "watchdog":
            _watchdog_interference(engine, env, root, receipt, save)
        elif fault == "cancel":
            with Session(engine) as control:
                request_job_cancel(control, root["id"])
                control.commit()
            assert _job(engine, root["id"])["requested_cancel"]
            receipt["cancellation_committed_before_gate_release"] = True
            save()
        if fault in {"death", "watchdog"}:
            replacement = _wait(
                "supervisor replaces the lost/fenced generation",
                lambda: (
                    rows
                    if (
                        rows := _rows(
                            engine,
                            "select * from background_workers where worker_id=:worker "
                            "and instance_id<>:old and stopping_at is null",
                            worker=root["worker_id"],
                            old=old_instance,
                        )
                    )
                    else None
                ),
                seconds=120,
            )[0]
            assert replacement["process_id"] != identity["pid"]
            receipt["replacement_process"] = _verify_process(engine, env, role="worker")[1]
            _wait(
                "replacement owns the running original obligation",
                lambda: (
                    row
                    if (row := _job(engine, root["id"]))["status"] == "RUNNING"
                    and row["worker_instance_id"] == replacement["instance_id"]
                    and row["execution_token"] != old_token
                    else None
                ),
                seconds=30,
            )
            receipt["fenced_original_job"] = {
                k: v for k, v in _job(engine, root["id"]).items() if k != "execution_token"
            }
            with Session(engine) as stale:
                with pytest.raises(JobLeaseLost, match="lease is no longer held"):
                    assert_current_execution_ownership(
                        stale, job_id=root["id"], execution_token=old_token
                    )
                stale.rollback()
            receipt["stale_refusal_after_replacement"] = (
                "JobLeaseLost at native ownership validator"
            )
            save()
        gate.execute(text("select pg_advisory_unlock(:key)"), {"key": GATE_KEY})
        gate.commit()
        if fault == "retry":
            failed_attempt = _wait(
                "failed attempt durably queued for permitted retry",
                lambda: (
                    row
                    if (row := _job(engine, root["id"]))["status"] == "QUEUED"
                    and row["retry_count"] == 1
                    else None
                ),
                seconds=30,
            )
            attempt = failed_attempt["operational_metadata_json"]["last_attempt"]
            assert attempt["status"] == "RETRYING"
            assert failed_attempt["operational_metadata_json"]["attempt_count"] == 1
            assert failed_attempt["run_after"] - datetime.fromisoformat(
                attempt["finished_at"]
            ) == default_retry_delay(1)
            assert not _rows(
                engine, "select id from technical_scores where run_id=:run", run=run_id
            )
            receipt["committed_failed_attempt_before_retry"] = {
                key: value for key, value in failed_attempt.items() if key != "execution_token"
            }
            save()
        expected = (
            "CANCELLED" if fault == "cancel" else "FAILED" if fault == "permanent" else "COMPLETED"
        )
        final = _wait(
            "truthful native pipeline terminal outcome",
            lambda: _terminal_pipeline(engine, pipeline_id, run_id),
            seconds=600,
        )
        receipt["pipeline_terminal"] = final
        save()
        assert final["status"] == expected, final
        jobs = _rows(
            engine,
            "select * from background_jobs where related_run_id=:run order by id",
            run=run_id,
        )
        root_final = next(r for r in jobs if r["id"] == root["id"])
        receipt["jobs"] = [{k: v for k, v in r.items() if k != "execution_token"} for r in jobs]
        assert root_final["status"] == ("COMPLETED" if expected == "COMPLETED" else expected)
        assert not root_final["execution_token"]
        assert all(
            root_final[key] == root[key]
            for key in (
                "scope_id",
                "refresh_cycle_id",
                "acquisition_plan_id",
                "request_key",
                "related_run_id",
            )
        )
        fundamental_after = _rows(
            engine, "select * from fundamental_scores where run_id=:run order by id", run=run_id
        )

        # Production replay deletes/rebuilds the mutable serving projection.
        # Its surrogate ID, insertion time and evidence pointer may change;
        # every financial/source/configuration value and all old immutable
        # calculation evidence must remain exact.
        def financial_projection(rows):
            return {
                row["ticker"]: {
                    key: value
                    for key, value in row.items()
                    if key not in {"id", "created_at", "evidence_id"}
                }
                for row in rows
            }

        assert len(fundamental_after) == 10
        assert financial_projection(fundamental_after) == financial_projection(fundamental_before)
        assert (
            _rows(
                engine,
                "select * from core_calculation_evidence where id=ANY(:ids) order by id",
                ids=evidence_ids,
            )
            == evidence_before
        )
        receipt["final_fundamental_ids"] = [row["id"] for row in fundamental_after]
        receipt["fundamental_financial_projection_unchanged"] = True
        receipt["original_immutable_evidence_unchanged"] = True
        if expected == "COMPLETED":
            assert_completed_oracle(engine, env, run_id, pipeline_id)
            assert len(jobs) == 15 and all(r["status"] == "COMPLETED" for r in jobs)
            dependencies = _rows(
                engine,
                "select * from pipeline_dependencies where pipeline_run_id=:id",
                id=pipeline_id,
            )
            assert len(dependencies) == 1 and dependencies[0]["state"] == "COMPLETED"
            continuation = next(
                r for r in jobs if r["id"] == dependencies[0]["continuation_job_id"]
            )
            required = [r for r in jobs if r["required_for_parent_completion"]]
            assert required and all(
                r["completed_at"] <= continuation["started_at"] for r in required
            )
            assert root_final["operational_metadata_json"]["attempt_count"] == 2
            assert root_final["retry_count"] == (1 if fault == "retry" else 0)
            assert root_final["recovery_count"] == (0 if fault == "retry" else 1)
            receipt["dependencies"] = dependencies
        else:
            assert fundamental_after == fundamental_before
            assert len(jobs) == 1 and root_final["operational_metadata_json"]["attempt_count"] == 1
            assert root_final["requested_cancel"] == (fault == "cancel")
            assert not _rows(
                engine, "select id from technical_scores where run_id=:run", run=run_id
            )
            assert not _rows(
                engine, "select id from winner_prediction_snapshots where run_id=:run", run=run_id
            )
            assert root_final["operational_metadata_json"]["last_attempt"]["status"] == expected
            if fault == "permanent":
                assert root_final["retry_count"] == 1
                assert (
                    root_final["operational_metadata_json"]["failure_classification"]["code"]
                    == "DATABASE_INTEGRITY_VIOLATION"
                )
        receipt["stale_terminal_publication_refusal"] = _stale_publication_is_refused(
            engine, root["id"], old_token, run_id
        )
        if fault == "cancel":
            process, cancelled_identity = _verify_process(engine, env, role="worker")
            receipt["verified_post_cancel_restart_process"] = cancelled_identity
            save()
            process.kill()
            process.wait(timeout=15)
            replacement = _wait(
                "authorized restart after cancellation",
                lambda: (
                    rows
                    if (
                        rows := _rows(
                            engine,
                            "select * from background_workers where worker_id=:worker "
                            "and instance_id<>:old and stopping_at is null",
                            worker=root["worker_id"],
                            old=cancelled_identity["instance_id"],
                        )
                    )
                    else None
                ),
                seconds=120,
            )[0]
            receipt["post_cancel_replacement"] = _verify_process(engine, env, role="worker")[1]
            _wait(
                "replacement claim loop runs after cancellation",
                lambda: _rows(
                    engine,
                    "select worker_id from background_workers where worker_id=:worker "
                    "and control_loop_heartbeat_at>:before",
                    worker=root["worker_id"],
                    before=replacement["control_loop_heartbeat_at"],
                ),
                seconds=15,
            )
            assert _job(engine, root["id"])["status"] == "CANCELLED"
            assert _job(engine, root["id"])["operational_metadata_json"]["attempt_count"] == 1
            assert not _rows(
                engine, "select id from technical_scores where run_id=:run", run=run_id
            )
        with Session(engine) as control:
            assert (
                requeue_stalled_jobs(
                    control,
                    authority=RecoveryAuthority.certification(
                        "test.a11.terminal_retry", session_id=env.execution_id
                    ),
                    job_ids=[root["id"]],
                )
                == 0
            )
            assert (
                claim_next_job(
                    control,
                    "a11-terminal-retry",
                    certification_only=True,
                    certification_session_id=env.execution_id,
                    included_job_types=("FULL_PIPELINE",),
                )
                is None
            )
            control.commit()
        receipt["terminal_requeue_and_claim_refused"] = True
        fidelity = [
            json.loads(line)
            for line in (env.artifact_dir / "logs/worker-fidelity.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        entries = [
            r
            for r in fidelity
            if r["event"] == "native_handler_entered" and r.get("pipeline_id") == pipeline_id
        ]
        assert entries and all(
            r["registered_original"] and r["native_worker_dispatch"] for r in entries
        )
        assert (
            len(
                {(r["scope_id"], r["refresh_cycle_id"], r["configuration_anchor"]) for r in entries}
            )
            == 1
        )
        assert len(entries) == (3 if expected == "COMPLETED" else 1)
        assert len({r["execution_token_hash"] for r in entries}) == len(entries)
        receipt["original_handler_entries"] = entries
        receipt["result"] = "PASS"
        save()
    finally:
        gate.close()
        save()
        engine.dispose()
