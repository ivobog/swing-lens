from __future__ import annotations

import argparse
import logging
import signal
from collections.abc import Sequence
from threading import Event

from app.observability.logging import configure_json_logging, log_event
from app.observability.metrics import operational_metrics, start_metrics_http_server
from app.observability.resource_sampler import ResourceSampler
from app.services.background_queue import VALID_WORKER_QUEUES, normalize_worker_queues
from app.services.background_worker import run_worker
from app.services.parent_watchdog import install_parent_watchdog
from app.services.process_roles import require_process_role
from app.settings import ProcessRole, get_settings

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run the durable SwingLens job worker.")
    parser.add_argument("--worker-id", default=settings.job_worker_id)
    parser.add_argument(
        "--queues",
        default=",".join(VALID_WORKER_QUEUES),
        help="Comma-separated queue allowlist: interactive,broker,background",
    )
    args = parser.parse_args(argv)
    try:
        args.queues = normalize_worker_queues(args.queues)
    except ValueError as exc:
        parser.error(str(exc))
    if not str(args.worker_id).strip():
        parser.error("--worker-id is required")
    args.worker_id = str(args.worker_id).strip()
    return args


def main(argv: Sequence[str] | None = None) -> None:
    configure_json_logging("worker")
    log_event(logger, "runtime.process_boot", stage="process_boot")
    args = parse_args(argv)
    settings = get_settings()
    try:
        require_process_role(settings, ProcessRole.DURABLE_WORKER)
    except Exception as exc:
        log_event(
            logger,
            "runtime.role_validation_failed",
            level=logging.ERROR,
            stage="role_validation",
            result="failure",
            reason_code="CONFIGURATION_CONFLICT",
            error=str(exc),
        )
        raise
    log_event(logger, "runtime.role_validation", stage="role_validation", result="success")
    stop_event = Event()
    watchdog = install_parent_watchdog(stop_event.set)
    log_event(
        logger,
        "runtime.parent_watchdog_install",
        stage="parent_watchdog_install",
        result="success",
        supervised=watchdog is not None,
    )

    def request_shutdown(_signum, _frame) -> None:
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        supported_signal = getattr(signal, signal_name, None)
        if supported_signal is not None:
            signal.signal(supported_signal, request_shutdown)

    operational_metrics.configure(enabled=settings.observability_metrics_enabled)
    metrics_server = None
    sampler = None
    if settings.observability_metrics_enabled:
        metrics_server = start_metrics_http_server(
            settings.observability_metrics_host,
            settings.observability_worker_metrics_port,
        )
        sampler = ResourceSampler(
            process_role="worker",
            interval_seconds=settings.observability_collection_interval_seconds,
            worker_id=args.worker_id,
        )
        sampler.start()
    try:
        log_event(logger, "runtime.worker_loop_begin", stage="worker_loop", result="success")
        run_worker(worker_id=args.worker_id, queues=args.queues, stop_event=stop_event)
    finally:
        log_event(logger, "runtime.process_shutdown", stage="process_shutdown")
        if settings.observability_metrics_enabled:
            operational_metrics.set_gauge("swinglens_worker_up", 0, worker_id=args.worker_id)
        if sampler is not None:
            sampler.stop()
        if metrics_server is not None:
            metrics_server.shutdown()


if __name__ == "__main__":
    main()
