from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import create_app
from app.routers import ib_gateway_admin_routes
from app.services.ib_gateway_health_service import (
    IBGatewayHealthState,
    check_status,
)
from app.services.ib_gateway_launcher import IBGatewayLaunchState, launch_gateway
from app.settings import ProcessRole, Settings


class FakeClient:
    def __init__(self, owner: FakeIB) -> None:
        self.owner = owner

    def isReady(self) -> bool:  # noqa: N802 - mirrors ib_insync
        return self.owner.session_ready

    def serverVersion(self) -> int:  # noqa: N802 - mirrors ib_insync
        return 176 if self.owner.session_ready else 0


class FakeIB:
    def __init__(
        self,
        *,
        connects: bool = True,
        raises: bool | Exception = False,
        session_ready: bool = True,
        smoke_raises: bool = False,
    ) -> None:
        self.connects = connects
        self.raises = raises
        self.session_ready = session_ready
        self.smoke_raises = smoke_raises
        self.connected = False
        self.client = FakeClient(self)
        self.connect_calls = []
        self.disconnect_calls = 0

    def connect(self, host, port, **kwargs) -> None:
        self.connect_calls.append((host, port, kwargs))
        if isinstance(self.raises, Exception):
            raise self.raises
        if self.raises:
            raise ConnectionError("connection refused")
        self.connected = self.connects

    def isConnected(self) -> bool:  # noqa: N802 - mirrors ib_insync
        return self.connected

    def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1

    def reqCurrentTime(self):  # noqa: N802 - mirrors ib_insync
        if self.smoke_raises:
            self.connected = False
            raise ConnectionError("session lost")
        return "2026-09-10T14:00:00+00:00"


def test_health_ready_requires_real_api_handshake() -> None:
    ib = FakeIB()
    settings = Settings(_env_file=None, ib_health_timeout_seconds=1.25)

    status = check_status(settings, ib_factory=lambda: ib)

    assert status.status == IBGatewayHealthState.READY
    assert status.api_connected is True
    assert status.api_ready is True
    assert status.server_version == 176
    assert status.smoke_response_received is True
    assert ib.connect_calls == [
        (
            "127.0.0.1",
            4002,
            {"clientId": 25, "timeout": 1.25, "readonly": True},
        )
    ]
    assert ib.disconnect_calls == 1


def test_health_client_ids_are_dedicated_and_unique_by_process_role() -> None:
    observed = {}
    for role in ProcessRole:
        ib = FakeIB()
        status = check_status(
            Settings(_env_file=None, process_role=role),
            ib_factory=lambda current=ib: current,
        )
        observed[role] = status.client_id
        assert ib.connect_calls[0][2]["clientId"] == status.client_id

    assert observed == {
        ProcessRole.WEB: 22,
        ProcessRole.DURABLE_WORKER: 23,
        ProcessRole.SUPERVISOR: 24,
        ProcessRole.CLI_OR_MAINTENANCE: 25,
    }
    assert 21 not in observed.values()


def test_health_unreachable_distinguishes_process_not_running() -> None:
    status = check_status(
        Settings(_env_file=None),
        ib_factory=lambda: FakeIB(raises=True),
        process_detector=lambda: False,
    )

    assert status.status == IBGatewayHealthState.NOT_RUNNING_OR_UNREACHABLE
    assert status.error_code == "IB_GATEWAY_API_UNREACHABLE"
    assert status.api_connected is False


def test_health_running_process_with_failed_handshake_is_not_ready() -> None:
    status = check_status(
        Settings(_env_file=None),
        ib_factory=lambda: FakeIB(connects=False),
        process_detector=lambda: True,
    )

    assert status.status == IBGatewayHealthState.PROCESS_RUNNING_API_NOT_READY
    assert status.error_code == "IB_GATEWAY_API_NOT_READY"
    assert status.api_connected is False


def test_connected_socket_without_api_handshake_is_not_ready() -> None:
    status = check_status(
        Settings(_env_file=None),
        ib_factory=lambda: FakeIB(session_ready=False),
        process_detector=lambda: True,
    )

    assert status.status == IBGatewayHealthState.IB_PROCESS_RUNNING_API_NOT_READY
    assert status.api_ready is False


def test_session_loss_during_smoke_request_is_distinct() -> None:
    status = check_status(
        Settings(_env_file=None),
        ib_factory=lambda: FakeIB(smoke_raises=True),
    )

    assert status.status == IBGatewayHealthState.IB_SESSION_LOST
    assert status.error_code == "IB_GATEWAY_SESSION_LOST"
    assert status.api_ready is False


def test_timeout_and_client_id_conflict_are_deterministically_classified() -> None:
    timeout = check_status(
        Settings(_env_file=None),
        ib_factory=lambda: FakeIB(raises=TimeoutError("probe timed out")),
        process_detector=lambda: True,
    )
    conflict_ib = FakeIB()

    def conflicting_connect(*_args, **_kwargs):
        raise ConnectionError("client id already in use")

    conflict_ib.connect = conflicting_connect
    conflict = check_status(
        Settings(_env_file=None),
        ib_factory=lambda: conflict_ib,
        process_detector=lambda: True,
    )

    assert timeout.error_code == "IB_GATEWAY_PROBE_TIMEOUT"
    assert conflict.error_code == "IB_GATEWAY_CLIENT_ID_IN_USE"


