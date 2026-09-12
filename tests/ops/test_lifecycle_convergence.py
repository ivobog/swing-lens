from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import database_safety
from app.database_safety import (
    DatabaseSafetyContext,
    assert_disposable_database,
    require_database_safety_context,
)
from app.services import lifecycle_control
from app.services.canonical_runtime_launcher import build_canonical_runtime_launch
from app.services.lifecycle_control import (
    append_lifecycle_event,
    runtime_generation,
    shutdown_request_path,
)
from app.services.readiness_service import ReadinessCheck, ReadinessService
from app.services.supervisor_restart import RestartBudget
from app.settings import Settings
from scripts.ops import lifecycle_probe

ROOT = Path(__file__).resolve().parents[2]


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://user:password@127.0.0.1:5432/swinglens",
        "grafana_admin_password": "grafana-secret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_database_safety_context_is_explicit_and_not_inferred(monkeypatch) -> None:
    monkeypatch.delenv("SWINGLENS_DATABASE_SAFETY_CONTEXT", raising=False)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "present")
    with pytest.raises(RuntimeError, match="must be explicitly set"):
        require_database_safety_context()
    assert (
        require_database_safety_context("disposable_test")
        is DatabaseSafetyContext.DISPOSABLE_TEST
    )


def test_disposable_identity_accepts_prefixed_database_and_rejects_real_database(
    monkeypatch,
) -> None:
    def identity(url):
        return str(url.database), "127.0.0.1:5432"

    monkeypatch.setattr(database_safety, "_read_identity", identity)
    disposable = "postgresql+psycopg://u:p@127.0.0.1:5432/swinglens_ci_gate"
    active = "postgresql+psycopg://u:p@127.0.0.1:5432/swinglens"
    result = assert_disposable_database(disposable, active_database_url=active, announce=False)
    assert result.database_name == "swinglens_ci_gate"
    assert "p@" not in result.safe_url
    with pytest.raises(RuntimeError, match="active SwingLens database"):
        assert_disposable_database(active, active_database_url=active, announce=False)


def test_canonical_durable_configuration_rejects_disabled_worker() -> None:
    with pytest.raises(ValidationError, match="canonical supervisor-root runtime"):
        _settings(use_durable_pipeline=True, durable_worker_process_enabled=False)
    with pytest.raises(ValidationError, match="legacy compatibility cannot override"):
        _settings(
            use_durable_pipeline=True,
            durable_worker_process_enabled=False,
            job_worker_enabled=True,
        )


@pytest.mark.parametrize("role", ["web", "worker"])
def test_restart_budget_uses_bounded_backoff_and_stops_at_crash_loop(role: str) -> None:
    budget = RestartBudget(
        role=role,
        budget=3,
        window_seconds=60,
        initial_backoff_seconds=0.5,
        max_backoff_seconds=1.0,
    )
    decisions = [
        budget.record_failure(
            now=float(index),
            exit_code=2,
            startup_stage="uvicorn_ready",
            reason_code="WEB_START_FAILED",
        )
        for index in range(3)
    ]
    assert [row.backoff_seconds for row in decisions] == [0.5, 1.0, 1.0]
    assert [row.crash_loop for row in decisions] == [False, False, True]
    assert budget.snapshot()["state"] == "CRASH_LOOP"
    assert budget.restart_count == 3


def test_canonical_launcher_builds_one_supervisor_root_with_role_safe_children(tmp_path) -> None:
    settings = _settings(process_role="SUPERVISOR")
    launch = build_canonical_runtime_launch(
        settings=settings,
        parent_environment={"JOB_WORKER_ENABLED": "true", "API_TOKEN": "preserved"},
        repo_root=tmp_path,
        git_sha="a" * 40,
        runtime_instance_id="runtime-test",
    )
    command = " ".join(launch.command)
    assert "-m app.worker_supervisor" in command
    assert "app.serve" not in command
    assert launch.environment["PROCESS_ROLE"] == "SUPERVISOR"
    assert launch.environment["JOB_WORKER_ENABLED"] == "false"
    assert launch.environment["SWINGLENS_RUNTIME_INSTANCE_ID"] == "runtime-test"
    assert launch.environment["SWINGLENS_GIT_SHA"] == "a" * 40


