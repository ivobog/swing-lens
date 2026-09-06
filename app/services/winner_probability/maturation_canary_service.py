"""Reviewed, explicit-ID Winner H5 maturation canary support.

The expectation builder deliberately does not call ``OutcomeMaturationService``.
It reproduces the published arithmetic and lineage contract from persisted inputs.
The executor calls the production service only after byte-for-byte preflight and
processes one reviewed outcome per database transaction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    IBContract,
    PriceBar,
    PriceSeriesVersion,
    WinnerForwardOutcome,
    WinnerMarketDataObligation,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
)
from app.services.bar_cache_service import price_bar_data_hash
from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.winner_probability.market_data_obligation_service import (
    complete_basis_for_rows,
    required_outcome_sessions,
)
from app.services.winner_probability.outcome_service import (
    BENCHMARK_TICKER,
    OutcomeMaturationService,
)
from app.services.winner_probability.temporal_eligibility import (
    load_current_temporal_decisions,
    prediction_temporally_eligible,
)
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
    canonicalize_manifest_value,
)

CANARY_SCHEMA = "swinglens-winner-h5-maturation-canary-v1"
MAX_CANARY_OUTCOMES = 30
_QUANTUM = Decimal("0.000001")


class CanaryApprovalError(RuntimeError):
    """The reviewed artifact or current database state failed closed."""


@dataclass(frozen=True)
class CanaryExecutionResult:
    reviewed_manifest_hash: str
    actor: str
    request_key: str
    executed_at: datetime
    outcome_ids: tuple[int, ...]
    processed: int
    matured: int
    revised: int
    target_stop_matured: int
    warnings: int
    failed: int
    material_evidence_changes: int
    per_outcome: tuple[dict[str, Any], ...]


def canonical_canary_hash(value: Any) -> str:
    return hashlib.sha256(canonical_manifest_bytes(value)).hexdigest()


def verify_canary_approval(
    manifest: dict[str, Any],
    *,
    reviewed_manifest_hash: str,
    approve_write: bool,
    actor: str,
    request_key: str,
) -> None:
    if not approve_write:
        raise PermissionError("explicit approve_write=True is required")
    if not actor.strip() or not request_key.strip():
        raise ValueError("actor and request_key are required")
    actual_hash = canonical_canary_hash(manifest)
    if actual_hash != reviewed_manifest_hash:
        raise CanaryApprovalError(
            f"reviewed manifest hash mismatch: expected {reviewed_manifest_hash}, got {actual_hash}"
        )
    outcome_ids = [int(item["outcome_id"]) for item in manifest.get("outcomes", [])]
    if not outcome_ids or len(outcome_ids) > MAX_CANARY_OUTCOMES:
        raise CanaryApprovalError("canary must contain between 1 and 30 outcomes")
    if outcome_ids != sorted(set(outcome_ids)):
        raise CanaryApprovalError("canary outcome IDs must be unique and sorted")


def independent_forward_metrics(bars: Sequence[Any]) -> dict[str, Any]:
    ordered = sorted(bars, key=lambda row: row.bar_date)
    if not ordered:
        raise CanaryApprovalError("five ADJUSTED_LAST bars are required")
    entry = Decimal(str(ordered[0].open))
    exit_price = Decimal(str(ordered[-1].close))
    if entry <= 0:
        raise CanaryApprovalError("entry open must be positive")
    highs = [(_pct(Decimal(str(row.high)), entry), index) for index, row in enumerate(ordered, 1)]
    lows = [(_pct(Decimal(str(row.low)), entry), index) for index, row in enumerate(ordered, 1)]
    mfe, sessions_to_mfe = max(highs, key=lambda item: item[0])
    mae, sessions_to_mae = min(lows, key=lambda item: item[0])
    close_return = _pct(exit_price, entry)
    return {
        "entry_price": entry,
        "exit_price": exit_price,
        "close_return_pct": close_return,
        "positive_return": close_return > 0,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "sessions_to_mfe": sessions_to_mfe,
        "sessions_to_mae": sessions_to_mae,
    }


def independent_target_stop(
    bars: Sequence[Any],
    *,
    entry_price: Decimal,
    target_pct: Decimal,
    stop_pct: Decimal,
    same_bar_conflict_policy: str,
) -> dict[str, Any]:
    target_price = entry_price * (Decimal("1") + target_pct / Decimal("100"))
    stop_price = entry_price * (Decimal("1") - stop_pct / Decimal("100"))
    target_hit_any = False
    stop_hit_any = False
    for row in sorted(bars, key=lambda item: item.bar_date):
        target_hit = Decimal(str(row.high)) >= target_price
        stop_hit = Decimal(str(row.low)) <= stop_price
        target_hit_any = target_hit_any or target_hit
        stop_hit_any = stop_hit_any or stop_hit
        if target_hit and stop_hit:
            optimistic = True
            conservative = False
            return {
                "target_hit": True,
                "stop_hit": True,
                "first_event": "SAME_BAR_CONFLICT",
                "event_session": row.bar_date,
                "same_bar_conflict": True,
                "primary_winner": (
                    conservative
                    if same_bar_conflict_policy == "CONSERVATIVE_STOP_FIRST"
                    else optimistic
                ),
                "optimistic_winner": optimistic,
                "conservative_winner": conservative,
            }
        if target_hit:
            return {
                "target_hit": True,
                "stop_hit": stop_hit_any,
                "first_event": "TARGET_FIRST",
                "event_session": row.bar_date,
                "same_bar_conflict": False,
                "primary_winner": True,
                "optimistic_winner": True,
                "conservative_winner": True,
            }
        if stop_hit:
            return {
                "target_hit": target_hit_any,
                "stop_hit": True,
                "first_event": "STOP_FIRST",
                "event_session": row.bar_date,
                "same_bar_conflict": False,
                "primary_winner": False,
                "optimistic_winner": False,
                "conservative_winner": False,
            }
    return {
        "target_hit": target_hit_any,
        "stop_hit": stop_hit_any,
        "first_event": "NEITHER",
        "event_session": None,
        "same_bar_conflict": False,
        "primary_winner": False,
        "optimistic_winner": False,
        "conservative_winner": False,
    }


def build_maturation_canary_manifest(
    db: Session,
    outcome_ids: Sequence[int],
) -> dict[str, Any]:
    ids = tuple(sorted({int(value) for value in outcome_ids}))
    if not ids or len(ids) > MAX_CANARY_OUTCOMES:
        raise CanaryApprovalError("canary must contain between 1 and 30 outcomes")
    outcomes = list(
        db.scalars(
            select(WinnerForwardOutcome)
            .where(WinnerForwardOutcome.id.in_(ids))
            .order_by(WinnerForwardOutcome.id)
        )
    )
    if tuple(int(row.id) for row in outcomes) != ids:
        raise CanaryApprovalError("one or more reviewed outcomes do not exist")

    prediction_ids = {int(row.prediction_id) for row in outcomes}
    predictions = {
        int(row.id): row
        for row in db.scalars(
            select(WinnerPredictionSnapshot).where(WinnerPredictionSnapshot.id.in_(prediction_ids))
        )
    }
    decisions = load_current_temporal_decisions(db, prediction_ids)
    obligations_by_outcome: dict[int, list[WinnerMarketDataObligation]] = {}
    for row in db.scalars(
        select(WinnerMarketDataObligation)
        .where(WinnerMarketDataObligation.forward_outcome_id.in_(ids))
        .order_by(WinnerMarketDataObligation.forward_outcome_id, WinnerMarketDataObligation.id)
    ):
        obligations_by_outcome.setdefault(int(row.forward_outcome_id), []).append(row)

    contract_ids = {
        int(row.ib_contract_id)
        for rows in obligations_by_outcome.values()
        for row in rows
        if row.ib_contract_id is not None
    }
    contracts = {
        int(row.id): row
        for row in db.scalars(select(IBContract).where(IBContract.id.in_(contract_ids)))
    }

    records = [
        _build_outcome_record(
            db,
            outcome,
            prediction=predictions.get(int(outcome.prediction_id)),
            decision=decisions.get(int(outcome.prediction_id)),
            obligations=obligations_by_outcome.get(int(outcome.id), []),
            contracts=contracts,
        )
        for outcome in outcomes
    ]
    return canonicalize_manifest_value(
        {
            "schema": CANARY_SCHEMA,
            "selection_policy": "EXPLICIT_SORTED_OUTCOME_IDS",
            "outcome_count": len(records),
            "outcomes": records,
        }
    )


def execute_reviewed_maturation_canary(
    session_factory: Callable[[], Session],
    manifest: dict[str, Any],
    *,
    reviewed_manifest_hash: str,
    approve_write: bool,
    actor: str,
    request_key: str,
    now: datetime | None = None,
) -> CanaryExecutionResult:
    verify_canary_approval(
        manifest,
        reviewed_manifest_hash=reviewed_manifest_hash,
        approve_write=approve_write,
        actor=actor,
        request_key=request_key,
    )
    reviewed_records = list(manifest["outcomes"])
    ids = tuple(int(item["outcome_id"]) for item in reviewed_records)
    with session_factory() as preflight_db:
        current = build_maturation_canary_manifest(preflight_db, ids)
        if canonical_manifest_bytes(current) != canonical_manifest_bytes(manifest):
            raise CanaryApprovalError("database preflight differs from reviewed manifest")
        preflight_db.rollback()

    execution_now = now or datetime.now(UTC)
    processed = matured = revised = target_stop_matured = warnings = failed = material = 0
    per_outcome: list[dict[str, Any]] = []
    service = OutcomeMaturationService()
    for reviewed in reviewed_records:
        outcome_id = int(reviewed["outcome_id"])
        with session_factory() as db:
            try:
                outcome = db.scalar(
                    select(WinnerForwardOutcome)
                    .where(WinnerForwardOutcome.id == outcome_id)
                    .with_for_update()
                )
                if outcome is None:
                    raise CanaryApprovalError(f"outcome {outcome_id} disappeared")
                current_record = build_maturation_canary_manifest(db, [outcome_id])["outcomes"][0]
                if canonical_manifest_bytes(current_record) != canonical_manifest_bytes(reviewed):
                    raise CanaryApprovalError(f"outcome {outcome_id} changed after review")
                outcome.metadata_json = {
                    **(outcome.metadata_json or {}),
                    "maturation_canary": {
                        "actor": actor.strip(),
                        "request_key": request_key.strip(),
                        "reviewed_manifest_hash": reviewed_manifest_hash,
                    },
                }
                context = service.build_batch_context(db, [outcome])
                result = service.process_forward_outcome(
                    db,
                    outcome,
                    now=execution_now,
                    context=context,
                )
                if result.processed != 1 or result.matured != 1 or result.failed:
                    raise CanaryApprovalError(
                        f"outcome {outcome_id} did not mature exactly once: {result.as_dict()}"
                    )
                db.flush()
                _verify_actual_result(db, reviewed, execution_now=execution_now)
                db.commit()
                processed += result.processed
                matured += result.matured
                revised += result.revised
                target_stop_matured += result.target_stop_matured
                warnings += result.warnings
                failed += result.failed
                material += len(result.material_changes)
                per_outcome.append(
                    {
                        "outcome_id": outcome_id,
                        "status": "MATURED_VERIFIED",
                        **result.as_dict(),
                    }
                )
            except Exception:
                db.rollback()
                raise
    return CanaryExecutionResult(
        reviewed_manifest_hash=reviewed_manifest_hash,
        actor=actor.strip(),
        request_key=request_key.strip(),
        executed_at=execution_now,
        outcome_ids=ids,
        processed=processed,
        matured=matured,
        revised=revised,
        target_stop_matured=target_stop_matured,
        warnings=warnings,
        failed=failed,
        material_evidence_changes=material,
        per_outcome=tuple(per_outcome),
    )


def verify_maturation_canary_results(
    db: Session,
    manifest: dict[str, Any],
    *,
    executed_at: datetime,
) -> None:
    """Assert every persisted canary result exactly matches the reviewed artifact."""
    for reviewed in manifest.get("outcomes", []):
        _verify_actual_result(db, reviewed, execution_now=executed_at)


def _build_outcome_record(
    db: Session,
    outcome: WinnerForwardOutcome,
    *,
    prediction: WinnerPredictionSnapshot | None,
    decision: Any | None,
    obligations: Sequence[WinnerMarketDataObligation],
    contracts: dict[int, IBContract],
) -> dict[str, Any]:
    if prediction is None:
        raise CanaryApprovalError(f"outcome {outcome.id} has no prediction")
    if outcome.entry_model != "NEXT_OPEN" or int(outcome.horizon_sessions) != 5:
        raise CanaryApprovalError(f"outcome {outcome.id} is not H5 NEXT_OPEN")
    if outcome.status != "PENDING" or not outcome.is_current_revision:
        raise CanaryApprovalError(f"outcome {outcome.id} is not current pending")
    if outcome.entry_session is None or outcome.due_session is None:
        raise CanaryApprovalError(f"outcome {outcome.id} has unresolved sessions")
    if decision is None or not prediction_temporally_eligible(prediction, decision):
        raise CanaryApprovalError(f"outcome {outcome.id} lacks an explicit VALID temporal decision")
    if prediction.ticker.upper() == "CLBK":
        raise CanaryApprovalError("CLBK is forbidden in maturation canaries")

    obligation_by_basis = {row.what_to_show: row for row in obligations}
    if set(obligation_by_basis) != {"ADJUSTED_LAST", "TRADES"}:
        raise CanaryApprovalError(f"outcome {outcome.id} must have both basis obligations")
    for basis, obligation in obligation_by_basis.items():
        if obligation.status != "SATISFIED":
            raise CanaryApprovalError(f"outcome {outcome.id} {basis} obligation is not SATISFIED")
        contract = contracts.get(int(obligation.ib_contract_id or 0))
        if contract is None or not _identity_matches(obligation, contract):
            raise CanaryApprovalError(f"outcome {outcome.id} contract identity changed")

    sessions = required_outcome_sessions(outcome.entry_session, 5)
    if sessions[-1] != outcome.due_session:
        raise CanaryApprovalError(f"outcome {outcome.id} due session differs from H5")
    ticker_bars = _load_bars(db, {prediction.ticker.upper()}, sessions[0], sessions[-1])
    selected_basis, selected = complete_basis_for_rows(ticker_bars, sessions)
    if selected_basis != "ADJUSTED_LAST" or len(selected) != 5:
        raise CanaryApprovalError(f"outcome {outcome.id} does not select five ADJUSTED_LAST bars")
    _, trades = complete_basis_for_rows(
        [row for row in ticker_bars if row.what_to_show == "TRADES"], sessions
    )
    if len(trades) != 5:
        raise CanaryApprovalError(f"outcome {outcome.id} does not have five TRADES control bars")
    _validate_ohlc(selected)
    metrics = independent_forward_metrics(selected)

    lineage_bars = list(selected)
    warnings: list[str] = []
    spy_return = _independent_comparison_return(
        db, BENCHMARK_TICKER, sessions, lineage_bars=lineage_bars, warnings=warnings
    )
    sector_proxy = _sector_proxy(prediction)
    sector_return = None
    if sector_proxy is None:
        warnings.append("missing_sector_proxy")
    else:
        sector_return = _independent_comparison_return(
            db, sector_proxy, sessions, lineage_bars=lineage_bars, warnings=warnings
        )
    lineage_hash, cutoff = _independent_lineage(lineage_bars)

    target_rows = list(
        db.scalars(
            select(WinnerTargetStopOutcome)
            .where(WinnerTargetStopOutcome.prediction_id == prediction.id)
            .where(WinnerTargetStopOutcome.entry_model == "NEXT_OPEN")
            .where(WinnerTargetStopOutcome.horizon_sessions == 5)
            .where(WinnerTargetStopOutcome.is_current_revision.is_(True))
            .order_by(WinnerTargetStopOutcome.id)
        )
    )
    definition_ids = {int(row.outcome_definition_id) for row in target_rows}
    definitions = {
        int(row.id): row
        for row in db.scalars(
            select(WinnerOutcomeDefinition).where(WinnerOutcomeDefinition.id.in_(definition_ids))
        )
    }
    target_stops = []
    for row in target_rows:
        definition = definitions.get(int(row.outcome_definition_id))
        policy = (
            definition.same_bar_conflict_policy
            if definition is not None and definition.same_bar_conflict_policy
            else "CONSERVATIVE_STOP_FIRST"
        )
        expected = independent_target_stop(
            selected,
            entry_price=metrics["entry_price"],
            target_pct=Decimal(str(row.target_pct)),
            stop_pct=Decimal(str(row.stop_pct)),
            same_bar_conflict_policy=policy,
        )
        target_stops.append(
            {
                "target_stop_outcome_id": int(row.id),
                "outcome_definition_id": int(row.outcome_definition_id),
                "definition_id": definition.definition_id if definition else None,
                "revision": int(row.revision),
                "status": row.status,
                "target_pct": row.target_pct,
                "stop_pct": row.stop_pct,
                "same_bar_conflict_policy": policy,
                "expected": expected,
            }
        )

    contract = contracts[int(obligation_by_basis["ADJUSTED_LAST"].ib_contract_id)]
    expected = {
        **metrics,
        "spy_return_pct": spy_return,
        "excess_spy_return_pct": (
            metrics["close_return_pct"] - spy_return if spy_return is not None else None
        ),
        "sector_return_pct": sector_return,
        "excess_sector_return_pct": (
            metrics["close_return_pct"] - sector_return if sector_return is not None else None
        ),
        "beat_spy": metrics["close_return_pct"] > spy_return if spy_return is not None else None,
        "beat_sector": (
            metrics["close_return_pct"] > sector_return if sector_return is not None else None
        ),
        "source_bar_lineage_hash": lineage_hash,
        "source_revision_cutoff_at": cutoff,
        "warnings": warnings,
    }
    return {
        "outcome_id": int(outcome.id),
        "prediction_id": int(prediction.id),
        "run_id": int(prediction.run_id),
        "ticker": prediction.ticker.upper(),
        "calculation_version": prediction.calculation_version,
        "setup_family": prediction.setup_family,
        "setup_classification": prediction.setup_classification,
        "ranking_profile": prediction.ranking_profile,
        "temporal_validity_decision_id": int(decision.id),
        "temporal_validity_status": decision.status,
        "ib_contract": _contract_payload(contract),
        "entry_model": outcome.entry_model,
        "horizon_sessions": int(outcome.horizon_sessions),
        "sessions": [value for value in sessions],
        "entry_session": sessions[0],
        "h2_session": sessions[1],
        "h3_session": sessions[2],
        "h4_session": sessions[3],
        "h5_session": sessions[4],
        "due_session": outcome.due_session,
        "selected_basis": selected_basis,
        "selected_bars": [_bar_payload(row) for row in selected],
        "trades_control_bars": [_bar_payload(row) for row in trades],
        "lineage_bars": [_lineage_payload(row) for row in _sort_lineage(lineage_bars)],
        "expected": expected,
        "target_stops": target_stops,
        "retry_baseline": {
            "status": outcome.status,
            "revision": int(outcome.revision),
            "last_attempted_at": outcome.last_attempted_at,
            "retry_not_before_at": outcome.retry_not_before_at,
            "pending_reason_code": outcome.pending_reason_code,
            "last_attempted_bar_watermark": outcome.last_attempted_bar_watermark,
        },
        "obligations": [
            _obligation_payload(row)
            for row in sorted(obligations, key=lambda item: item.what_to_show)
        ],
        "price_series_versions": _series_versions(db, prediction.ticker.upper()),
    }


def _verify_actual_result(
    db: Session,
    reviewed: dict[str, Any],
    *,
    execution_now: datetime,
) -> None:
    outcome = db.get(WinnerForwardOutcome, int(reviewed["outcome_id"]))
    if outcome is None:
        raise CanaryApprovalError("matured outcome disappeared")
    expected = reviewed["expected"]
    actual = canonicalize_manifest_value(
        {
            "status": outcome.status,
            "revision": int(outcome.revision),
            "is_current_revision": outcome.is_current_revision,
            "entry_price": _stored_price(outcome.entry_price),
            "exit_price": _stored_price(outcome.exit_price),
            "close_return_pct": outcome.close_return_pct,
            "spy_return_pct": outcome.spy_return_pct,
            "excess_spy_return_pct": outcome.excess_spy_return_pct,
            "sector_return_pct": outcome.sector_return_pct,
            "excess_sector_return_pct": outcome.excess_sector_return_pct,
            "mfe_pct": outcome.mfe_pct,
            "mae_pct": outcome.mae_pct,
            "sessions_to_mfe": outcome.sessions_to_mfe,
            "sessions_to_mae": outcome.sessions_to_mae,
            "positive_return": outcome.positive_return,
            "beat_spy": outcome.beat_spy,
            "beat_sector": outcome.beat_sector,
            "source_bar_lineage_hash": outcome.source_bar_lineage_hash,
            "source_revision_cutoff_at": outcome.source_revision_cutoff_at,
            "matured_at": outcome.matured_at,
            "pending_reason_code": outcome.pending_reason_code,
            "retry_not_before_at": outcome.retry_not_before_at,
        }
    )
    expected_actual = canonicalize_manifest_value(
        {
            "status": "MATURED",
            "revision": int(reviewed["retry_baseline"]["revision"]),
            "is_current_revision": True,
            "entry_price": _stored_price(expected["entry_price"]),
            "exit_price": _stored_price(expected["exit_price"]),
            "close_return_pct": expected["close_return_pct"],
            "spy_return_pct": expected["spy_return_pct"],
            "excess_spy_return_pct": expected["excess_spy_return_pct"],
            "sector_return_pct": expected["sector_return_pct"],
            "excess_sector_return_pct": expected["excess_sector_return_pct"],
            "mfe_pct": expected["mfe_pct"],
            "mae_pct": expected["mae_pct"],
            "sessions_to_mfe": expected["sessions_to_mfe"],
            "sessions_to_mae": expected["sessions_to_mae"],
            "positive_return": expected["positive_return"],
            "beat_spy": expected["beat_spy"],
            "beat_sector": expected["beat_sector"],
            "source_bar_lineage_hash": expected["source_bar_lineage_hash"],
            "source_revision_cutoff_at": expected["source_revision_cutoff_at"],
            "matured_at": execution_now,
            "pending_reason_code": None,
            "retry_not_before_at": None,
        }
    )
    if actual != expected_actual:
        details = json.dumps({"expected": expected_actual, "actual": actual}, sort_keys=True)
        raise CanaryApprovalError("forward result mismatch: " + details)
    canary_audit = (outcome.metadata_json or {}).get("maturation_canary") or {}
    if set(canary_audit) != {"actor", "request_key", "reviewed_manifest_hash"}:
        raise CanaryApprovalError("durable maturation canary audit metadata is missing")
    for reviewed_target in reviewed["target_stops"]:
        target = db.get(WinnerTargetStopOutcome, int(reviewed_target["target_stop_outcome_id"]))
        if target is None:
            raise CanaryApprovalError("target/stop outcome disappeared")
        expected_target = reviewed_target["expected"]
        actual_target = canonicalize_manifest_value(
            {
                "status": target.status,
                "revision": int(target.revision),
                "is_current_revision": target.is_current_revision,
                "forward_outcome_id": int(target.forward_outcome_id or 0),
                "target_hit": target.target_hit,
                "stop_hit": target.stop_hit,
                "first_event": target.first_event,
                "event_session": target.event_session,
                "same_bar_conflict": target.same_bar_conflict,
                "primary_winner": target.primary_winner,
                "optimistic_winner": target.optimistic_winner,
                "conservative_winner": target.conservative_winner,
                "source_bar_lineage_hash": target.source_bar_lineage_hash,
                "evaluated_at": target.evaluated_at,
            }
        )
        expected_target_actual = canonicalize_manifest_value(
            {
                "status": "MATURED",
                "revision": int(reviewed_target["revision"]),
                "is_current_revision": True,
                "forward_outcome_id": int(reviewed["outcome_id"]),
                **expected_target,
                "source_bar_lineage_hash": expected["source_bar_lineage_hash"],
                "evaluated_at": execution_now,
            }
        )
        if actual_target != expected_target_actual:
            raise CanaryApprovalError("target/stop result differs from reviewed expectation")


def _independent_comparison_return(
    db: Session,
    ticker: str,
    sessions: Sequence[date],
    *,
    lineage_bars: list[PriceBar],
    warnings: list[str],
) -> Decimal | None:
    rows = _load_bars(db, {ticker}, sessions[0], sessions[-1])
    adjusted = [row for row in rows if row.what_to_show == "ADJUSTED_LAST"]
    trades = [row for row in rows if row.what_to_show == "TRADES"]
    chosen = adjusted or trades
    by_date = {row.bar_date: row for row in chosen}
    if not all(value in by_date for value in sessions):
        warnings.append(f"missing_{ticker.lower()}_benchmark_data")
        return None
    selected = [by_date[value] for value in sessions]
    try:
        _validate_ohlc(selected)
    except CanaryApprovalError:
        warnings.append(f"invalid_{ticker.lower()}_benchmark_data")
        return None
    entry = Decimal(str(selected[0].open))
    if entry <= 0:
        warnings.append(f"invalid_{ticker.lower()}_entry_price")
        return None
    lineage_bars.extend(selected)
    return _pct(Decimal(str(selected[-1].close)), entry)


def _independent_lineage(bars: Sequence[PriceBar]) -> tuple[str, datetime | None]:
    payload = [_lineage_payload(row) for row in _sort_lineage(bars)]
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    cutoff = max(
        (
            value
            for row in bars
            for value in (row.revised_at, row.last_seen_at, row.created_at)
            if isinstance(value, datetime)
        ),
        default=None,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest(), cutoff


def _lineage_payload(row: PriceBar) -> dict[str, Any]:
    return {
        "ticker": row.ticker.upper(),
        "bar_date": row.bar_date.isoformat(),
        "what_to_show": row.what_to_show,
        "timeframe": row.timeframe,
        "data_hash": row.data_hash or price_bar_data_hash(row),
        "revision_count": int(row.revision_count or 0),
    }


def _bar_payload(row: PriceBar) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "ticker": row.ticker.upper(),
        "bar_date": row.bar_date,
        "timeframe": row.timeframe,
        "what_to_show": row.what_to_show,
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
        "data_hash": row.data_hash or price_bar_data_hash(row),
        "revision_count": int(row.revision_count or 0),
        "created_at": row.created_at,
        "last_seen_at": row.last_seen_at,
        "revised_at": row.revised_at,
    }


def _obligation_payload(row: WinnerMarketDataObligation) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "what_to_show": row.what_to_show,
        "status": row.status,
        "ib_contract_id": int(row.ib_contract_id or 0),
        "ib_conid_snapshot": int(row.ib_conid_snapshot or 0),
        "ticker_snapshot": row.ticker_snapshot,
        "required_sessions": row.required_sessions_json,
        "price_series_watermark": row.price_series_watermark,
    }


def _contract_payload(row: IBContract) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "ib_conid": int(row.ib_conid or 0),
        "ticker": row.ticker,
        "symbol": row.symbol,
        "local_symbol": row.local_symbol,
        "exchange": row.exchange,
        "primary_exchange": row.primary_exchange,
        "currency": row.currency,
        "sec_type": row.sec_type,
        "trading_class": row.trading_class,
        "resolution_status": row.resolution_status,
    }


def _series_versions(db: Session, ticker: str) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(PriceSeriesVersion)
            .where(PriceSeriesVersion.ticker == ticker)
            .where(PriceSeriesVersion.timeframe == "1 day")
            .where(PriceSeriesVersion.what_to_show.in_(("ADJUSTED_LAST", "TRADES")))
            .order_by(PriceSeriesVersion.what_to_show)
        )
    )
    return [
        {
            "id": int(row.id),
            "what_to_show": row.what_to_show,
            "series_version": int(row.series_version),
            "bar_count": int(row.bar_count),
            "first_bar_date": row.first_bar_date,
            "latest_bar_date": row.latest_bar_date,
            "last_changed_at": row.last_changed_at,
        }
        for row in rows
    ]


def _load_bars(db: Session, tickers: set[str], start: date, end: date) -> list[PriceBar]:
    return list(
        db.scalars(
            select(PriceBar)
            .where(PriceBar.ticker.in_(sorted(tickers)))
            .where(PriceBar.timeframe == "1 day")
            .where(PriceBar.what_to_show.in_(("ADJUSTED_LAST", "TRADES")))
            .where(PriceBar.bar_date >= start)
            .where(PriceBar.bar_date <= end)
            .order_by(PriceBar.ticker, PriceBar.bar_date, PriceBar.id)
        )
    )


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


def _sector_proxy(prediction: WinnerPredictionSnapshot) -> str | None:
    sector = (
        (prediction.feature_json or {}).get("canonical_sector")
        or (prediction.feature_json or {}).get("raw_sector")
        or getattr(prediction, "sector", None)
    )
    if not sector:
        return None
    try:
        proxy = load_sector_rotation_config().get("sector_etf_proxies", {}).get(sector)
    except Exception:
        return None
    return str(proxy).upper() if proxy else None


def _sort_lineage(bars: Sequence[PriceBar]) -> list[PriceBar]:
    return sorted(bars, key=lambda item: (item.ticker, item.bar_date, item.what_to_show))


def _validate_ohlc(bars: Sequence[PriceBar]) -> None:
    adjustment_basis = {(row.what_to_show, row.adjustment_type or "") for row in bars}
    if len(adjustment_basis) > 1:
        raise CanaryApprovalError("selected bars have mixed adjustment basis")
    for row in bars:
        if any(value is None for value in (row.open, row.high, row.low, row.close)):
            raise CanaryApprovalError("selected bar is missing OHLC")
        open_price = Decimal(str(row.open))
        high = Decimal(str(row.high))
        low = Decimal(str(row.low))
        close = Decimal(str(row.close))
        if min(open_price, close) < low or max(open_price, close) > high or low > high:
            raise CanaryApprovalError("selected bar has invalid OHLC")


def _pct(value: Decimal, entry: Decimal) -> Decimal:
    return ((value - entry) / entry * Decimal("100")).quantize(_QUANTUM)


def _stored_price(value: Any) -> Decimal | None:
    """Normalize to the persisted ``NUMERIC(18, 6)`` representation."""
    return Decimal(str(value)).quantize(_QUANTUM) if value is not None else None
