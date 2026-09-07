from __future__ import annotations

import argparse
import signal
from collections.abc import Sequence
from threading import Event

from app.observability.logging import configure_json_logging
from app.observability.metrics import operational_metrics, start_metrics_http_server
from app.observability.resource_sampler import ResourceSampler
from app.services.background_queue import VALID_WORKER_QUEUES, normalize_worker_queues
from app.services.background_worker import run_worker
from app.settings import get_settings


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
    args = parse_args(argv)
    settings = get_settings()
    stop_event = Event()

    def request_shutdown(_signum, _frame) -> None:
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        supported_signal = getattr(signal, signal_name, None)
        if supported_signal is not None:
            signal.signal(supported_signal, request_shutdown)

    configure_json_logging("worker")
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
        run_worker(worker_id=args.worker_id, queues=args.queues, stop_event=stop_event)
    finally:
        operational_metrics.set_gauge("swinglens_worker_up", 0, worker_id=args.worker_id)
        if sampler is not None:
            sampler.stop()
        if metrics_server is not None:
            metrics_server.shutdown()


if __name__ == "__main__":
    main()
