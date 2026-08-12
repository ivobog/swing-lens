from __future__ import annotations

import argparse
import logging
import signal
from collections.abc import Sequence
from threading import Event

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
    stop_event = Event()

    def request_shutdown(_signum, _frame) -> None:
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        supported_signal = getattr(signal, signal_name, None)
        if supported_signal is not None:
            signal.signal(supported_signal, request_shutdown)

    logging.basicConfig(level=logging.INFO)
    run_worker(
        worker_id=args.worker_id,
        queues=args.queues,
        stop_event=stop_event,
    )


if __name__ == "__main__":
    main()
