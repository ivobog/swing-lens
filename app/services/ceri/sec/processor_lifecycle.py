from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriSecProcessorRelease
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.pipeline_prerequisites import (
    SecProcessorPromotionRequiredError,
    WorkerProcessorDriftError,
)


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
            if row.status
            in {SecProcessorReleaseStatus.CERTIFIED, SecProcessorReleaseStatus.ACTIVE}
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
    git_sha: str | None = None,
) -> CeriSecProcessorRelease:
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


def certify_processor(
    db: Session,
    *,
    processor_signature: str,
    evidence: dict[str, Any],
    actor: str,
) -> CeriSecProcessorRelease:
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
    row.certification_evidence_json = dict(evidence)
    db.flush()
    return row


def promote_processor(
    db: Session,
    *,
    processor_signature: str,
    actor: str,
) -> CeriSecProcessorRelease:
    releases = list(
        db.scalars(select(CeriSecProcessorRelease).with_for_update()).all()
    )
    target = next(
        (row for row in releases if row.processor_signature == processor_signature),
        None,
    )
    if target is not None and target.status == SecProcessorReleaseStatus.ACTIVE:
        return target
    if target is None or target.status != SecProcessorReleaseStatus.CERTIFIED:
        raise ValueError("SEC processor must be CERTIFIED before explicit promotion.")
    now = datetime.now(UTC)
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
