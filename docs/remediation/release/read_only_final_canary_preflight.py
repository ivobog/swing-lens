"""Read-only IB qualification and both-feed diagnostic for a 100-symbol source.

No upload, pipeline admission, database mutation, or order API is used. The
current force-refresh fetch plan supplies each historical request's exact
duration, end time, bar size, and feed; PostgreSQL is transaction-read-only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical  # noqa: E402
from app.services.ib_api import IB, Stock  # noqa: E402
from app.services.ib_data_fetcher import fetch_daily_bars  # noqa: E402
from app.services.ib_fetch_plan_service import build_fetch_plan  # noqa: E402
from app.settings import get_settings  # noqa: E402

FEEDS = ("TRADES", "ADJUSTED_LAST")


def _source_symbols(path: Path) -> tuple[list[str], str]:
    raw = path.read_bytes()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        symbols = [str(row["Symbol"]).strip().upper() for row in csv.DictReader(handle)]
    if len(symbols) != 100 or len(set(symbols)) != 100 or any(not symbol for symbol in symbols):
        raise ValueError("SOURCE_MUST_HAVE_100_DISTINCT_NONEMPTY_SYMBOLS")
    return symbols, hashlib.sha256(raw).hexdigest()


def _live_identity(contract) -> dict:
    return {
        "conId": int(getattr(contract, "conId", 0) or 0),
        "symbol": getattr(contract, "symbol", None),
        "secType": getattr(contract, "secType", None),
        "exchange": getattr(contract, "exchange", None),
        "primaryExchange": getattr(contract, "primaryExchange", None),
        "currency": getattr(contract, "currency", None),
        "localSymbol": getattr(contract, "localSymbol", None),
        "tradingClass": getattr(contract, "tradingClass", None),
    }


def _request_feed(ib, contract, item, settings) -> dict:
    diagnostics: dict = {}
    request = {
        "duration": item.duration or item.post_resolution_duration,
        "end_datetime": item.request_end_datetime or "",
        "bar_size": item.bar_size,
        "what_to_show": item.what_to_show,
        "use_rth": bool(settings.ib_use_rth),
        "timeout_seconds": float(settings.ib_timeout_seconds),
    }
    if not request["duration"]:
        return {"status": "PLAN_HAS_NO_REQUEST", "bars": 0, "request": request}
    try:
        bars = fetch_daily_bars(
            ib,
            contract,
            item.what_to_show,
            settings=settings,
            duration=request["duration"],
            bar_size=request["bar_size"],
            end_datetime=request["end_datetime"],
            diagnostics=diagnostics,
        )
        status = "AVAILABLE" if bars else "NO_BARS"
        return {
            "status": status,
            "bars": len(bars),
            "request": request,
            "provider_error_codes": [
                event.get("code") for event in diagnostics.get("provider_callbacks", [])
            ],
        }
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "bars": 0,
            "request": request,
            "exception_type": type(exc).__name__,
            "provider_code": getattr(exc, "code", None),
            "provider_classification": getattr(exc, "classification", None),
            "provider_error_codes": [
                event.get("code") for event in diagnostics.get("provider_callbacks", [])
            ],
        }


def _checkpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--client-id", type=int, default=29)
    parser.add_argument("--reference-only", action="store_true")
    args = parser.parse_args()
    if not args.reference_only:
        parser.error("this diagnostic requires explicit --reference-only acknowledgement")
    symbols, source_sha256 = _source_symbols(args.source)
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            plan = build_fetch_plan(db, symbols, include_benchmarks=False, force_refresh=True)
            db.rollback()
    finally:
        engine.dispose()
    items = {(item.ticker, item.what_to_show): item for item in plan.items}
    if len(items) != 200:
        raise ValueError("PRODUCTION_PLAN_DID_NOT_COVER_BOTH_FEEDS_FOR_100_SYMBOLS")
    payload = {
        "schema_version": "reference-universe-ib-preflight-v1",
        "source_path": str(args.source.resolve()),
        "source_sha256": source_sha256,
        "source_freshness": "REFERENCE_ONLY_NOT_FRESH",
        "generated_at": datetime.now(UTC).isoformat(),
        "request_mode": "PRODUCTION_FORCE_REFRESH_PLAN_READ_ONLY",
        "ib_readonly": True,
        "rows": [],
    }
    if args.output.exists():
        prior = json.loads(args.output.read_text(encoding="utf-8"))
        if (
            prior.get("source_sha256") != source_sha256
            or prior.get("source_freshness") != "REFERENCE_ONLY_NOT_FRESH"
        ):
            raise ValueError("EXISTING_CHECKPOINT_SOURCE_MISMATCH")
        payload = prior
    done = {row["symbol"] for row in payload["rows"]}
    ib = IB()
    ib.RequestTimeout = float(settings.ib_timeout_seconds)
    ib.RaiseRequestErrors = True
    try:
        ib.connect(
            settings.ib_host,
            settings.ib_port,
            clientId=args.client_id,
            timeout=float(settings.ib_timeout_seconds),
            readonly=True,
        )
        if not ib.isConnected():
            raise ConnectionError("IB_READONLY_CONNECTION_NOT_READY")
        for index, symbol in enumerate(symbols, start=1):
            if symbol in done:
                continue
            row = {"symbol": symbol, "qualification_status": "UNAVAILABLE"}
            try:
                qualified = list(ib.qualifyContracts(Stock(symbol, "SMART", "USD")))
                if len(qualified) != 1:
                    row["qualification_status"] = f"EXPECTED_ONE_GOT_{len(qualified)}"
                else:
                    contract = qualified[0]
                    identity = _live_identity(contract)
                    row["qualification_status"] = "QUALIFIED"
                    row["contract"] = identity
                    row["contract_fingerprint"] = Canonical.fingerprint(identity)
                    row["plan_contract_fingerprints"] = {
                        feed: items[(symbol, feed)].contract_fingerprint for feed in FEEDS
                    }
                    for feed in FEEDS:
                        row[feed] = _request_feed(ib, contract, items[(symbol, feed)], settings)
                        # Match the production historical request pacing across
                        # tickers and feeds, including failed provider responses.
                        ib.sleep(float(settings.ib_min_seconds_between_requests))
            except Exception as exc:
                row["qualification_status"] = "UNAVAILABLE"
                row["qualification_exception_type"] = type(exc).__name__
            if row["qualification_status"] != "QUALIFIED":
                for feed in FEEDS:
                    row[feed] = {"status": "NOT_REQUESTED_NO_CONTRACT", "bars": 0}
            row["candidate_semantics"] = (
                "INCLUDE_WITH_TYPED_INSUFFICIENCY"
                if any(row[feed]["status"] != "AVAILABLE" for feed in FEEDS)
                else "INCLUDE"
            )
            row["failure_class"] = (
                "NONE"
                if row["candidate_semantics"] == "INCLUDE"
                else "IB_EXTERNAL_CONTRACT_OR_HISTORICAL_UNAVAILABLE"
            )
            payload["rows"].append(row)
            _checkpoint(args.output, payload)
            print(
                f"{index}/100 {symbol} qualification={row['qualification_status']} "
                f"TRADES={row['TRADES']['status']} "
                f"ADJUSTED_LAST={row['ADJUSTED_LAST']['status']}",
                flush=True,
            )
    finally:
        ib.disconnect()
    return 0 if len(payload["rows"]) == 100 else 2


if __name__ == "__main__":
    raise SystemExit(main())