def test_readiness_has_explicit_expiry_and_cannot_be_reused_stale() -> None:
    status = check_status(
        Settings(_env_file=None, ib_readiness_max_age_seconds=2),
        ib_factory=FakeIB,
    )

    assert status.is_fresh(max_age_seconds=2, now=status.checked_at + timedelta(seconds=1))
    assert not status.is_fresh(
        max_age_seconds=2,
        now=status.checked_at + timedelta(seconds=3),
    )


def test_health_invalid_config_is_structured_and_has_no_client_side_effect() -> None:
    created = []

    status = check_status(
        Settings(_env_file=None, ib_host=""),
        ib_factory=lambda: created.append(FakeIB()) or created[-1],
    )

    assert status.status == IBGatewayHealthState.CONFIG_ERROR
    assert status.error_code == "IB_GATEWAY_CONFIG_ERROR"
    assert created == []


def test_repeated_health_calls_only_open_and_close_transient_connections() -> None:
    clients = []

    def factory() -> FakeIB:
        clients.append(FakeIB())
        return clients[-1]

    first = check_status(Settings(_env_file=None), ib_factory=factory)
    second = check_status(Settings(_env_file=None), ib_factory=factory)

    assert first.status == second.status == IBGatewayHealthState.READY
    assert len(clients) == 2
    assert all(client.disconnect_calls == 1 for client in clients)


def test_launcher_disabled_and_missing_path_are_explicit(tmp_path: Path) -> None:
    disabled = launch_gateway(Settings(_env_file=None))
    not_configured = launch_gateway(Settings(_env_file=None, ib_gateway_auto_launch_enabled=True))
    missing = launch_gateway(
        Settings(
            _env_file=None,
            ib_gateway_auto_launch_enabled=True,
            ib_gateway_executable_path=tmp_path / "ibgateway.exe",
        )
    )

    assert disabled.status == IBGatewayLaunchState.DISABLED
    assert disabled.error_code == "IB_GATEWAY_LAUNCH_DISABLED"
    assert not_configured.status == IBGatewayLaunchState.NOT_CONFIGURED
    assert not_configured.error_code == "IB_GATEWAY_EXECUTABLE_NOT_CONFIGURED"
    assert missing.status == IBGatewayLaunchState.EXECUTABLE_NOT_FOUND
    assert missing.error_code == "IB_GATEWAY_EXECUTABLE_NOT_FOUND"


def test_launcher_uses_safe_direct_process_creation_without_credentials(tmp_path: Path) -> None:
    executable = tmp_path / "ibgateway.exe"
    executable.write_bytes(b"test executable placeholder")
    calls = []

    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(pid=1234)

    result = launch_gateway(
        Settings(
            _env_file=None,
            ib_gateway_auto_launch_enabled=True,
            ib_gateway_executable_path=executable,
        ),
        process_detector=lambda _name: False,
        process_launcher=popen,
    )

    assert result.status == IBGatewayLaunchState.STARTED
    assert result.process_id == 1234
    assert calls == [
        (
            [str(executable.resolve())],
            {"shell": False, "cwd": str(executable.resolve().parent)},
        )
    ]
    assert "password" not in repr(calls).lower()
    assert "username" not in repr(calls).lower()
    assert "2fa" not in repr(calls).lower()


def test_repeated_launch_does_not_spawn_when_gateway_is_already_running(tmp_path: Path) -> None:
    executable = tmp_path / "ibgateway.exe"
    executable.write_bytes(b"test executable placeholder")
    launches = []

    result = launch_gateway(
        Settings(
            _env_file=None,
            ib_gateway_auto_launch_enabled=True,
            ib_gateway_executable_path=executable,
        ),
        process_detector=lambda _name: True,
        process_launcher=lambda *args, **kwargs: launches.append((args, kwargs)),
    )

    assert result.status == IBGatewayLaunchState.ALREADY_RUNNING
    assert launches == []


def test_launcher_returns_structured_failure(tmp_path: Path) -> None:
    executable = tmp_path / "ibgateway.exe"
    executable.write_bytes(b"test executable placeholder")

    result = launch_gateway(
        Settings(
            _env_file=None,
            ib_gateway_auto_launch_enabled=True,
            ib_gateway_executable_path=executable,
        ),
        process_detector=lambda _name: False,
        process_launcher=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )

    assert result.status == IBGatewayLaunchState.LAUNCH_FAILED
    assert result.error_code == "IB_GATEWAY_LAUNCH_FAILED"


def test_status_and_local_admin_launch_routes(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "ibgateway.exe"
    executable.write_bytes(b"test executable placeholder")
    settings = Settings(
        _env_file=None,
        job_worker_enabled=False,
        ib_gateway_auto_launch_enabled=True,
        ib_gateway_executable_path=executable,
    )
    app = create_app(settings)
    client = TestClient(app)
    monkeypatch.setattr(
        ib_gateway_admin_routes,
        "check_status",
        lambda **_kwargs: SimpleNamespace(to_dict=lambda: {"status": "READY"}),
    )
    monkeypatch.setattr(
        ib_gateway_admin_routes,
        "launch_gateway",
        lambda **_kwargs: SimpleNamespace(to_dict=lambda: {"status": "STARTED"}),
    )

    assert client.get("/api/ib-gateway/status").json() == {"status": "READY"}
    assert client.post("/api/ib-gateway/launch").status_code == 403
    response = client.post(
        "/api/ib-gateway/launch",
        headers={"x-csrf-token": app.state.local_admin_csrf_token},
        json={"executable_path": "C:/untrusted/evil.exe", "password": "never"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "STARTED"}
