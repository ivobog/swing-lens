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
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.models.tables import (
    IBContract,
    IBFetchItem,
    IBFetchRun,
    PriceBar,
    PriceBarRevision,
    PriceSeriesVersion,
    WinnerMarketDataObligation,
    WinnerPredictionSnapshot,
    WinnerTemporalValidityDecision,
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
from app.services.winner_probability.temporal_eligibility import (
    load_current_temporal_decisions,
    prediction_temporally_eligible,
)
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)
from app.settings import get_settings

SCHEMA_V1_AUDIT = "swinglens-winner-ib-request-scope-v1-audit"
SCHEMA_V3_CANARY = "swinglens-winner-ib-request-scope-v3-canary"
SCHEMA_V3_RESULT = "swinglens-winner-ib-request-scope-v3-result"
SCHEMA_RECOVERY_MASTER = "swinglens-winner-ib-recovery-master-v1"
SCHEMA_RECOVERY_BATCH = "swinglens-winner-ib-recovery-batch-v1"
SCHEMA_RECOVERY_BATCH_RESULT = "swinglens-winner-ib-recovery-batch-result-v1"
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


def _series_version_snapshot(row: PriceSeriesVersion | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": int(row.id),
        "series_version": int(row.series_version),
        "bar_count": int(row.bar_count),
        "first_bar_date": row.first_bar_date,
        "latest_bar_date": row.latest_bar_date,
    }


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


