from __future__ import annotations

import argparse
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import worker_supervisor
from app.services.process_roles import build_process_environment, require_process_role
from app.settings import ProcessRole, RuntimeMode, Settings


def _certification(role: ProcessRole, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "runtime_mode": RuntimeMode.CERTIFICATION,
        "process_role": role,
        "use_durable_pipeline": True,
        "durable_worker_process_enabled": True,
        "embedded_job_worker_enabled": False,
        "winner_probability_auto_maturation_enabled": False,
        "winner_probability_auto_cohort_refresh_enabled": False,
        "market_data_prewarm_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize("role", list(ProcessRole))
def test_certification_roles_have_one_authoritative_identity(role: ProcessRole) -> None:
    settings = _certification(role)
    assert settings.process_role is role
    assert settings.durable_worker_process_enabled is True
    assert settings.embedded_job_worker_enabled is False


def test_certification_rejects_embedded_or_missing_standalone_worker() -> None:
    with pytest.raises(ValidationError, match="DURABLE_WORKER_PROCESS_ENABLED"):
        _certification(ProcessRole.WEB, durable_worker_process_enabled=False)
    with pytest.raises(ValidationError, match="EMBEDDED_JOB_WORKER_ENABLED"):
        _certification(ProcessRole.WEB, embedded_job_worker_enabled=True)


def test_legacy_worker_switch_maps_only_to_standalone_capability() -> None:
    settings = Settings(_env_file=None, job_worker_enabled=True)
    assert settings.durable_worker_process_enabled is True
    assert settings.embedded_job_worker_enabled is False


def test_per_child_environments_are_isolated_normalized_and_order_independent() -> None:
    parent = {
        "PROCESS_ROLE": "STALE",
        "JOB_WORKER_ENABLED": "true",
        "RUNTIME_MODE": "normal",
        "WINNER_PROBABILITY_AUTO_MATURATION_ENABLED": "true",
    }
    settings = _certification(ProcessRole.SUPERVISOR)

    worker_first = build_process_environment(
        parent, role=ProcessRole.DURABLE_WORKER, settings=settings
    )
    web_second = build_process_environment(parent, role=ProcessRole.WEB, settings=settings)
    web_first = build_process_environment(parent, role=ProcessRole.WEB, settings=settings)
    worker_second = build_process_environment(
        parent, role=ProcessRole.DURABLE_WORKER, settings=settings
    )

    assert parent["PROCESS_ROLE"] == "STALE"
    assert worker_first == worker_second
    assert web_first == web_second
    assert worker_first is not web_second
    assert worker_first["PROCESS_ROLE"] == "DURABLE_WORKER"
    assert web_first["PROCESS_ROLE"] == "WEB"
    for environment in (worker_first, web_first):
        assert environment["RUNTIME_MODE"] == "CERTIFICATION"
        assert environment["DURABLE_WORKER_PROCESS_ENABLED"] == "true"
        assert environment["EMBEDDED_JOB_WORKER_ENABLED"] == "false"
        assert environment["JOB_WORKER_ENABLED"] == "false"
        assert environment["WINNER_PROBABILITY_AUTO_MATURATION_ENABLED"] == "false"


def test_certification_entry_points_reject_wrong_role() -> None:
    with pytest.raises(RuntimeError, match="PROCESS_ROLE=DURABLE_WORKER"):
        require_process_role(_certification(ProcessRole.WEB), ProcessRole.DURABLE_WORKER)


def test_supervisor_child_commands_and_roles(monkeypatch) -> None:
    settings = _certification(ProcessRole.SUPERVISOR)
    launches: list[tuple[list[str], dict[str, str]]] = []

    def popen(command, **kwargs):
        launches.append((list(command), dict(kwargs["env"])))
        return SimpleNamespace(pid=991, poll=lambda: None)

    monkeypatch.setattr(worker_supervisor, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_supervisor.subprocess, "Popen", popen)
    monkeypatch.setattr(worker_supervisor, "process_started_at", lambda _pid: datetime.now(UTC))

    worker_supervisor._start_worker("worker-a", "interactive")
    worker_supervisor._start_web(
        argparse.Namespace(
            host="127.0.0.1",
            port=8000,
            runtime_instance_id="runtime-a",
            repo_root="C:/repo",
        )
    )

    assert "app.worker" in launches[0][0]
    assert launches[0][1]["PROCESS_ROLE"] == "DURABLE_WORKER"
    assert "app.serve" in launches[1][0]
    assert launches[1][1]["PROCESS_ROLE"] == "WEB"
    assert launches[0][1] is not launches[1][1]


def test_delayed_registration_does_not_spawn_a_duplicate(monkeypatch) -> None:
    settings = Settings(_env_file=None)
    child = worker_supervisor.LaunchedWorker(
        SimpleNamespace(pid=123, poll=lambda: None), worker_supervisor.monotonic()
    )
    monkeypatch.setattr(worker_supervisor, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_supervisor, "_registered_worker", lambda _worker_id: None)
    monkeypatch.setattr(
        worker_supervisor,
        "_start_worker",
        lambda *_args: pytest.fail("a registering live child must not be duplicated"),
    )

    assert (
        worker_supervisor._supervise_once(worker_id="worker-a", queues="interactive", child=child)
        is child
    )


def test_web_crash_is_replaced_once(monkeypatch) -> None:
    replacement = SimpleNamespace(pid=456, poll=lambda: None)
    monkeypatch.setattr(worker_supervisor, "_start_web", lambda _args: replacement)
    args = argparse.Namespace(runtime_instance_id="runtime-a")
    exited = worker_supervisor.LaunchedWeb(
        SimpleNamespace(pid=123, poll=lambda: 7), worker_supervisor.monotonic()
    )

    result = worker_supervisor._supervise_web_once(args=args, child=exited)

    assert result.process is replacement
