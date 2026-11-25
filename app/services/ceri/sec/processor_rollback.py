from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriSecProcessorRelease, CeriSecScopeEvidence, CeriSecSyncState
from app.models.tables import RawCompanyRow
from app.services.ceri.sec.processor_signature import (
    SEC_SCOPE_EVIDENCE_VERSION,
    scope_evidence_contract,
)
from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness
from app.services.ceri.sec.scope_evidence import OPEN, SELECTED, payload
from app.services.runtime_mutation_authority import (
    MutationCapability,
    RuntimeMutationAuthority,
    require_runtime_mutation_authority,
)

PROMOTION_RECEIPT = "sec_processor_promotion_v1"
SIGNATURE_PATTERN = r"^sec-guidance:[0-9a-f]{16}$"
SHA_PATTERN = r"^[0-9a-f]{64}$"
GIT_PATTERN = r"^[0-9a-f]{40}$"


class RollbackApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    promotion_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    predecessor: str = Field(pattern=SIGNATURE_PATTERN)
    candidate: str = Field(pattern=SIGNATURE_PATTERN)
    predecessor_git: str = Field(pattern=GIT_PATTERN)
    candidate_git: str = Field(pattern=GIT_PATTERN)
    predecessor_root: str = Field(min_length=1, max_length=512)
    candidate_root: str = Field(min_length=1, max_length=512)
    predecessor_config: str = Field(pattern=SHA_PATTERN)
    candidate_config: str = Field(pattern=SHA_PATTERN)
    qualification_sha256: str = Field(pattern=SHA_PATTERN)
    database_fingerprint: str = Field(pattern=SHA_PATTERN)
    database_principal: str = Field(min_length=1, max_length=128)
    alembic_head: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=500)
    starts_at: datetime
    cutoff_at: datetime


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _certification_digest(release: CeriSecProcessorRelease) -> str:
    evidence = {
        key: value
        for key, value in release.certification_evidence_json.items()
        if key != PROMOTION_RECEIPT
    }
    return _digest((release.certified_at, release.certified_by, evidence))


def _lock_scope(db: Session) -> None:
    # Prevent a readiness refresh, scope edit, job claim or lease change while
    # checking the transition. Abort on contention instead of taking over work.
    db.execute(
        text(
            "LOCK TABLE background_jobs, ceri_sec_document_extractions, "
            "ceri_sec_sync_states, ceri_sec_scope_evidence, ceri_sec_filing_documents, "
            "ceri_companies, raw_company_rows, "
            "background_workers, background_supervisors IN SHARE MODE NOWAIT"
        )
    )


def _readiness_seal(db: Session, tickers: tuple[str, ...], signature: str) -> str:
    if not tickers:
        raise ValueError("An exact nonempty SEC readiness scope is required.")
    readiness = diagnose_sec_readiness(db, tickers=tickers, processor_signature=signature)
    ciks = {cik for row in readiness.tickers for cik in row.ciks}
    if not readiness.complete or not ciks:
        raise ValueError("The exact signature-bound SEC readiness scope is incomplete.")
    rows = db.scalars(
        select(CeriSecSyncState)
        .where(
            CeriSecSyncState.cik.in_(ciks),
            CeriSecSyncState.dataset == "guidance",
            CeriSecSyncState.processor_signature == signature,
        )
        .order_by(CeriSecSyncState.cik)
    ).all()
    seal = {
        "cohort": readiness.as_dict(),
        "sync": [
            (
                r.id,
                r.cik,
                r.bootstrap_completed_at,
                r.last_discovered_at,
                r.latest_filing_date,
                r.document_count,
            )
            for r in rows
        ],
    }
    evidence = CeriSecScopeEvidence
    predicate = (
        evidence.cik.in_(ciks),
        evidence.dataset == "guidance",
        evidence.processor_signature == signature,
    )
    latest = (
        select(evidence.cik, func.max(evidence.generation).label("generation"))
        .where(*predicate)
        .group_by(evidence.cik)
        .subquery()
    )
    events = db.execute(
        select(evidence.cik, evidence.generation, evidence.ordinal, evidence.evidence_sha256)
        .join(latest, (evidence.cik == latest.c.cik) & (evidence.generation == latest.c.generation))
        .where(*predicate)
        .order_by(evidence.cik, evidence.ordinal)
    ).all()
    if events:
        # A generation's event hashes bind selection, possession and outcomes.
        seal["scope_evidence"] = [tuple(event) for event in events]
    return _digest(seal)


