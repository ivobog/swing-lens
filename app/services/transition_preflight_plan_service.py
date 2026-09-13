from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriScoreSnapshot, CeriSourceRecord
from app.models.tables import (
    MarketCalculationContext,
    MarketRegimeSnapshot,
    PipelineRun,
    RawCompanyRow,
    SectorRotationSnapshot,
    TechnicalScore,
    TransitionDecisionHandoffManifest,
    TransitionPreflightPlan,
    UploadRun,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.evidence_eligibility import eligible_snapshot_predicate
from app.services.ceri.pit_eligibility import price_bar_is_eligible
from app.services.market_calculation_context_service import (
    attach_reserved_market_context,
    cutoff_from_row,
    reserve_preflight_market_context,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.setup_lifecycle.decision_manifest import (
    build_transition_decision_manifest,
    candidate_type_for,
)
from app.services.setup_lifecycle.transition_candidate_service import (
    TransitionCandidateDiscoveryService,
    TransitionCandidateResult,
    _prospective_inputs_are_complete,
    _technical_reconstruction_fingerprint,
    aggregate_evidence_fingerprint,
)

DEFAULT_PREFLIGHT_TTL = timedelta(minutes=30)
RUN_START_ANCHOR_CONTRACT = "transition-run-start-anchor-v1"
DECISION_HANDOFF_CONTRACT = "transition-decision-handoff-v1"
logger = logging.getLogger(__name__)


class TransitionPreflightPlanStatus(StrEnum):
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"


PREFLIGHT_STATE_TRANSITIONS = {
    (TransitionPreflightPlanStatus.RESERVED, "CONSUME"): TransitionPreflightPlanStatus.CONSUMED,
    (TransitionPreflightPlanStatus.RESERVED, "CANCEL"): TransitionPreflightPlanStatus.CANCELLED,
    (TransitionPreflightPlanStatus.RESERVED, "EXPIRE"): TransitionPreflightPlanStatus.EXPIRED,
    (TransitionPreflightPlanStatus.RESERVED, "INVALIDATE"): TransitionPreflightPlanStatus.STALE,
}


class TransitionPreflightError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class VerifiedTransitionPreflight:
    plan: TransitionPreflightPlan
    market_cutoff: MarketCalculationCutoff
    results: tuple[TransitionCandidateResult, ...]


def create_transition_preflight_plan(
    db: Session,
    *,
    upload_run_id: int,
    idempotency_key: str,
    tickers: set[str] | None = None,
    cutoff_at: datetime | None = None,
    expires_in: timedelta = DEFAULT_PREFLIGHT_TTL,
    discovery: TransitionCandidateDiscoveryService | None = None,
) -> TransitionPreflightPlan:
    """Freeze and persist a retryable preflight contract without business writes."""

    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise ValueError("idempotency_key is required")
    existing = db.scalar(
        select(TransitionPreflightPlan).where(
            TransitionPreflightPlan.idempotency_key == normalized_key
        )
    )
    if existing is not None:
        if existing.upload_run_id != upload_run_id:
            raise TransitionPreflightError(
                "IDEMPOTENCY_CONFLICT",
                "the idempotency key is already bound to a different upload run",
            )
        if tickers is not None and sorted(ticker.strip().upper() for ticker in tickers) != sorted(
            existing.tickers_json
        ):
            raise TransitionPreflightError(
                "IDEMPOTENCY_CONFLICT",
                "the idempotency key is already bound to a different ticker set",
            )
        if cutoff_at is not None:
            existing_context = db.get(
                MarketCalculationContext,
                existing.market_calculation_context_id,
            )
            if existing_context is None or existing_context.cutoff_at != _aware(cutoff_at):
                raise TransitionPreflightError(
                    "IDEMPOTENCY_CONFLICT",
                    "the idempotency key is already bound to a different cutoff instant",
                )
        return existing

    market_cutoff = reserve_preflight_market_context(
        db,
        upload_run_id=upload_run_id,
        cutoff_at=cutoff_at,
    )
    results = (discovery or TransitionCandidateDiscoveryService()).discover_for_run(
        db,
        upload_run_id,
        market_cutoff=market_cutoff,
        tickers=tickers,
    )
    evidence_fingerprint = aggregate_evidence_fingerprint(results)
    technical_fingerprint = _aggregate_technical_fingerprint(results)
    run_start_anchor = build_run_start_anchor_manifest(
        db,
        upload_run_id=upload_run_id,
        market_cutoff=market_cutoff,
        results=results,
    )
    run_start_anchor_fingerprint = CanonicalEvidenceSerializer.fingerprint(run_start_anchor)
    now = _utcnow()
    plan = TransitionPreflightPlan(
        market_calculation_context_id=market_cutoff.context_id,
        upload_run_id=upload_run_id,
        pipeline_run_id=None,
        status="RESERVED",
        idempotency_key=normalized_key,
        candidate_classification=_aggregate_classification(results),
        tickers_json=sorted(row.ticker for row in results),
        selection_keys_json=sorted(row.prospective_new_key for row in results),
        expected_pointers_json={
            row.ticker: {
                "latest_snapshot_id": row.current_pointer_snapshot_id,
                "latest_revision": row.expected_latest_pointer_revision,
                "exact_snapshot_id": row.expected_exact_pointer_snapshot_id,
                "exact_revision": row.expected_exact_pointer_revision,
            }
            for row in results
        },
        predicted_snapshot_identities_json={
            row.ticker: {
                "selection_key": row.prospective_new_key,
                "latest_reconstructable_session": CanonicalEvidenceSerializer.canonicalize(
                    row.latest_reconstructable_session
                ),
                "technical_reconstruction_fingerprint": (row.technical_reconstruction_fingerprint),
                "evidence_fingerprint": row.evidence_fingerprint,
            }
            for row in results
        },
        candidate_results_json=[_run_start_candidate_dict(row) for row in results],
        evidence_fingerprint=evidence_fingerprint,
        technical_reconstruction_fingerprint=technical_fingerprint,
        run_start_anchor_json=run_start_anchor,
        run_start_anchor_fingerprint=run_start_anchor_fingerprint,
        created_at=now,
        expires_at=now + expires_in,
    )
    db.add(plan)
    db.flush()
    return plan


def pipeline_for_consumed_preflight(db: Session, plan_id: int) -> PipelineRun | None:
    plan = db.get(TransitionPreflightPlan, plan_id)
    if plan is None or plan.status != "CONSUMED" or plan.pipeline_run_id is None:
        return None
    return db.get(PipelineRun, plan.pipeline_run_id)


def verify_transition_preflight_for_enqueue(
    db: Session,
    *,
    plan_id: int,
    upload_run_id: int,
    discovery: TransitionCandidateDiscoveryService | None = None,
    now: datetime | None = None,
) -> VerifiedTransitionPreflight:
    plan = db.scalar(
        select(TransitionPreflightPlan)
        .where(TransitionPreflightPlan.id == plan_id)
        .with_for_update()
    )
    if plan is None:
        raise TransitionPreflightError("STALE_PREFLIGHT", f"plan {plan_id} was not found")
    if plan.upload_run_id != upload_run_id:
        raise TransitionPreflightError("STALE_PREFLIGHT", "plan belongs to a different upload run")
    if plan.status == "CONSUMED":
        raise TransitionPreflightError("DUPLICATE_ENQUEUE", "plan already has a pipeline")
    if plan.status == "CANCELLED":
        raise TransitionPreflightError("PLAN_CANCELLED", "plan was cancelled")
    if plan.status == "EXPIRED":
        raise TransitionPreflightError("PLAN_EXPIRED", "plan has expired")
    if plan.status != "RESERVED":
        raise TransitionPreflightError("STALE_PREFLIGHT", f"plan status is {plan.status}")
    observed_at = now or _utcnow()
    if _aware(plan.expires_at) <= _aware(observed_at):
        raise TransitionPreflightError("PLAN_EXPIRED", "plan has expired")

    context_row = db.get(MarketCalculationContext, plan.market_calculation_context_id)
    if context_row is None or context_row.pipeline_run_id is not None:
        raise TransitionPreflightError(
            "CONTEXT_MISMATCH", "reserved market context is missing or already owned"
        )
    market_cutoff = cutoff_from_row(context_row)
    results = (discovery or TransitionCandidateDiscoveryService()).discover_for_run(
        db,
        upload_run_id,
        market_cutoff=market_cutoff,
        tickers=set(plan.tickers_json),
    )
    observed_selection_keys = sorted(row.prospective_new_key for row in results)
    if observed_selection_keys != sorted(plan.selection_keys_json):
        raise _rejection(
            plan,
            code="SELECTION_KEY_MISMATCH",
            category="selection_keys",
            message="candidate selection keys changed after preflight",
        )
    observed_pointers = {
        row.ticker: {
            "latest_snapshot_id": row.current_pointer_snapshot_id,
            "latest_revision": row.expected_latest_pointer_revision,
            "exact_snapshot_id": row.expected_exact_pointer_snapshot_id,
            "exact_revision": row.expected_exact_pointer_revision,
        }
        for row in results
    }
    if observed_pointers != plan.expected_pointers_json:
        raise _rejection(
            plan,
            code="PRECONDITION_CHANGED",
            category="canonical_pointer",
            message="canonical pointer state changed after preflight",
        )
    observed_evidence = aggregate_evidence_fingerprint(results)
    if observed_evidence != plan.evidence_fingerprint:
        raise _rejection(
            plan,
            code="PRECONDITION_CHANGED",
            category="pit_evidence",
            message="required PIT evidence changed after preflight",
            expected_fingerprint=plan.evidence_fingerprint,
            actual_fingerprint=observed_evidence,
        )
    observed_technical = _aggregate_technical_fingerprint(results)
    if observed_technical != plan.technical_reconstruction_fingerprint:
        raise _rejection(
            plan,
            code="PRECONDITION_CHANGED",
            category="technical_reconstruction",
            message="technical reconstruction changed after preflight",
            expected_fingerprint=plan.technical_reconstruction_fingerprint,
            actual_fingerprint=observed_technical,
        )
    if not plan.run_start_anchor_json or not plan.run_start_anchor_fingerprint:
        raise _rejection(
            plan,
            code="MALFORMED_DECISION_MANIFEST",
            category="run_start_anchor",
            message="preflight is missing its immutable run-start anchor",
        )
    observed_anchor = build_run_start_anchor_manifest(
        db,
        upload_run_id=upload_run_id,
        market_cutoff=market_cutoff,
        results=results,
    )
    observed_anchor_fingerprint = CanonicalEvidenceSerializer.fingerprint(observed_anchor)
    if (
        observed_anchor != plan.run_start_anchor_json
        or observed_anchor_fingerprint != plan.run_start_anchor_fingerprint
    ):
        raise _rejection(
            plan,
            code="DECISION_MANIFEST_MISMATCH",
            category="run_start_anchor",
            message="immutable run-start anchor changed after preflight",
            expected_fingerprint=plan.run_start_anchor_fingerprint,
            actual_fingerprint=observed_anchor_fingerprint,
        )
    if not results or not any(row.confidence == "HIGH" for row in results):
        raise TransitionPreflightError(
            "STALE_PREFLIGHT", "candidate set no longer contains a HIGH result"
        )
    return VerifiedTransitionPreflight(plan, market_cutoff, tuple(results))


def verify_transition_decision_manifests_before_mutation(
    db: Session,
    *,
    upload_run_id: int,
    market_cutoff: MarketCalculationCutoff,
    built_rows,
    repository,
) -> None:
    """Compare post-upstream artifacts with the persisted handoff contract."""

    plan = db.scalar(
        select(TransitionPreflightPlan)
        .where(
            TransitionPreflightPlan.market_calculation_context_id == market_cutoff.context_id,
            TransitionPreflightPlan.upload_run_id == upload_run_id,
            TransitionPreflightPlan.status == "CONSUMED",
        )
        .with_for_update()
    )
    if plan is None:
        return

    expected_tickers = set(plan.tickers_json or ())
    actual = reconstruct_transition_decision_manifests(
        db,
        market_cutoff=market_cutoff,
        built_rows=built_rows,
        repository=repository,
        tickers=expected_tickers,
    )
    handoff = db.scalar(
        select(TransitionDecisionHandoffManifest)
        .where(TransitionDecisionHandoffManifest.preflight_plan_id == plan.id)
        .with_for_update()
    )
    if handoff is None:
        raise _rejection(
            plan,
            code="MALFORMED_DECISION_MANIFEST",
            category="decision_handoff",
            message="post-upstream decision handoff manifest was not persisted",
        )
    _validate_handoff_temporal_lineage(
        db,
        upload_run_id=upload_run_id,
        market_cutoff=market_cutoff,
        built_rows=built_rows,
    )
    observed_anchor = build_run_start_anchor_manifest(
        db,
        upload_run_id=upload_run_id,
        market_cutoff=market_cutoff,
        decision_manifests=actual,
    )
    observed_anchor_fingerprint = CanonicalEvidenceSerializer.fingerprint(observed_anchor)
    if (
        not plan.run_start_anchor_json
        or not plan.run_start_anchor_fingerprint
        or observed_anchor != plan.run_start_anchor_json
        or observed_anchor_fingerprint != plan.run_start_anchor_fingerprint
    ):
        raise _rejection(
            plan,
            code="DECISION_MANIFEST_MISMATCH",
            category="run_start_anchor",
            message="run-start anchor no longer matches at decision handoff",
            expected_fingerprint=plan.run_start_anchor_fingerprint,
            actual_fingerprint=observed_anchor_fingerprint,
        )
    observed = _build_decision_handoff_payload(
        db,
        plan=plan,
        market_cutoff=market_cutoff,
        built_rows=built_rows,
        decision_manifests=actual,
    )
    observed_fingerprint = CanonicalEvidenceSerializer.fingerprint(observed)
    if (
        set(actual) != expected_tickers
        or observed != handoff.manifest_json
        or observed_fingerprint != handoff.manifest_fingerprint
        or handoff.run_start_anchor_fingerprint != plan.run_start_anchor_fingerprint
    ):
        differing = sorted(
            ticker
            for ticker in expected_tickers | set(actual)
            if actual.get(ticker)
            != ((handoff.manifest_json or {}).get("decision_manifests") or {}).get(ticker)
        )
        raise _rejection(
            plan,
            code="DECISION_MANIFEST_MISMATCH",
            category="production_execution_manifest",
            message=(
                "production lifecycle inputs differ from the immutable preflight manifest; "
                f"tickers={','.join(differing)}"
            ),
            expected_fingerprint=handoff.manifest_fingerprint,
            actual_fingerprint=observed_fingerprint,
        )


def freeze_transition_decision_handoff_manifest(
    db: Session,
    *,
    upload_run_id: int,
    market_cutoff: MarketCalculationCutoff,
) -> TransitionDecisionHandoffManifest | None:
    """Persist the actual decision inputs after upstream stages have committed."""

    plan = db.scalar(
        select(TransitionPreflightPlan)
        .where(
            TransitionPreflightPlan.market_calculation_context_id == market_cutoff.context_id,
            TransitionPreflightPlan.upload_run_id == upload_run_id,
            TransitionPreflightPlan.status == "CONSUMED",
        )
        .with_for_update()
    )
    if plan is None:
        return None
    if not plan.run_start_anchor_json or not plan.run_start_anchor_fingerprint:
        raise _rejection(
            plan,
            code="MALFORMED_DECISION_MANIFEST",
            category="run_start_anchor",
            message="preflight is missing its immutable run-start anchor",
        )

    from app.services.setup_lifecycle.repository import SetupLifecycleRepository
    from app.services.setup_lifecycle.snapshot_builder import (
        SetupLifecycleSnapshotBuilder,
        build_run_context_snapshots,
    )
    from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader

    repository = SetupLifecycleRepository()
    run_context = SetupLifecycleSourceLoader().load_run_context(
        db,
        upload_run_id,
        market_cutoff=market_cutoff,
        tickers=set(plan.tickers_json or ()),
    )
    built_rows = build_run_context_snapshots(
        db,
        run_context,
        builder=SetupLifecycleSnapshotBuilder(),
        repository=repository,
    )
    actual = reconstruct_transition_decision_manifests(
        db,
        market_cutoff=market_cutoff,
        built_rows=built_rows,
        repository=repository,
        tickers=set(plan.tickers_json or ()),
    )
    if set(actual) != set(plan.tickers_json or ()):
        raise _rejection(
            plan,
            code="DECISION_LINEAGE_MISMATCH",
            category="ticker_population",
            message="handoff ticker population differs from the run-start anchor",
        )
    _validate_handoff_temporal_lineage(
        db,
        upload_run_id=upload_run_id,
        market_cutoff=market_cutoff,
        built_rows=built_rows,
    )
    observed_anchor = build_run_start_anchor_manifest(
        db,
        upload_run_id=upload_run_id,
        market_cutoff=market_cutoff,
        decision_manifests=actual,
    )
    observed_anchor_fingerprint = CanonicalEvidenceSerializer.fingerprint(observed_anchor)
    if (
        observed_anchor != plan.run_start_anchor_json
        or observed_anchor_fingerprint != plan.run_start_anchor_fingerprint
    ):
        raise _rejection(
            plan,
            code="DECISION_MANIFEST_MISMATCH",
            category="run_start_anchor",
            message="run-start anchor changed before handoff",
            expected_fingerprint=plan.run_start_anchor_fingerprint,
            actual_fingerprint=observed_anchor_fingerprint,
        )

    payload = _build_decision_handoff_payload(
        db,
        plan=plan,
        market_cutoff=market_cutoff,
        built_rows=built_rows,
        decision_manifests=actual,
    )
    fingerprint = CanonicalEvidenceSerializer.fingerprint(payload)
    existing = db.scalar(
        select(TransitionDecisionHandoffManifest).where(
            TransitionDecisionHandoffManifest.preflight_plan_id == plan.id
        )
    )
    if existing is not None:
        if existing.manifest_fingerprint != fingerprint or existing.manifest_json != payload:
            raise _rejection(
                plan,
                code="IMMUTABLE_EVIDENCE_MISMATCH",
                category="decision_handoff",
                message="an immutable handoff manifest already exists with different evidence",
                expected_fingerprint=existing.manifest_fingerprint,
                actual_fingerprint=fingerprint,
            )
        return existing
    manifest = TransitionDecisionHandoffManifest(
        preflight_plan_id=plan.id,
        market_calculation_context_id=market_cutoff.context_id,
        upload_run_id=upload_run_id,
        pipeline_run_id=plan.pipeline_run_id,
        run_start_anchor_fingerprint=plan.run_start_anchor_fingerprint,
        manifest_json=payload,
        manifest_fingerprint=fingerprint,
    )
    db.add(manifest)
    db.flush()
    return manifest


def reconstruct_transition_decision_manifests(
    db: Session,
    *,
    market_cutoff: MarketCalculationCutoff,
    built_rows,
    repository,
    tickers: set[str] | None = None,
) -> dict[str, dict[str, object]]:
    """Reconstruct the exact manifest consumed by lifecycle persistence."""

    discovery = TransitionCandidateDiscoveryService(repository=repository)
    actual: dict[str, dict[str, object]] = {}
    for ticker_context, built in built_rows:
        if tickers is not None and ticker_context.ticker not in tickers:
            continue
        latest_pointer, exact_pointer, latest_revision, exact_revision = discovery._pointers(
            db,
            ticker=built.dto.ticker,
            timeframe=built.dto.timeframe,
            data_as_of_date=built.dto.data_as_of_date,
        )
        assessed = discovery.assess(
            built,
            prospective=market_cutoff,
            latest_pointer=latest_pointer,
            exact_pointer=exact_pointer,
            all_required_pit_inputs=_prospective_inputs_are_complete(
                ticker_context, built, market_cutoff
            ),
            latest_pointer_revision=latest_revision,
            exact_pointer_revision=exact_revision,
        )
        technical_fingerprint = _technical_reconstruction_fingerprint(
            ticker_context.technical_score
        )
        manifest = build_transition_decision_manifest(
            built=built,
            context=ticker_context,
            market_cutoff=market_cutoff,
            technical_reconstruction_fingerprint=technical_fingerprint,
            current_pointer_snapshot_id=assessed.current_pointer_snapshot_id,
            current_pointer_revision=assessed.expected_latest_pointer_revision,
            exact_pointer_snapshot_id=assessed.expected_exact_pointer_snapshot_id,
            exact_pointer_revision=assessed.expected_exact_pointer_revision,
            candidate_type=candidate_type_for(assessed),
            candidate_reason=assessed.reason,
            candidate_confidence=assessed.confidence,
            predicted_pointer_advance=assessed.predicted_pointer_advance,
            predicted_current_state_advance=assessed.predicted_current_state_advance,
        )
        actual[ticker_context.ticker] = {
            "decision_manifest": manifest.as_dict(),
            "decision_manifest_fingerprint": manifest.fingerprint,
        }
    return actual


def consume_transition_preflight(
    db: Session,
    *,
    verified: VerifiedTransitionPreflight,
    pipeline: PipelineRun,
) -> MarketCalculationCutoff:
    if verified.plan.status != "RESERVED":
        raise TransitionPreflightError("ALREADY_CONSUMED", "plan is no longer reserved")
    market_cutoff = attach_reserved_market_context(
        db,
        pipeline,
        context_id=verified.plan.market_calculation_context_id,
    )
    verified.plan.status = "CONSUMED"
    verified.plan.pipeline_run_id = pipeline.id
    verified.plan.consumed_at = _utcnow()
    db.flush()
    logger.info(
        "transition_preflight_consumed plan_id=%s context_id=%s upload_run_id=%s "
        "pipeline_id=%s candidate_type=%s selection_keys=%s cutoff=%s session=%s",
        verified.plan.id,
        verified.plan.market_calculation_context_id,
        verified.plan.upload_run_id,
        pipeline.id,
        verified.plan.candidate_classification,
        ",".join(verified.plan.selection_keys_json),
        CanonicalEvidenceSerializer.canonicalize(market_cutoff.cutoff_at),
        market_cutoff.latest_completed_session.isoformat(),
    )
    return market_cutoff


def cancel_transition_preflight(db: Session, plan_id: int) -> TransitionPreflightPlan:
    plan = db.scalar(
        select(TransitionPreflightPlan)
        .where(TransitionPreflightPlan.id == plan_id)
        .with_for_update()
    )
    if plan is None:
        raise ValueError(f"Transition preflight plan {plan_id} was not found.")
    if plan.status == "RESERVED":
        plan.status = "CANCELLED"
        plan.cancelled_at = _utcnow()
        db.flush()
    return plan


def expire_abandoned_preflights(db: Session, *, now: datetime | None = None) -> int:
    observed_at = now or _utcnow()
    rows = list(
        db.scalars(
            select(TransitionPreflightPlan)
            .where(TransitionPreflightPlan.status == "RESERVED")
            .where(TransitionPreflightPlan.expires_at <= observed_at)
            .with_for_update(skip_locked=True)
        )
    )
    for plan in rows:
        plan.status = "EXPIRED"
        plan.stale_reason = "TTL_EXPIRED"
    if rows:
        db.flush()
    return len(rows)


def build_run_start_anchor_manifest(
    db: Session,
    *,
    upload_run_id: int,
    market_cutoff: MarketCalculationCutoff,
    results: list[TransitionCandidateResult] | None = None,
    decision_manifests: dict[str, dict[str, object]] | None = None,
) -> dict[str, Any]:
    """Build only facts that already exist and are immutable at run start."""

    run = db.get(UploadRun, upload_run_id)
    if run is None:
        raise TransitionPreflightError("MALFORMED_DECISION_MANIFEST", "upload run is missing")
    raw_rows = list(
        db.scalars(
            select(RawCompanyRow)
            .where(RawCompanyRow.run_id == upload_run_id)
            .order_by(RawCompanyRow.row_number, RawCompanyRow.id)
        )
    )
    manifests = decision_manifests or {
        row.ticker: {
            "decision_manifest": row.decision_manifest or {},
            "decision_manifest_fingerprint": row.decision_manifest_fingerprint,
        }
        for row in results or ()
    }
    per_ticker: dict[str, Any] = {}
    for ticker, wrapper in sorted(manifests.items()):
        manifest = dict(wrapper.get("decision_manifest") or {})
        lifecycle = dict(manifest.get("lifecycle_semantics") or {})
        candidate = dict(manifest.get("candidate") or {})
        per_ticker[ticker] = {
            "ticker": ticker,
            "selection_key": candidate.get("selection_key"),
            "raw_row_id": (manifest.get("source_ids") or {}).get("raw_row_id"),
            "expected_pointer": manifest.get("expected_pointer"),
            "eligible_price_bars": manifest.get("eligible_price_bars") or [],
            "producer_signatures": {
                "setup_engine_version": lifecycle.get("engine_version"),
                "setup_config_version": lifecycle.get("config_version"),
                "setup_config_hash": lifecycle.get("config_hash"),
                "setup_schema_version": lifecycle.get("schema_version"),
                "decision_manifest_builder": "transition-decision-manifest-v1",
            },
        }
    raw_payloads = [_semantic_model_payload(row) for row in raw_rows]
    payload = {
        "contract": RUN_START_ANCHOR_CONTRACT,
        "run": {
            "id": run.id,
            "filename": run.filename,
            "uploaded_at": run.uploaded_at,
            "processed_at": run.processed_at,
            "row_count": run.row_count,
            "status": run.status,
            "pine_engine_version": run.pine_engine_version,
            "python_engine_version": run.python_engine_version,
            "raw_row_count": len(raw_payloads),
            "raw_rows_fingerprint": CanonicalEvidenceSerializer.fingerprint(raw_payloads),
        },
        "ticker_population": sorted(per_ticker),
        "market_context": _market_context_payload(market_cutoff),
        "source_cutoffs": {
            "market_cutoff_at": market_cutoff.cutoff_at,
            "latest_completed_session": market_cutoff.latest_completed_session,
            "price_observed_at_or_before": market_cutoff.cutoff_at,
            "source_document_known_at_or_before": market_cutoff.cutoff_at,
        },
        "immutable_source_lineage": per_ticker,
    }
    return CanonicalEvidenceSerializer.canonicalize(payload)


def _build_decision_handoff_payload(
    db: Session,
    *,
    plan: TransitionPreflightPlan,
    market_cutoff: MarketCalculationCutoff,
    built_rows,
    decision_manifests: dict[str, dict[str, object]],
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for context, built in built_rows:
        ticker = context.ticker
        artifacts[ticker] = {
            "raw_row": _artifact_identity(context.raw_row),
            "fundamental_score": _artifact_identity(context.fundamental_score),
            "technical_score": _artifact_identity(context.technical_score),
            "combined_result": _artifact_identity(context.combined_result),
            "ranking_results": [
                _artifact_identity(row)
                for row in sorted(
                    context.ranking_results,
                    key=lambda row: (row.ranking_profile, row.profile_rank, row.id or 0),
                )
            ],
            "market_regime_snapshot": _artifact_identity(context.market_regime_snapshot),
            "sector_rotation_snapshot": _artifact_identity(context.sector_rotation_snapshot),
            "sector_rotation_row": _artifact_identity(context.sector_rotation_row),
            "eligible_price_bars": [
                {
                    "id": row.id,
                    "session": row.bar_date,
                    "timeframe": row.timeframe,
                    "what_to_show": row.what_to_show,
                    "revision_count": row.revision_count,
                    "data_hash": row.data_hash,
                }
                for row in context.price_bars
            ],
            "setup_signal_input_hash": built.source_data_hash,
        }
    ceri_rows = list(
        db.scalars(
            select(CeriScoreSnapshot)
            .where(
                CeriScoreSnapshot.run_id == plan.upload_run_id,
                eligible_snapshot_predicate(),
            )
            .order_by(CeriScoreSnapshot.ticker, CeriScoreSnapshot.id)
        )
    )
    payload = {
        "contract": DECISION_HANDOFF_CONTRACT,
        "binding": {
            "preflight_plan_id": plan.id,
            "run_start_anchor_fingerprint": plan.run_start_anchor_fingerprint,
        },
        "run": {
            "upload_run_id": plan.upload_run_id,
            "pipeline_run_id": plan.pipeline_run_id,
        },
        "market_context": _market_context_payload(market_cutoff),
        "decision_manifests": decision_manifests,
        "artifact_lineage": artifacts,
        "ceri_score_snapshots": [_artifact_identity(row) for row in ceri_rows],
    }
    return CanonicalEvidenceSerializer.canonicalize(payload)


def _validate_handoff_temporal_lineage(
    db: Session,
    *,
    upload_run_id: int,
    market_cutoff: MarketCalculationCutoff,
    built_rows,
) -> None:
    failures: list[str] = []
    for context, _built in built_rows:
        ticker = context.ticker
        raw = context.raw_row
        fundamental = context.fundamental_score
        technical = context.technical_score
        combined = context.combined_result
        market = context.market_regime_snapshot
        sector = context.sector_rotation_snapshot
        if raw.run_id != upload_run_id:
            failures.append(f"{ticker}:raw_run")
        if fundamental is not None and (
            fundamental.run_id != upload_run_id or fundamental.ticker.upper() != ticker
        ):
            failures.append(f"{ticker}:fundamental_run")
        if technical is None:
            failures.append(f"{ticker}:technical_missing")
        elif not _temporal_artifact_matches(technical, market_cutoff, upload_run_id):
            failures.append(f"{ticker}:technical_cutoff")
        if combined is not None:
            if combined.run_id != upload_run_id or combined.ticker.upper() != ticker:
                failures.append(f"{ticker}:combined_run")
            expected_sources = {
                "raw_row_id": raw.id,
                "fundamental_score_id": getattr(fundamental, "id", None),
                "technical_score_id": getattr(technical, "id", None),
            }
            observed_sources = dict((combined.debug_json or {}).get("source_ids") or {})
            if observed_sources != expected_sources:
                failures.append(f"{ticker}:combined_sources")
        for ranking in context.ranking_results:
            if (
                ranking.run_id != upload_run_id
                or ranking.ticker.upper() != ticker
                or ranking.raw_row_id != raw.id
            ):
                failures.append(f"{ticker}:ranking_sources")
        if market is not None and not _temporal_artifact_matches(
            market, market_cutoff, upload_run_id
        ):
            failures.append(f"{ticker}:market_regime_cutoff")
        if sector is not None:
            if not _temporal_artifact_matches(sector, market_cutoff, upload_run_id):
                failures.append(f"{ticker}:sector_cutoff")
            if market is not None and sector.market_regime_snapshot_id != market.id:
                failures.append(f"{ticker}:sector_market_parent")
        if context.sector_rotation_row is not None and (
            sector is None or context.sector_rotation_row.snapshot_id != sector.id
        ):
            failures.append(f"{ticker}:sector_row_parent")
        for bar in context.price_bars:
            if not price_bar_is_eligible(
                bar,
                latest_completed_session=market_cutoff.latest_completed_session,
                cutoff_at=market_cutoff.cutoff_at,
            ):
                failures.append(f"{ticker}:post_cutoff_bar")

    ceri_rows = list(
        db.scalars(
            select(CeriScoreSnapshot).where(
                CeriScoreSnapshot.run_id == upload_run_id,
                eligible_snapshot_predicate(),
            )
        )
    )
    ceri_source_ids: set[int] = set()
    for row in ceri_rows:
        if (
            row.calculation_context_id != market_cutoff.context_id
            or _canonical_time(row.cutoff_at) != _canonical_time(market_cutoff.cutoff_at)
            or row.as_of_session > market_cutoff.latest_completed_session
            or row.calendar_version != market_cutoff.calendar_version
        ):
            failures.append(f"{row.ticker}:ceri_cutoff")
        _collect_source_ids(row.evidence_lineage_json or {}, ceri_source_ids)
    if ceri_source_ids:
        source_rows = list(
            db.scalars(select(CeriSourceRecord).where(CeriSourceRecord.id.in_(ceri_source_ids)))
        )
        found = {row.id for row in source_rows}
        if found != ceri_source_ids:
            failures.append("ceri:source_record_missing")
        for row in source_rows:
            known_at = row.retrieved_at or row.ingested_at
            if known_at is None or _aware(known_at) > _aware(market_cutoff.cutoff_at):
                failures.append(f"ceri:post_cutoff_source:{row.id}")
    if failures:
        raise TransitionPreflightError(
            "DECISION_LINEAGE_MISMATCH",
            "decision artifacts do not descend from the frozen cutoff: "
            + ",".join(sorted(set(failures))[:25]),
        )


def _temporal_artifact_matches(
    row: TechnicalScore | MarketRegimeSnapshot | SectorRotationSnapshot,
    market_cutoff: MarketCalculationCutoff,
    upload_run_id: int,
) -> bool:
    return bool(
        row.run_id == upload_run_id
        and row.calculation_context_id == market_cutoff.context_id
        and _canonical_time(row.calculation_cutoff_at) == _canonical_time(market_cutoff.cutoff_at)
        and row.input_as_of_session == market_cutoff.latest_completed_session
        and row.calendar_version == market_cutoff.calendar_version
    )


def _market_context_payload(market_cutoff: MarketCalculationCutoff) -> dict[str, Any]:
    return {
        "id": market_cutoff.context_id,
        "cutoff_at": market_cutoff.cutoff_at,
        "exchange_timezone": market_cutoff.exchange_timezone,
        "latest_completed_session": market_cutoff.latest_completed_session,
        "daily_bar_ready_at": market_cutoff.daily_bar_ready_at,
        "calendar_version": market_cutoff.calendar_version,
        "bar_readiness_version": market_cutoff.bar_readiness_version,
        "cutoff_reason": market_cutoff.cutoff_reason,
    }


def _semantic_model_payload(row: Any) -> dict[str, Any]:
    excluded = {"created_at", "updated_at", "last_seen_at", "calculated_at"}
    return {
        column.name: getattr(row, column.name, None)
        for column in row.__table__.columns
        if column.name not in excluded
    }


def _artifact_identity(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = _semantic_model_payload(row)
    return {
        "id": getattr(row, "id", None),
        "semantic_hash": CanonicalEvidenceSerializer.fingerprint(payload),
    }


def _collect_source_ids(value: Any, target: set[int], *, key: str | None = None) -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            _collect_source_ids(child, target, key=str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            _collect_source_ids(child, target, key=key)
    elif key is not None and "source_id" in key and isinstance(value, int):
        target.add(value)


def _canonical_time(value: datetime | None) -> Any:
    return CanonicalEvidenceSerializer.canonicalize(value) if value is not None else None


def _run_start_candidate_dict(row: TransitionCandidateResult) -> dict[str, object]:
    payload = row.as_dict()
    payload.pop("decision_manifest", None)
    payload.pop("decision_manifest_fingerprint", None)
    return payload


def _aggregate_classification(results: list[TransitionCandidateResult]) -> str:
    for classification in ("HIGH", "MEDIUM", "LOW"):
        if any(row.confidence == classification for row in results):
            return classification
    return "LOW"


def _aggregate_technical_fingerprint(results: list[TransitionCandidateResult]) -> str:
    payload = [
        (row.ticker, row.technical_reconstruction_fingerprint)
        for row in sorted(results, key=lambda item: item.ticker)
    ]
    return CanonicalEvidenceSerializer.fingerprint(payload)


def _rejection(
    plan: TransitionPreflightPlan,
    *,
    code: str,
    category: str,
    message: str,
    expected_fingerprint: str | None = None,
    actual_fingerprint: str | None = None,
) -> TransitionPreflightError:
    logger.warning(
        "transition_preflight_rejected code=%s category=%s plan_id=%s context_id=%s "
        "upload_run_id=%s pipeline_id=%s candidate_type=%s selection_keys=%s "
        "expected_fingerprint=%s actual_fingerprint=%s",
        code,
        category,
        plan.id,
        plan.market_calculation_context_id,
        plan.upload_run_id,
        plan.pipeline_run_id,
        plan.candidate_classification,
        ",".join(plan.selection_keys_json),
        expected_fingerprint,
        actual_fingerprint,
    )
    return TransitionPreflightError(code, message)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TransitionPreflightError(
            "INVARIANT_VIOLATION", "preflight timestamps must be timezone-aware"
        )
    return value


def _utcnow() -> datetime:
    return datetime.now(UTC)