def _selected_plan(
    db,
    tickers: list[str],
    *,
    required_keys: set[tuple[str, str]] | None = None,
) -> FetchPlan:
    plan = build_fetch_plan(
        db=db,
        tickers=tickers,
        include_benchmarks=False,
        what_to_show_values=BASES,
    )
    requested = {ticker.upper() for ticker in tickers}
    expected_keys = required_keys or {
        (ticker, basis) for ticker in requested for basis in BASES
    }
    items = [
        item for item in plan.items if (item.ticker, item.what_to_show) in expected_keys
    ]
    if {(item.ticker, item.what_to_show) for item in items} != expected_keys:
        raise RuntimeError("selected plan does not cover every required ticker/basis")
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
    required_keys = {
        (row.ticker_snapshot, row.what_to_show) for row in obligations
    }
    plan = _selected_plan(db, tickers, required_keys=required_keys)
    prediction_ids = {int(row.prediction_id) for row in obligations}
    predictions = {
        int(row.id): row
        for row in db.scalars(
            select(WinnerPredictionSnapshot).where(
                WinnerPredictionSnapshot.id.in_(sorted(prediction_ids))
            )
        )
    }
    decisions = load_current_temporal_decisions(db, prediction_ids)
    invalid = [
        prediction_id
        for prediction_id, prediction in predictions.items()
        if not prediction_temporally_eligible(prediction, decisions.get(prediction_id))
    ]
    if invalid:
        raise RuntimeError(f"recovery batch contains temporally ineligible predictions: {invalid}")
    by_key: dict[tuple[str, str], list[WinnerMarketDataObligation]] = defaultdict(list)
    for row in obligations:
        by_key[(row.ticker_snapshot, row.what_to_show)].append(row)
    series_versions = {
        (row.ticker, row.what_to_show): row
        for row in db.scalars(
            select(PriceSeriesVersion).where(
                PriceSeriesVersion.ticker.in_(sorted({ticker.upper() for ticker in tickers})),
                PriceSeriesVersion.timeframe == "1 day",
            )
        )
    }
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
                "before_price_series_version": _series_version_snapshot(
                    series_versions.get((item.ticker, item.what_to_show))
                ),
                "associated_outcome_ids": sorted({int(row.forward_outcome_id) for row in related}),
                "associated_prediction_ids": sorted({int(row.prediction_id) for row in related}),
                "obligations": [
                    {
                        "obligation_id": int(row.id),
                        "outcome_id": int(row.forward_outcome_id),
                        "prediction_id": int(row.prediction_id),
                        "status": row.status,
                        "required_sessions": list(row.required_sessions_json),
                        "price_series_watermark": row.price_series_watermark,
                    }
                    for row in related
                ],
                "before_region_hashes": _region_hashes(
                    db,
                    ticker=item.ticker,
                    basis=item.what_to_show,
                    reviewed_start=item.request_start_date,
                    reviewed_end=item.request_end_date,
                ),
            }
        )
    quarantine_count = _quarantine_count(db)
    if quarantine_count != 1292:
        raise RuntimeError(f"quarantine count drifted from 1292 to {quarantine_count}")
    active_jobs = int(
        db.scalar(
            text(
                "SELECT count(*) FROM background_jobs "
                "WHERE status IN ('QUEUED', 'RUNNING')"
            )
        )
        or 0
    )
    if active_jobs:
        raise RuntimeError(f"{active_jobs} active jobs prevent controlled recovery")
    payload: dict[str, Any] = {
        "schema": SCHEMA_V3_CANARY,
        "recovery_manifest_hash": RECOVERY_MANIFEST_HASH,
        "completed_session": latest_completed_us_trading_day(),
        "tickers": sorted({ticker.upper() for ticker in tickers}),
        "request_count": len(records),
        "quarantined_prediction_count": quarantine_count,
        "active_job_count": active_jobs,
        "historical_counts": _historical_counts(db),
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
        current_series_versions = {
            (row.ticker, row.what_to_show): row
            for row in db.scalars(
                select(PriceSeriesVersion).where(
                    PriceSeriesVersion.ticker.in_(
                        sorted({row["ticker"] for row in reviewed["items"]})
                    ),
                    PriceSeriesVersion.timeframe == "1 day",
                )
            )
        }
        historical_counts_unchanged = (
            reviewed.get("historical_counts") is None
            or reviewed["historical_counts"] == _historical_counts(db)
        )
        quarantine_unchanged = _quarantine_count(db) == int(
            reviewed.get("quarantined_prediction_count", 1292)
        )
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
            provider_result = metadata.get("provider_result")
            before_series = reviewed_item.get("before_price_series_version")
            after_series = _series_version_snapshot(
                current_series_versions.get((item.ticker, item.what_to_show))
            )
            changed = int(item.inserted or 0) + int(item.revised or 0) > 0
            expected_series_version = (
                (int(before_series["series_version"]) + 1 if before_series else 1)
                if changed
                else (int(before_series["series_version"]) if before_series else None)
            )
            series_version_valid = bool(
                (after_series or {}).get("series_version") == expected_series_version
            )
            successful = bool(
                item.status == "SUCCESS"
                and metadata.get("boundary_status") == "PASS"
                and provider_result == "SUCCESS_WITH_BARS"
            )
            truthful_soft_failure = bool(
                item.status == "FAILED"
                and provider_result in {"PROVIDER_NO_DATA", "PROVIDER_ERROR", "TIMEOUT"}
                and metadata.get("boundary_status") != "FAIL"
            )
            passed = bool(
                (successful or truthful_soft_failure)
                and parameter_match
                and outside_unchanged
                and series_version_valid
                and historical_counts_unchanged
                and quarantine_unchanged
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
                    "provider_result": provider_result,
                    "reviewed_start": metadata.get("reviewed_start_date"),
                    "actual_start": metadata.get("actual_start_date"),
                    "actual_end": metadata.get("actual_end_date"),
                    "reviewed_end": metadata.get("reviewed_end_date"),
                    "boundary_status": metadata.get("boundary_status"),
                    "parameter_match": parameter_match,
                    "outside_hashes_unchanged": outside_unchanged,
                    "post_region_hashes": post,
                    "before_price_series_version": before_series,
                    "after_price_series_version": after_series,
                    "price_series_version_valid": series_version_valid,
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
            "schema": (
                SCHEMA_RECOVERY_BATCH_RESULT
                if reviewed.get("schema") == SCHEMA_RECOVERY_BATCH
                else SCHEMA_V3_RESULT
            ),
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
                "success": sum(
                    row["provider_result"] == "SUCCESS_WITH_BARS" for row in results
                ),
                "provider_no_data": sum(
                    row["provider_result"] == "PROVIDER_NO_DATA" for row in results
                ),
                "provider_rejected": sum(
                    row["provider_result"] == "PROVIDER_REJECTED" for row in results
                ),
                "provider_error": sum(
                    row["provider_result"] == "PROVIDER_ERROR" for row in results
                ),
                "timeout": sum(row["provider_result"] == "TIMEOUT" for row in results),
            },
            "associated_outcome_count": len(outcome_ids),
            "maturation_ready_count": int(ready or 0),
            "obligation_status_counts": status_counts,
            "historical_counts_unchanged": historical_counts_unchanged,
            "quarantine_unchanged": quarantine_unchanged,
            "items": results,
        }
        payload["artifact_hash"] = _hash(payload)
        prefix = (
            f"batch_{int(reviewed['batch_number']):03d}_result"
            if reviewed.get("schema") == SCHEMA_RECOVERY_BATCH
            else "canary_v3_result"
        )
        result_path = output_dir / f"{prefix}_{payload['artifact_hash']}.json"
        _write_immutable(result_path, payload)
        print(json.dumps({"path": str(result_path.resolve()), **_summary(payload)}, indent=2))
        return result_path