def seal_prepared_scope(db, *, tickers, signature, preparation_sha256):
    """Bind the exact completed owner-authorized frozen scope to certification."""
    if not re.fullmatch(SHA_PATTERN, preparation_sha256 or ""):
        raise ValueError("An exact SEC preparation scope digest is required.")
    _lock_scope(db)
    db.execute(text(
        "LOCK TABLE work_scope_records, work_scope_members, ceri_ingestion_runs, "
        "ceri_source_records, ceri_ingestion_run_source_records, ceri_guidance_events "
        "IN SHARE MODE NOWAIT"
    ))
    readiness = diagnose_sec_readiness(db, tickers=tickers, processor_signature=signature)
    ciks = {cik for item in readiness.tickers for cik in item.ciks}
    exemptions = {
        item.ticker for item in readiness.tickers if item.category == "SEC_NOT_APPLICABLE"
    }
    if len(tickers) != 516 or len(ciks) != 514 or exemptions != {"NBN", "TOWN"}:
        raise ValueError("SEC preparation certification requires the full 514-CIK cohort.")
    if not readiness.complete:
        raise ValueError("SEC preparation certification requires every filing obligation.")
    evidence = CeriSecScopeEvidence
    latest = (
        select(evidence.cik, func.max(evidence.generation).label("generation"))
        .where(evidence.cik.in_(ciks), evidence.processor_signature == signature)
        .group_by(evidence.cik).subquery()
    )
    events = db.scalars(select(evidence).join(latest, (
        (latest.c.cik == evidence.cik) & (latest.c.generation == evidence.generation)
    )).where(
        evidence.processor_signature == signature, evidence.kind.in_((OPEN, SELECTED)),
    )).all()
    opened = [item for item in events if item.kind == OPEN]
    selected = [item for item in events if item.kind == SELECTED]
    if (
        len(opened) != 514 or len(selected) != 514
        or any(payload(item).get("preparation_sha256") != preparation_sha256 for item in opened)
        or sum(len(payload(item)["required"]) for item in selected) != 2570
    ):
        raise ValueError("SEC preparation scope has changed or contains foreign evidence.")
    recovery = payload(opened[0]).get("preparation_recovery")
    if (
        not isinstance(recovery, dict) or not recovery.get("native_verified")
        or not re.fullmatch(GIT_PATTERN, recovery.get("git", ""))
        or not re.fullmatch(SHA_PATTERN, recovery.get("config", ""))
        or not isinstance(recovery.get("root"), str) or not recovery["root"]
        or any(payload(item).get("preparation_recovery") != recovery for item in opened)
    ):
        raise ValueError("The exact compatible native predecessor was not established.")
    return {
        "digest": preparation_sha256, "recovery": recovery,
        "readiness_seal": _readiness_seal(db, tuple(tickers), signature),
    }


def record_recoverable_promotion(
    db: Session,
    *,
    predecessor: CeriSecProcessorRelease,
    candidate: CeriSecProcessorRelease,
    run_id: int,
    candidate_git: str | None,
    actor: str,
    now: datetime,
) -> dict:
    if (
        not candidate_git
        or not re.fullmatch(GIT_PATTERN, candidate_git)
        or not predecessor.certified_at
        or not predecessor.certified_by
        or not predecessor.activated_at
        or not candidate.certified_at
        or not candidate.certified_by
        or candidate.certification_evidence_json.get("run_id") != run_id
        or candidate.certification_evidence_json.get("certification", {}).get("passed") is not True
        or PROMOTION_RECEIPT in candidate.certification_evidence_json
    ):
        raise ValueError(
            "A recoverable promotion requires certified releases and the exact certified run."
        )
    _lock_scope(db)
    tickers = certified_run_tickers(db, run_id)
    if candidate.certification_evidence_json.get("scope_tickers") != list(tickers):
        raise ValueError("The promoted run cohort changed after certification.")
    preparation = candidate.certification_evidence_json.get("preparation_scope")
    if (
        scope_evidence_contract(candidate.processor_signature) == SEC_SCOPE_EVIDENCE_VERSION
        and preparation is not None
        and preparation != seal_prepared_scope(
            db, tickers=tickers, signature=candidate.processor_signature,
            preparation_sha256=preparation["digest"],
        )
    ):
        raise ValueError("The exact certified SEC preparation receipt has changed.")
    return {
        "id": uuid4().hex,
        "predecessor": predecessor.processor_signature,
        "candidate": candidate.processor_signature,
        "candidate_git": candidate_git,
        "promoted_at": now.isoformat(),
        "promoted_by": actor,
        "run_id": run_id,
        "tickers": list(tickers),
        "predecessor_readiness": _readiness_seal(db, tickers, predecessor.processor_signature),
        "candidate_readiness": _readiness_seal(db, tickers, candidate.processor_signature),
        "predecessor_certification": _certification_digest(predecessor),
        "candidate_certification": _certification_digest(candidate),
        **({"preparation_recovery": preparation["recovery"]} if preparation else {}),
    }


def certified_run_tickers(db: Session, run_id: int) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value).strip().upper()
                for value in db.scalars(
                    select(RawCompanyRow.ticker).where(RawCompanyRow.run_id == run_id)
                )
                if value
            }
        )
    )