def test_runtime_generation_changes_for_config_database_and_alembic(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(lifecycle_control, "_git_sha", lambda _root: "a" * 40)
    heads = ["0072"]
    monkeypatch.setattr(lifecycle_control, "repository_alembic_heads", lambda _root: tuple(heads))
    provenance = {
        "verified": True,
        "service": "postgresql-x64-18",
        "dataDirectory": "C:/pg18/data",
        "listenerExecutable": "C:/pg18/bin/postgres.exe",
    }
    db = {"serverVersionNum": 180003}
    base = runtime_generation(_settings(), repo_root=tmp_path, database=db, provenance=provenance)
    config_changed = runtime_generation(
        _settings(app_port=8010), repo_root=tmp_path, database=db, provenance=provenance
    )
    db_changed = runtime_generation(
        _settings(database_url="postgresql+psycopg://u:p@127.0.0.1:5433/swinglens"),
        repo_root=tmp_path,
        database=db,
        provenance=provenance,
    )
    heads[:] = ["0073"]
    schema_changed = runtime_generation(
        _settings(), repo_root=tmp_path, database=db, provenance=provenance
    )
    fingerprints = {
        base["fingerprint"],
        config_changed["fingerprint"],
        db_changed["fingerprint"],
        schema_changed["fingerprint"],
    }
    assert len(fingerprints) == 4
    serialized = json.dumps(base)
    assert "password" not in serialized
    assert "grafana-secret" not in serialized


def test_tree_identical_merge_sha_changes_runtime_fingerprint(monkeypatch, tmp_path) -> None:
    shas = iter(
        (
            "095a552e21f92523a7d8e67df2109742a0cad0a9",
            "0fb435ee6eb76049ddf6d76d859775fec35768f5",
        )
    )
    monkeypatch.setattr(lifecycle_control, "_git_sha", lambda _root: next(shas))
    monkeypatch.setattr(
        lifecycle_control,
        "repository_alembic_heads",
        lambda _root: ("0072_ceri_artifact_context_lineage",),
    )
    provenance = {
        "verified": True,
        "service": "postgresql-x64-18",
        "dataDirectory": "C:/pg18/data",
        "listenerExecutable": "C:/pg18/bin/postgres.exe",
    }
    database = {"serverVersionNum": 180003}
    old = runtime_generation(
        _settings(), repo_root=tmp_path, database=database, provenance=provenance
    )
    merged = runtime_generation(
        _settings(), repo_root=tmp_path, database=database, provenance=provenance
    )
    assert old["generation"] | {"git_sha": merged["generation"]["git_sha"]} == merged[
        "generation"
    ]
    assert old["fingerprint"] != merged["fingerprint"]


def test_same_git_different_runtime_fingerprint_requires_restart(monkeypatch, tmp_path) -> None:
    created = "2026-09-11T10:00:00+00:00"
    runtime_id = "runtime-active"
    web = {
        "pid": 123,
        "createdAt": created,
        "role": "web",
        "module": "app.serve",
        "repoRoot": str(tmp_path),
        "runtimeInstanceId": runtime_id,
        "port": 8000,
    }
    state_path = tmp_path / "runtime.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 5,
                "gitCommit": "a" * 40,
                "runtimeConfigFingerprint": "old-generation",
                "topologyVersion": "supervisor-root-v1",
                "repoRoot": str(tmp_path),
                "runtimeInstanceId": runtime_id,
                "web": web,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(lifecycle_probe, "RUNTIME_STATE", state_path)
    monkeypatch.setattr(
        lifecycle_probe,
        "inspect_process",
        lambda _pid: {
            "pid": 123,
            "createdAt": created,
            "cwd": str(tmp_path),
            "commandLine": ["python", "-m", "app.serve", runtime_id],
        },
    )
    monkeypatch.setenv("SWINGLENS_RUNTIME_CONFIG_FINGERPRINT", "desired-generation")
    report = lifecycle_probe._runtime_state_report(listener_pid=None)
    assert report["conflict"] is True
    assert report["classification"] == "ACTIVE_GENERATION_MISMATCH"
    assert "RESTART_REQUIRED" in report["error"]


def test_lifecycle_journal_is_append_only_bounded_and_redacted(tmp_path) -> None:
    path = append_lifecycle_event(
        tmp_path,
        event="operation_complete",
        action="start",
        stage="preflight",
        result="failure",
        reason_code="DATABASE_UNAVAILABLE",
        message="postgresql://user:db-secret@127.0.0.1/swinglens token=api-secret",
        arbitrary_business_payload={"ticker": "SECRET"},
    )
    append_lifecycle_event(tmp_path, event="operation_begin", action="status", result="started")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "db-secret" not in lines[0]
    assert "api-secret" not in lines[0]
    assert "arbitrary_business_payload" not in lines[0]
    assert json.loads(lines[0])["reason_code"] == "DATABASE_UNAVAILABLE"


def test_shutdown_request_path_is_instance_scoped_and_path_safe(tmp_path) -> None:
    first = shutdown_request_path(tmp_path, "runtime/../first")
    repeated = shutdown_request_path(tmp_path, "runtime/../first")
    second = shutdown_request_path(tmp_path, "runtime-second")

    assert first == repeated
    assert first != second
    assert first.parent == tmp_path / "data" / "cache" / "shutdown-requests"
    assert first.name.endswith(".json")
    assert "runtime" not in first.name


def test_prometheus_three_of_three_uses_targets_api(monkeypatch) -> None:
    active = [
        {
            "labels": {"job": job},
            "discoveredLabels": {"__address__": address},
            "health": health,
            "lastScrape": "2026-09-11T10:00:00Z",
            "lastError": error,
        }
        for job, address, health, error in (
            ("swinglens-web", "host:8000", "up", ""),
            ("swinglens-worker", "host:9101", "up", ""),
            ("swinglens-supervisor", "host:9102", "down", "connection refused"),
        )
    ]
    monkeypatch.setattr(
        lifecycle_probe,
        "_http_json",
        lambda url: {
            "reachable": True,
            "payload": {"status": "success", "data": {"activeTargets": active}},
        },
    )
    report = lifecycle_probe._prometheus_targets_report()
    assert report["allUp"] is False
    assert [row["job"] for row in report["targets"]] == [
        "swinglens-web",
        "swinglens-worker",
        "swinglens-supervisor",
    ]
    assert report["targets"][2]["lastScrapeError"] == "connection refused"


def test_core_readiness_does_not_evaluate_business_or_observability_state(monkeypatch) -> None:
    service = ReadinessService(engine=object(), settings=_settings())
    ok = ReadinessCheck(True, "ok")
    for name in (
        "_database_check",
        "_database_provenance_check",
        "_migration_check",
        "_storage_check",
        "_core_supervisor_check",
        "_core_web_check",
        "_core_worker_check",
        "_core_topology_check",
        "_core_metrics_listener_check",
    ):
        monkeypatch.setattr(service, name, lambda: ok)

    def forbidden():
        raise AssertionError("application concern reached the core readiness contract")

    for name in ("_jobs_check", "_telemetry_check", "_ib_check", "_sec_provider_check"):
        monkeypatch.setattr(service, name, forbidden)
    report = service.core_report()
    assert report.status == "ok"
    assert "jobs" not in report.checks
    assert "telemetry" not in report.checks


def test_topology_docs_and_monitoring_contract_cannot_regress() -> None:
    adr = (ROOT / "docs/architecture/runtime_process_ownership.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    lifecycle = (ROOT / "docs/operations/lifecycle.md").read_text(encoding="utf-8")
    dashboard = json.loads(
        (ROOT / "monitoring/grafana/dashboards/swinglens-lifecycle.json").read_text(
            encoding="utf-8"
        )
    )
    alerts = (ROOT / "monitoring/prometheus/alerts.yml").read_text(encoding="utf-8")
    for document in (adr, readme, lifecycle):
        assert "app.worker_supervisor" in document
        assert "app.serve" in document
    assert "SupervisorProcessManager" not in "\n".join((adr, readme, lifecycle))
    assert dashboard["uid"] == "swinglens-lifecycle"
    assert "SwingLensSupervisorChildCrashLoop" in alerts
    assert "SwingLensPrometheusTargetDown" in alerts


def test_diagnostic_bundle_manifest_and_secret_scan(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(lifecycle_probe, "ROOT", tmp_path)
    monkeypatch.setattr(lifecycle_probe, "RUNTIME_STATE", tmp_path / "runtime.json")
    monkeypatch.setattr(lifecycle_probe, "_config_report", lambda: {"password": "db-secret"})
    monkeypatch.setattr(lifecycle_probe, "_database_report", lambda: {"reachable": False})
    monkeypatch.setattr(lifecycle_probe, "_provenance_report", lambda: {"verified": False})
    monkeypatch.setattr(lifecycle_probe, "_runtime_generation_report", lambda: {})
    monkeypatch.setattr(lifecycle_probe, "_process_tree_report", lambda: {})
    monkeypatch.setattr(lifecycle_probe, "_listeners_report", lambda: {})
    monkeypatch.setattr(lifecycle_probe, "_registrations_report", lambda: {})
    monkeypatch.setattr(lifecycle_probe, "_jobs_report", lambda: {"active": []})
    monkeypatch.setattr(lifecycle_probe, "_prometheus_targets_report", lambda: {"allUp": False})
    monkeypatch.setattr(lifecycle_probe, "_observability_report", lambda _action: {"ok": False})
    monkeypatch.setattr(lifecycle_probe, "_http_json", lambda _url: {"reachable": False})
    monkeypatch.setattr(lifecycle_probe, "_git_commit", lambda: "a" * 40)
    monkeypatch.setattr(
        lifecycle_probe.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "clean\n", ""),
    )
    lifecycle_log = tmp_path / "logs/lifecycle/lifecycle.jsonl"
    lifecycle_log.parent.mkdir(parents=True)
    lifecycle_log.write_text("token=api-secret\n", encoding="utf-8")

    report = lifecycle_probe._diagnose("unit-operation")
    bundle = Path(report["bundle"])
    assert (bundle / "SUMMARY.md").is_file()
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert "SUMMARY.md" in manifest["files"]
    assert "shutdown-request.json" in manifest["files"]
    combined = "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in bundle.iterdir()
    )
    assert "db-secret" not in combined
    assert "api-secret" not in combined
