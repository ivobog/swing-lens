from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriSecProcessorRelease
from app.services.ceri.sec.processor_rollback import (
    PROMOTION_RECEIPT,
    certified_run_tickers,
    record_recoverable_promotion,
    seal_prepared_scope,
)
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.entrypoint_authority import require_disposable_tool_target
from app.services.pipeline_prerequisites import (
    SecProcessorPromotionRequiredError,
    WorkerProcessorDriftError,
)
from app.services.runtime_mutation_authority import (
    MutationCapability,
    RuntimeMutationAuthority,
    RuntimeMutationAuthorityError,
    require_runtime_mutation_authority,
)
from app.settings import RuntimeMode


class SecProcessorReleaseStatus:
    DEPLOYED = "DEPLOYED"
    CERTIFIED = "CERTIFIED"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class SecProcessorLifecycleState:
    deployed_signature: str
    active_signature: str | None
    certified_signatures: tuple[str, ...]

    @property
    def deployed_is_active(self) -> bool:
        return self.active_signature == self.deployed_signature

    def as_dict(self) -> dict[str, Any]:
        return {
            "deployed_signature": self.deployed_signature,
            "active_signature": self.active_signature,
            "certified_signatures": list(self.certified_signatures),
            "deployed_is_active": self.deployed_is_active,
        }


def lifecycle_state(db: Session) -> SecProcessorLifecycleState:
    releases = list(db.scalars(select(CeriSecProcessorRelease)).all())
    active = next(
        (
            row.processor_signature
            for row in releases
            if row.status == SecProcessorReleaseStatus.ACTIVE
        ),
        None,
    )
    certified = tuple(
        sorted(
            row.processor_signature
            for row in releases
            if row.status in {SecProcessorReleaseStatus.CERTIFIED, SecProcessorReleaseStatus.ACTIVE}
        )
    )
    return SecProcessorLifecycleState(
        deployed_signature=sec_guidance_processor_signature(),
        active_signature=active,
        certified_signatures=certified,
    )


def require_deployed_processor_active(db: Session) -> SecProcessorLifecycleState:
    state = lifecycle_state(db)
    if not state.active_signature:
        raise SecProcessorPromotionRequiredError(
            "SEC processor has no explicitly promoted ACTIVE release.",
            diagnostics=state.as_dict(),
        )
    if not state.deployed_is_active:
        raise SecProcessorPromotionRequiredError(
            "Deployed SEC processor is not the explicitly promoted ACTIVE release: "
            f"deployed={state.deployed_signature}, active={state.active_signature}.",
            diagnostics=state.as_dict(),
        )
    return state


def fence_worker_against_active_processor(db: Session) -> SecProcessorLifecycleState:
    state = lifecycle_state(db)
    if not state.active_signature or not state.deployed_is_active:
        raise WorkerProcessorDriftError(
            "Worker SEC processor is incompatible with the ACTIVE release: "
            f"loaded={state.deployed_signature}, active={state.active_signature or 'NONE'}.",
            diagnostics=state.as_dict(),
        )
    return state


def register_deployed_processor(
    db: Session,
    *,
    authority: RuntimeMutationAuthority,
    git_sha: str | None = None,
) -> CeriSecProcessorRelease:
    authority = require_runtime_mutation_authority(authority)
    authority.require_capability(MutationCapability.NORMAL_ONLY)
    signature = sec_guidance_processor_signature()
    row = db.get(CeriSecProcessorRelease, signature)
    if row is None:
        row = CeriSecProcessorRelease(
            processor_signature=signature,
            status=SecProcessorReleaseStatus.DEPLOYED,
            deployed_git_sha=git_sha,
            certification_evidence_json={},
        )
        db.add(row)
    elif git_sha and not row.deployed_git_sha:
        row.deployed_git_sha = git_sha
    db.flush()
    return row


def establish_worker_processor_identity(
    db: Session,
    *,
    authority: RuntimeMutationAuthority,
    git_sha: str | None = None,
) -> CeriSecProcessorRelease:
    """Register in NORMAL, but make certification startup strictly read-only."""

    authority = require_runtime_mutation_authority(authority)
    if authority.mode is RuntimeMode.NORMAL:
        return register_deployed_processor(db, authority=authority, git_sha=git_sha)
    authority.require_capability(MutationCapability.READ_ONLY)
    signature = sec_guidance_processor_signature()
    row = db.get(CeriSecProcessorRelease, signature)
    if row is None:
        raise RuntimeMutationAuthorityError(
            "CERTIFICATION_PROCESSOR_NOT_REGISTERED",
            "the deployed SEC processor must be registered before certification starts",
        )
    if git_sha and row.deployed_git_sha and row.deployed_git_sha != git_sha:
        raise RuntimeMutationAuthorityError(
            "CERTIFICATION_PROCESSOR_IDENTITY_MISMATCH",
            "the registered SEC processor deployment identity does not match this runtime",
        )
    return row