def _assert_database_quiescent(db: Session) -> None:
    if db.execute(
        text("""
        SELECT EXISTS(
            SELECT 1 FROM background_jobs
            WHERE status IN ('RUNNING', 'CLAIMED') OR lease_expires_at > clock_timestamp()
            UNION ALL
            SELECT 1 FROM ceri_sec_document_extractions
            WHERE status = 'RUNNING' OR lease_expires_at > clock_timestamp()
            UNION ALL
            SELECT 1 FROM background_workers
            WHERE stopping_at IS NULL AND heartbeat_at > clock_timestamp() - interval '2 minutes'
            UNION ALL
            SELECT 1 FROM background_supervisors
            WHERE stopping_at IS NULL AND heartbeat_at > clock_timestamp() - interval '2 minutes'
        )
    """)
    ).scalar_one():
        raise ValueError("Active durable jobs or SEC extraction leases block processor rollback.")


def rollback_committed_promotion(
    db: Session,
    *,
    approval: RollbackApproval,
    approval_sha256: str,
    actor: str,
    authority: RuntimeMutationAuthority,
) -> dict:
    """Reverse only the current promotion. The operator authenticates and holds launcher locks."""
    require_runtime_mutation_authority(authority).require_capability(MutationCapability.NORMAL_ONLY)
    if actor != approval.operator or not approval.reason.strip():
        raise ValueError("Rollback actor or reason is invalid.")
    releases = {
        row.processor_signature: row
        for row in db.scalars(
            select(CeriSecProcessorRelease)
            .order_by(CeriSecProcessorRelease.processor_signature)
            .with_for_update(nowait=True)
            .execution_options(populate_existing=True)
        )
    }
    candidate = releases.get(approval.candidate)
    predecessor = releases.get(approval.predecessor)
    if candidate is None or predecessor is None or candidate is predecessor:
        raise ValueError("Rollback signatures do not identify two registered releases.")
    receipt = candidate.certification_evidence_json.get(PROMOTION_RECEIPT)
    if (
        not receipt
        or receipt["id"] != approval.promotion_id
        or receipt["candidate"] != approval.candidate
        or receipt["candidate_git"] != approval.candidate_git
        or receipt["predecessor"] != approval.predecessor
        or "rollback" in receipt
        or candidate.status != "ACTIVE"
        or predecessor.status != "RETIRED"
        or candidate.activated_at is None
        or predecessor.retired_at != candidate.activated_at
        or receipt["promoted_at"] != candidate.activated_at.astimezone(UTC).isoformat()
        or receipt["promoted_by"] != candidate.activated_by
        or receipt["predecessor_certification"] != _certification_digest(predecessor)
        or receipt["candidate_certification"] != _certification_digest(candidate)
    ):
        raise ValueError("The exact immediately preceding promotion is no longer reversible.")
    _lock_scope(db)
    _assert_database_quiescent(db)
    recovery = receipt.get("preparation_recovery")
    if recovery and (
        approval.predecessor_git != recovery["git"]
        or approval.predecessor_root != recovery["root"]
        or approval.predecessor_config != recovery["config"]
    ):
        raise ValueError("Rollback approval does not bind the certified compatible recovery.")
    if certified_run_tickers(db, receipt["run_id"]) != tuple(receipt["tickers"]):
        raise ValueError("The recorded SEC readiness cohort changed.")
    if (
        _readiness_seal(db, tuple(receipt["tickers"]), approval.predecessor)
        != receipt["predecessor_readiness"]
    ):
        raise ValueError("Predecessor signature-bound readiness evidence changed.")
    now = datetime.now(UTC)
    if (
        approval.starts_at.utcoffset() != timedelta(0)
        or approval.cutoff_at.utcoffset() != timedelta(0)
        or not approval.starts_at <= now < approval.cutoff_at
        or approval.cutoff_at - approval.starts_at > timedelta(minutes=30)
    ):
        raise ValueError("Rollback approval is outside its signed UTC window.")
    candidate.status = "RETIRED"
    candidate.retired_at = now
    db.flush()
    predecessor.status = "ACTIVE"
    predecessor.activated_at = now
    predecessor.activated_by = actor
    predecessor.retired_at = None
    receipt = {
        **receipt,
        "rollback": {
            "committed_at": now.isoformat(),
            "actor": actor,
            "reason": approval.reason,
            "approval_sha256": approval_sha256,
            "restored_git": approval.predecessor_git,
            "restored_config": approval.predecessor_config,
            "qualification_sha256": approval.qualification_sha256,
        },
    }
    candidate.certification_evidence_json = {
        **candidate.certification_evidence_json,
        PROMOTION_RECEIPT: receipt,
    }
    db.flush()
    return receipt
