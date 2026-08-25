from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from threading import Event, Lock, Thread

from app.settings import Settings

logger = logging.getLogger(__name__)


class SupervisorProcessManager:
    """Keep the out-of-process durable worker supervisor alive with the web app."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stop = Event()
        self._lock = Lock()
        self._process: subprocess.Popen | None = None
        self._thread = Thread(
            target=self._run,
            name=f"swinglens-supervisor-guardian-{settings.job_worker_id}",
            daemon=True,
        )

    @property
    def process_id(self) -> int | None:
        with self._lock:
            return self._process.pid if self._process is not None else None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            _request_stop(process)
        self._thread.join(
            timeout=max(5.0, self.settings.worker_shutdown_grace_seconds + 5.0)
        )
        if process is not None and process.poll() is None:
            _force_stop(process)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                process = _start_supervisor(self.settings)
                with self._lock:
                    self._process = process
                logger.info(
                    "worker.supervisor.guardian_started",
                    extra={
                        "worker_id": self.settings.job_worker_id,
                        "supervisor_launcher_pid": process.pid,
                    },
                )
                while not self._stop.wait(1.0) and process.poll() is None:
                    pass
                if self._stop.is_set():
                    if process.poll() is None:
                        _request_stop(process)
                    try:
                        process.wait(timeout=self.settings.worker_shutdown_grace_seconds + 5.0)
                    except subprocess.TimeoutExpired:
                        _force_stop(process)
                    return
                logger.error(
                    "worker.supervisor.guardian_restarting",
                    extra={
                        "worker_id": self.settings.job_worker_id,
                        "supervisor_launcher_pid": process.pid,
                        "return_code": process.returncode,
                    },
                )
            except Exception:
                logger.exception(
                    "worker.supervisor.guardian_cycle_failed",
                    extra={"worker_id": self.settings.job_worker_id},
                )
            finally:
                with self._lock:
                    self._process = None
            self._stop.wait(1.0)


def _start_supervisor(settings: Settings) -> subprocess.Popen:
    kwargs: dict[str, object] = {"env": dict(os.environ)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.worker_supervisor",
            "--worker-id",
            settings.job_worker_id,
            "--queues",
            "interactive,broker,background",
        ],
        **kwargs,
    )


def _request_stop(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
    except Exception:
        logger.exception(
            "worker.supervisor.guardian_stop_failed", extra={"process_id": process.pid}
        )


def _force_stop(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            process.kill()
    except Exception:
        logger.exception(
            "worker.supervisor.guardian_force_stop_failed",
            extra={"process_id": process.pid},
        )
