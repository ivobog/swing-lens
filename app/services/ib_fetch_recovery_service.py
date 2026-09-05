from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import IBFetchItem, IBFetchRun
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
)

INTERRUPTED_CANARY_FINALIZATION_SCHEMA = (
    "swinglens-ib-interrupted-canary-finalization-v1"
)
_REJECTED_MESSAGE = (
    "IB historical request error 321: explicit end date is not supported "
    "with ADJUSTED_LAST."
)


@dataclass(frozen=True)
class InterruptedCanaryFinalizationResult:
    fetch_run_id: int
    manifest_hash: str
    changed_items: int
    run_status: str


def build_interrupted_canary_finalization_manifest(
    db: Session,
    *,
    fetch_run_id: int,
) -> dict[str, Any]:
    """Describe, without writing, the only certified corrections to an interrupted run."""

    fetch_run = db.get(IBFetchRun, fetch_run_id)
    if fetch_run is None:
        raise ValueError(f"IB fetch run {fetch_run_id} was not found.")
    if fetch_run.status != "RUNNING":
        raise ValueError(f"IB fetch run {fetch_run_id} is not interrupted/running.")
    items = list(
        db.scalars(
            select(IBFetchItem)
            .where(IBFetchItem.fetch_run_id == fetch_run_id)
            .order_by(IBFetchItem.id)
        )
    )
    decisions = [_finalization_decision(item) for item in items]
    proposed_statuses = {
        int(row["fetch_item_id"]): str(row["proposed_status"]) for row in decisions
    }
    expected = _expected_totals(items, proposed_statuses)
    payload: dict[str, Any] = {
        "schema": INTERRUPTED_CANARY_FINALIZATION_SCHEMA,
        "fetch_run_id": int(fetch_run.id),
        "source_run_status": fetch_run.status,
        "planned_request_count": int(fetch_run.planned_request_count or 0),
        "materialized_item_count": len(items),
        "unmaterialized_request_count": max(
            0, int(fetch_run.planned_request_count or 0) - len(items)
        ),
        "expected_run_status": _status_from_totals(expected),
        "expected_totals": expected,
        "items": decisions,
    }
    payload["manifest_hash"] = _hash_without_manifest_hash(payload)
    return payload


def apply_interrupted_canary_finalization(
    db: Session,
    *,
    manifest: dict[str, Any],
    reviewed_manifest_hash: str,
    actor: str,
    request_key: str,
    approve_write: bool,
    now: datetime | None = None,
) -> InterruptedCanaryFinalizationResult:
    """Atomically terminalize a reviewed interrupted canary; never touches price bars."""

    if not approve_write:
        raise ValueError("approve_write=True is required")
    if not actor.strip() or not request_key.strip():
        raise ValueError("actor and request_key are required")
    if manifest.get("schema") != INTERRUPTED_CANARY_FINALIZATION_SCHEMA:
        raise ValueError("unsupported interrupted-canary finalization schema")
    embedded_hash = str(manifest.get("manifest_hash") or "")
    computed_hash = _hash_without_manifest_hash(manifest)
    if not reviewed_manifest_hash or reviewed_manifest_hash != embedded_hash:
        raise ValueError("reviewed manifest hash does not match the artifact")
    if computed_hash != reviewed_manifest_hash:
        raise ValueError("manifest hash does not match canonical manifest contents")

    fetch_run_id = int(manifest["fetch_run_id"])
    fetch_run = db.scalar(
        select(IBFetchRun).where(IBFetchRun.id == fetch_run_id).with_for_update()
    )
    if fetch_run is None:
        raise ValueError(f"IB fetch run {fetch_run_id} was not found.")
    prior_audit = (fetch_run.decision_counts_json or {}).get("controlled_finalization")
    if prior_audit is not None:
        if (
            prior_audit.get("manifest_hash") == reviewed_manifest_hash
            and prior_audit.get("request_key") == request_key
        ):
            return InterruptedCanaryFinalizationResult(
                fetch_run_id=fetch_run_id,
                manifest_hash=reviewed_manifest_hash,
                changed_items=0,
                run_status=fetch_run.status,
            )
        raise ValueError("fetch run was already finalized under a different audit identity")

    live = build_interrupted_canary_finalization_manifest(db, fetch_run_id=fetch_run_id)
    if canonical_manifest_bytes(live) != canonical_manifest_bytes(manifest):
        raise ValueError("live interrupted run differs from the reviewed manifest")

    observed_at = now or datetime.now(UTC)
    items = {
        int(item.id): item
        for item in db.scalars(
            select(IBFetchItem)
            .where(IBFetchItem.fetch_run_id == fetch_run_id)
            .with_for_update()
        )
    }
    changed = 0
    audit = {
        "actor": actor,
        "request_key": request_key,
        "manifest_hash": reviewed_manifest_hash,
        "finalized_at": observed_at.isoformat(),
    }
    for decision in manifest["items"]:
        item = items[int(decision["fetch_item_id"])]
        disposition = decision["disposition"]
        if disposition == "RETAIN_SUCCESS":
            continue
        metadata = dict(item.decision_metadata_json or {})
        metadata["controlled_finalization"] = audit
        if disposition == "FINALIZE_PROVIDER_REJECTED":
            item.status = "FAILED"
            item.error_message = _REJECTED_MESSAGE
            metadata.update(
                {
                    "provider_result": "PROVIDER_REJECTED",
                    "provider_error_code": 321,
                    "provider_error_message": _REJECTED_MESSAGE,
                    "boundary_status": "NOT_EVALUATED",
                }
            )
        elif disposition == "FINALIZE_NOT_ATTEMPTED":
            item.status = "SKIPPED"
            item.reason = "Interrupted before the reviewed provider request was attempted."
            item.error_message = None
            metadata.update(
                {
                    "provider_result": "NOT_ATTEMPTED",
                    "boundary_status": "NOT_EXECUTED",
                }
            )
        else:  # pragma: no cover - manifest builder prevents this
            raise ValueError(f"unsupported finalization disposition: {disposition}")
        item.decision_metadata_json = metadata
        item.completed_at = observed_at
        changed += 1

    totals = manifest["expected_totals"]
    for name, value in totals.items():
        setattr(fetch_run, name, int(value))
    fetch_run.status = str(manifest["expected_run_status"])
    fetch_run.completed_at = observed_at
    fetch_run.last_progress_at = observed_at
    fetch_run.progress_sequence = int(fetch_run.progress_sequence or 0) + 1
    run_metadata = dict(fetch_run.decision_counts_json or {})
    run_metadata["controlled_finalization"] = {
        **audit,
        "unmaterialized_request_count": int(manifest["unmaterialized_request_count"]),
    }
    fetch_run.decision_counts_json = run_metadata
    fetch_run.message = (
        "Interrupted canary terminalized by reviewed manifest "
        f"{reviewed_manifest_hash}; {totals['success_count']} succeeded, "
        f"{totals['failure_count']} failed, {totals['skipped_count']} materialized "
        f"items not attempted, {manifest['unmaterialized_request_count']} requests "
        "not materialized."
    )
    db.flush()
    return InterruptedCanaryFinalizationResult(
        fetch_run_id=fetch_run_id,
        manifest_hash=reviewed_manifest_hash,
        changed_items=changed,
        run_status=fetch_run.status,
    )


