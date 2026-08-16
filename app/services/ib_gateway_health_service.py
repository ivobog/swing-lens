from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from time import perf_counter
from typing import Any

from app.services.ib_api import IB
from app.services.ib_connection import create_ib_client
from app.settings import Settings, get_settings


class IBGatewayHealthState(StrEnum):
    READY = "READY"
    PROCESS_RUNNING_API_NOT_READY = "PROCESS_RUNNING_API_NOT_READY"
    NOT_RUNNING_OR_UNREACHABLE = "NOT_RUNNING_OR_UNREACHABLE"
    CONFIG_ERROR = "CONFIG_ERROR"


class IBGatewayHealthError(StrEnum):
    API_UNREACHABLE = "IB_GATEWAY_API_UNREACHABLE"
    API_NOT_READY = "IB_GATEWAY_API_NOT_READY"
    CONFIG_ERROR = "IB_GATEWAY_CONFIG_ERROR"


@dataclass(frozen=True)
class IBGatewayHealthStatus:
    status: str
    host: str
    port: int
    api_connected: bool
    checked_at: datetime
    latency_ms: int
    error_code: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checked_at"] = self.checked_at.isoformat()
        return payload


IBFactory = Callable[[], IB]
ProcessDetector = Callable[[], bool]


def check_status(
    settings: Settings | None = None,
    *,
    ib_factory: IBFactory = IB,
    process_detector: ProcessDetector | None = None,
) -> IBGatewayHealthStatus:
    """Perform a short, read-only IB API handshake suitable for UI polling."""
    settings = settings or get_settings()
    checked_at = datetime.now(UTC)
    started_at = perf_counter()
    config_error = _validate_config(settings)
    if config_error:
        return _status(
            state=IBGatewayHealthState.CONFIG_ERROR,
            settings=settings,
            connected=False,
            checked_at=checked_at,
            started_at=started_at,
            error_code=IBGatewayHealthError.CONFIG_ERROR,
            message=config_error,
        )

    ib = create_ib_client(ib_factory)
    try:
        if hasattr(ib, "RequestTimeout"):
            ib.RequestTimeout = settings.ib_health_timeout_seconds
        ib.connect(
            settings.ib_host,
            settings.ib_port,
            clientId=settings.ib_client_id,
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
        return _status(
            state=IBGatewayHealthState.READY,
            settings=settings,
            connected=True,
            checked_at=checked_at,
            started_at=started_at,
            error_code=None,
            message="IB Gateway API connection successful.",
        )
    except Exception:
        return _not_ready_status(
            settings,
            checked_at=checked_at,
            started_at=started_at,
            process_detector=process_detector,
        )
    finally:
        try:
            if ib.isConnected():
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
    return None


def _not_ready_status(
    settings: Settings,
    *,
    checked_at: datetime,
    started_at: float,
    process_detector: ProcessDetector | None,
) -> IBGatewayHealthStatus:
    detector = process_detector or is_gateway_process_running
    process_running = False
    try:
        process_running = bool(detector())
    except Exception:
        process_running = False
    if process_running:
        return _status(
            state=IBGatewayHealthState.PROCESS_RUNNING_API_NOT_READY,
            settings=settings,
            connected=False,
            checked_at=checked_at,
            started_at=started_at,
            error_code=IBGatewayHealthError.API_NOT_READY,
            message=(
                "IB Gateway is running but its API is not ready. Complete login / 2FA "
                "and verify the configured API settings."
            ),
        )
    return _status(
        state=IBGatewayHealthState.NOT_RUNNING_OR_UNREACHABLE,
        settings=settings,
        connected=False,
        checked_at=checked_at,
        started_at=started_at,
        error_code=IBGatewayHealthError.API_UNREACHABLE,
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
    checked_at: datetime,
    started_at: float,
    error_code: IBGatewayHealthError | None,
    message: str,
) -> IBGatewayHealthStatus:
    return IBGatewayHealthStatus(
        status=state.value,
        host=str(settings.ib_host),
        port=int(settings.ib_port),
        api_connected=connected,
        checked_at=checked_at,
        latency_ms=max(0, round((perf_counter() - started_at) * 1000)),
        error_code=error_code.value if error_code else None,
        message=message,
    )
