"""Explicit, bounded repair of receipt-backed historical session obligations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from app.models.tables import IBFetchRun, MarketCalculationContext, PipelineRun, WorkScopeRecord
from app.services.background_job_service import active_job_for_request_key, enqueue_job
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.configuration_delivery import ANCHOR_KEY, binding_reference, current_delivery
from app.services.ib_bar_admission_service import load_bar_admissions
from app.services.ib_fetch_job_service import (
    IB_FETCH_JOB_TYPE,
    FetchJobOptions,
    _admit_fetch_authority,
    create_queued_fetch_run,
)
from app.services.ib_fetch_plan_service import (
    FetchAction,
    FetchPlan,
    FetchPlanItem,
    fetch_plan_to_dict,
)
from app.services.ib_historical_request_scope import (
    DAILY_BAR_ADMISSION_V4,
    build_historical_request_scope,
    validate_reviewed_session_current,
)
from app.services.scope_refresh_adoption import bind_semantic_authority, require_semantic_authority
from app.services.us_market_calendar import (
    latest_completed_us_trading_day,
    us_trading_sessions_between,
)
from app.services.work_scope_identity import AcquisitionPlanRevisionReason


@dataclass(frozen=True)
class GapRepairPreview:
    manifest: dict
    manifest_fingerprint: str
    plan: FetchPlan

    def as_dict(self) -> dict:
        return {
            "manifest": self.manifest,
            "manifest_fingerprint": self.manifest_fingerprint,
            "fetch_plan": fetch_plan_to_dict(self.plan),
        }


@dataclass(frozen=True)
class GapRepairSubmission:
    fetch_run_id: int
    job_id: int
    manifest_fingerprint: str
    manifest: dict


def _receipt_key(receipt) -> tuple[str, str, str, str]:
    return (
        receipt.ticker,
        receipt.what_to_show,
        receipt.timeframe,
        Canonical.fingerprint(receipt.contract_identity),
    )


def build_gap_repair_preview(
    *, pipeline, context, receipts, configuration_reference
) -> GapRepairPreview:
    """Use immutable acceptance manifests, never the cache's latest-date heuristic."""
    if not receipts:
        raise ValueError("GAP_REPAIR_RECEIPT_REQUIRED")
    grouped: dict[tuple, list] = {}
    for receipt in receipts:
        grouped.setdefault(_receipt_key(receipt), []).append(receipt)
    items, obligations = [], []
    for (ticker, feed, timeframe, contract_hash), members in sorted(grouped.items()):
        accepted = {
            date.fromisoformat(bar["bar_date"])
            for receipt in members
            for bar in receipt.payload_json["accepted_bars"]
        }
        pending = sorted(
            {day for receipt in members for day in receipt.missing_sessions} - accepted
        )
        if not pending:
            continue
        source = max(members, key=lambda receipt: (receipt.accepted_at, receipt.receipt_hash))
        reviewed_end = context.latest_completed_session if feed == "ADJUSTED_LAST" else pending[-1]
        duration = f"{1 + us_trading_sessions_between(pending[0], reviewed_end)} D"
        scope = build_historical_request_scope(
            required_start_date=pending[0],
            required_end_date=pending[-1],
            reviewed_end_date=reviewed_end,
            duration=duration,
            bar_size=timeframe,
            what_to_show=feed,
            response_admission_policy=DAILY_BAR_ADMISSION_V4,
        )
        item = FetchPlanItem(
            ticker=ticker,
            contract_status="RESOLVED",
            what_to_show=feed,
            action=FetchAction.TOP_UP_RECENT,
            duration=duration,
            bar_size=timeframe,
            current_bar_count=len(accepted),
            first_bar_date=min(accepted, default=None),
            latest_bar_date=max(accepted, default=None),
            required_bars=0,
            reason="Explicit repair of immutable historical-session obligations.",
            estimated_request_count=1,
            data_role="REQUESTED",
            coverage_state="HISTORY_GAPS",
            existing_coverage_reused=True,
            full_backfill_completed=False,
            freshness_threshold_date=context.latest_completed_session,
            missing_start_date=pending[0],
            missing_end_date=pending[-1],
            request_start_date=scope.reviewed_start_date,
            request_end_date=scope.reviewed_end_date,
            request_end_datetime=scope.end_datetime,
            request_end_mode=scope.end_mode.value,
            reviewed_session_expiry=scope.reviewed_session_expiry,
            contract_identity=source.contract_identity,
            contract_fingerprint=contract_hash,
            response_admission_policy=DAILY_BAR_ADMISSION_V4,
        )
        items.append(item)
        obligations.append(
            {
                "ticker": ticker,
                "what_to_show": feed,
                "timeframe": timeframe,
                "missing_sessions": [day.isoformat() for day in pending],
                "contract_identity": source.contract_identity,
                "contract_fingerprint": contract_hash,
                "source_receipt_hashes": sorted(receipt.receipt_hash for receipt in members),
                "source_plan_id": source.acquisition_plan_id,
                "request_scope": scope.to_dict(),
            }
        )
    if not items:
        raise ValueError("NO_OUTSTANDING_HISTORY_GAPS")
    symbols = sorted({item.ticker for item in items})
    plan = FetchPlan(
        run_id=pipeline.upload_run_id,
        requested_tickers=symbols,
        symbols_including_benchmarks=symbols,
        items=items,
        estimated_request_count=len(items),
        estimated_full_backfills=0,
        estimated_top_ups=len(items),
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
        decision_counts={"history_gap_repair": len(items)},
    )
    manifest = Canonical.canonicalize(
        {
            "version": "ib-history-gap-repair-v1",
            "pipeline_run_id": pipeline.id,
            "upload_run_id": pipeline.upload_run_id,
            "parent_scope_id": pipeline.scope_id,
            "market_calculation_context_id": context.id,
            "market_cutoff_at": context.cutoff_at,
            "expected_session": context.latest_completed_session,
            "configuration_reference": configuration_reference,
            "obligations": obligations,
            "fetch_plan": fetch_plan_to_dict(plan),
        }
    )
    return GapRepairPreview(manifest, Canonical.fingerprint(manifest), plan)