def _remaining_master_payload(db) -> dict[str, Any]:
    service = MarketDataObligationService()
    needs = service.recovery_needs(db)
    need_by_key = {(row.outcome_id, row.what_to_show): row for row in needs}
    obligations = list(
        db.scalars(
            select(WinnerMarketDataObligation)
            .where(WinnerMarketDataObligation.status == "FETCH_REQUIRED")
            .order_by(WinnerMarketDataObligation.id)
        )
    )
    if len(obligations) != len(needs):
        raise RuntimeError(
            "FETCH_REQUIRED obligations and live missing-session needs do not reconcile"
        )
    if any(row.ticker_snapshot == "CLBK" for row in obligations):
        raise RuntimeError("CLBK obligation entered the ordinary recovery population")
    prediction_ids = {int(row.prediction_id) for row in obligations}
    predictions = {
        int(row.id): row
        for row in db.scalars(
            select(WinnerPredictionSnapshot).where(
                WinnerPredictionSnapshot.id.in_(sorted(prediction_ids))
            )
        )
    }
    decisions = load_current_temporal_decisions(db, prediction_ids)
    contracts = {
        int(row.id): row
        for row in db.scalars(
            select(IBContract).where(
                IBContract.id.in_(
                    {row.ib_contract_id for row in obligations if row.ib_contract_id}
                )
            )
        )
    }
    records: list[dict[str, Any]] = []
    for obligation in obligations:
        prediction = predictions[int(obligation.prediction_id)]
        if not prediction_temporally_eligible(
            prediction, decisions.get(int(obligation.prediction_id))
        ):
            raise RuntimeError(
                f"obligation {obligation.id} belongs to a temporally ineligible prediction"
            )
        contract = contracts.get(int(obligation.ib_contract_id or 0))
        if contract is None or not _identity_matches(obligation, contract):
            raise RuntimeError(f"obligation {obligation.id} has stale contract identity")
        need = need_by_key.get(
            (int(obligation.forward_outcome_id), obligation.what_to_show)
        )
        if need is None:
            raise RuntimeError(f"obligation {obligation.id} has no current missing sessions")
        records.append(
            {
                "obligation_id": int(obligation.id),
                "outcome_id": int(obligation.forward_outcome_id),
                "prediction_id": int(obligation.prediction_id),
                "ticker": obligation.ticker_snapshot,
                "ib_conid": int(obligation.ib_conid_snapshot),
                "contract": _contract_snapshot(contract),
                "basis": obligation.what_to_show,
                "required_sessions": list(obligation.required_sessions_json),
                "missing_sessions": list(need.missing_sessions),
                "required_missing_start": need.missing_sessions[0],
                "required_missing_end": need.missing_sessions[-1],
                "current_status": obligation.status,
                "price_series_watermark": obligation.price_series_watermark,
            }
        )
    payload: dict[str, Any] = {
        "schema": SCHEMA_RECOVERY_MASTER,
        "reviewed_completed_session": latest_completed_us_trading_day(),
        "recovery_outcome_count": len({row["outcome_id"] for row in records}),
        "obligation_count": len(records),
        "contract_count": len({row["contract"]["id"] for row in records}),
        "quarantined_prediction_count": _quarantine_count(db),
        "clbk_obligation_count": sum(row["ticker"] == "CLBK" for row in records),
        "records": records,
    }
    payload["artifact_hash"] = _hash(payload)
    return payload


def plan_remaining_master(output_dir: Path) -> Path:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        payload = _remaining_master_payload(db)
        path = output_dir / f"recovery_master_{payload['artifact_hash']}.json"
        _write_immutable(path, payload)
        print(json.dumps({"path": str(path.resolve()), **_summary(payload)}, indent=2))
        return path


