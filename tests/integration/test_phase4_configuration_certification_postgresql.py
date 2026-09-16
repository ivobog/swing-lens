"""T13E deployed authority proofs, using disposable PostgreSQL and fresh processes."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
REPO = Path(__file__).resolve().parents[2]


def _process(mode, cwd, database_url, **overrides):
    env = dict(os.environ)
    env.update(
        DATABASE_URL=database_url,
        PYTHONPATH=str(REPO),
        UPLOAD_DIR=str(cwd / "uploads"),
        CACHE_DIR=str(cwd / "cache"),
        EXPORT_DIR=str(cwd / "exports"),
        DB_MONITOR_ENABLED="false",
        OBSERVABILITY_METRICS_ENABLED="false",
        PROCESS_ROLE="CLI_OR_MAINTENANCE",
        RUNTIME_MODE="NORMAL",
        USE_DURABLE_PIPELINE="true",
        JOB_WORKER_ENABLED="false",
        TECHNICAL_V5_ENABLED="false",
        TECHNICAL_V5_SHADOW_COMPARE_ENABLED="false",
        WINNER_PROBABILITY_CAPTURE_IN_PIPELINE="false",
        SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED="false",
        CERI_RUN_CAPTURE_ENABLED="false",
        EODHD_API_KEY="T13E_SECRET_MUST_NOT_APPEAR",
        IB_FLEX_TOKEN="T13E_SECRET_MUST_NOT_APPEAR",
    )
    env.update(overrides)
    completed = subprocess.run(
        [sys.executable, str(REPO / "scripts/qa/t13e_deployed_probe.py"), mode],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert "T13E_SECRET_MUST_NOT_APPEAR" not in completed.stdout + completed.stderr
    (REPO / ".qa_work" / f"t13e-{mode}-last.stderr.log").write_text(
        completed.stderr,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout[-20000:]
    result = json.loads(
        next(
            line.split("=", 1)[1]
            for line in completed.stdout.splitlines()
            if line.startswith("T13E_RESULT=")
        )
    )
    (REPO / ".qa_work" / f"t13e-{mode}-last.json").write_text(
        json.dumps(result, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return result


@pytest.mark.parametrize("full_graph", [False, True], ids=["core-context", "decision-graph"])
def test_public_upload_enqueue_process_restart_native_worker_and_frozen_history(
    disposable_postgres_database,
    tmp_path,
    full_graph,
):
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    command.check(config)
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(REPO / "app/static", tmp_path / "app/static")
    shutil.copytree(REPO / "app/templates", tmp_path / "app/templates")
    overrides = {}
    if full_graph:
        import yaml

        for name in ("setup_lifecycle", "winner_probability"):
            path = tmp_path / "config" / f"{name}.yaml"
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            raw["engine"]["enabled"] = True
            if name == "setup_lifecycle":
                raw["alerts"]["rules"]["T13E_EXPIRY"] = {
                    "enabled": True,
                    "severity": "RISK",
                    "source": "lifecycle_transition",
                    "to_state": "EXPIRED",
                    "cooldown_sessions": 1,
                    "minimum_confidence": 0,
                }
            path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        overrides = dict(
            WINNER_PROBABILITY_ENABLED="true",
            WINNER_PROBABILITY_CAPTURE_IN_PIPELINE="true",
            SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED="true",
            SETUP_LIFECYCLE_ENABLED="true",
            CERI_ENABLED="true",
            CERI_RUN_CAPTURE_ENABLED="true",
            T13E_READY_BOUNDARY="true",
        )
    queued = _process("enqueue", tmp_path, disposable_postgres_database, **overrides)
    # The web process has exited. Replace every source file with invalid C2:
    # persisted queue execution must not depend on any surviving parsed cache.
    for path in (tmp_path / "config").rglob("*.yaml"):
        path.write_text("invalid-current-C2: true\n", encoding="utf-8")
    if full_graph:
        prerequisites = _process("prerequisites", tmp_path, disposable_postgres_database)
        assert all(
            j["status"] == "COMPLETED" for j in prerequisites["jobs"] if j["id"] != queued["job_id"]
        ), prerequisites["jobs"]
        claimed = _process("claim", tmp_path, disposable_postgres_database)
        abandoned = next(j for j in claimed["jobs"] if j["id"] == queued["job_id"])
        assert abandoned["status"] == "RUNNING"
        assert abandoned["payload"]["effective_configuration_anchor"] == queued["anchor"]
    executed = _process(
        "execute",
        tmp_path,
        disposable_postgres_database,
        TECHNICAL_V5_ENABLED="true",
        JOB_POLL_INTERVAL_SECONDS="7",
        T13E_READY_BOUNDARY="true" if full_graph else "false",
    )
    assert executed["pid"] != queued["pid"]
    job = next(j for j in executed["jobs"] if j["id"] == queued["job_id"])
    assert job["status"] == "COMPLETED", job
    assert job["payload"]["effective_configuration_anchor"] == queued["anchor"]
    if full_graph:
        assert job["token"] != abandoned["token"]
    assert {e["kind"] for e in executed["evidence"]} >= {
        "FUNDAMENTAL",
        "TECHNICAL",
        "COMBINED",
        "RANKING",
        "REGIME",
        "SECTOR",
    }
    if full_graph:
        downstream = _process(
            "queue-downstream",
            tmp_path,
            disposable_postgres_database,
            T13E_PARENT_JOB_ID=str(queued["job_id"]),
        )
        assert all(
            j["payload"]["effective_configuration_anchor"] == queued["anchor"]
            for j in downstream["jobs"]
        )
        executed = _process("prerequisites", tmp_path, disposable_postgres_database)
        assert all(j["status"] == "COMPLETED" for j in executed["jobs"]), executed["jobs"]
        assert executed["alerts"]
        performance = _process(
            "benchmark",
            tmp_path,
            disposable_postgres_database,
            T13E_PARENT_JOB_ID=str(queued["job_id"]),
        )
        assert performance["cold_bundle_selects"] == 2
        assert performance["batch_db_selects"] == performance["batch_current_file_reads"] == 0
        root_performance = next(j for j in executed["jobs"] if j["id"] == queued["job_id"])[
            "result"
        ]["performance"]
        for stage in ("RANKING_PROFILES", "MARKET_REGIME_SNAPSHOT"):
            assert root_performance["step_durations_ms"][stage] > 0
    # A second fresh application process retrieves retained proof with unusable
    # current sources, independently of the execution process's ContextVars.
    historical = _process("history", tmp_path, disposable_postgres_database)
    assert historical["pid"] not in {queued["pid"], executed["pid"]}
    assert historical["evidence"] == executed["evidence"]
    assert historical["records"] == executed["records"]
    if full_graph:
        assert {e["kind"] for e in executed["evidence"]} >= {"CERI", "SETUP"}
        assert executed["lifecycle"]
        assert executed["winner"]
        for family in ("lifecycle", "transitions", "alerts", "winner"):
            assert historical[family] == executed[family]
        # Restore valid current sources and drift every financial family. A new
        # public route creates new C2 work; old proof remains byte-for-byte C1.
        shutil.copytree(REPO / "config", tmp_path / "config", dirs_exist_ok=True)
        _write_c2_sources(tmp_path)
        c2 = _process("enqueue", tmp_path, disposable_postgres_database, **overrides)
        assert c2["anchor"]["fingerprint"] != queued["anchor"]["fingerprint"]
        for namespace in (
            "core.fundamental",
            "core.technical",
            "core.combined",
            "core.ranking:momentum_swing",
            "contextual.regime",
            "contextual.sector",
            "contextual.ceri",
            "contextual.ibmi.liquidity",
            "decision.setup",
            "decision.lifecycle",
            "decision.alerts.setup",
            "decision.winner.prediction",
            "decision.winner.outcome",
            "decision.winner.cohort",
            "decision.winner.generation",
        ):
            assert c2["identities"][namespace] != queued["identities"][namespace], namespace
        _process("prerequisites", tmp_path, disposable_postgres_database)
        new = _process(
            "execute", tmp_path, disposable_postgres_database, T13E_READY_BOUNDARY="true"
        )
        assert next(j for j in new["jobs"] if j["id"] == c2["job_id"])["status"] == "COMPLETED"
        assert new["evidence"][: len(historical["evidence"])] == historical["evidence"]
        for family in ("lifecycle", "transitions", "alerts", "winner"):
            assert new[family][: len(historical[family])] == historical[family]
        current = _process("queue-current-rules", tmp_path, disposable_postgres_database)
        current_job = current["jobs"][-1]
        current_anchor = next(
            anchor
            for anchor in current["anchors"]
            if anchor["integrity"]
            == current_job["payload"]["effective_configuration_anchor"]["anchor_id"]
        )
        assert (
            current_anchor["configurations"]["decision.lifecycle"]["identity"]
            == (c2["identities"]["decision.lifecycle"])
        )
        retrospective = _process("prerequisites", tmp_path, disposable_postgres_database)
        replay = retrospective["jobs"][-1]
        assert replay["status"] == "COMPLETED", replay["error"]
        assert replay["result"]["mode"] == "DRY_RUN_REPLAY"
        assert replay["result"]["snapshot_count"] > 0
        assert "ORIGINAL_CONTEXT" not in json.dumps(replay["result"])
        for family in ("evidence", "lifecycle", "transitions", "alerts", "winner"):
            assert retrospective[family] == new[family]


def _write_c2_sources(root):
    import yaml

    changes = {
        "fundamentals_v2": [(("missing_data", "critical_field_penalty"), 0.36)],
        "pine_defaults": [(("trend", "emaFastLen"), 12)],
        "scoring_weights": [
            (("combined_score", "fundamental_score"), 0.54),
            (("combined_score", "dual_score"), 0.46),
        ],
        "ranking_profiles": [
            (("profiles", "momentum_swing", "weights", "fundamental"), 0.44),
            (("profiles", "momentum_swing", "weights", "technical"), 0.56),
        ],
        "market_regime_command_center": [(("freshness", "max_stale_trading_days"), 4)],
        "sector_rotation": [(("defaults", "min_tickers_for_normal_confidence"), 6)],
        "ceri": [(("confidence", "high_min"), 8.1)],
        "ib_market_intelligence": [(("liquidity", "lookback_sessions"), 21)],
        "setup_lifecycle": [
            (("engine", "enabled"), True),
            (("confidence", "high_min"), 86),
            (("alerts", "default_cooldown_sessions"), 6),
        ],
        "winner_probability": [
            (("engine", "enabled"), True),
            (("episode", "cooldown_sessions"), 6),
            (("horizon", "sessions"), [1, 3, 5, 10, 20, 30]),
            (("cohort", "prior_strength"), 21),
        ],
    }
    for name, updates in changes.items():
        path = root / "config" / f"{name}.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for keys, value in updates:
            target = raw
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
        if name == "setup_lifecycle":
            raw["families"]["generic"]["observation_gap_sessions"] += 1
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_public_resume_retains_c1_and_duplicate_completed_work_fails_closed(
    disposable_postgres_database,
    tmp_path,
):
    import yaml

    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    for source in ("config", "app/static", "app/templates"):
        shutil.copytree(REPO / source, tmp_path / source)
    for name in ("setup_lifecycle", "winner_probability"):
        path = tmp_path / "config" / f"{name}.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["engine"]["enabled"] = True
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    flags = dict(
        CERI_ENABLED="true",
        CERI_RUN_CAPTURE_ENABLED="true",
        CERI_PROVIDER_INGEST_ENABLED="true",
        SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED="true",
        WINNER_PROBABILITY_CAPTURE_IN_PIPELINE="true",
        WINNER_PROBABILITY_ENABLED="true",
        T13E_READY_BOUNDARY="true",
        T13E_SCHEDULER_BOUNDARY="true",
    )
    queued = _process("enqueue", tmp_path, disposable_postgres_database, **flags)
    _process("prerequisites", tmp_path, disposable_postgres_database)
    interrupted = _process("interrupt", tmp_path, disposable_postgres_database, **flags)
    failed = next(j for j in interrupted["jobs"] if j["id"] == queued["job_id"])
    assert failed["status"] == "QUEUED", failed
    assert "transient acquisition scheduling boundary" in failed["error"]
    assert {e["kind"] for e in interrupted["evidence"]} >= {
        "FUNDAMENTAL",
        "TECHNICAL",
        "COMBINED",
        "RANKING",
        "REGIME",
        "SECTOR",
    }
    for path in (tmp_path / "config").rglob("*.yaml"):
        path.write_text("invalid-current-C2: true\n", encoding="utf-8")
    resumed = _process(
        "resume", tmp_path, disposable_postgres_database, T13E_PARENT_JOB_ID=str(queued["job_id"])
    )
    continuation = resumed["jobs"][-1]
    assert continuation["payload"]["resume_from_step"] == "CERI_PROVIDER_INGEST"
    assert continuation["payload"]["effective_configuration_anchor"] == queued["anchor"]
    completed = _process(
        "execute",
        tmp_path,
        disposable_postgres_database,
        T13E_READY_BOUNDARY="true",
        T13E_SCHEDULER_BOUNDARY="true",
    )
    assert completed["jobs"][-1]["status"] == "COMPLETED", completed["jobs"][-1]["error"]
    assert completed["lifecycle"] and completed["winner"]
    _process(
        "retry-ready",
        tmp_path,
        disposable_postgres_database,
        T13E_PARENT_JOB_ID=str(queued["job_id"]),
    )
    retry = _process(
        "execute",
        tmp_path,
        disposable_postgres_database,
        T13E_READY_BOUNDARY="true",
        T13E_SCHEDULER_BOUNDARY="true",
    )
    retried = next(j for j in retry["jobs"] if j["id"] == queued["job_id"])
    assert retried["status"] == "FAILED", retried
    assert "DECISION_MANIFEST_MISMATCH" in retried["error"]
    assert retried["payload"]["effective_configuration_anchor"] == queued["anchor"]
    assert retried["attempts"]["attempt_count"] >= 2
    assert retry["evidence"][: len(interrupted["evidence"])] == interrupted["evidence"]


def test_real_worker_retry_after_native_fundamental_commit_uses_c1(
    disposable_postgres_database, tmp_path
):
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    for source in ("config", "app/static", "app/templates"):
        shutil.copytree(REPO / source, tmp_path / source)
    queued = _process("enqueue", tmp_path, disposable_postgres_database)
    interrupted = _process("interrupt-early", tmp_path, disposable_postgres_database)
    first = next(j for j in interrupted["jobs"] if j["id"] == queued["job_id"])
    assert first["status"] == "QUEUED"
    assert "transient acquisition readiness boundary" in first["error"]
    assert [e["kind"] for e in interrupted["evidence"]] == ["FUNDAMENTAL"]
    for path in (tmp_path / "config").rglob("*.yaml"):
        path.write_text("invalid-current-C2: true\n", encoding="utf-8")
    _process(
        "retry-ready",
        tmp_path,
        disposable_postgres_database,
        T13E_PARENT_JOB_ID=str(queued["job_id"]),
    )
    completed = _process(
        "execute",
        tmp_path,
        disposable_postgres_database,
        TECHNICAL_V5_ENABLED="true",
        JOB_POLL_INTERVAL_SECONDS="7",
    )
    job = next(j for j in completed["jobs"] if j["id"] == queued["job_id"])
    assert job["status"] == "COMPLETED", job
    assert job["attempts"]["attempt_count"] == 2
    assert job["payload"]["effective_configuration_anchor"] == queued["anchor"]
    assert completed["evidence"][0] == interrupted["evidence"][0]


def test_populated_0080_downgrade_reupgrade_preserves_business_history_and_fails_old_jobs_closed(
    disposable_postgres_database,
):
    from copy import deepcopy

    from sqlalchemy import create_engine, inspect, select, text
    from sqlalchemy.orm import Session

    from app.models.tables import (
        BackgroundJob,
        CoreCalculationEvidence,
        PipelineRun,
        RawCompanyRow,
        UploadRun,
    )
    from app.services.background_job_service import enqueue_job
    from app.services.background_worker import default_job_handlers, execute_job
    from app.services.configuration_delivery import binding_reference
    from app.services.fundamental_score_service import recalculate_run_fundamentals
    from app.services.market_calculation_context_service import create_pipeline_market_context

    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "0079_setup_lifecycle_alert_ev")
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        db.add(UploadRun(id=1, filename="pre-0080.csv", status="COMPLETED"))
        db.flush()
        db.add(RawCompanyRow(run_id=1, row_number=1, ticker="ACME", raw_json={"Symbol": "ACME"}))
        db.add(PipelineRun(id=1, upload_run_id=1, status="PENDING"))
        db.commit()
    command.upgrade(config, "head")
    command.check(config)
    with Session(engine) as db:
        pipeline = db.get(PipelineRun, 1)
        cutoff = create_pipeline_market_context(db, pipeline)
        recalculate_run_fundamentals(db, 1, market_cutoff=cutoff, pipeline_run_id=1)
        job = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 1}, related_run_id=1)
        reference = binding_reference(db, job_id=job.id)
        assert reference
        db.commit()
        retained = deepcopy(db.scalar(select(CoreCalculationEvidence)).payload_json)
        job_id = job.id
        for table in (
            "effective_configuration_records",
            "execution_configuration_anchors",
            "execution_configuration_bindings",
        ):
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) > 0
            for verb in ("DELETE", "UPDATE"):
                # Even no-op writes are forbidden; supported storage cleanup
                # cannot delete a referenced authority row.
                statement = (
                    f"DELETE FROM {table}"
                    if verb == "DELETE"
                    else {
                        ("effective_configuration_records"): (
                            "UPDATE effective_configuration_records SET namespace=namespace"
                        ),
                        ("execution_configuration_anchors"): (
                            "UPDATE execution_configuration_anchors SET fingerprint=fingerprint"
                        ),
                        ("execution_configuration_bindings"): (
                            "UPDATE execution_configuration_bindings SET anchor_id=anchor_id"
                        ),
                    }[table]
                )
                with pytest.raises(Exception, match="immutable"):
                    with db.begin_nested():
                        db.execute(text(statement))
    command.downgrade(config, "0079_setup_lifecycle_alert_ev")
    assert "effective_configuration_records" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    command.check(config)
    with Session(engine) as db:
        assert db.scalar(select(CoreCalculationEvidence)).payload_json == retained
        with pytest.raises(ValueError, match="MISSING_CONFIGURATION_ANCHOR_BINDING"):
            execute_job(db, db.get(BackgroundJob, job_id), default_job_handlers())
        assert db.scalar(select(CoreCalculationEvidence)).payload_json == retained
    engine.dispose()


def test_all_registered_business_handlers_reject_missing_binding_before_financial_writes(
    disposable_postgres_database,
):
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from app.models.tables import BackgroundJob, CoreCalculationEvidence
    from app.services.background_worker import default_job_handlers, execute_job
    from app.services.configuration_delivery import durable_business_job

    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    handlers = default_job_handlers()
    assert len(handlers) == 34
    excluded = {name for name in handlers if not durable_business_job(name)}
    assert excluded == {
        "IB_FETCH",
        "IB_FLEX_IMPORT",
        "IB_SCANNER_RUN",
        "MARKET_DATA_PREWARM",
        "SEC_READINESS_REPAIR",
        "WORKER_RECOVERY_PROBE",
    }
    with Session(engine) as db:
        for name in sorted(handlers.keys() - excluded):
            job = BackgroundJob(job_type=name, status="QUEUED", payload_json={"run_id": 1})
            db.add(job)
            db.flush()
            with pytest.raises(ValueError, match="MISSING_CONFIGURATION_ANCHOR_BINDING"):
                execute_job(db, job, handlers)
            assert db.scalar(select(func.count()).select_from(CoreCalculationEvidence)) == 0
        db.rollback()
    engine.dispose()


def test_real_dsn_and_provider_sentinels_do_not_enter_public_worker_authority(
    disposable_postgres_database,
    tmp_path,
):
    from uuid import uuid4

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    for source in ("config", "app/static", "app/templates"):
        shutil.copytree(REPO / source, tmp_path / source)
    engine = create_engine(disposable_postgres_database)
    role = "t13e_secret_" + uuid4().hex[:16]
    with engine.begin() as connection:
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'T13E_SECRET_MUST_NOT_APPEAR'"))
        connection.execute(text(f"GRANT USAGE, CREATE ON SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {role}"))
        connection.execute(text(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {role}"))
    try:
        app_url = (
            make_url(disposable_postgres_database)
            .set(username=role, password="T13E_SECRET_MUST_NOT_APPEAR")
            .render_as_string(hide_password=False)
        )
        queued = _process("enqueue", tmp_path, app_url)
        for path in (tmp_path / "config").rglob("*.yaml"):
            path.write_text("invalid-current-C2: true\n", encoding="utf-8")
        executed = _process("execute", tmp_path, app_url)
        assert executed["jobs"][0]["status"] == "COMPLETED"
        assert executed["jobs"][0]["payload"]["effective_configuration_anchor"] == queued["anchor"]
        assert "T13E_SECRET_MUST_NOT_APPEAR" not in json.dumps(executed) + repr(executed)
    finally:
        # This GUID role owns no schema objects: DROP OWNED removes only its
        # task-database grants before the fixture drops the database itself.
        with engine.begin() as connection:
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE {role}"))
        engine.dispose()
