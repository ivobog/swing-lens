from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services import certification_session_discovery as discovery
from app.services.certification_session_discovery import (
    CertificationSessionBinding,
    CertificationSessionDiscoveryError,
    bind_certification_session_environment,
    discover_active_certification_session,
)
from app.services.process_roles import certification_profile_environment
from app.settings import Settings
from scripts import ceri_feature_certification as launcher

RUNTIME_ID = "runtime-certification-test"
GIT_SHA = "a" * 40
FINGERPRINT = "b" * 64
ROOT = Path.cwd()


class _Rows:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class FakeDb:
    def __init__(self, *, worker_generation: int = 8):
        observed = datetime(2026, 9, 28, 12, tzinfo=UTC)
        self.worker = {
            "worker_id": "local-worker-1",
            "instance_id": "worker-instance",
            "process_id": 104,
            "process_started_at": observed,
            "generation": worker_generation,
            "heartbeat_at": observed,
            "stopping_at": None,
        }
        self.supervisor = {
            "worker_id": "local-worker-1",
            "instance_id": "supervisor-instance",
            "process_id": 102,
            "process_started_at": observed,
            "generation": 7,
            "heartbeat_at": observed,
            "stopping_at": None,
        }

    def execute(self, statement, _parameters):
        return _Rows(self.worker if "background_workers" in str(statement) else self.supervisor)

    def connection(self):
        return object()


def _runtime_state(*, git_sha: str = GIT_SHA):
    return {
        "version": 5,
        "gitCommit": git_sha,
        "repoRoot": str(ROOT),
        "runtimeInstanceId": RUNTIME_ID,
        "runtimeConfigFingerprint": FINGERPRINT,
        "topologyVersion": "supervisor-root-v1",
        "web": _runtime_identity(101, "web", "app.serve"),
        "supervisor": _runtime_identity(102, "supervisor", "app.worker_supervisor"),
        "processGroup": _runtime_identity(103, "supervisor", "app.worker_supervisor"),
    }


def _runtime_identity(pid: int, role: str, module: str):
    return {
        "pid": pid,
        "createdAt": "2026-09-28T12:00:00+00:00",
        "role": role,
        "module": module,
        "repoRoot": str(ROOT),
        "runtimeInstanceId": RUNTIME_ID,
        **({"port": 8000} if role == "web" else {}),
    }


def _supervisor_state(*, worker_generation: int = 8):
    return {
        "runtime_instance_id": RUNTIME_ID,
        "runtime_config_fingerprint": FINGERPRINT,
        "worker": {
            "state": "RUNNING",
            "worker_id": "local-worker-1",
            "registered_instance_id": "worker-instance",
            "registered_generation": worker_generation,
            "registered_pid": 104,
        },
        "supervisor": {
            "worker_id": "local-worker-1",
            "registered_instance_id": "supervisor-instance",
            "registered_generation": 7,
            "registered_pid": 102,
        },
    }


def _readiness(*, runtime: str = "CERTIFICATION_ISOLATION_ACTIVE"):
    names = ("database", "migrations", "runtime", "topology", "worker", "jobs")
    return {
        "status": "ok",
        "checks": {"runtime": runtime, "topology": "READY_EXACTLY_ONE_WORKER"},
        "check_states": {name: "ok" for name in names},
    }


def _process(pid: int):
    modules = {
        101: "app.serve",
        102: "app.worker_supervisor",
        103: "app.worker_supervisor",
        104: "app.worker",
    }
    command = ["python", "-m", modules[pid]]
    if pid != 104:
        command.append(RUNTIME_ID)
    return {
        "pid": pid,
        "createdAt": "2026-09-28T12:00:00+00:00",
        "commandLine": command,
        "cwd": str(ROOT),
    }


def _install_evidence(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state=None,
    recorder=None,
    repository_sha: str = GIT_SHA,
    schema_at_head: bool = True,
):
    runtime = state or _runtime_state()
    supervisor = recorder or _supervisor_state()
    monkeypatch.setattr(
        discovery,
        "read_runtime_state",
        lambda path: supervisor if path.name == "supervisor.json" else runtime,
    )
    monkeypatch.setattr(discovery, "supervisor_state_path", lambda _root: Path("supervisor.json"))
    monkeypatch.setattr(discovery, "_repository_git_sha", lambda _root: repository_sha)
    monkeypatch.setattr(
        discovery,
        "schema_is_at_head",
        lambda _connection, _repo_root: schema_at_head,
    )


def _discover(
    db: FakeDb,
    *,
    environment: dict[str, str] | None = None,
    readiness=None,
    process_inspector=_process,
):
    return discover_active_certification_session(
        db,
        repo_root=ROOT,
        settings=Settings(database_url="sqlite://", app_port=8000),
        environment=environment or {},
        readiness_loader=lambda _url: readiness or _readiness(),
        process_inspector=process_inspector,
        now=datetime(2026, 9, 28, 12, 0, 5, tzinfo=UTC),
    )