def prepare_gap_repair(db, *, pipeline_run_id: int) -> GapRepairPreview:
    pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None or pipeline.status not in {"BLOCKED", "PARTIAL", "COMPLETED"}:
        raise ValueError("GAP_REPAIR_REQUIRES_TERMINAL_PARENT")
    require_semantic_authority(pipeline)
    context_id = (pipeline.result_json or {}).get("market_calculation_context_id")
    context = db.get(MarketCalculationContext, context_id) if context_id else None
    if (
        context is None
        or context.pipeline_run_id != pipeline.id
        or context.upload_run_id != pipeline.upload_run_id
    ):
        raise ValueError("GAP_REPAIR_FROZEN_CONTEXT_REQUIRED")
    preview = build_gap_repair_preview(
        pipeline=pipeline,
        context=context,
        receipts=load_bar_admissions(db, pipeline_run_id=pipeline.id),
        configuration_reference=binding_reference(db, pipeline_run_id=pipeline.id),
    )
    for obligation in preview.manifest["obligations"]:
        raw = obligation["request_scope"]
        scope = build_historical_request_scope(
            required_start_date=date.fromisoformat(raw["required_start_date"]),
            required_end_date=date.fromisoformat(raw["required_end_date"]),
            reviewed_end_date=date.fromisoformat(raw["reviewed_end_date"]),
            duration=raw["duration"],
            bar_size=raw["bar_size"],
            what_to_show=raw["what_to_show"],
            response_admission_policy=DAILY_BAR_ADMISSION_V4,
        )
        validate_reviewed_session_current(
            scope, latest_completed_session=latest_completed_us_trading_day()
        )
    return preview


