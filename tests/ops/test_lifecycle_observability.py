from __future__ import annotations

import subprocess

import pytest

from app.settings import Settings
from scripts.ops import lifecycle_probe


def _settings() -> Settings:
    return Settings(_env_file=None, swinglens_observability_timeout_seconds=5)


def test_docker_absence_and_timeout_are_bounded_failures(monkeypatch) -> None:
    def missing(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(lifecycle_probe.subprocess, "run", missing)
    assert lifecycle_probe._docker_command(["info"], 5) == {
        "ok": False,
        "error": "Docker CLI is unavailable",
    }

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("docker", 5)

    monkeypatch.setattr(lifecycle_probe.subprocess, "run", timeout)
    assert "exceeded 5 seconds" in lifecycle_probe._docker_command(["info"], 5)["error"]


@pytest.mark.parametrize("failed_service", ["grafana", "prometheus"])
def test_observability_stop_attempts_both_components_after_one_fails(
    monkeypatch, failed_service
) -> None:
    calls: list[list[str]] = []

    def command(arguments, _timeout):
        calls.append(arguments)
        service = arguments[-1]
        return {
            "ok": service != failed_service,
            "error": "injected" if service == failed_service else "",
        }

    monkeypatch.setattr(lifecycle_probe, "_settings", _settings)
    monkeypatch.setattr(lifecycle_probe, "_docker_command", command)

    report = lifecycle_probe._observability_report("stop")

    assert report["ok"] is False
    assert [call[-1] for call in calls] == ["{{.ServerVersion}}", "grafana", "prometheus"]


def test_observability_start_failure_is_a_degraded_result(monkeypatch) -> None:
    def command(arguments, _timeout):
        if arguments[0] == "info":
            return {"ok": True}
        return {"ok": False, "error": "injected compose failure"}

    monkeypatch.setattr(lifecycle_probe, "_settings", _settings)
    monkeypatch.setattr(lifecycle_probe, "_docker_command", command)

    assert lifecycle_probe._observability_report("start") == {
        "ok": False,
        "error": "injected compose failure",
    }
