from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.services.ib_gateway_health_service import is_gateway_process_running
from app.settings import Settings, get_settings


class IBGatewayLaunchState(StrEnum):
    STARTED = "STARTED"
    ALREADY_RUNNING = "ALREADY_RUNNING"
    DISABLED = "DISABLED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    EXECUTABLE_NOT_FOUND = "EXECUTABLE_NOT_FOUND"
    UNAPPROVED_EXECUTABLE = "UNAPPROVED_EXECUTABLE"
    LAUNCH_FAILED = "LAUNCH_FAILED"


@dataclass(frozen=True)
class IBGatewayLaunchResult:
    status: str
    error_code: str | None
    message: str
    process_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ProcessDetector = Callable[[str | None], bool]
ProcessLauncher = Callable[..., Any]


def launch_gateway(
    settings: Settings | None = None,
    *,
    process_detector: ProcessDetector = is_gateway_process_running,
    process_launcher: ProcessLauncher = subprocess.Popen,
) -> IBGatewayLaunchResult:
    settings = settings or get_settings()
    if not settings.ib_gateway_auto_launch_enabled:
        return _result(
            IBGatewayLaunchState.DISABLED,
            "IB_GATEWAY_LAUNCH_DISABLED",
            "IB Gateway auto-launch is disabled. Set IB_GATEWAY_AUTO_LAUNCH_ENABLED=true.",
        )
    configured_path = settings.ib_gateway_executable_path
    if configured_path is None or not str(configured_path).strip():
        return _result(
            IBGatewayLaunchState.NOT_CONFIGURED,
            "IB_GATEWAY_EXECUTABLE_NOT_CONFIGURED",
            "IB Gateway executable path is not configured. Configure "
            "IB_GATEWAY_EXECUTABLE_PATH and retry.",
        )
    path = Path(configured_path).expanduser()
    if not path.is_file():
        return _result(
            IBGatewayLaunchState.EXECUTABLE_NOT_FOUND,
            "IB_GATEWAY_EXECUTABLE_NOT_FOUND",
            "The configured IB Gateway executable was not found. Verify "
            "IB_GATEWAY_EXECUTABLE_PATH.",
        )
    if not _is_approved_gateway_executable(path):
        return _result(
            IBGatewayLaunchState.UNAPPROVED_EXECUTABLE,
            "IB_GATEWAY_EXECUTABLE_NOT_APPROVED",
            "The configured executable is not an approved IB Gateway target.",
        )
    try:
        already_running = process_detector(path.name)
    except Exception:
        already_running = False
    if already_running:
        return _result(
            IBGatewayLaunchState.ALREADY_RUNNING,
            None,
            "IB Gateway is already running. Complete login / 2FA and wait for API readiness.",
        )
    try:
        process = process_launcher(
            [str(path.resolve())],
            shell=False,
            cwd=str(path.resolve().parent),
        )
    except Exception:
        return _result(
            IBGatewayLaunchState.LAUNCH_FAILED,
            "IB_GATEWAY_LAUNCH_FAILED",
            "SwingLens could not launch IB Gateway. Verify the configured executable "
            "and permissions.",
        )
    return _result(
        IBGatewayLaunchState.STARTED,
        None,
        "IB Gateway started. Complete login and 2FA in IB Gateway.",
        process_id=getattr(process, "pid", None),
    )


def _is_approved_gateway_executable(path: Path) -> bool:
    return path.suffix.lower() == ".exe" and "ibgateway" in path.stem.lower()


def _result(
    state: IBGatewayLaunchState,
    error_code: str | None,
    message: str,
    *,
    process_id: int | None = None,
) -> IBGatewayLaunchResult:
    return IBGatewayLaunchResult(
        status=state.value,
        error_code=error_code,
        message=message,
        process_id=process_id,
    )