def submit_gap_repair(
    db,
    *,
    pipeline_run_id: int,
    expected_manifest_fingerprint: str,
) -> GapRepairSubmission:
    # Serialize operator submissions on the unchanged parent; never resume its failed items.
    pipeline = db.scalar(
        select(PipelineRun).where(PipelineRun.id == pipeline_run_id).with_for_update()
    )
    if pipeline is None:
        raise ValueError("GAP_REPAIR_PARENT_NOT_FOUND")
    preview = prepare_gap_repair(db, pipeline_run_id=pipeline_run_id)
    if (
        not expected_manifest_fingerprint
        or preview.manifest_fingerprint != expected_manifest_fingerprint
    ):
        raise ValueError("GAP_REPAIR_MANIFEST_CHANGED")
    request_key = f"ib-history-gap-repair:{pipeline.id}:{preview.manifest_fingerprint}"
    existing = active_job_for_request_key(db, IB_FETCH_JOB_TYPE, request_key)
    if existing is not None:
        return GapRepairSubmission(
            int(existing.payload_json["fetch_run_id"]),
            existing.id,
            preview.manifest_fingerprint,
            preview.manifest,
        )
    options = FetchJobOptions(include_benchmarks=False)
    authority = _admit_fetch_authority(
        db,
        preview.plan,
        options,
        parent_scope_id=pipeline.scope_id,
        previous_plan_id=preview.manifest["obligations"][0]["source_plan_id"],
        revision_reason=AcquisitionPlanRevisionReason.MISSING_SOURCE,
    )
    fetch_run: IBFetchRun = create_queued_fetch_run(
        db, preview.plan, options, semantic_authority=authority
    )
    job = enqueue_job(
        db,
        IB_FETCH_JOB_TYPE,
        {
            "fetch_run_id": fetch_run.id,
            "run_id": pipeline.upload_run_id,
            "pipeline_run_id": pipeline.id,
            "tickers": preview.plan.requested_tickers,
            "include_benchmarks": False,
            "force_refresh": False,
            "force_full_backfill": False,
            "fetch_plan": fetch_plan_to_dict(preview.plan),
            "gap_repair_manifest": preview.manifest,
            "gap_repair_manifest_fingerprint": preview.manifest_fingerprint,
            "market_calculation_context_id": preview.manifest["market_calculation_context_id"],
            "market_cutoff_at": preview.manifest["market_cutoff_at"],
            "input_as_of_session": preview.manifest["expected_session"],
            ANCHOR_KEY: preview.manifest["configuration_reference"],
        },
        related_run_id=pipeline.upload_run_id,
        request_key=request_key,
        max_retries=1,
    )
    bind_semantic_authority(job, authority)
    return GapRepairSubmission(fetch_run.id, job.id, preview.manifest_fingerprint, preview.manifest)


def validate_gap_repair_delivery(db, *, job, fetch_run, plan) -> None:
    """Recheck immutable obligations and child authority before any provider request."""
    payload = job.payload_json or {}
    manifest = payload.get("gap_repair_manifest")
    fingerprint = payload.get("gap_repair_manifest_fingerprint")
    if (
        not isinstance(manifest, dict)
        or not fingerprint
        or Canonical.fingerprint(manifest) != fingerprint
        or manifest.get("version") != "ib-history-gap-repair-v1"
    ):
        raise ValueError("GAP_REPAIR_MANIFEST_INVALID")
    delivery = current_delivery()
    if delivery is None:
        raise ValueError("GAP_REPAIR_CONFIGURATION_DELIVERY_REQUIRED")
    if delivery.anchor != manifest.get("configuration_reference"):
        raise ValueError("GAP_REPAIR_CONFIGURATION_DELIVERY_MISMATCH")
    if (
        payload.get("market_calculation_context_id") != manifest["market_calculation_context_id"]
        or payload.get("market_cutoff_at") != manifest["market_cutoff_at"]
        or payload.get("input_as_of_session") != manifest["expected_session"]
    ):
        raise ValueError("GAP_REPAIR_CONTEXT_PAYLOAD_CHANGED")
    preview = prepare_gap_repair(db, pipeline_run_id=manifest["pipeline_run_id"])
    serialized = fetch_plan_to_dict(plan)
    scope = db.get(WorkScopeRecord, fetch_run.scope_id)
    if (
        preview.manifest_fingerprint != fingerprint
        or serialized != manifest.get("fetch_plan")
        or fetch_run.run_id != manifest["upload_run_id"]
        or payload.get("pipeline_run_id") != manifest["pipeline_run_id"]
        or scope is None
        or scope.scope_kind != "ib-price-acquisition"
        or scope.parent_scope_id != manifest["parent_scope_id"]
        or scope.acquisition_plan_id != fetch_run.acquisition_plan_id
        or (scope.payload_json.get("scope_definition") or {}).get("exact_fetch_plan_hash")
        != Canonical.fingerprint(serialized)
    ):
        raise ValueError("GAP_REPAIR_DELIVERY_CHANGED")
