"""Operational policies composed with current-main ownership/configuration rules."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import worker_supervisor
from app.services.background_worker import _post_job_recycle_required
from app.services.contextual_consumer_eligibility import (
    FUNDAMENTAL_TO_COMBINED,
    record_fundamental_admission,
)
from app.services.decision_effective_configuration import (
    freeze_decision_configuration,
    resolve_readiness_policy_configuration,
    validate_delivered_readiness_policy,
)
from app.services.process_memory import ProcessMemorySnapshot
from app.settings import FundamentalAdmissionMode, Settings


def test_required_settings_default_active_and_recycle_two_gib(monkeypatch):
    monkeypatch.delenv("FUNDAMENTAL_ADMISSION_MODE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.fundamental_admission_mode is FundamentalAdmissionMode.ACTIVE
    assert settings.worker_memory_recycle_mb == 2048
    with pytest.raises(ValidationError, match="SHADOW requires"):
        Settings(_env_file=None, fundamental_admission_mode="SHADOW", runtime_mode="NORMAL")
    assert Settings(
        _env_file=None, fundamental_admission_mode="SHADOW", runtime_mode="CERTIFICATION",
    ).fundamental_admission_mode is FundamentalAdmissionMode.SHADOW


def test_admission_configuration_cache_binds_mode_and_retains_exact_main_legacy(monkeypatch):
    from dataclasses import asdict

    from app.services.effective_configuration import (
        ConfigurationSource,
    )
    from app.services.effective_configuration import (
        ConfigurationSourceKind as SourceKind,
    )

    policy = FUNDAMENTAL_TO_COMBINED
    snapshots = {}
    for mode in FundamentalAdmissionMode:
        monkeypatch.setattr(
            "app.settings.get_settings",
            lambda mode=mode: SimpleNamespace(fundamental_admission_mode=mode),
        )
        snapshots[mode] = resolve_readiness_policy_configuration(policy)
    assert len({s.snapshot.semantic_hash for s in snapshots.values()}) == 3
    legacy = freeze_decision_configuration(
        "decision.readiness.fundamental.combined",
        {"policy": asdict(policy),
         "algorithm": "immutable-policy-version-strict-ready-blocked-degraded-unknown-v1"},
        ("policy", "algorithm"),
        source=ConfigurationSource(SourceKind.CODE_DEFAULT, "phase3-named-consumer-policy"),
    )
    assert snapshots[FundamentalAdmissionMode.LEGACY].snapshot == legacy.snapshot
    monkeypatch.setattr("app.services.configuration_delivery.current_delivery", lambda: object())
    monkeypatch.setattr(
        "app.services.configuration_delivery.delivered_configuration", lambda _: snapshots[
            FundamentalAdmissionMode.LEGACY
        ],
    )
    with pytest.raises(ValueError, match="READINESS_POLICY_CONFIGURATION_ANCHOR_MISMATCH"):
        validate_delivered_readiness_policy(policy)


def test_admission_mode_drift_rejected_before_any_session_access(monkeypatch):
    monkeypatch.setattr(
        "app.settings.get_settings", lambda: SimpleNamespace(
            fundamental_admission_mode=FundamentalAdmissionMode.ACTIVE,
        ),
    )
    with pytest.raises(ValueError, match="FUNDAMENTAL_ADMISSION_MODE_DRIFT"):
        record_fundamental_admission(
            object(), row=object(), policy=FUNDAMENTAL_TO_COMBINED,
            permission={}, pipeline_run_id=1,
        )


def test_opportunity_actionability_version_is_in_frozen_ceri_alert_configuration():
    from app.services.ceri.actionability import CERI_ACTIONABILITY_POLICY_VERSION
    from app.services.decision_effective_configuration import (
        resolve_ceri_decision_configuration,
        validate_executable_configuration,
    )
    from app.services.effective_configuration import ConfigurationSource, ConfigurationSourceKind

    config = resolve_ceri_decision_configuration()
    assert config.values["policy"]["opportunity_actionability"] == CERI_ACTIONABILITY_POLICY_VERSION
    validate_executable_configuration(config)
    old = freeze_decision_configuration(
        "decision.alerts.ceri",
        {**config.values, "policy": {"algorithm_version": "ceri-alerts-v1"}},
        ("config", "enabled", "policy", "rules"),
        source=ConfigurationSource(ConfigurationSourceKind.CODE_DEFAULT, "prior-alert-contract"),
    )
    assert old.ceri_decision_config() is not None
    with pytest.raises(ValueError, match="FROZEN_CONFIGURATION_CODE_POLICY_MISMATCH"):
        validate_executable_configuration(old)
    changes = resolve_ceri_decision_configuration(family="changes")
    assert changes.values["policy"] == {"algorithm_version": "ceri-changes-v1"}


@pytest.mark.parametrize("megabytes,active,expected", [(2047, False, False),
                                                      (2048, False, True),
                                                      (4096, True, False)])
def test_recycle_only_at_idle_exact_instance_boundary(monkeypatch, megabytes, active, expected):
    statements = []

    class Session:
        def scalar(self, statement):
            statements.append(statement)
            return active

        def close(self):
            pass

    monkeypatch.setattr(
        "app.services.background_worker.process_memory_snapshot",
        lambda: ProcessMemorySnapshot(
            process_id=12, total_physical_bytes=None, available_physical_bytes=None,
            rss_bytes=megabytes * 1024 * 1024, private_bytes=megabytes * 1024 * 1024,
        ),
    )
    assert _post_job_recycle_required(
        settings=Settings(_env_file=None), worker_id="worker-one",
        worker_instance_id="generation-one", session_factory=Session,
    ) is expected
    if megabytes >= 2048:
        params = statements[0].compile().params
        assert "worker-one" in params.values()
        assert "generation-one" in params.values()
    else:
        assert statements == []


def test_graceful_recycle_cannot_borrow_stopping_registration_from_other_child():
    child = SimpleNamespace(process=SimpleNamespace(pid=123, poll=lambda: 0))
    worker = SimpleNamespace(
        stopping_at=datetime.now(UTC), launcher_process_id=124, process_id=125,
    )
    assert not worker_supervisor._gracefully_stopped_worker_child(worker, child)
    worker.launcher_process_id = 123
    assert worker_supervisor._gracefully_stopped_worker_child(worker, child)


def test_supervisor_defines_recycle_guard_before_module_entrypoint():
    import ast

    module = ast.parse(Path(worker_supervisor.__file__).read_text(encoding="utf-8"))
    guard = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                 and n.name == "_gracefully_stopped_worker_child")
    entrypoint = next(n for n in module.body if isinstance(n, ast.If)
                      and "__main__" in ast.unparse(n.test))
    assert guard.lineno < entrypoint.lineno


def test_control_loop_recycles_after_completed_job_before_another_claim(monkeypatch):
    from threading import Event

    from app.services import background_worker

    calls = []

    def completed_job(**_kwargs):
        calls.append("job_completed")
        return True

    def recycle(**kwargs):
        assert calls == ["job_completed"]
        assert kwargs["worker_instance_id"] == "exact-generation"
        calls.append("idle_recycle_checked")
        return True

    monkeypatch.setattr(background_worker, "_post_job_recycle_required", recycle)
    background_worker._run_worker_control_loop(
        settings=Settings(_env_file=None, runtime_mode="CERTIFICATION"),
        worker_id="worker-one", worker_instance_id="exact-generation",
        queue_names=("interactive",), handlers={}, claim_state=SimpleNamespace(),
        session_factory=lambda: pytest.fail("unexpected control-loop database write"),
        runtime_stop_event=Event(), certification_session_id="fresh-epoch",
        stop_after_one=False, poll_once=completed_job,
    )
    assert calls == ["job_completed", "idle_recycle_checked"]


def test_recycle_unwinds_worker_and_marks_same_instance_stopping(monkeypatch):
    from app.services import background_worker as worker

    registrations, stops, polls = [], [], []

    class Session:
        def scalar(self, _statement):
            assert polls == ["completed"]
            return False

        def commit(self):
            pass

        def rollback(self):
            pytest.fail("unexpected recycle rollback")

        def close(self):
            pass

    class Thread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

        def join(self, **_kwargs):
            pass

    monkeypatch.setattr(worker, "Thread", Thread)
    monkeypatch.setattr(worker, "register_worker", lambda _db, **kw: registrations.append(kw))
    monkeypatch.setattr(worker, "mark_worker_stopping",
                        lambda _db, _worker_id, **kw: stops.append(kw))
    monkeypatch.setattr(worker, "establish_worker_processor_identity", lambda *_a, **_kw: None)
    monkeypatch.setattr(worker, "log_worker_startup_configuration", lambda *_a, **_kw: None)
    monkeypatch.setattr(worker, "run_worker_once", lambda **_kw: polls.append("completed") or True)
    monkeypatch.setattr(worker, "process_memory_snapshot", lambda: ProcessMemorySnapshot(
        process_id=12, total_physical_bytes=None, available_physical_bytes=None,
        rss_bytes=2048 * 1024 * 1024, private_bytes=2048 * 1024 * 1024,
    ))
    worker.run_worker(
        settings=Settings(_env_file=None, runtime_mode="CERTIFICATION",
                          runtime_instance_id="fresh-epoch"),
        session_factory=Session, handlers={"test": lambda: None},
        worker_id="contained-worker", queues=("interactive",),
    )
    assert polls == ["completed"]
    assert len(registrations) == len(stops) == 1
    assert registrations[0]["instance_id"] == stops[0]["instance_id"]


def test_sanitized_template_uses_supported_canonical_containment(monkeypatch):
    from app.services.canonical_runtime_launcher import build_canonical_runtime_launch

    root = Path(__file__).resolve().parents[1]
    for key in ["RUNTIME_MODE", "FUNDAMENTAL_ADMISSION_MODE", "DATABASE_URL"]:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=root / "config/msi_operational_compatibility.env.example")
    assert settings.runtime_mode.value == "CERTIFICATION"
    assert settings.fundamental_admission_mode is FundamentalAdmissionMode.ACTIVE
    assert settings.technical_worker_processes == 2
    assert settings.technical_max_in_flight == 4
    assert settings.technical_artifact_cache_mode.value == "ACTIVE"
    assert not settings.market_data_prewarm_enabled
    assert not settings.winner_probability_auto_maturation_enabled
    assert not settings.winner_probability_auto_cohort_refresh_enabled
    assert not settings.ib_gateway_auto_launch_enabled
    launch = build_canonical_runtime_launch(
        settings=settings, parent_environment={}, repo_root=root,
        git_sha="dc4224856a160bf7e0fe3866a356cad3bafa31fc", runtime_instance_id="fresh-epoch",
    )
    assert "app.worker_supervisor" in launch.command
    assert launch.environment["RUNTIME_MODE"] == "CERTIFICATION"
    assert launch.environment["SWINGLENS_RUNTIME_INSTANCE_ID"] == "fresh-epoch"
    assert "--job-types" not in launch.command