def test_active_canonical_session_is_auto_discovered_without_manual_environment(monkeypatch):
    _install_evidence(monkeypatch)

    binding = _discover(FakeDb())

    assert binding.runtime_instance_id == RUNTIME_ID
    assert binding.git_sha == GIT_SHA
    assert binding.worker_instance_id == "worker-instance"
    assert binding.worker_generation == 8


def test_explicit_matching_runtime_override_is_accepted(monkeypatch):
    _install_evidence(monkeypatch)

    binding = _discover(FakeDb(), environment={"SWINGLENS_RUNTIME_INSTANCE_ID": RUNTIME_ID})

    assert binding.runtime_instance_id == RUNTIME_ID


def test_stale_runtime_override_is_rejected(monkeypatch):
    _install_evidence(monkeypatch)

    with pytest.raises(
        CertificationSessionDiscoveryError,
        match="CERTIFICATION_RUNTIME_OVERRIDE_MISMATCH",
    ):
        _discover(FakeDb(), environment={"SWINGLENS_RUNTIME_INSTANCE_ID": "stale"})


def test_wrong_deployed_sha_is_rejected(monkeypatch):
    _install_evidence(monkeypatch, state=_runtime_state(git_sha="c" * 40))

    with pytest.raises(
        CertificationSessionDiscoveryError,
        match="CERTIFICATION_RUNTIME_SHA_MISMATCH",
    ):
        _discover(FakeDb())


def test_wrong_runtime_mode_is_rejected(monkeypatch):
    _install_evidence(monkeypatch)

    with pytest.raises(
        CertificationSessionDiscoveryError,
        match="CERTIFICATION_RUNTIME_MODE_MISMATCH",
    ):
        _discover(FakeDb(), readiness=_readiness(runtime="runtime:NORMAL"))


def test_stopped_runtime_is_rejected(monkeypatch):
    _install_evidence(monkeypatch)

    def stopped(_pid: int):
        raise OSError("process is gone")

    with pytest.raises(
        CertificationSessionDiscoveryError,
        match="CERTIFICATION_RUNTIME_NOT_ACTIVE",
    ):
        _discover(FakeDb(), process_inspector=stopped)


def test_mismatched_worker_generation_is_rejected(monkeypatch):
    _install_evidence(monkeypatch, recorder=_supervisor_state(worker_generation=9))

    with pytest.raises(
        CertificationSessionDiscoveryError,
        match="CERTIFICATION_RUNTIME_WORKER_MISMATCH",
    ):
        _discover(FakeDb(worker_generation=8))


def test_schema_mismatch_is_rejected_before_binding(monkeypatch):
    _install_evidence(monkeypatch, schema_at_head=False)

    with pytest.raises(
        CertificationSessionDiscoveryError,
        match="CERTIFICATION_RUNTIME_SCHEMA_MISMATCH",
    ):
        _discover(FakeDb())


def test_binding_populates_canonical_session_environment():
    environment = {}
    binding = CertificationSessionBinding(
        runtime_instance_id=RUNTIME_ID,
        git_sha=GIT_SHA,
        runtime_config_fingerprint=FINGERPRINT,
        worker_id="local-worker-1",
        worker_instance_id="worker-instance",
        worker_generation=8,
        runtime_state_path="runtime-state.json",
    )

    bind_certification_session_environment(binding, environment=environment)

    assert environment == {
        **certification_profile_environment({}),
        "SWINGLENS_GIT_SHA": GIT_SHA,
        "SWINGLENS_RUNTIME_INSTANCE_ID": RUNTIME_ID,
    }


def test_bound_environment_constructs_isolated_certification_settings(monkeypatch):
    binding = CertificationSessionBinding(
        runtime_instance_id=RUNTIME_ID,
        git_sha=GIT_SHA,
        runtime_config_fingerprint=FINGERPRINT,
        worker_id="local-worker-1",
        worker_instance_id="worker-instance",
        worker_generation=8,
        runtime_state_path="runtime-state.json",
    )
    for key, value in certification_profile_environment({}).items():
        monkeypatch.setenv(key, "true" if value == "false" else "false")

    bind_certification_session_environment(binding)
    settings = Settings()

    assert settings.runtime_mode.value == "CERTIFICATION"
    assert settings.use_durable_pipeline is True
    assert settings.durable_worker_process_enabled is True
    assert settings.embedded_job_worker_enabled is False
    assert settings.job_worker_enabled is False
    assert settings.winner_probability_auto_maturation_enabled is False
    assert settings.winner_probability_auto_cohort_refresh_enabled is False
    assert settings.market_data_prewarm_enabled is False


def test_launcher_discovers_session_before_importing_durable_admission():
    source = inspect.getsource(launcher.main)

    discovery_offset = source.index("binding = discover_active_certification_session")
    durable_import_offset = source.index("from app.db import SessionLocal")
    admission_offset = source.index("admitted = admit_ceri_feature_certification")

    assert discovery_offset < durable_import_offset < admission_offset
