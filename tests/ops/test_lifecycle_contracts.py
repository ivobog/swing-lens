from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MODULE = (ROOT / "scripts" / "ops" / "SwingLensLifecycle.psm1").read_text(encoding="utf-8")
PROBE = (ROOT / "scripts" / "ops" / "lifecycle_probe.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "swinglens.ps1").read_text(encoding="utf-8")


def _docker_compose_commands() -> list[str]:
    return [line.strip() for line in PROBE.splitlines() if '"compose"' in line]


def test_root_lifecycle_exposes_exact_canonical_actions() -> None:
    assert "ValidateSet('start', 'stop', 'restart', 'status')" in LAUNCHER
    assert "SwingLensLifecycle.psm1" in LAUNCHER


def test_normal_lifecycle_never_operates_docker_postgresql() -> None:
    commands = _docker_compose_commands()
    assert commands
    assert "docker-compose.postgres-test.yml" not in PROBE
    assert not any(
        re.search(r"\bpostgres(?:-test)?\b", command, re.IGNORECASE) for command in commands
    )
    assert "docker-compose.yml" not in PROBE


def test_normal_lifecycle_uses_only_observability_compose_path() -> None:
    assert 'ROOT / "docker-compose.observability.yml"' in PROBE
    assert "observability-start" in MODULE
    assert "observability-stop" in MODULE


def test_lifecycle_has_no_broad_or_destructive_process_and_data_operations() -> None:
    forbidden = (
        "taskkill /IM",
        "Stop-Process -Name",
        "Stop-Process -Name python",
        "Stop-Process -Name postgres",
        "initdb",
        "DROP DATABASE",
        "down -v",
        "docker volume rm",
        "Remove-Item $HOME",
    )
    lowered = MODULE.lower()
    for fragment in forbidden:
        assert fragment.lower() not in lowered


def test_database_url_is_loaded_but_never_rewritten_in_dotenv() -> None:
    assert "Import-SwingLensEnvironment" not in MODULE
    assert "Get-LifecycleConfig" in MODULE
    assert "Invoke-LifecycleProbe -Command 'provenance'" in MODULE


def test_docker_outage_is_observability_degradation_not_core_teardown() -> None:
    assert "startup failed; core remains running" in MODULE
    start_body = MODULE.split("function Start-SwingLensStack", 1)[1]
    assert start_body.index("Start-SwingLensWeb") < start_body.index("Start-SwingLensObservability")


def test_start_reuses_verified_healthy_web_for_idempotency() -> None:
    assert "reusing strongly verified PID" in MODULE
    assert "Get-ValidatedRuntime" in MODULE
    assert "use restart after reviewing status" in MODULE


def test_disposable_postgres_compose_is_isolated_from_authoritative_endpoint() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.postgres-test.yml").read_text())
    assert compose["name"] == "swinglens-postgres-test"
    service = compose["services"]["postgres-test"]
    assert service["container_name"] == "swinglens-postgres-test"
    assert service["ports"] == ["127.0.0.1:5433:5432"]
    assert service["environment"]["POSTGRES_DB"] == "swinglens_disposable"
    assert not (ROOT / "docker-compose.yml").exists()


def test_restart_composes_shared_stop_and_start_implementations() -> None:
    restart = MODULE.split("'restart' {", 1)[1]
    assert restart.index("Stop-SwingLensStack") < restart.index("Start-SwingLensStack")
    assert "Invoke-WithLifecycleLock" in MODULE
