"""Audited Winner IB request-scope reconstruction and bounded canary tooling.

Planning and auditing are read-only. ``execute-v3`` requires an exact reviewed
artifact hash and explicit write approval. This script never invokes Winner
maturation, cohort generation, rescoring, estimate generation, or publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.models.tables import (
    IBContract,
    IBFetchItem,
    IBFetchRun,
    PriceBar,
    PriceBarRevision,
    WinnerMarketDataObligation,
)
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import (
    FetchAction,
    FetchPlan,
    build_fetch_plan,
)
from app.services.ib_fetch_recovery_service import (
    apply_interrupted_canary_finalization,
    build_interrupted_canary_finalization_manifest,
)
from app.services.ib_historical_request_scope import build_historical_request_scope
from app.services.us_market_calendar import latest_completed_us_trading_day
from app.services.winner_probability.market_data_obligation_service import (
    MarketDataObligationService,
)
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)
from app.settings import get_settings

SCHEMA_V1_AUDIT = "swinglens-winner-ib-request-scope-v1-audit"
SCHEMA_V3_CANARY = "swinglens-winner-ib-request-scope-v3-canary"
SCHEMA_V3_RESULT = "swinglens-winner-ib-request-scope-v3-result"
RECOVERY_MANIFEST_HASH = "f74cdd39c79527573e92e7089a682dda9d780203939d3f247da88fff41f4388b"
BASES = ("TRADES", "ADJUSTED_LAST")


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_manifest_bytes(value)).hexdigest()


def _write_immutable(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(canonicalize_manifest_value(payload), indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != rendered:
        raise RuntimeError(f"immutable artifact already exists with different bytes: {path}")
    path.write_text(rendered, encoding="utf-8")
    return path


def _bar_records(db, ticker: str, basis: str) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            PriceBar.id,
            PriceBar.bar_date,
            PriceBar.data_hash,
            PriceBar.revision_count,
        )
        .where(PriceBar.ticker == ticker)
        .where(PriceBar.timeframe == "1 day")
        .where(PriceBar.what_to_show == basis)
        .order_by(PriceBar.bar_date, PriceBar.id)
    ).all()
    return [
        {
            "id": int(row.id),
            "bar_date": row.bar_date,
            "data_hash": row.data_hash,
            "revision_count": int(row.revision_count or 0),
        }
        for row in rows
    ]


def _region_hashes(
    db,
    *,
    ticker: str,
    basis: str,
    reviewed_start: date,
    reviewed_end: date,
) -> dict[str, dict[str, Any]]:
    all_rows = _bar_records(db, ticker, basis)
    regions = {
        "before": [row for row in all_rows if row["bar_date"] < reviewed_start],
        "inside": [row for row in all_rows if reviewed_start <= row["bar_date"] <= reviewed_end],
        "after": [row for row in all_rows if row["bar_date"] > reviewed_end],
    }
    return {name: {"count": len(rows), "sha256": _hash(rows)} for name, rows in regions.items()}


def audit_v1(fetch_run_id: int, output_dir: Path) -> Path:
    use_rth = get_settings().ib_use_rth
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        fetch_items = list(
            db.scalars(
                select(IBFetchItem)
                .where(IBFetchItem.fetch_run_id == fetch_run_id)
                .order_by(IBFetchItem.ticker, IBFetchItem.what_to_show)
            )
        )
        requests: list[dict[str, Any]] = []
        classifications: Counter[str] = Counter()
        revisions: list[dict[str, Any]] = []
        for item in fetch_items:
            contract = db.scalar(select(IBContract).where(IBContract.ticker == item.ticker))
            touched = list(
                db.scalars(
                    select(PriceBar)
                    .where(PriceBar.ticker == item.ticker)
                    .where(PriceBar.what_to_show == item.what_to_show)
                    .where(PriceBar.timeframe == item.bar_size)
                    .where(PriceBar.last_seen_at >= item.started_at)
                    .where(PriceBar.last_seen_at <= item.completed_at)
                    .order_by(PriceBar.bar_date)
                )
            )
            metadata = item.decision_metadata_json or {}
            old_start = date.fromisoformat(metadata["request_start_date"])
            old_end = date.fromisoformat(metadata["request_end_date"])
            modeled = build_historical_request_scope(
                required_start_date=(
                    date.fromisoformat(metadata["missing_start_date"])
                    if metadata.get("missing_start_date")
                    else None
                ),
                required_end_date=(
                    date.fromisoformat(metadata["missing_end_date"])
                    if metadata.get("missing_end_date")
                    else None
                ),
                duration=str(item.duration),
                bar_size=item.bar_size,
                what_to_show=item.what_to_show,
                reviewed_end_date=old_end,
            )
            requests.append(
                {
                    "fetch_item_id": int(item.id),
                    "ticker": item.ticker,
                    "ib_conid": getattr(contract, "ib_conid", None),
                    "what_to_show": item.what_to_show,
                    "bar_size": item.bar_size,
                    "duration": item.duration,
                    "end_datetime": "",
                    "use_rth": use_rth,
                    "format_date": 1,
                    "advertised_start": old_start,
                    "advertised_end": old_end,
                    "modeled_provider_start": modeled.reviewed_start_date,
                    "actual_start": min((row.bar_date for row in touched), default=None),
                    "actual_end": max((row.bar_date for row in touched), default=None),
                    "actual_count": len(touched),
                    "fetched": int(item.fetched),
                    "inserted": int(item.inserted),
                    "revised": int(item.revised),
                    "unchanged": int(item.unchanged),
                    "advertised_start_delta_days": (
                        old_start - min(row.bar_date for row in touched)
                    ).days,
                }
            )
            for revision in db.scalars(
                select(PriceBarRevision)
                .where(PriceBarRevision.fetch_item_id == item.id)
                .order_by(PriceBarRevision.bar_date, PriceBarRevision.id)
            ):
                current = db.get(PriceBar, revision.price_bar_id)
                valid_ohlc = _valid_ohlc(revision.new_values_json)
                legitimate = bool(
                    current is not None
                    and revision.source == "IB"
                    and revision.previous_data_hash != revision.new_data_hash
                    and revision.new_data_hash == current.data_hash
                    and modeled.reviewed_start_date
                    <= revision.bar_date
                    <= modeled.reviewed_end_date
                    and valid_ohlc
                )
                classification = "LEGITIMATE_PROVIDER_REVISION" if legitimate else "UNRESOLVED"
                classifications[classification] += 1
                revisions.append(
                    {
                        "revision_id": int(revision.id),
                        "fetch_item_id": int(item.id),
                        "ticker": revision.ticker,
                        "what_to_show": revision.what_to_show,
                        "bar_date": revision.bar_date,
                        "old_hash": revision.previous_data_hash,
                        "new_hash": revision.new_data_hash,
                        "old_ohlcv": revision.previous_values_json,
                        "new_ohlcv": revision.new_values_json,
                        "old_advertised_start": old_start,
                        "modeled_provider_start": modeled.reviewed_start_date,
                        "classification": classification,
                    }
                )
        payload: dict[str, Any] = {
            "schema": SCHEMA_V1_AUDIT,
            "fetch_run_id": fetch_run_id,
            "request_count": len(requests),
            "revision_count": len(revisions),
            "revision_classifications": dict(sorted(classifications.items())),
            "requests": requests,
            "revisions": revisions,
        }
        payload["artifact_hash"] = _hash(payload)
        path = (
            output_dir / f"run_{fetch_run_id}_request_scope_audit_{payload['artifact_hash']}.json"
        )
        _write_immutable(path, payload)
        print(json.dumps({"path": str(path.resolve()), **_summary(payload)}, indent=2))
        return path


def _valid_ohlc(values: dict[str, Any]) -> bool:
    try:
        open_value = float(values["open"])
        high = float(values["high"])
        low = float(values["low"])
        close = float(values["close"])
    except (KeyError, TypeError, ValueError):
        return False
    return low <= min(open_value, close) <= max(open_value, close) <= high


def _selected_plan(db, tickers: list[str]) -> FetchPlan:
    plan = build_fetch_plan(
        db=db,
        tickers=tickers,
        include_benchmarks=False,
        what_to_show_values=BASES,
    )
    requested = {ticker.upper() for ticker in tickers}
    items = [item for item in plan.items if item.ticker in requested]
    if len(items) != len(requested) * len(BASES):
        raise RuntimeError("selected canary did not produce exactly two basis requests per ticker")
    if any(
        item.action not in {FetchAction.TOP_UP_RECENT, FetchAction.REFRESH_RECENT} for item in items
    ):
        actions = Counter(item.action.value for item in items)
        raise RuntimeError(f"canary contains unexpected fetch actions: {dict(actions)}")
    return replace(
        plan,
        items=items,
        symbols_including_benchmarks=sorted(requested),
        estimated_request_count=sum(item.estimated_request_count for item in items),
        estimated_full_backfills=0,
        estimated_top_ups=sum(item.action == FetchAction.TOP_UP_RECENT for item in items),
        estimated_refreshes=sum(item.action == FetchAction.REFRESH_RECENT for item in items),
        estimated_skips=0,
    )


def _preflight_payload(db, tickers: list[str]) -> tuple[dict[str, Any], FetchPlan]:
    if "CLBK" in {ticker.upper() for ticker in tickers}:
        raise RuntimeError("CLBK is excluded from request-scope canaries")
    plan = _selected_plan(db, tickers)
    obligations = list(
        db.scalars(
            select(WinnerMarketDataObligation)
            .where(WinnerMarketDataObligation.ticker_snapshot.in_(tickers))
            .where(WinnerMarketDataObligation.status == "FETCH_REQUIRED")
            .order_by(
                WinnerMarketDataObligation.ticker_snapshot,
                WinnerMarketDataObligation.what_to_show,
                WinnerMarketDataObligation.forward_outcome_id,
            )
        )
    )
    by_key: dict[tuple[str, str], list[WinnerMarketDataObligation]] = defaultdict(list)
    for row in obligations:
        by_key[(row.ticker_snapshot, row.what_to_show)].append(row)
    records: list[dict[str, Any]] = []
    for item in sorted(plan.items, key=lambda value: (value.ticker, value.what_to_show)):
        related = by_key[(item.ticker, item.what_to_show)]
        if not related:
            raise RuntimeError(
                f"no FETCH_REQUIRED obligations for {item.ticker}/{item.what_to_show}"
            )
        contract = db.scalar(select(IBContract).where(IBContract.ticker == item.ticker))
        if contract is None or contract.resolution_status != "RESOLVED":
            raise RuntimeError(f"canonical contract is not resolved for {item.ticker}")
        if any(not _identity_matches(row, contract) for row in related):
            raise RuntimeError(
                f"obligation identity differs from canonical contract for {item.ticker}"
            )
        if item.request_start_date is None or item.request_end_date is None:
            raise RuntimeError("fetch plan does not contain a reviewed request footprint")
        present_dates = _present_dates(db, item.ticker, item.what_to_show)
        required_dates = sorted(
            {
                date.fromisoformat(value)
                for row in related
                for value in row.required_sessions_json
                if date.fromisoformat(value) not in present_dates
            }
        )
        records.append(
            {
                "ticker": item.ticker,
                "contract": _contract_snapshot(contract),
                "what_to_show": item.what_to_show,
                "bar_size": item.bar_size,
                "duration": item.duration,
                "end_datetime": item.request_end_datetime,
                "end_mode": item.request_end_mode,
                "reviewed_session_expiry": item.reviewed_session_expiry,
                "required_missing_dates": required_dates,
                "required_missing_start": min(required_dates),
                "required_missing_end": max(required_dates),
                "reviewed_start": item.request_start_date,
                "reviewed_end": item.request_end_date,
                "associated_outcome_ids": sorted({int(row.forward_outcome_id) for row in related}),
                "associated_prediction_ids": sorted({int(row.prediction_id) for row in related}),
                "before_region_hashes": _region_hashes(
                    db,
                    ticker=item.ticker,
                    basis=item.what_to_show,
                    reviewed_start=item.request_start_date,
                    reviewed_end=item.request_end_date,
                ),
            }
        )
    payload: dict[str, Any] = {
        "schema": SCHEMA_V3_CANARY,
        "recovery_manifest_hash": RECOVERY_MANIFEST_HASH,
        "completed_session": latest_completed_us_trading_day(),
        "tickers": sorted({ticker.upper() for ticker in tickers}),
        "request_count": len(records),
        "items": records,
    }
    payload["artifact_hash"] = _hash(payload)
    return payload, plan


def plan_v3(tickers: list[str], output_dir: Path) -> Path:
    tickers = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not 8 <= len(tickers) <= 12:
        raise RuntimeError("canary v3 requires 8-12 distinct tickers")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        payload, _ = _preflight_payload(db, tickers)
        path = output_dir / f"canary_v3_reviewed_{payload['artifact_hash']}.json"
        _write_immutable(path, payload)
        print(json.dumps({"path": str(path.resolve()), **_summary(payload)}, indent=2))
        return path


def execute_v3(path: Path, *, expected_hash: str, approve_write: bool) -> int:
    if not approve_write:
        raise RuntimeError("--approve-write is required")
    reviewed = json.loads(path.read_text(encoding="utf-8"))
    if reviewed.get("schema") != SCHEMA_V3_CANARY:
        raise RuntimeError("unsupported canary artifact schema")
    if reviewed.get("artifact_hash") != expected_hash:
        raise RuntimeError("reviewed canary hash mismatch")
    unhashed = {key: value for key, value in reviewed.items() if key != "artifact_hash"}
    if _hash(unhashed) != expected_hash:
        raise RuntimeError("reviewed canary artifact bytes do not match its hash")
    with SessionLocal() as db:
        live, plan = _preflight_payload(db, reviewed["tickers"])
        if canonical_manifest_bytes(live) != canonical_manifest_bytes(reviewed):
            raise RuntimeError(
                "live request plan or preflight hashes differ from reviewed artifact"
            )
        fetch_run = execute_fetch_plan(
            db=db,
            plan=plan,
            include_benchmarks=False,
            force_refresh=False,
            force_full_backfill=False,
        )
        print(json.dumps({"fetch_run_id": int(fetch_run.id), "status": fetch_run.status}, indent=2))
        return int(fetch_run.id)


def verify_v3(path: Path, fetch_run_id: int, output_dir: Path) -> Path:
    reviewed = json.loads(path.read_text(encoding="utf-8"))
    by_key = {(row["ticker"], row["what_to_show"]): row for row in reviewed["items"]}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        fetch_run = db.get(IBFetchRun, fetch_run_id)
        if fetch_run is None:
            raise RuntimeError("canary fetch run was not found")
        results: list[dict[str, Any]] = []
        for item in sorted(fetch_run.items, key=lambda value: (value.ticker, value.what_to_show)):
            key = (item.ticker, item.what_to_show)
            reviewed_item = by_key.get(key)
            if reviewed_item is None:
                raise RuntimeError(f"unreviewed request was executed: {key}")
            metadata = item.decision_metadata_json or {}
            post = _region_hashes(
                db,
                ticker=item.ticker,
                basis=item.what_to_show,
                reviewed_start=date.fromisoformat(reviewed_item["reviewed_start"]),
                reviewed_end=date.fromisoformat(reviewed_item["reviewed_end"]),
            )
            before = reviewed_item["before_region_hashes"]
            outside_unchanged = (
                before["before"] == post["before"] and before["after"] == post["after"]
            )
            parameter_match = all(
                (
                    item.duration == reviewed_item["duration"],
                    item.bar_size == reviewed_item["bar_size"],
                    metadata.get("request_end_datetime") == reviewed_item["end_datetime"],
                    metadata.get("request_end_mode") == reviewed_item["end_mode"],
                    metadata.get("reviewed_session_expiry")
                    == reviewed_item["reviewed_session_expiry"],
                    metadata.get("reviewed_start_date") == reviewed_item["reviewed_start"],
                    metadata.get("reviewed_end_date") == reviewed_item["reviewed_end"],
                )
            )
            passed = bool(
                item.status == "SUCCESS"
                and metadata.get("boundary_status") == "PASS"
                and metadata.get("provider_result") == "SUCCESS_WITH_BARS"
                and parameter_match
                and outside_unchanged
            )
            results.append(
                {
                    "fetch_item_id": int(item.id),
                    "ticker": item.ticker,
                    "what_to_show": item.what_to_show,
                    "duration": item.duration,
                    "end_datetime": metadata.get("request_end_datetime"),
                    "end_mode": metadata.get("request_end_mode"),
                    "reviewed_session_expiry": metadata.get("reviewed_session_expiry"),
                    "provider_result": metadata.get("provider_result"),
                    "reviewed_start": metadata.get("reviewed_start_date"),
                    "actual_start": metadata.get("actual_start_date"),
                    "actual_end": metadata.get("actual_end_date"),
                    "reviewed_end": metadata.get("reviewed_end_date"),
                    "boundary_status": metadata.get("boundary_status"),
                    "parameter_match": parameter_match,
                    "outside_hashes_unchanged": outside_unchanged,
                    "post_region_hashes": post,
                    "fetched": int(item.fetched),
                    "inserted": int(item.inserted),
                    "revised": int(item.revised),
                    "unchanged": int(item.unchanged),
                    "passed": passed,
                }
            )
        if len(results) != len(reviewed["items"]) or not all(row["passed"] for row in results):
            raise RuntimeError("canary boundary, parameter, or outside-hash verification failed")
        outcome_ids = {
            value for row in reviewed["items"] for value in row["associated_outcome_ids"]
        }
        status_counts = dict(
            db.execute(
                select(
                    WinnerMarketDataObligation.status,
                    func.count(WinnerMarketDataObligation.id),
                )
                .where(WinnerMarketDataObligation.forward_outcome_id.in_(outcome_ids))
                .group_by(WinnerMarketDataObligation.status)
            ).all()
        )
        ready = db.scalar(
            text(
                """
                SELECT count(*) FROM (
                  SELECT forward_outcome_id
                  FROM winner_market_data_obligations
                  WHERE forward_outcome_id = ANY(:outcome_ids)
                  GROUP BY forward_outcome_id
                  HAVING count(*)=2 AND bool_and(status='SATISFIED')
                ) ready
                """
            ),
            {"outcome_ids": sorted(outcome_ids)},
        )
        payload: dict[str, Any] = {
            "schema": SCHEMA_V3_RESULT,
            "reviewed_artifact_hash": reviewed["artifact_hash"],
            "fetch_run_id": fetch_run_id,
            "fetch_run_status": fetch_run.status,
            "request_count": len(results),
            "totals": {
                "fetched": sum(row["fetched"] for row in results),
                "inserted": sum(row["inserted"] for row in results),
                "revised": sum(row["revised"] for row in results),
                "unchanged": sum(row["unchanged"] for row in results),
                "failed": sum(not row["passed"] for row in results),
            },
            "associated_outcome_count": len(outcome_ids),
            "maturation_ready_count": int(ready or 0),
            "obligation_status_counts": status_counts,
            "items": results,
        }
        payload["artifact_hash"] = _hash(payload)
        result_path = output_dir / f"canary_v3_result_{payload['artifact_hash']}.json"
        _write_immutable(result_path, payload)
        print(json.dumps({"path": str(result_path.resolve()), **_summary(payload)}, indent=2))
        return result_path


def dry_run_remaining() -> None:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        needs = MarketDataObligationService().recovery_needs(db)
        tickers = sorted({row.ticker for row in needs})
        plan = _selected_plan(db, tickers) if tickers else None
        items = plan.items if plan else []
        print(
            json.dumps(
                {
                    "remaining_outcomes": len({row.outcome_id for row in needs}),
                    "remaining_obligations": len(needs),
                    "distinct_contracts": len({row.contract_id for row in needs}),
                    "planned_provider_requests": len(items),
                    "basis_counts": dict(Counter(row.what_to_show for row in items)),
                    "required_start": min(
                        (session for row in needs for session in row.missing_sessions), default=None
                    ),
                    "required_end": max(
                        (session for row in needs for session in row.missing_sessions), default=None
                    ),
                    "reviewed_start": min(
                        (row.request_start_date for row in items if row.request_start_date),
                        default=None,
                    ),
                    "reviewed_end": max(
                        (row.request_end_date for row in items if row.request_end_date),
                        default=None,
                    ),
                },
                indent=2,
                default=str,
            )
        )


def plan_interrupted_finalization(fetch_run_id: int, output_dir: Path) -> Path:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        payload = build_interrupted_canary_finalization_manifest(
            db,
            fetch_run_id=fetch_run_id,
        )
        path = output_dir / (
            f"run_{fetch_run_id}_finalization_{payload['manifest_hash']}.json"
        )
        _write_immutable(path, payload)
        print(
            json.dumps(
                {
                    "path": str(path.resolve()),
                    "manifest_hash": payload["manifest_hash"],
                    "fetch_run_id": fetch_run_id,
                    "expected_run_status": payload["expected_run_status"],
                    "expected_totals": payload["expected_totals"],
                    "unmaterialized_request_count": payload[
                        "unmaterialized_request_count"
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return path


def apply_interrupted_finalization(
    path: Path,
    *,
    expected_hash: str,
    actor: str,
    request_key: str,
    approve_write: bool,
) -> None:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        result = apply_interrupted_canary_finalization(
            db,
            manifest=manifest,
            reviewed_manifest_hash=expected_hash,
            actor=actor,
            request_key=request_key,
            approve_write=approve_write,
        )
        db.commit()
        print(json.dumps(result.__dict__, indent=2, sort_keys=True))


def reconcile_successful_run_storage(
    fetch_run_id: int,
    *,
    approve_write: bool,
) -> None:
    if not approve_write:
        raise RuntimeError("--approve-write is required")
    with SessionLocal() as db:
        fetch_run = db.get(IBFetchRun, fetch_run_id)
        if fetch_run is None or fetch_run.status not in {"COMPLETED", "PARTIAL", "FAILED"}:
            raise RuntimeError("fetch run must exist and be terminal before reconciliation")
        tickers = sorted(
            {
                str(item.ticker).upper()
                for item in fetch_run.items
                if item.status == "SUCCESS" and int(item.fetched or 0) > 0
            }
        )
        if not tickers:
            raise RuntimeError("fetch run has no successful bar-bearing items to reconcile")
        result = MarketDataObligationService().evaluate(db, tickers=tickers)
        db.commit()
        print(
            json.dumps(
                {"fetch_run_id": fetch_run_id, "tickers": tickers, **result.__dict__},
                indent=2,
                sort_keys=True,
            )
        )


def _present_dates(db, ticker: str, basis: str) -> set[date]:
    return set(
        db.scalars(
            select(PriceBar.bar_date)
            .where(PriceBar.ticker == ticker)
            .where(PriceBar.timeframe == "1 day")
            .where(PriceBar.what_to_show == basis)
        )
    )


def _contract_snapshot(contract: IBContract) -> dict[str, Any]:
    return {
        "id": int(contract.id),
        "ib_conid": int(contract.ib_conid),
        "symbol": contract.symbol,
        "local_symbol": contract.local_symbol,
        "exchange": contract.exchange,
        "primary_exchange": contract.primary_exchange,
        "currency": contract.currency,
        "sec_type": contract.sec_type,
        "trading_class": contract.trading_class,
    }


def _identity_matches(obligation: WinnerMarketDataObligation, contract: IBContract) -> bool:
    return all(
        (
            obligation.ib_contract_id == contract.id,
            obligation.ib_conid_snapshot == contract.ib_conid,
            obligation.symbol_snapshot == contract.symbol,
            obligation.local_symbol_snapshot == contract.local_symbol,
            obligation.exchange_snapshot == contract.exchange,
            obligation.primary_exchange_snapshot == contract.primary_exchange,
            obligation.currency_snapshot == contract.currency,
            obligation.sec_type_snapshot == contract.sec_type,
            obligation.trading_class_snapshot == contract.trading_class,
        )
    )


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            "artifact_hash",
            "request_count",
            "revision_count",
            "revision_classifications",
            "fetch_run_id",
            "fetch_run_status",
            "totals",
            "associated_outcome_count",
            "maturation_ready_count",
            "obligation_status_counts",
        )
        if key in payload
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit-v1")
    audit.add_argument("--fetch-run-id", type=int, default=190)
    audit.add_argument("--output-dir", type=Path, required=True)
    plan = sub.add_parser("plan-v3")
    plan.add_argument("--tickers", nargs="+", required=True)
    plan.add_argument("--output-dir", type=Path, required=True)
    execute = sub.add_parser("execute-v3")
    execute.add_argument("--artifact", type=Path, required=True)
    execute.add_argument("--expected-hash", required=True)
    execute.add_argument("--approve-write", action="store_true")
    verify = sub.add_parser("verify-v3")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--fetch-run-id", type=int, required=True)
    verify.add_argument("--output-dir", type=Path, required=True)
    finalization_plan = sub.add_parser("plan-interrupted-finalization")
    finalization_plan.add_argument("--fetch-run-id", type=int, required=True)
    finalization_plan.add_argument("--output-dir", type=Path, required=True)
    finalization_apply = sub.add_parser("apply-interrupted-finalization")
    finalization_apply.add_argument("--artifact", type=Path, required=True)
    finalization_apply.add_argument("--expected-hash", required=True)
    finalization_apply.add_argument("--actor", required=True)
    finalization_apply.add_argument("--request-key", required=True)
    finalization_apply.add_argument("--approve-write", action="store_true")
    reconcile = sub.add_parser("reconcile-run-storage")
    reconcile.add_argument("--fetch-run-id", type=int, required=True)
    reconcile.add_argument("--approve-write", action="store_true")
    sub.add_parser("dry-run-remaining")
    args = parser.parse_args()
    if args.command == "audit-v1":
        audit_v1(args.fetch_run_id, args.output_dir)
    elif args.command == "plan-v3":
        plan_v3(args.tickers, args.output_dir)
    elif args.command == "execute-v3":
        execute_v3(
            args.artifact,
            expected_hash=args.expected_hash,
            approve_write=args.approve_write,
        )
    elif args.command == "verify-v3":
        verify_v3(args.artifact, args.fetch_run_id, args.output_dir)
    elif args.command == "plan-interrupted-finalization":
        plan_interrupted_finalization(args.fetch_run_id, args.output_dir)
    elif args.command == "apply-interrupted-finalization":
        apply_interrupted_finalization(
            args.artifact,
            expected_hash=args.expected_hash,
            actor=args.actor,
            request_key=args.request_key,
            approve_write=args.approve_write,
        )
    elif args.command == "reconcile-run-storage":
        reconcile_successful_run_storage(
            args.fetch_run_id,
            approve_write=args.approve_write,
        )
    else:
        dry_run_remaining()


if __name__ == "__main__":
    main()
