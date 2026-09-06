"""Controlled Winner H5 NEXT_OPEN maturation certification canary.

Planning and snapshots are read-only. Execution requires an exact reviewed
manifest hash, explicit approval, actor, and request key. The general Winner
queue, scheduler, provider fetch, evidence, cohort, and estimate paths are not
called by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.models.tables import (
    IBContract,
    PriceBar,
    PriceSeriesVersion,
    WinnerForwardOutcome,
    WinnerMarketDataObligation,
    WinnerPredictionSnapshot,
    WinnerTemporalValidityDecision,
)
from app.services.winner_probability.maturation_canary_service import (
    build_maturation_canary_manifest,
    canonical_canary_hash,
    execute_reviewed_maturation_canary,
    verify_maturation_canary_results,
)
from app.services.winner_probability.outcome_orchestration_service import (
    H5NextOpenOrchestrationService,
)
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)

DEFAULT_COUNT = 25
PROTECTED_TABLES = (
    "winner_estimate_evidence_members",
    "winner_evidence_manifest_members",
    "winner_probability_estimates",
    "winner_cohort_generations",
    "winner_temporal_validity_decisions",
)


def plan(
    *,
    recovery_manifest_path: Path,
    output_dir: Path,
    count: int = DEFAULT_COUNT,
) -> Path:
    if count < 20 or count > 30:
        raise RuntimeError("reviewed canary count must be between 20 and 30")
    recovery = json.loads(recovery_manifest_path.read_text(encoding="utf-8"))
    recovery_rows = {
        int(row["outcome_id"]): row
        for row in recovery["records"]
        if row.get("temporal_classification") == "CERTIFIABLE_VALID"
    }
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        candidates = _candidate_profiles(db, recovery_rows)
        selected = _select_stratified(candidates, count)
        selected_ids = [int(item["outcome_id"]) for item in selected]
        manifest_1 = build_maturation_canary_manifest(db, selected_ids)
        manifest_2 = build_maturation_canary_manifest(db, selected_ids)
        if canonical_manifest_bytes(manifest_1) != canonical_manifest_bytes(manifest_2):
            raise RuntimeError("manifest regeneration was not byte deterministic")
        reviewed_hash = canonical_canary_hash(manifest_1)
        queue = _queue_state(db)
        controls = _control_state(db)
        if controls["active_winner_jobs"] != 0:
            raise RuntimeError("active Winner jobs make canary review unsafe")
        document: dict[str, Any] = {
            "schema": "swinglens-winner-h5-maturation-reviewed-artifact-v1",
            "recovery_manifest_path": str(recovery_manifest_path.resolve()),
            "recovery_manifest_hash": recovery.get("manifest_hash"),
            "reviewed_manifest_hash": reviewed_hash,
            "byte_deterministic_regeneration": True,
            "queue_state": queue,
            "control_state": controls,
            "selection": selected,
            "coverage": _coverage(selected),
            "manifest": manifest_1,
        }
        document["artifact_hash"] = _hash_without_artifact_hash(document)
        path = output_dir / f"reviewed_{document['artifact_hash']}.json"
        _write_immutable(path, document)
        db.rollback()
    print(json.dumps(_artifact_summary(path, document), indent=2, sort_keys=True))
    return path


def snapshot(*, label: str, artifact_path: Path, output_dir: Path) -> Path:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    manifest = artifact["manifest"]
    outcome_ids = [int(item["outcome_id"]) for item in manifest["outcomes"]]
    prediction_ids = [int(item["prediction_id"]) for item in manifest["outcomes"]]
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        tables = {name: _table_hash(db, name) for name in PROTECTED_TABLES}
        tables["winner_forward_outcomes_non_canary"] = _table_hash_excluding(
            db, "winner_forward_outcomes", "id", outcome_ids
        )
        tables["winner_target_stop_outcomes_non_canary"] = _table_hash_excluding(
            db, "winner_target_stop_outcomes", "prediction_id", prediction_ids
        )
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-h5-maturation-state-v1",
            "label": label,
            "reviewed_manifest_hash": artifact["reviewed_manifest_hash"],
            "queue_state": _queue_state(db),
            "control_state": _control_state(db),
            "tables": tables,
            "canary_price_state": _canary_price_state(db, manifest),
            "canary_outcome_state": _canary_outcome_state(db, manifest),
            "quarantine_count": _quarantine_count(db),
            "clbk_state": _clbk_state(db),
        }
        payload["artifact_hash"] = _hash_without_artifact_hash(payload)
        path = output_dir / f"state_{label}_{payload['artifact_hash']}.json"
        _write_immutable(path, payload)
        db.rollback()
    print(json.dumps(_artifact_summary(path, payload), indent=2, sort_keys=True))
    return path


def execute(
    *,
    artifact_path: Path,
    expected_hash: str,
    actor: str,
    request_key: str,
    approve_write: bool,
    output_dir: Path,
) -> Path:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if artifact.get("reviewed_manifest_hash") != expected_hash:
        raise RuntimeError("command hash differs from reviewed artifact")
    with SessionLocal() as db:
        controls = _control_state(db)
        if controls["active_winner_jobs"] != 0:
            raise RuntimeError("active Winner jobs make canary execution unsafe")
        db.rollback()
    result = execute_reviewed_maturation_canary(
        SessionLocal,
        artifact["manifest"],
        reviewed_manifest_hash=expected_hash,
        approve_write=approve_write,
        actor=actor,
        request_key=request_key,
    )
    payload = canonicalize_manifest_value(
        {
            "schema": "swinglens-winner-h5-maturation-execution-v1",
            **result.__dict__,
        }
    )
    payload["artifact_hash"] = _hash_without_artifact_hash(payload)
    path = output_dir / f"execution_{payload['artifact_hash']}.json"
    _write_immutable(path, payload)
    print(json.dumps(_artifact_summary(path, payload), indent=2, sort_keys=True))
    return path


def verify(
    *,
    artifact_path: Path,
    execution_path: Path,
    before_state_path: Path,
    output_dir: Path,
) -> Path:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    before = json.loads(before_state_path.read_text(encoding="utf-8"))
    if (
        len(
            {
                artifact["reviewed_manifest_hash"],
                execution["reviewed_manifest_hash"],
                before["reviewed_manifest_hash"],
            }
        )
        != 1
    ):
        raise RuntimeError("verification inputs refer to different reviewed manifests")
    executed_at = datetime.fromisoformat(execution["executed_at"].replace("Z", "+00:00"))
    outcome_ids = [int(item["outcome_id"]) for item in artifact["manifest"]["outcomes"]]
    prediction_ids = [int(item["prediction_id"]) for item in artifact["manifest"]["outcomes"]]
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        verify_maturation_canary_results(db, artifact["manifest"], executed_at=executed_at)
        after_tables = {name: _table_hash(db, name) for name in PROTECTED_TABLES}
        after_tables["winner_forward_outcomes_non_canary"] = _table_hash_excluding(
            db, "winner_forward_outcomes", "id", outcome_ids
        )
        after_tables["winner_target_stop_outcomes_non_canary"] = _table_hash_excluding(
            db, "winner_target_stop_outcomes", "prediction_id", prediction_ids
        )
        table_checks = {
            name: before["tables"][name] == value for name, value in after_tables.items()
        }
        price_after = _canary_price_state(db, artifact["manifest"])
        quarantine_after = _quarantine_count(db)
        clbk_after = _clbk_state(db)
        queue_after = _queue_state(db)
        controls_after = _control_state(db)
        checks = {
            "all_expected_results_match": True,
            "protected_and_non_canary_tables_unchanged": all(table_checks.values()),
            "price_state_unchanged": before["canary_price_state"] == price_after,
            "quarantine_unchanged": before["quarantine_count"] == quarantine_after == 1292,
            "clbk_unchanged": before["clbk_state"] == clbk_after,
            "active_winner_jobs_zero": controls_after["active_winner_jobs"] == 0,
            "queue_reduced_exactly": (
                int(before["queue_state"]["due_total"]) - len(outcome_ids)
                == int(queue_after["due_total"])
            ),
        }
        if not all(checks.values()):
            raise RuntimeError(f"post-canary certification failed: {checks}")
        payload: dict[str, Any] = {
            "schema": "swinglens-winner-h5-maturation-verification-v1",
            "reviewed_manifest_hash": artifact["reviewed_manifest_hash"],
            "execution_artifact_hash": execution["artifact_hash"],
            "before_state_hash": before["artifact_hash"],
            "checks": checks,
            "table_checks": table_checks,
            "queue_before": before["queue_state"],
            "queue_after": queue_after,
            "control_state_after": controls_after,
            "quarantine_count_after": quarantine_after,
            "clbk_state_after": clbk_after,
            "price_state_after": price_after,
            "canary_outcome_state_after": _canary_outcome_state(db, artifact["manifest"]),
        }
        payload["artifact_hash"] = _hash_without_artifact_hash(payload)
        path = output_dir / f"verification_{payload['artifact_hash']}.json"
        _write_immutable(path, payload)
        db.rollback()
    print(json.dumps(_artifact_summary(path, payload), indent=2, sort_keys=True))
    return path


def _candidate_profiles(db, recovery_rows: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    ids = sorted(recovery_rows)
    rows = db.execute(
        select(WinnerForwardOutcome, WinnerPredictionSnapshot)
        .join(
            WinnerPredictionSnapshot,
            WinnerPredictionSnapshot.id == WinnerForwardOutcome.prediction_id,
        )
        .where(WinnerForwardOutcome.id.in_(ids))
        .where(WinnerForwardOutcome.status == "PENDING")
        .where(WinnerForwardOutcome.is_current_revision.is_(True))
        .order_by(WinnerForwardOutcome.id)
    ).all()
    outcome_ids = [int(outcome.id) for outcome, _ in rows]
    obligations: dict[int, list[WinnerMarketDataObligation]] = defaultdict(list)
    for obligation in db.scalars(
        select(WinnerMarketDataObligation).where(
            WinnerMarketDataObligation.forward_outcome_id.in_(outcome_ids)
        )
    ):
        obligations[int(obligation.forward_outcome_id)].append(obligation)
    contract_ids = {
        int(item.ib_contract_id)
        for values in obligations.values()
        for item in values
        if item.ib_contract_id is not None
    }
    contracts = {
        int(row.id): row
        for row in db.scalars(select(IBContract).where(IBContract.id.in_(contract_ids)))
    }
    tickers = {prediction.ticker.upper() for _, prediction in rows}
    min_date = min(outcome.entry_session for outcome, _ in rows)
    max_date = max(outcome.due_session for outcome, _ in rows)
    bars: dict[tuple[str, Any], PriceBar] = {}
    for bar in db.scalars(
        select(PriceBar)
        .where(PriceBar.ticker.in_(sorted(tickers)))
        .where(PriceBar.timeframe == "1 day")
        .where(PriceBar.what_to_show == "ADJUSTED_LAST")
        .where(PriceBar.bar_date.between(min_date, max_date))
    ):
        bars[(bar.ticker.upper(), bar.bar_date)] = bar
    result = []
    for outcome, prediction in rows:
        source = recovery_rows[int(outcome.id)]
        obligation_rows = obligations[int(outcome.id)]
        if len(obligation_rows) != 2 or any(item.status != "SATISFIED" for item in obligation_rows):
            continue
        contract = contracts.get(int(obligation_rows[0].ib_contract_id or 0))
        if contract is None or prediction.ticker.upper() == "CLBK":
            continue
        entry = bars.get((prediction.ticker.upper(), outcome.entry_session))
        due = bars.get((prediction.ticker.upper(), outcome.due_session))
        if entry is None or due is None or Decimal(str(entry.open)) <= 0:
            continue
        expected_return = (
            (Decimal(str(due.close)) - Decimal(str(entry.open)))
            / Decimal(str(entry.open))
            * Decimal("100")
        ).quantize(Decimal("0.000001"))
        result.append(
            {
                "outcome_id": int(outcome.id),
                "prediction_id": int(prediction.id),
                "ticker": prediction.ticker.upper(),
                "run_id": int(prediction.run_id),
                "prediction_as_of_date": prediction.prediction_as_of_date,
                "calculation_version": prediction.calculation_version,
                "exchange": contract.primary_exchange or contract.exchange,
                "historical_missing_pattern": _missing_pattern(source),
                "setup_family": prediction.setup_family,
                "setup_classification": prediction.setup_classification,
                "ranking_profile": prediction.ranking_profile,
                "expected_return_sign": "POSITIVE" if expected_return > 0 else "NON_POSITIVE",
                "expected_close_return_pct": expected_return,
            }
        )
    return canonicalize_manifest_value(result)


def _select_stratified(candidates: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    desired = {
        *(f"pattern:{value}" for value in ("ENTRY_H5", "H2_H5", "H3_H5", "H4_H5", "H5_ONLY")),
        *(f"exchange:{value}" for value in ("NASDAQ", "NYSE", "AMEX", "ARCA", "BATS")),
        "calculation:owpe-calc-1.0.0",
        "calculation:owpe-calc-1.1.0",
        "sign:POSITIVE",
        "sign:NON_POSITIVE",
    }
    remaining = list(candidates)
    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    used_tickers: set[str] = set()
    used_runs: set[int] = set()
    while remaining and len(selected) < count:

        def score(item: dict[str, Any]) -> tuple[int, int]:
            dimensions = _dimensions(item)
            core = len((dimensions & desired) - covered)
            diversity = (
                (item["ticker"] not in used_tickers) * 8
                + (int(item["run_id"]) not in used_runs) * 2
                + bool(item.get("setup_family"))
                + bool(item.get("ranking_profile"))
            )
            return core * 100 + diversity, -int(item["outcome_id"])

        chosen = max(remaining, key=score)
        selected.append(chosen)
        remaining.remove(chosen)
        covered.update(_dimensions(chosen))
        used_tickers.add(chosen["ticker"])
        used_runs.add(int(chosen["run_id"]))
    selected.sort(key=lambda item: int(item["outcome_id"]))
    if len(selected) != count:
        raise RuntimeError(f"only {len(selected)} eligible candidates were available")
    missing = desired - {dimension for item in selected for dimension in _dimensions(item)}
    available = {dimension for item in candidates for dimension in _dimensions(item)}
    if missing & available:
        raise RuntimeError(
            f"stratified selection failed to cover available dimensions: {missing & available}"
        )
    return selected


def _dimensions(item: dict[str, Any]) -> set[str]:
    return {
        f"pattern:{item['historical_missing_pattern']}",
        f"exchange:{item['exchange']}",
        f"calculation:{item['calculation_version']}",
        f"sign:{item['expected_return_sign']}",
    }


def _coverage(selected: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "historical_missing_patterns": dict(
            Counter(item["historical_missing_pattern"] for item in selected)
        ),
        "exchanges": dict(Counter(item["exchange"] for item in selected)),
        "calculation_versions": dict(Counter(item["calculation_version"] for item in selected)),
        "expected_return_signs": dict(Counter(item["expected_return_sign"] for item in selected)),
        "distinct_tickers": len({item["ticker"] for item in selected}),
        "distinct_runs": len({item["run_id"] for item in selected}),
        "distinct_prediction_dates": len({item["prediction_as_of_date"] for item in selected}),
        "setup_families": dict(Counter(str(item.get("setup_family")) for item in selected)),
        "ranking_profiles": dict(Counter(str(item.get("ranking_profile")) for item in selected)),
    }


def _missing_pattern(row: dict[str, Any]) -> str:
    required = list(row["adjusted_last_missing_sessions"])
    sessions = [
        row["entry_session"],
        row["h2_session"],
        row["h3_session"],
        row["h4_session"],
        row["h5_session"],
    ]
    first_index = min(sessions.index(value) for value in required)
    return ("ENTRY_H5", "H2_H5", "H3_H5", "H4_H5", "H5_ONLY")[first_index]


def _queue_state(db) -> dict[str, Any]:
    state = H5NextOpenOrchestrationService().queue_state(db)
    return canonicalize_manifest_value(state.__dict__)


def _control_state(db) -> dict[str, int]:
    active = int(
        db.scalar(
            text(
                "SELECT count(*) FROM background_jobs "
                "WHERE status IN ('PENDING','RUNNING','RETRYING') "
                "AND job_type LIKE 'WINNER%'"
            )
        )
        or 0
    )
    return {"active_winner_jobs": active}


def _table_hash(db, table_name: str) -> dict[str, Any]:
    return _hash_query(db, f"SELECT row_to_json(t)::text FROM {table_name} t ORDER BY id")


def _table_hash_excluding(db, table: str, column: str, excluded: list[int]) -> dict[str, Any]:
    rendered = ",".join(str(int(value)) for value in sorted(set(excluded)))
    query = (
        f"SELECT row_to_json(t)::text FROM {table} t WHERE {column} NOT IN ({rendered}) ORDER BY id"
    )
    return _hash_query(db, query)


def _hash_query(db, query: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    for value in db.scalars(text(query).execution_options(stream_results=True, yield_per=5000)):
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def _canary_price_state(db, manifest: dict[str, Any]) -> dict[str, Any]:
    bar_ids = sorted(
        {
            int(bar["id"])
            for item in manifest["outcomes"]
            for group in (item["selected_bars"], item["trades_control_bars"])
            for bar in group
        }
    )
    series_ids = sorted(
        {int(row["id"]) for item in manifest["outcomes"] for row in item["price_series_versions"]}
    )
    bars = list(db.scalars(select(PriceBar).where(PriceBar.id.in_(bar_ids)).order_by(PriceBar.id)))
    series = list(
        db.scalars(
            select(PriceSeriesVersion)
            .where(PriceSeriesVersion.id.in_(series_ids))
            .order_by(PriceSeriesVersion.id)
        )
    )
    return {
        "bars": _object_rows_hash(bars),
        "series_versions": _object_rows_hash(series),
    }


def _canary_outcome_state(db, manifest: dict[str, Any]) -> dict[str, Any]:
    ids = [int(item["outcome_id"]) for item in manifest["outcomes"]]
    rows = list(
        db.scalars(
            select(WinnerForwardOutcome)
            .where(WinnerForwardOutcome.id.in_(ids))
            .order_by(WinnerForwardOutcome.id)
        )
    )
    return _object_rows_hash(rows)


def _object_rows_hash(rows: list[Any]) -> dict[str, Any]:
    payload = []
    for row in rows:
        payload.append({column.name: getattr(row, column.name) for column in row.__table__.columns})
    return {"count": len(payload), "sha256": canonical_canary_hash(payload)}


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
                (latest.c.prediction_id == WinnerTemporalValidityDecision.prediction_id)
                & (latest.c.sequence == WinnerTemporalValidityDecision.validation_sequence),
            )
            .where(WinnerTemporalValidityDecision.evidence_eligible.is_(False))
        )
        or 0
    )


def _clbk_state(db) -> dict[str, Any]:
    query = (
        "SELECT row_to_json(t)::text FROM winner_forward_outcomes t "
        "JOIN winner_prediction_snapshots p ON p.id=t.prediction_id "
        "WHERE p.ticker='CLBK' ORDER BY t.id"
    )
    return _hash_query(db, query)


def _hash_without_artifact_hash(payload: dict[str, Any]) -> str:
    return canonical_canary_hash(
        {key: value for key, value in payload.items() if key != "artifact_hash"}
    )


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable artifact: {path}")
    path.write_bytes(canonical_manifest_bytes(payload) + b"\n")


def _artifact_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        **{
            key: payload[key]
            for key in (
                "artifact_hash",
                "reviewed_manifest_hash",
                "outcome_count",
                "processed",
                "matured",
                "failed",
                "checks",
                "queue_state",
                "queue_before",
                "queue_after",
            )
            if key in payload
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--recovery-manifest", type=Path, required=True)
    plan_parser.add_argument("--output-dir", type=Path, required=True)
    plan_parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    snapshot_parser = sub.add_parser("snapshot")
    snapshot_parser.add_argument("--label", required=True)
    snapshot_parser.add_argument("--artifact", type=Path, required=True)
    snapshot_parser.add_argument("--output-dir", type=Path, required=True)
    execute_parser = sub.add_parser("execute")
    execute_parser.add_argument("--artifact", type=Path, required=True)
    execute_parser.add_argument("--expected-hash", required=True)
    execute_parser.add_argument("--actor", required=True)
    execute_parser.add_argument("--request-key", required=True)
    execute_parser.add_argument("--approve-write", action="store_true")
    execute_parser.add_argument("--output-dir", type=Path, required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--artifact", type=Path, required=True)
    verify_parser.add_argument("--execution", type=Path, required=True)
    verify_parser.add_argument("--before-state", type=Path, required=True)
    verify_parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        plan(
            recovery_manifest_path=args.recovery_manifest,
            output_dir=args.output_dir,
            count=args.count,
        )
    elif args.command == "snapshot":
        snapshot(label=args.label, artifact_path=args.artifact, output_dir=args.output_dir)
    elif args.command == "execute":
        execute(
            artifact_path=args.artifact,
            expected_hash=args.expected_hash,
            actor=args.actor,
            request_key=args.request_key,
            approve_write=args.approve_write,
            output_dir=args.output_dir,
        )
    else:
        verify(
            artifact_path=args.artifact,
            execution_path=args.execution,
            before_state_path=args.before_state,
            output_dir=args.output_dir,
        )


if __name__ == "__main__":
    main()
