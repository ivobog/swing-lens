"""Controlled Winner H5 market-data recovery tooling.

The default ``generate`` command is read-only. Mutating commands require an
explicit reviewed manifest hash and ``--approve-write``. This module never
invokes Winner maturation, cohort generation, rescoring, or publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.models.tables import (
    IBContract,
    PriceBar,
    WinnerForwardOutcome,
    WinnerMarketDataObligation,
    WinnerPredictionSnapshot,
)
from app.services.us_market_calendar import latest_completed_us_trading_day, us_market_session
from app.services.winner_probability.feature_extractor import _stable_hash
from app.services.winner_probability.market_data_obligation_service import (
    MarketDataObligationService,
    build_recovery_request_plan,
    complete_basis_for_rows,
    global_daily_bar_lag,
    required_outcome_sessions,
)
from app.services.winner_probability.temporal_eligibility import (
    load_current_temporal_decisions,
    prediction_temporally_eligible,
)
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)
from app.services.winner_probability.temporal_validation_service import (
    TemporalCertificationItem,
    TemporalValidationService,
)

SCHEMA = "swinglens-winner-h5-market-recovery-v1"
CERTIFICATION_REASON = "HISTORICAL_DURABLE_POINT_IN_TIME_LINEAGE_VERIFIED"
CERTIFICATION_ACTOR = "swinglens-production-recovery"
CERTIFICATION_REQUEST_KEY = "winner-h5-positive-temporal-certification-20260905"
REQUIRED_SOURCE_IDS = (
    "raw_row_id",
    "technical_score_id",
    "fundamental_score_id",
    "combined_result_id",
    "market_regime_snapshot_id",
)
EXCLUDED_IDENTITY_TICKERS = frozenset({"CLBK"})


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_manifest_bytes(value)).hexdigest()


def _candidate_rows(db, completed_on: date):
    return db.execute(
        select(WinnerPredictionSnapshot, WinnerForwardOutcome)
        .join(
            WinnerForwardOutcome,
            WinnerForwardOutcome.prediction_id == WinnerPredictionSnapshot.id,
        )
        .where(WinnerForwardOutcome.entry_model == "NEXT_OPEN")
        .where(WinnerForwardOutcome.horizon_sessions == 5)
        .where(WinnerForwardOutcome.status == "PENDING")
        .where(WinnerForwardOutcome.is_current_revision.is_(True))
        .where(WinnerForwardOutcome.due_session <= completed_on)
        .order_by(WinnerForwardOutcome.id)
    ).all()


def _load_bars(db, tickers: set[str], start: date, end: date) -> dict[str, list[PriceBar]]:
    result: dict[str, list[PriceBar]] = defaultdict(list)
    for row in db.scalars(
        select(PriceBar)
        .where(PriceBar.ticker.in_(sorted(tickers)))
        .where(PriceBar.timeframe == "1 day")
        .where(PriceBar.what_to_show.in_(("ADJUSTED_LAST", "TRADES")))
        .where(PriceBar.bar_date >= start)
        .where(PriceBar.bar_date <= end)
        .order_by(PriceBar.ticker, PriceBar.bar_date, PriceBar.id)
    ):
        result[row.ticker.upper()].append(row)
    return result


def _historical_temporal_evidence(
    prediction: WinnerPredictionSnapshot,
    outcome: WinnerForwardOutcome,
    bars: list[PriceBar],
) -> tuple[bool, tuple[str, ...], dict[str, Any]]:
    reasons: list[str] = []
    lineage = prediction.lineage_json or {}
    audit = lineage.get("feature_cutoff_audit")
    audit_hash = lineage.get("feature_cutoff_audit_hash")
    session = us_market_session(outcome.entry_session) if outcome.entry_session else None
    if session is None:
        reasons.append("ENTRY_SESSION_INVALID")
    elif prediction.captured_at >= session.open_at:
        reasons.append("ENTRY_NOT_STRICTLY_AFTER_CAPTURE")
    if prediction.source_data_cutoff_at > prediction.captured_at:
        reasons.append("SOURCE_CUTOFF_AFTER_CAPTURE")
    if outcome.entry_session != prediction.planned_entry_session:
        reasons.append("OUTCOME_ENTRY_DIFFERS_FROM_PREDICTION")
    if (
        prediction.planned_entry_session is None
        or prediction.prediction_as_of_date >= prediction.planned_entry_session
    ):
        reasons.append("SOURCE_SESSION_NOT_BEFORE_ENTRY")
    if lineage.get("point_in_time_validated") is not True:
        reasons.append("POINT_IN_TIME_AUDIT_NOT_ASSERTED")
    source_ids = prediction.source_ids_json or {}
    missing_source_ids = [name for name in REQUIRED_SOURCE_IDS if not source_ids.get(name)]
    if missing_source_ids:
        reasons.append("SOURCE_LINEAGE_IDS_MISSING")
    if not isinstance(audit, dict) or not audit:
        reasons.append("FEATURE_CUTOFF_AUDIT_MISSING")
    elif _stable_hash(audit) != audit_hash:
        reasons.append("FEATURE_CUTOFF_AUDIT_HASH_MISMATCH")
    else:
        for feature_name, item in sorted(audit.items()):
            if not isinstance(item, dict) or item.get("status") not in {"available", "missing"}:
                reasons.append(f"FEATURE_AUDIT_INVALID:{feature_name}")
                continue
            raw_available = item.get("source_available_at")
            if not raw_available:
                if item.get("status") == "available":
                    reasons.append(f"FEATURE_AVAILABILITY_MISSING:{feature_name}")
                continue
            available = datetime.fromisoformat(str(raw_available))
            if available.tzinfo is None:
                # A date-only semantic as-of marker is compared as a session,
                # never relabeled as a provider observation timestamp.
                if available.date() > prediction.prediction_as_of_date:
                    reasons.append(f"FEATURE_SESSION_AFTER_SOURCE:{feature_name}")
            elif available > prediction.source_data_cutoff_at:
                reasons.append(f"FEATURE_AVAILABLE_AFTER_CUTOFF:{feature_name}")
    post_entry_visible = [
        row
        for row in bars
        if session is not None
        and row.bar_date >= session.session
        and row.first_seen_at <= prediction.captured_at
    ]
    if post_entry_visible:
        reasons.append("ENTRY_OR_LATER_BAR_VISIBLE_AT_CAPTURE")
    evidence = {
        "decision_proxy": "captured_at",
        "feature_cutoff_audit_hash": audit_hash,
        "feature_cutoff_audit_recomputed_hash": (
            _stable_hash(audit) if isinstance(audit, dict) and audit else None
        ),
        "required_source_ids": {name: source_ids.get(name) for name in REQUIRED_SOURCE_IDS},
        "post_entry_bar_visible_at_capture_count": len(post_entry_visible),
        "semantic_timestamp_policy": (
            "DURABLE_FEATURE_AVAILABILITY_AUDIT_PLUS_SEMANTIC_SESSION_BOUNDARY"
        ),
    }
    return not reasons, tuple(reasons), evidence


def build_manifest(db, *, completed_on: date) -> dict[str, Any]:
    pairs = _candidate_rows(db, completed_on)
    predictions = {int(prediction.id): prediction for prediction, _ in pairs}
    decisions = load_current_temporal_decisions(db, set(predictions))
    tickers = {prediction.ticker.upper() for prediction in predictions.values()}
    contracts = {
        row.ticker.upper(): row
        for row in db.scalars(select(IBContract).where(IBContract.ticker.in_(sorted(tickers))))
    }
    start = min(outcome.entry_session for _, outcome in pairs if outcome.entry_session)
    end = max(outcome.due_session for _, outcome in pairs if outcome.due_session)
    bars_by_ticker = _load_bars(db, tickers, start, end)

    records: list[dict[str, Any]] = []
    certification_items: list[TemporalCertificationItem] = []
    classifications: Counter[str] = Counter()
    for prediction, outcome in pairs:
        sessions = required_outcome_sessions(outcome.entry_session, 5)
        ticker_bars = bars_by_ticker[prediction.ticker.upper()]
        complete_basis, _ = complete_basis_for_rows(ticker_bars, sessions)
        if complete_basis is not None:
            continue
        current_decision = decisions.get(int(prediction.id))
        if current_decision is not None and not prediction_temporally_eligible(
            prediction, current_decision
        ):
            classification = "QUARANTINED_INVALID"
            reasons = tuple(current_decision.reason_codes_json)
            evidence: dict[str, Any] = {"temporal_decision_id": int(current_decision.id)}
        elif prediction.ticker.upper() in EXCLUDED_IDENTITY_TICKERS:
            classification = "IDENTITY_BLOCKED"
            reasons = ("KNOWN_SAME_SYMBOL_CONTRACT_REORGANIZATION",)
            evidence = {}
        elif current_decision is not None and prediction_temporally_eligible(
            prediction, current_decision
        ):
            classification = "CERTIFIABLE_VALID"
            reasons = ("EXPLICIT_TEMPORAL_LEDGER_VALID",)
            evidence = {"temporal_decision_id": int(current_decision.id)}
        else:
            valid, reasons, evidence = _historical_temporal_evidence(
                prediction, outcome, ticker_bars
            )
            classification = "CERTIFIABLE_VALID" if valid else "TEMPORAL_LINEAGE_UNRESOLVED"
        classifications[classification] += 1

        contract = contracts.get(prediction.ticker.upper())
        by_basis = {
            basis: {row.bar_date for row in ticker_bars if row.what_to_show == basis}
            for basis in ("ADJUSTED_LAST", "TRADES")
        }
        missing = {
            basis: [session for session in sessions if session not in by_basis[basis]]
            for basis in by_basis
        }
        record = {
            "outcome_id": int(outcome.id),
            "prediction_id": int(prediction.id),
            "run_id": int(prediction.run_id),
            "ticker": prediction.ticker.upper(),
            "calculation_version": prediction.calculation_version,
            "prediction_as_of_date": prediction.prediction_as_of_date,
            "decision_or_capture_at": prediction.decision_at or prediction.captured_at,
            "decision_proxy": "decision_at" if prediction.decision_at else "captured_at",
            "source_data_cutoff_at": prediction.source_data_cutoff_at,
            "entry_session": sessions[0],
            "h2_session": sessions[1],
            "h3_session": sessions[2],
            "h4_session": sessions[3],
            "h5_session": sessions[4],
            "due_session": outcome.due_session,
            "latest_existing_local_bar": max((row.bar_date for row in ticker_bars), default=None),
            "adjusted_last_missing_sessions": missing["ADJUSTED_LAST"],
            "trades_missing_sessions": missing["TRADES"],
            "first_missing_session": min((*missing["ADJUSTED_LAST"], *missing["TRADES"])),
            "temporal_classification": classification,
            "temporal_reason_codes": reasons,
            "temporal_supporting_evidence": evidence,
            "canonical_contract": {
                "id": getattr(contract, "id", None),
                "ib_conid": getattr(contract, "ib_conid", None),
                "symbol": getattr(contract, "symbol", None),
                "local_symbol": getattr(contract, "local_symbol", None),
                "exchange": getattr(contract, "exchange", None),
                "primary_exchange": getattr(contract, "primary_exchange", None),
                "currency": getattr(contract, "currency", None),
                "sec_type": getattr(contract, "sec_type", None),
                "trading_class": getattr(contract, "trading_class", None),
                "resolution_status": getattr(contract, "resolution_status", None),
            },
        }
        records.append(record)
        if classification == "CERTIFIABLE_VALID" and current_decision is None:
            certification_items.append(
                TemporalCertificationItem(
                    prediction_id=int(prediction.id),
                    decision_at=prediction.captured_at,
                    entry_session=sessions[0],
                    semantic_input_time_valid=True,
                    certification_reason=CERTIFICATION_REASON,
                    metadata={
                        "outcome_id": int(outcome.id),
                        "ticker": prediction.ticker.upper(),
                        "calculation_version": prediction.calculation_version,
                        **evidence,
                    },
                )
            )

    ordered = sorted(records, key=lambda row: (row["outcome_id"], row["prediction_id"]))
    certification_plan = TemporalValidationService().plan_certification(
        db, items=tuple(certification_items)
    )
    recovery = [row for row in ordered if row["temporal_classification"] == "CERTIFIABLE_VALID"]
    lag = global_daily_bar_lag(db, latest_completed_session=completed_on)
    return {
        "schema": SCHEMA,
        "completed_on": completed_on,
        "manifest_hash": _hash(recovery),
        "all_classified_hash": _hash(ordered),
        "certification_manifest_hash": certification_plan.manifest_hash,
        "certification_item_count": certification_plan.item_count,
        "counts": dict(sorted(classifications.items())),
        "recovery_count": len(recovery),
        "global_daily_bar_lag": {
            "latest_completed_session": lag.latest_completed_session,
            "latest_local_session": lag.latest_local_session,
            "lag_sessions": lag.lag_sessions,
            "degraded": lag.degraded,
        },
        "records": ordered,
    }


def independent_sql_ids(db, *, completed_on: date) -> tuple[int, ...]:
    """Independent set-level check; semantic lineage is verified separately in Python."""
    rows = db.execute(
        text(
            """
            WITH latest_temporal AS (
              SELECT DISTINCT ON (prediction_id)
                     prediction_id, status, evidence_eligible,
                     entry_timing_valid, source_cutoff_valid, semantic_input_time_valid
              FROM winner_temporal_validity_decisions
              ORDER BY prediction_id, validation_sequence DESC
            )
            SELECT o.id
            FROM winner_forward_outcomes o
            JOIN winner_prediction_snapshots p ON p.id=o.prediction_id
            LEFT JOIN latest_temporal tv ON tv.prediction_id=p.id
            WHERE o.entry_model='NEXT_OPEN' AND o.horizon_sessions=5
              AND o.status='PENDING' AND o.is_current_revision
              AND o.due_session <= :completed_on
              AND p.ticker <> 'CLBK'
              AND (
                tv.prediction_id IS NULL
                OR (
                  tv.status='VALID' AND tv.evidence_eligible
                  AND tv.entry_timing_valid AND tv.source_cutoff_valid
                  AND tv.semantic_input_time_valid IS TRUE
                )
              )
              AND p.captured_at < (
                (o.entry_session::timestamp + time '09:30')
                AT TIME ZONE 'America/New_York'
              )
              AND p.source_data_cutoff_at <= p.captured_at
            ORDER BY o.id
            """
        ),
        {"completed_on": completed_on},
    )
    return tuple(int(row.id) for row in rows)


def generate(output_dir: Path, *, completed_on: date | None = None) -> Path:
    completed_on = completed_on or latest_completed_us_trading_day()
    output_dir.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        first = build_manifest(db, completed_on=completed_on)
        second = build_manifest(db, completed_on=completed_on)
        if canonical_manifest_bytes(first) != canonical_manifest_bytes(second):
            raise RuntimeError("recovery manifest is not deterministic")
        independent = independent_sql_ids(db, completed_on=completed_on)
        recovery_ids = tuple(
            row["outcome_id"]
            for row in first["records"]
            if row["temporal_classification"] == "CERTIFIABLE_VALID"
        )
        if recovery_ids != independent:
            raise RuntimeError(
                "independent SQL recovery set differs from service set: "
                f"service={len(recovery_ids)} sql={len(independent)}"
            )
        first["independent_sql_count"] = len(independent)
        first["independent_sql_id_hash"] = _hash(independent)
        first["canonical_bytes_sha256"] = _hash(first)
        payload = canonicalize_manifest_value(first)
        path = output_dir / f"winner_h5_market_recovery_{first['manifest_hash']}.json"
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") != serialized:
            raise RuntimeError("manifest path exists with different bytes")
        path.write_text(serialized, encoding="utf-8")
        print(
            json.dumps(
                {
                    "manifest_path": str(path.resolve()),
                    "manifest_hash": first["manifest_hash"],
                    "certification_manifest_hash": first["certification_manifest_hash"],
                    "counts": first["counts"],
                    "recovery_count": first["recovery_count"],
                    "independent_sql_count": len(independent),
                    "global_daily_bar_lag": payload["global_daily_bar_lag"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return path


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise RuntimeError("unsupported recovery manifest schema")
    return payload


def _certification_items(payload: dict[str, Any]) -> tuple[TemporalCertificationItem, ...]:
    items = []
    for row in payload["records"]:
        if row["temporal_classification"] != "CERTIFIABLE_VALID":
            continue
        if row["temporal_reason_codes"] == ["EXPLICIT_TEMPORAL_LEDGER_VALID"]:
            continue
        items.append(
            TemporalCertificationItem(
                prediction_id=int(row["prediction_id"]),
                decision_at=datetime.fromisoformat(row["decision_or_capture_at"]),
                entry_session=date.fromisoformat(row["entry_session"]),
                semantic_input_time_valid=True,
                certification_reason=CERTIFICATION_REASON,
                metadata={
                    "outcome_id": int(row["outcome_id"]),
                    "ticker": row["ticker"],
                    "calculation_version": row["calculation_version"],
                    **row["temporal_supporting_evidence"],
                },
            )
        )
    return tuple(items)


def apply_certification(path: Path, *, expected_hash: str, approve_write: bool) -> None:
    payload = _load_manifest(path)
    if payload["certification_manifest_hash"] != expected_hash:
        raise RuntimeError("reviewed certification hash mismatch")
    with SessionLocal() as db:
        plan = TemporalValidationService().plan_certification(
            db, items=_certification_items(payload)
        )
        if plan.manifest_hash != expected_hash:
            raise RuntimeError("live certification plan differs from reviewed manifest")
        result = TemporalValidationService().apply_certification(
            db,
            plan=plan,
            expected_manifest_hash=expected_hash,
            actor=CERTIFICATION_ACTOR,
            request_key=CERTIFICATION_REQUEST_KEY,
            approve_write=approve_write,
        )
        db.commit()
        print(json.dumps(result.__dict__, indent=2, default=str, sort_keys=True))


def install_obligations(path: Path, *, expected_hash: str, approve_write: bool) -> None:
    if not approve_write:
        raise PermissionError("--approve-write is required")
    payload = _load_manifest(path)
    if payload["manifest_hash"] != expected_hash:
        raise RuntimeError("reviewed recovery hash mismatch")
    outcome_ids = [
        int(row["outcome_id"])
        for row in payload["records"]
        if row["temporal_classification"] == "CERTIFIABLE_VALID"
    ]
    with SessionLocal() as db:
        outcomes = list(
            db.scalars(
                select(WinnerForwardOutcome)
                .where(WinnerForwardOutcome.id.in_(outcome_ids))
                .order_by(WinnerForwardOutcome.id)
                .with_for_update()
            )
        )
        if [int(row.id) for row in outcomes] != outcome_ids:
            raise RuntimeError("live outcome set differs from reviewed recovery manifest")
        result = MarketDataObligationService().ensure_for_outcomes(
            db,
            outcomes,
            excluded_tickers=EXCLUDED_IDENTITY_TICKERS,
        )
        if result.excluded:
            raise RuntimeError(f"reviewed recovery rows unexpectedly excluded: {result.excluded}")
        db.commit()
        print(json.dumps(result.__dict__, indent=2, sort_keys=True))


def show_request_plan(path: Path, *, expected_hash: str) -> None:
    payload = _load_manifest(path)
    if payload["manifest_hash"] != expected_hash:
        raise RuntimeError("reviewed recovery hash mismatch")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        service = MarketDataObligationService()
        requests = build_recovery_request_plan(service.recovery_needs(db))
        obligations = db.execute(
            select(
                WinnerMarketDataObligation.status,
                func.count(WinnerMarketDataObligation.id),
            ).group_by(WinnerMarketDataObligation.status)
        ).all()
        print(
            json.dumps(
                {
                    "obligation_counts": {status: count for status, count in obligations},
                    "provider_request_count": len(requests),
                    "outcome_count": len({value for row in requests for value in row.outcome_ids}),
                    "ticker_count": len({row.ticker for row in requests}),
                    "contract_count": len({row.contract_id for row in requests}),
                    "basis_counts": dict(Counter(row.what_to_show for row in requests)),
                    "request_plan_hash": _hash([row.__dict__ for row in requests]),
                },
                indent=2,
                sort_keys=True,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("--output-dir", type=Path, required=True)
    generate_parser.add_argument("--completed-on", type=date.fromisoformat)
    certify_parser = sub.add_parser("apply-certification")
    certify_parser.add_argument("--manifest", type=Path, required=True)
    certify_parser.add_argument("--expected-hash", required=True)
    certify_parser.add_argument("--approve-write", action="store_true")
    obligation_parser = sub.add_parser("install-obligations")
    obligation_parser.add_argument("--manifest", type=Path, required=True)
    obligation_parser.add_argument("--expected-hash", required=True)
    obligation_parser.add_argument("--approve-write", action="store_true")
    plan_parser = sub.add_parser("request-plan")
    plan_parser.add_argument("--manifest", type=Path, required=True)
    plan_parser.add_argument("--expected-hash", required=True)
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.output_dir, completed_on=args.completed_on)
    elif args.command == "apply-certification":
        apply_certification(
            args.manifest,
            expected_hash=args.expected_hash,
            approve_write=args.approve_write,
        )
    elif args.command == "install-obligations":
        install_obligations(
            args.manifest,
            expected_hash=args.expected_hash,
            approve_write=args.approve_write,
        )
    else:
        show_request_plan(args.manifest, expected_hash=args.expected_hash)


if __name__ == "__main__":
    main()
