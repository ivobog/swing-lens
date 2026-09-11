from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from time import perf_counter
from typing import Any

from app.services.ib_api import IB
from app.services.ib_connection import create_ib_client
from app.settings import ProcessRole, Settings, get_settings


class IBGatewayHealthState(StrEnum):
    IB_API_READY = "IB_API_READY"
    IB_PROCESS_RUNNING_API_NOT_READY = "IB_PROCESS_RUNNING_API_NOT_READY"
    IB_PROCESS_NOT_RUNNING = "IB_PROCESS_NOT_RUNNING"
    IB_SESSION_LOST = "IB_SESSION_LOST"
    CONFIG_ERROR = "CONFIG_ERROR"

    # Source compatibility for callers that compared enum members. Serialized
    # values intentionally use the authoritative IB-prefixed contract above.
    READY = IB_API_READY
    PROCESS_RUNNING_API_NOT_READY = IB_PROCESS_RUNNING_API_NOT_READY
    NOT_RUNNING_OR_UNREACHABLE = IB_PROCESS_NOT_RUNNING


class IBGatewayHealthError(StrEnum):
    API_UNREACHABLE = "IB_GATEWAY_API_UNREACHABLE"
    API_NOT_READY = "IB_GATEWAY_API_NOT_READY"
    CONFIG_ERROR = "IB_GATEWAY_CONFIG_ERROR"
    SESSION_LOST = "IB_GATEWAY_SESSION_LOST"
    TIMEOUT = "IB_GATEWAY_PROBE_TIMEOUT"
    CLIENT_ID_IN_USE = "IB_GATEWAY_CLIENT_ID_IN_USE"


@dataclass(frozen=True)
class IBGatewayHealthStatus:
    status: str
    host: str
    port: int
    client_id: int
    api_connected: bool
    api_ready: bool
    process_running: bool
    server_version: int | None
    smoke_response_received: bool
    checked_at: datetime
    expires_at: datetime
    latency_ms: int
    error_code: str | None
    failure_category: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checked_at"] = self.checked_at.isoformat()
        payload["expires_at"] = self.expires_at.isoformat()
        return payload

    def is_fresh(self, *, max_age_seconds: float, now: datetime | None = None) -> bool:
        observed_at = now or datetime.now(UTC)
        return (
            observed_at >= self.checked_at
            and observed_at - self.checked_at <= timedelta(seconds=max_age_seconds)
            and observed_at <= self.expires_at
        )


IBFactory = Callable[[], IB]
ProcessDetector = Callable[[], bool]
_HEALTH_PROBE_LOCK = Lock()
_HEALTH_CLIENT_ROLE_OFFSET = {
    ProcessRole.WEB: 0,
    ProcessRole.DURABLE_WORKER: 1,
    ProcessRole.SUPERVISOR: 2,
    ProcessRole.CLI_OR_MAINTENANCE: 3,
}


def is_api_ready_status(status: IBGatewayHealthStatus | str | Any) -> bool:
    """Recognize current API-ready evidence plus one-release serialized compatibility."""
    value = str(getattr(status, "status", status))
    if value not in {IBGatewayHealthState.IB_API_READY.value, "READY"}:
        return False
    return bool(getattr(status, "api_ready", True))


def check_status(
    settings: Settings | None = None,
    *,
    ib_factory: IBFactory = IB,
    process_detector: ProcessDetector | None = None,
) -> IBGatewayHealthStatus:
    """Perform a bounded API handshake and read-only current-time smoke request."""
    settings = settings or get_settings()
    checked_at = datetime.now(UTC)
    started_at = perf_counter()
    config_error = _validate_config(settings)
    if config_error:
        return _status(
            state=IBGatewayHealthState.CONFIG_ERROR,
            settings=settings,
            connected=False,
            api_ready=False,
            process_running=False,
            server_version=None,
            smoke_response_received=False,
            checked_at=checked_at,
            started_at=started_at,
            error_code=IBGatewayHealthError.CONFIG_ERROR,
            message=config_error,
        )

    # Browser load, modal preflight, and enqueue checks can arrive together.
    # Serialize this process's probes so the deterministic health client ID is
    # never reused concurrently.
    with _HEALTH_PROBE_LOCK:
        ib = create_ib_client(ib_factory)
        health_client_id = _health_client_id(settings)
        try:
            if hasattr(ib, "RequestTimeout"):
                ib.RequestTimeout = settings.ib_health_timeout_seconds
            ib.connect(
                settings.ib_host,
                settings.ib_port,
                clientId=health_client_id,
                timeout=settings.ib_health_timeout_seconds,
                readonly=True,
            )
            if not ib.isConnected():
                return _not_ready_status(
                    settings,
                    checked_at=checked_at,
                    started_at=started_at,
                    process_detector=process_detector,
                )
            api_ready, server_version = _api_session_ready(ib)
            if not api_ready:
                return _not_ready_status(
                    settings,
                    checked_at=checked_at,
                    started_at=started_at,
                    process_detector=process_detector,
                    known_process_running=True,
                )
            try:
                smoke_response = ib.reqCurrentTime()
            except Exception as exc:
                return _session_lost_status(
                    settings,
                    checked_at=checked_at,
                    started_at=started_at,
                    server_version=server_version,
                    error=exc,
                )
            if smoke_response is None or not ib.isConnected():
                return _session_lost_status(
                    settings,
                    checked_at=checked_at,
                    started_at=started_at,
                    server_version=server_version,
                )
            return _status(
                state=IBGatewayHealthState.IB_API_READY,
                settings=settings,
                connected=True,
                api_ready=True,
                process_running=True,
                server_version=server_version,
                smoke_response_received=True,
                checked_at=checked_at,
                started_at=started_at,
                error_code=None,
                message="IB Gateway API connection successful.",
            )
        except Exception as exc:
            return _not_ready_status(
                settings,
                checked_at=checked_at,
                started_at=started_at,
                process_detector=process_detector,
                error=exc,
            )
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass


