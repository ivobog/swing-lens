"""Explicit, opt-in live capability smoke test for IBKR Market Intelligence.

This script never places, modifies, cancels, or requests broker orders/executions.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import SessionLocal
from app.services.ib_connection import create_ib_client
from app.services.ib_contract_resolver import resolve_us_stock_contract
from app.services.ib_market_intelligence.adapters import (
    IBHistogramClient,
    IBHistoricalMetricClient,
    IBLiveSnapshotManager,
    IBScannerClient,
    capability_status_from_error,
)
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.ib_market_intelligence.enums import HistoricalMetricType
from app.services.ib_market_intelligence.flex import flex_client_from_settings, parse_flex_report
from app.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run opt-in live IBMI capability validation")
    parser.add_argument(
        "--module",
        required=True,
        choices=(
            "liquidity",
            "short-pressure",
            "volatility",
            "options",
            "scanner",
            "histogram",
            "flex",
        ),
    )
    parser.add_argument("--tickers", default="AAPL", help="Comma-separated tiny validation set")
    parser.add_argument("--flex-query-type", choices=("trade", "activity"), default="trade")
    args = parser.parse_args()
    settings = get_settings()
    config = load_ib_market_intelligence_config(settings=settings)
    tickers = [item.strip().upper() for item in args.tickers.split(",") if item.strip()][:3]
    if args.module == "flex":
        query_id = (
            settings.ib_flex_trade_query_id
            if args.flex_query_type == "trade"
            else settings.ib_flex_activity_query_id
        )
        if not query_id:
            print(
                json.dumps(
                    {"module": "flex", "status": "UNAVAILABLE", "reason": "query ID not configured"}
                )
            )
            return 2
        client = flex_client_from_settings(settings)
        try:
            _reference, content = client.download(
                query_id,
                attempts=settings.ib_flex_poll_attempts,
                poll_seconds=settings.ib_flex_poll_seconds,
            )
            rows = parse_flex_report(
                content, report_timezone=settings.ib_flex_report_timezone
            )
            print(json.dumps({"module": "flex", "status": "AVAILABLE", "rows": len(rows)}))
            return 0
        except Exception as exc:
            status, reason = capability_status_from_error(exc)
            print(json.dumps({"module": "flex", "status": status, "reason": reason[:300]}))
            return 2

    ib = create_ib_client()
    results: list[dict[str, Any]] = []
    try:
        ib.connect(
            settings.ib_host,
            settings.ib_port,
            clientId=settings.ib_client_id,
            timeout=settings.ib_timeout_seconds,
            readonly=True,
        )
        with SessionLocal() as db:
            if args.module == "scanner":
                preset = config.scanner_presets[0]
                client = IBScannerClient(ib)
                parameters = client.parameters()
                if preset.scan_code not in parameters:
                    raise RuntimeError(f"{preset.scan_code} not present in scanner parameters")
                rows = client.run(preset)
                results.append({"preset": preset.name, "status": "AVAILABLE", "rows": len(rows)})
            else:
                for ticker in tickers:
                    resolution = resolve_us_stock_contract(db, ticker, ib)
                    if not resolution.contract:
                        results.append(
                            {
                                "ticker": ticker,
                                "status": "NOT_SUPPORTED",
                                "reason": resolution.error_message,
                            }
                        )
                        continue
                    try:
                        if args.module == "liquidity":
                            rows = IBHistoricalMetricClient(ib, settings=settings).fetch(
                                resolution.contract, HistoricalMetricType.BID_ASK, duration="5 D"
                            )
                        elif args.module == "short-pressure":
                            rows = IBHistoricalMetricClient(ib, settings=settings).fetch(
                                resolution.contract, HistoricalMetricType.FEE_RATE, duration="5 D"
                            )
                            live = IBLiveSnapshotManager(ib).capture(
                                resolution.contract, "SHORTABLE"
                            )
                            results.append(
                                {
                                    "ticker": ticker,
                                    "live_status": live.availability_status,
                                    "live_fields": sorted(live.values),
                                }
                            )
                        elif args.module == "volatility":
                            rows = []
                            client = IBHistoricalMetricClient(ib, settings=settings)
                            for metric in (
                                HistoricalMetricType.HISTORICAL_VOLATILITY,
                                HistoricalMetricType.OPTION_IMPLIED_VOLATILITY,
                            ):
                                rows.extend(
                                    client.fetch(resolution.contract, metric, duration="5 D")
                                )
                        elif args.module == "options":
                            live = IBLiveSnapshotManager(ib).capture(
                                resolution.contract, "OPTIONS_ACTIVITY"
                            )
                            results.append(
                                {
                                    "ticker": ticker,
                                    "status": live.availability_status,
                                    "fields": sorted(live.values),
                                }
                            )
                            continue
                        else:
                            rows = IBHistogramClient(ib).fetch(
                                resolution.contract,
                                use_rth=bool(config.section("histogram").get("use_rth", True)),
                                period=str(config.section("histogram").get("period", "20 days")),
                            )
                        results.append(
                            {
                                "ticker": ticker,
                                "status": "AVAILABLE" if rows else "UNAVAILABLE",
                                "rows": len(rows),
                            }
                        )
                    except Exception as exc:
                        status, reason = capability_status_from_error(exc)
                        results.append({"ticker": ticker, "status": status, "reason": reason[:300]})
            db.rollback()
    finally:
        if ib.isConnected():
            ib.disconnect()
    print(json.dumps({"module": args.module, "results": results}, default=str))
    return (
        0
        if all(item.get("status", item.get("live_status")) == "AVAILABLE" for item in results)
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
