from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import date

from app.db import SessionLocal
from app.services.market_data_prewarm_service import (
    DEFAULT_RECENT_RUN_COUNT,
    MarketDataPrewarmRequest,
    enqueue_market_data_prewarm,
)

CLI_SOURCES = ("recent-runs", "watchlist", "explicit")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Queue a bounded SwingLens market-data prewarm job.",
    )
    parser.add_argument("--source", choices=CLI_SOURCES, default="recent-runs")
    parser.add_argument(
        "--recent-run-count",
        type=int,
        default=DEFAULT_RECENT_RUN_COUNT,
    )
    parser.add_argument(
        "--tickers",
        default="",
        help="Comma-separated tickers; required when --source explicit.",
    )
    parser.add_argument(
        "--no-benchmarks",
        action="store_true",
        help="Exclude configured benchmark/sector dependencies.",
    )
    parser.add_argument(
        "--session",
        type=date.fromisoformat,
        default=None,
        help="Completed market session in YYYY-MM-DD form; defaults to latest complete.",
    )
    parser.add_argument("--requested-by", default="prewarm-cli")
    args = parser.parse_args(argv)
    if args.recent_run_count < 1:
        parser.error("--recent-run-count must be positive")
    args.tickers = tuple(
        ticker.strip().upper() for ticker in args.tickers.split(",") if ticker.strip()
    )
    if args.source == "explicit" and not args.tickers:
        parser.error("--tickers is required when --source explicit")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    request = MarketDataPrewarmRequest(
        universe_source=args.source,
        recent_run_count=args.recent_run_count,
        tickers=args.tickers,
        include_benchmarks=not args.no_benchmarks,
        freshness_date=args.session,
        requested_by=args.requested_by,
    )
    with SessionLocal() as db:
        job, universe = enqueue_market_data_prewarm(db, request)
        db.commit()
        output = {
            "job_id": job.id,
            "status": job.status,
            "coalesced": bool(getattr(job, "_coalesced", False)),
            "request_key": job.request_key,
            "source": universe.source,
            "ticker_count": len(universe.tickers),
            "effective_session": universe.effective_session.isoformat(),
            "bar_size": universe.bar_size,
            "data_types": list(universe.data_types),
            "config_version": universe.config_version,
        }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