def is_gateway_process_running(executable_name: str | None = None) -> bool:
    """Best-effort process check; API readiness is never inferred from this result."""
    names = {
        (executable_name or "").strip().lower(),
        "ibgateway.exe",
        "ibgateway",
    }
    names.discard("")
    try:
        if sys.platform == "win32":
            completed = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                check=False,
                text=True,
                timeout=2,
            )
            output = completed.stdout.lower()
            return any(f'"{name}"' in output for name in names)
        completed = subprocess.run(
            ["pgrep", "-f", "ibgateway"],
            capture_output=True,
            check=False,
            timeout=2,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _validate_config(settings: Settings) -> str | None:
    if not str(settings.ib_host).strip():
        return "IB Gateway host is not configured. Configure IB_HOST and retry."
    if not 1 <= int(settings.ib_port) <= 65535:
        return "IB Gateway API port is invalid. Configure IB_PORT and retry."
    if int(settings.ib_client_id) < 0:
        return "IB Gateway client ID is invalid. Configure IB_CLIENT_ID and retry."
    if not 0 <= _health_client_id(settings) <= 65535:
        return "IB Gateway health client ID is invalid. Configure IB_HEALTH_CLIENT_ID and retry."
    return None


def _health_client_id(settings: Settings) -> int:
    """Return a deterministic, process-role-specific transient probe ID."""

    return int(settings.ib_health_client_id) + _HEALTH_CLIENT_ROLE_OFFSET[settings.process_role]


def _not_ready_status(
    settings: Settings,
    *,
    checked_at: datetime,
    started_at: float,
    process_detector: ProcessDetector | None,
    error: Exception | None = None,
    known_process_running: bool = False,
) -> IBGatewayHealthStatus:
    detector = process_detector or is_gateway_process_running
    process_running = known_process_running
    if not process_running:
        try:
            process_running = bool(detector())
        except Exception:
            process_running = False
    if process_running:
        return _status(
            state=IBGatewayHealthState.IB_PROCESS_RUNNING_API_NOT_READY,
            settings=settings,
            connected=False,
            api_ready=False,
            process_running=True,
            server_version=None,
            smoke_response_received=False,
            checked_at=checked_at,
            started_at=started_at,
            error_code=_failure_code(error, IBGatewayHealthError.API_NOT_READY),
            message=(
                "IB Gateway is running, but its API is not ready. Complete login/session "
                "setup and retry. No pipeline was created."
            ),
        )
    return _status(
        state=IBGatewayHealthState.IB_PROCESS_NOT_RUNNING,
        settings=settings,
        connected=False,
        api_ready=False,
        process_running=False,
        server_version=None,
        smoke_response_received=False,
        checked_at=checked_at,
        started_at=started_at,
        error_code=_failure_code(error, IBGatewayHealthError.API_UNREACHABLE),
        message=(
            f"SwingLens could not connect to IB Gateway at "
            f"{settings.ib_host}:{settings.ib_port}. Start IB Gateway and verify IB_PORT."
        ),
    )


def _status(
    *,
    state: IBGatewayHealthState,
    settings: Settings,
    connected: bool,
    api_ready: bool,
    process_running: bool,
    server_version: int | None,
    smoke_response_received: bool,
    checked_at: datetime,
    started_at: float,
    error_code: IBGatewayHealthError | None,
    message: str,
) -> IBGatewayHealthStatus:
    return IBGatewayHealthStatus(
        status=state.value,
        host=str(settings.ib_host),
        port=int(settings.ib_port),
        client_id=_health_client_id(settings),
        api_connected=connected,
        api_ready=api_ready,
        process_running=process_running,
        server_version=server_version,
        smoke_response_received=smoke_response_received,
        checked_at=checked_at,
        expires_at=checked_at + timedelta(seconds=settings.ib_readiness_max_age_seconds),
        latency_ms=max(0, round((perf_counter() - started_at) * 1000)),
        error_code=error_code.value if error_code else None,
        failure_category=error_code.value if error_code else None,
        message=message,
    )


def _api_session_ready(ib: IB) -> tuple[bool, int | None]:
    client = getattr(ib, "client", None)
    ready_method = getattr(client, "isReady", None)
    server_version_method = getattr(client, "serverVersion", None)
    if not callable(ready_method) or not bool(ready_method()):
        return False, None
    server_version = int(server_version_method()) if callable(server_version_method) else 0
    return server_version > 0, server_version or None


def _session_lost_status(
    settings: Settings,
    *,
    checked_at: datetime,
    started_at: float,
    server_version: int | None,
    error: Exception | None = None,
) -> IBGatewayHealthStatus:
    return _status(
        state=IBGatewayHealthState.IB_SESSION_LOST,
        settings=settings,
        connected=False,
        api_ready=False,
        process_running=True,
        server_version=server_version,
        smoke_response_received=False,
        checked_at=checked_at,
        started_at=started_at,
        error_code=_failure_code(error, IBGatewayHealthError.SESSION_LOST),
        message=(
            "IB Gateway API session was lost during the readiness smoke request. "
            "Restore the session and retry. No pipeline was created."
        ),
    )


def _failure_code(
    error: Exception | None,
    default: IBGatewayHealthError,
) -> IBGatewayHealthError:
    if error is None:
        return default
    message = str(error).lower()
    if isinstance(error, TimeoutError) or "timeout" in message or "timed out" in message:
        return IBGatewayHealthError.TIMEOUT
    if "client id" in message and ("use" in message or "conflict" in message):
        return IBGatewayHealthError.CLIENT_ID_IN_USE
    return default
