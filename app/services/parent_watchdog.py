from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Event, Thread

from app.services.lifecycle_control import read_shutdown_request
from app.services.process_identity import process_is_alive

PARENT_PID_ENV = "SWINGLENS_PARENT_PID"
PARENT_STARTED_AT_ENV = "SWINGLENS_PARENT_STARTED_AT"
PARENT_SHUTDOWN_REQUEST_ENV = "SWINGLENS_PARENT_SHUTDOWN_REQUEST"
PARENT_RUNTIME_INSTANCE_ENV = "SWINGLENS_PARENT_RUNTIME_INSTANCE"


def install_parent_watchdog(request_stop: Callable[[], None]) -> Thread | None:
    """Stop a child if its exact parent disappears or accepts a controlled shutdown."""

    pid_value = os.environ.get(PARENT_PID_ENV)
    started_value = os.environ.get(PARENT_STARTED_AT_ENV)
    if not pid_value or not started_value:
        return None
    parent_pid = int(pid_value)
    parent_started_at = datetime.fromisoformat(started_value.replace("Z", "+00:00"))
    request_path = os.environ.get(PARENT_SHUTDOWN_REQUEST_ENV)
    runtime_instance_id = os.environ.get(PARENT_RUNTIME_INSTANCE_ENV)

    def watch() -> None:
        interval = Event()
        while process_is_alive(parent_pid, parent_started_at):
            if request_path and runtime_instance_id:
                request = read_shutdown_request(
                    Path(request_path),
                    runtime_instance_id=runtime_instance_id,
                    supervisor_pid=parent_pid,
                    supervisor_started_at=parent_started_at,
                )
                if request and request.get("accepted") is True:
                    break
            interval.wait(0.5)
        request_stop()

    thread = Thread(target=watch, name="swinglens-parent-watchdog", daemon=True)
    thread.start()
    return thread
