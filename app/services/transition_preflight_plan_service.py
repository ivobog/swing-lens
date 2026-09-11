from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    MarketCalculationContext,
    PipelineRun,
    TransitionPreflightPlan,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
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
                "decision_manifest": row.decision_manifest,
                "decision_manifest_fingerprint": row.decision_manifest_fingerprint,
            }
            for row in results
        },
        candidate_results_json=[row.as_dict() for row in results],
        evidence_fingerprint=evidence_fingerprint,
        technical_reconstruction_fingerprint=technical_fingerprint,
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
    expected_manifests = {
        ticker: values.get("decision_manifest_fingerprint")
        for ticker, values in plan.predicted_snapshot_identities_json.items()
    }
    observed_manifests = {row.ticker: row.decision_manifest_fingerprint for row in results}
    if observed_manifests != expected_manifests:
        raise _rejection(
            plan,
            code="DECISION_MANIFEST_MISMATCH",
            category="decision_manifest",
            message="semantic decision manifest changed after preflight",
            expected_fingerprint=CanonicalEvidenceSerializer.fingerprint(expected_manifests),
            actual_fingerprint=CanonicalEvidenceSerializer.fingerprint(observed_manifests),
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
    """Fail atomically if production inputs differ from the consumed preflight."""

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

    expected_rows = plan.predicted_snapshot_identities_json or {}
    expected_tickers = set(plan.tickers_json or ())
    actual = reconstruct_transition_decision_manifests(
        db,
        market_cutoff=market_cutoff,
        built_rows=built_rows,
        repository=repository,
        tickers=expected_tickers,
    )
    expected = {
        ticker: {
            "decision_manifest": values.get("decision_manifest"),
            "decision_manifest_fingerprint": values.get("decision_manifest_fingerprint"),
        }
        for ticker, values in expected_rows.items()
        if ticker in expected_tickers
    }
    if actual != expected or set(actual) != expected_tickers:
        differing = sorted(
            ticker
            for ticker in expected_tickers | set(actual)
            if actual.get(ticker) != expected.get(ticker)
        )
        raise _rejection(
            plan,
            code="DECISION_MANIFEST_MISMATCH",
            category="production_execution_manifest",
            message=(
                "production lifecycle inputs differ from the immutable preflight manifest; "
                f"tickers={','.join(differing)}"
            ),
            expected_fingerprint=CanonicalEvidenceSerializer.fingerprint(expected),
            actual_fingerprint=CanonicalEvidenceSerializer.fingerprint(actual),
        )


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