def _requires_prepared_scope(signature):
    from app.services.ceri.sec.processor_signature import (
        SEC_SCOPE_EVIDENCE_VERSION,
        scope_evidence_contract,
    )
    return scope_evidence_contract(signature) == SEC_SCOPE_EVIDENCE_VERSION


def _require_disposable_release_control(db):
    bind = db.get_bind()
    target = bind.url if hasattr(bind, "url") else bind.engine.url
    require_disposable_tool_target(target)
    if db.scalar(text("SELECT current_database()")) != target.database:
        raise ValueError("SEC disposable release database identity changed.")


def certify_processor(
    db: Session,
    *,
    processor_signature: str,
    evidence: dict[str, Any],
    actor: str,
) -> CeriSecProcessorRelease:
    if _requires_prepared_scope(processor_signature):
        preparation = evidence.get("preparation_scope")
        if preparation is None:
            _require_disposable_release_control(db)
        else:
            tickers = certified_run_tickers(db, evidence["run_id"])
            if list(tickers) != evidence.get("scope_tickers") or (
                preparation != seal_prepared_scope(
                    db, tickers=tickers, signature=processor_signature,
                    preparation_sha256=preparation["digest"],
                )
            ):
                raise ValueError("Exact SEC preparation certification evidence is invalid.")
    row = db.get(CeriSecProcessorRelease, processor_signature)
    if row is None:
        row = CeriSecProcessorRelease(
            processor_signature=processor_signature,
            status=SecProcessorReleaseStatus.DEPLOYED,
            certification_evidence_json={},
        )
        db.add(row)
    if row.status == SecProcessorReleaseStatus.RETIRED:
        raise ValueError("A retired SEC processor cannot be recertified in place.")
    if row.status != SecProcessorReleaseStatus.ACTIVE:
        row.status = SecProcessorReleaseStatus.CERTIFIED
    row.certified_at = datetime.now(UTC)
    row.certified_by = actor
    if PROMOTION_RECEIPT in evidence:
        raise ValueError("Promotion receipt is reserved lifecycle evidence.")
    preserved = (row.certification_evidence_json or {}).get(PROMOTION_RECEIPT)
    row.certification_evidence_json = dict(evidence)
    if preserved is not None:
        row.certification_evidence_json = {**evidence, PROMOTION_RECEIPT: preserved}
    db.flush()
    return row


def promote_processor(
    db: Session,
    *,
    processor_signature: str,
    actor: str,
    rollback_run_id: int | None = None,
    rollback_candidate_git: str | None = None,
) -> CeriSecProcessorRelease:
    releases = list(
        db.scalars(
            select(CeriSecProcessorRelease)
            .order_by(CeriSecProcessorRelease.processor_signature)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    target = next(
        (row for row in releases if row.processor_signature == processor_signature),
        None,
    )
    if target is not None and target.status == SecProcessorReleaseStatus.ACTIVE:
        return target
    if target is None or target.status != SecProcessorReleaseStatus.CERTIFIED:
        raise ValueError("SEC processor must be CERTIFIED before explicit promotion.")
    if _requires_prepared_scope(processor_signature):
        if not target.certification_evidence_json.get("preparation_scope"):
            _require_disposable_release_control(db)
        elif rollback_run_id is None:
            raise ValueError("SEC preparation activation requires an exact recoverable promotion.")
    now = datetime.now(UTC)
    if rollback_run_id is not None:
        previous = next(
            (row for row in releases if row.status == SecProcessorReleaseStatus.ACTIVE), None
        )
        if previous is None:
            raise ValueError("A recoverable promotion requires an ACTIVE predecessor.")
        receipt = record_recoverable_promotion(
            db,
            predecessor=previous,
            candidate=target,
            run_id=rollback_run_id,
            candidate_git=rollback_candidate_git,
            actor=actor,
            now=now,
        )
        target.certification_evidence_json = {
            **target.certification_evidence_json,
            PROMOTION_RECEIPT: receipt,
        }
    for row in releases:
        if row.status == SecProcessorReleaseStatus.ACTIVE:
            row.status = SecProcessorReleaseStatus.RETIRED
            row.retired_at = now
    # Release the partial unique ACTIVE slot before assigning it to the target.
    # Both flushes remain inside the caller's transaction, so promotion is still atomic.
    db.flush()
    target.status = SecProcessorReleaseStatus.ACTIVE
    target.activated_at = now
    target.activated_by = actor
    db.flush()
    return target