def _load_master(path: Path, expected_hash: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA_RECOVERY_MASTER:
        raise RuntimeError("unsupported recovery master schema")
    if payload.get("artifact_hash") != expected_hash:
        raise RuntimeError("reviewed recovery master hash mismatch")
    unhashed = {key: value for key, value in payload.items() if key != "artifact_hash"}
    if _hash(unhashed) != expected_hash:
        raise RuntimeError("recovery master contents do not match its hash")
    return payload


def plan_recovery_batch(
    *,
    master_path: Path,
    master_hash: str,
    batch_number: int,
    contract_limit: int,
    output_dir: Path,
) -> Path:
    if not 1 <= contract_limit <= 50:
        raise RuntimeError("contract limit must be between 1 and 50")
    master = _load_master(master_path, master_hash)
    original_obligations = {int(row["obligation_id"]) for row in master["records"]}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        needs = MarketDataObligationService().recovery_needs(db)
        contracts = sorted({(row.contract_id, row.ticker) for row in needs})[:contract_limit]
        if not contracts:
            raise RuntimeError("no remaining recovery contracts")
        tickers = [ticker for _, ticker in contracts]
        payload, _ = _preflight_payload(db, tickers)
        live_obligations = {
            int(obligation["obligation_id"])
            for item in payload["items"]
            for obligation in item["obligations"]
        }
        if not live_obligations.issubset(original_obligations):
            raise RuntimeError("batch contains obligations outside the reviewed recovery master")
        payload.update(
            {
                "schema": SCHEMA_RECOVERY_BATCH,
                "batch_number": batch_number,
                "created_at": datetime.now(UTC),
                "recovery_master_hash": master_hash,
                "contract_count": len(contracts),
                "artifact_expiry_condition": (
                    "latest_completed_session must equal reviewed completed session"
                ),
            }
        )
        payload.pop("artifact_hash", None)
        payload["artifact_hash"] = _hash(payload)
        if int(payload["request_count"]) > 100:
            raise RuntimeError("reviewed batch exceeds 100 provider requests")
        path = output_dir / (
            f"batch_{batch_number:03d}_{payload['artifact_hash']}.json"
        )
        _write_immutable(path, payload)
        print(json.dumps({"path": str(path.resolve()), **_summary(payload)}, indent=2))
        return path


def _live_batch_payload(db, reviewed: dict[str, Any]) -> tuple[dict[str, Any], FetchPlan]:
    live, plan = _preflight_payload(db, reviewed["tickers"])
    for key in (
        "schema",
        "batch_number",
        "created_at",
        "recovery_master_hash",
        "contract_count",
        "artifact_expiry_condition",
    ):
        live[key] = reviewed[key]
    live.pop("artifact_hash", None)
    live["artifact_hash"] = _hash(live)
    return live, plan


def execute_recovery_batch(
    path: Path,
    *,
    expected_hash: str,
    approve_write: bool,
) -> int:
    if not approve_write:
        raise RuntimeError("--approve-write is required")
    reviewed = json.loads(path.read_text(encoding="utf-8"))
    if reviewed.get("schema") != SCHEMA_RECOVERY_BATCH:
        raise RuntimeError("unsupported recovery batch schema")
    if reviewed.get("artifact_hash") != expected_hash:
        raise RuntimeError("reviewed batch hash mismatch")
    unhashed = {key: value for key, value in reviewed.items() if key != "artifact_hash"}
    if _hash(unhashed) != expected_hash:
        raise RuntimeError("reviewed batch contents do not match its hash")
    with SessionLocal() as db:
        live, plan = _live_batch_payload(db, reviewed)
        if canonical_manifest_bytes(live) != canonical_manifest_bytes(reviewed):
            raise RuntimeError("live batch differs from reviewed artifact")
        started = perf_counter()
        fetch_run = execute_fetch_plan(
            db=db,
            plan=plan,
            include_benchmarks=False,
            force_refresh=False,
            force_full_backfill=False,
            stop_on_hard_failure=True,
        )
        elapsed = round(perf_counter() - started, 3)
        print(
            json.dumps(
                {
                    "batch_number": reviewed["batch_number"],
                    "fetch_run_id": int(fetch_run.id),
                    "status": fetch_run.status,
                    "elapsed_seconds": elapsed,
                },
                indent=2,
            )
        )
        return int(fetch_run.id)


def dry_run_remaining() -> None:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        needs = MarketDataObligationService().recovery_needs(db)
        tickers = sorted({row.ticker for row in needs})
        required_keys = {(row.ticker, row.what_to_show) for row in needs}
        plan = (
            _selected_plan(db, tickers, required_keys=required_keys) if tickers else None
        )
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


_HISTORICAL_TABLES = (
    "winner_cohort_generations",
    "winner_probability_estimates",
    "winner_estimate_evidence_members",
    "winner_evidence_manifest_members",
    "winner_temporal_validity_decisions",
    "winner_prediction_snapshots",
    "winner_forward_outcomes",
)


def historical_state_snapshot(label: str, output_dir: Path) -> Path:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        tables: dict[str, dict[str, Any]] = {}
        for table_name in _HISTORICAL_TABLES:
            digest = hashlib.sha256()
            count = 0
            statement = text(
                f"SELECT row_to_json(source_row)::text "  # noqa: S608 - constant allowlist
                f"FROM {table_name} source_row ORDER BY id"
            ).execution_options(stream_results=True, yield_per=5000)
            for rendered in db.scalars(statement):
                digest.update(str(rendered).encode("utf-8"))
                digest.update(b"\n")
                count += 1
            tables[table_name] = {"count": count, "sha256": digest.hexdigest()}
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-recovery-historical-state-v1",
            "label": label,
            "tables": tables,
        }
        payload["artifact_hash"] = _hash(payload)
        path = output_dir / f"historical_state_{label}_{payload['artifact_hash']}.json"
        _write_immutable(path, payload)
        print(json.dumps({"path": str(path.resolve()), **payload}, indent=2))
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


