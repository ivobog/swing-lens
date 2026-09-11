from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from threading import Event, Thread

from app.services.process_identity import process_is_alive

PARENT_PID_ENV = "SWINGLENS_PARENT_PID"
PARENT_STARTED_AT_ENV = "SWINGLENS_PARENT_STARTED_AT"


def install_parent_watchdog(on_parent_lost: Callable[[], None]) -> Thread | None:
    """Stop a supervised child if its exact parent process instance disappears."""

    pid_value = os.environ.get(PARENT_PID_ENV)
    started_value = os.environ.get(PARENT_STARTED_AT_ENV)
    if not pid_value or not started_value:
        return None
    parent_pid = int(pid_value)
    parent_started_at = datetime.fromisoformat(started_value.replace("Z", "+00:00"))

    def watch() -> None:
        while process_is_alive(parent_pid, parent_started_at):
            Event().wait(0.5)
        on_parent_lost()

    thread = Thread(target=watch, name="swinglens-parent-watchdog", daemon=True)
    thread.start()
    return thread