def _finalization_decision(item: IBFetchItem) -> dict[str, Any]:
    metadata = item.decision_metadata_json or {}
    base = {
        "fetch_item_id": int(item.id),
        "ticker": item.ticker,
        "what_to_show": item.what_to_show,
        "source_status": item.status,
        "attempt_count": int(item.attempt_count or 0),
        "fetched": int(item.fetched or 0),
        "inserted": int(item.inserted or 0),
        "updated": int(item.updated or 0),
        "revised": int(item.revised or 0),
        "unchanged": int(item.unchanged or 0),
        "request_end_datetime": metadata.get("request_end_datetime"),
    }
    if item.status == "SUCCESS" and int(item.fetched or 0) > 0:
        return {**base, "disposition": "RETAIN_SUCCESS", "proposed_status": "SUCCESS"}
    if (
        item.status == "SUCCESS"
        and int(item.fetched or 0) == 0
        and item.what_to_show == "ADJUSTED_LAST"
        and bool(metadata.get("request_end_datetime"))
    ):
        return {
            **base,
            "disposition": "FINALIZE_PROVIDER_REJECTED",
            "proposed_status": "FAILED",
            "provider_error_code": 321,
        }
    if item.status in {"RUNNING", "PLANNED"} and int(item.attempt_count or 0) == 0:
        return {
            **base,
            "disposition": "FINALIZE_NOT_ATTEMPTED",
            "proposed_status": "SKIPPED",
        }
    raise ValueError(
        "interrupted canary contains an item without a certified finalization rule: "
        f"{item.id} {item.ticker}/{item.what_to_show} {item.status}"
    )


def _expected_totals(
    items: list[IBFetchItem],
    proposed_statuses: dict[int, str],
) -> dict[str, int]:
    return {
        "executed_request_count": sum(int(item.attempt_count or 0) > 0 for item in items),
        "skipped_count": sum(proposed_statuses[int(item.id)] == "SKIPPED" for item in items),
        "success_count": sum(proposed_statuses[int(item.id)] == "SUCCESS" for item in items),
        "failure_count": sum(proposed_statuses[int(item.id)] == "FAILED" for item in items),
        "fetched_count": sum(int(item.fetched or 0) for item in items),
        "inserted_count": sum(int(item.inserted or 0) for item in items),
        "updated_count": sum(int(item.updated or 0) for item in items),
        "revised_count": sum(int(item.revised or 0) for item in items),
        "unchanged_count": sum(int(item.unchanged or 0) for item in items),
    }


def _status_from_totals(totals: dict[str, int]) -> str:
    if totals["failure_count"] and totals["success_count"]:
        return "PARTIAL"
    if totals["failure_count"]:
        return "FAILED"
    return "COMPLETED"


def _hash_without_manifest_hash(payload: dict[str, Any]) -> str:
    unhashed = {key: value for key, value in payload.items() if key != "manifest_hash"}
    return hashlib.sha256(canonical_manifest_bytes(unhashed)).hexdigest()