def _quarantine_count(db) -> int:
    latest = (
        select(
            WinnerTemporalValidityDecision.prediction_id,
            func.max(WinnerTemporalValidityDecision.validation_sequence).label("sequence"),
        )
        .group_by(WinnerTemporalValidityDecision.prediction_id)
        .subquery()
    )
    return int(
        db.scalar(
            select(func.count(WinnerTemporalValidityDecision.id))
            .join(
                latest,
                (
                    latest.c.prediction_id
                    == WinnerTemporalValidityDecision.prediction_id
                )
                & (
                    latest.c.sequence
                    == WinnerTemporalValidityDecision.validation_sequence
                ),
            )
            .where(WinnerTemporalValidityDecision.evidence_eligible.is_(False))
        )
        or 0
    )


def _historical_counts(db) -> dict[str, int]:
    return {
        table_name: int(
            db.scalar(
                text(f"SELECT count(*) FROM {table_name}")  # noqa: S608 - constant allowlist
            )
            or 0
        )
        for table_name in _HISTORICAL_TABLES
    }


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
            "recovery_outcome_count",
            "obligation_count",
            "contract_count",
            "batch_number",
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
    master = sub.add_parser("plan-recovery-master")
    master.add_argument("--output-dir", type=Path, required=True)
    batch = sub.add_parser("plan-recovery-batch")
    batch.add_argument("--master", type=Path, required=True)
    batch.add_argument("--master-hash", required=True)
    batch.add_argument("--batch-number", type=int, required=True)
    batch.add_argument("--contract-limit", type=int, default=25)
    batch.add_argument("--output-dir", type=Path, required=True)
    execute_batch = sub.add_parser("execute-recovery-batch")
    execute_batch.add_argument("--artifact", type=Path, required=True)
    execute_batch.add_argument("--expected-hash", required=True)
    execute_batch.add_argument("--approve-write", action="store_true")
    verify_batch = sub.add_parser("verify-recovery-batch")
    verify_batch.add_argument("--artifact", type=Path, required=True)
    verify_batch.add_argument("--fetch-run-id", type=int, required=True)
    verify_batch.add_argument("--output-dir", type=Path, required=True)
    historical = sub.add_parser("historical-state")
    historical.add_argument("--label", required=True)
    historical.add_argument("--output-dir", type=Path, required=True)
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
    elif args.command == "plan-recovery-master":
        plan_remaining_master(args.output_dir)
    elif args.command == "plan-recovery-batch":
        plan_recovery_batch(
            master_path=args.master,
            master_hash=args.master_hash,
            batch_number=args.batch_number,
            contract_limit=args.contract_limit,
            output_dir=args.output_dir,
        )
    elif args.command == "execute-recovery-batch":
        execute_recovery_batch(
            args.artifact,
            expected_hash=args.expected_hash,
            approve_write=args.approve_write,
        )
    elif args.command == "verify-recovery-batch":
        verify_v3(args.artifact, args.fetch_run_id, args.output_dir)
    elif args.command == "historical-state":
        historical_state_snapshot(args.label, args.output_dir)
    else:
        dry_run_remaining()


if __name__ == "__main__":
    main()
