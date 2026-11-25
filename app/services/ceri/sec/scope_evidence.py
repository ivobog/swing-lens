"""Authoritative SEC guidance selection and document outcomes."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCompany,
    CeriIngestionRun,
    CeriIngestionRunSourceRecord,
    CeriSecDocumentExtraction,
    CeriSecFilingDocument,
    CeriSecProcessorRelease,
    CeriSecScopeEvidence,
    CeriSourceRecord,
)
from app.models.tables import RefreshCycleRecord, WorkScopeMember, WorkScopeRecord
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.preparation_visibility import (
    PREPARATION_REQUEST_PREFIX,
    is_preparation_source,
)
from app.services.ceri.sec.preparation_evidence import (
    EXCLUDABLE_REFUSALS,
    EXCLUDED_ATTRIBUTION,
    has_unresolved_legacy_collision,
    matches_reviewed_quarantine,
)
from app.services.ceri.sec.preparation_sources import (
    PreparationSourceOwner,
    matches_prepared_sources,
)
from app.services.ceri.sec.processor_signature import (
    SEC_SCOPE_EVIDENCE_VERSION,
    scope_evidence_contract,
    sec_guidance_processor_signature,
)
from app.services.work_scope_identity import (
    ScopeIdentityStore,
    ScopeMember,
    ScopeMembershipPolicy,
    WorkScopeSnapshot,
)

GUIDANCE = "guidance"
SUCCESS = frozenset({"COMPLETED_NO_RECORDS", "COMPLETED_WITH_RECORDS"})
OPEN = "OPEN"
SELECTED = "SELECTED"
OUTCOME = "DOCUMENT_OUTCOME"
REFUSAL = "ATTRIBUTION_REFUSAL"


class ScopeEvidenceError(ValueError):
    """The current SEC scope cannot support the requested transition."""


@dataclass(frozen=True)
class ScopeGeneration:
    cik: str
    signature: str
    number: int
    run_id: int
    parent_id: str

    def predicate(self):
        row = CeriSecScopeEvidence
        return (
            row.cik == self.cik,
            row.dataset == GUIDANCE,
            row.processor_signature == self.signature,
        )


def exact_filing(document, cik: str) -> dict:
    if (
        document.cik != cik
        or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", document.accession_number)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", document.document_name)
        or document.form not in {"8-K", "10-Q", "10-K", "6-K", "20-F"}
    ):
        raise ScopeEvidenceError("SEC_REQUIRED_FILING_IDENTITY_INVALID")
    return {
        "cik": cik,
        "accession": document.accession_number,
        "filename": document.document_name,
        "form": document.form,
        "filing_date": date.fromisoformat(document.filing_date[:10]).isoformat(),
        **({"accepted_at": Canonical.canonicalize(document.accepted_at)}
           if document.accepted_at is not None else {}),
    }


def _lock(db: Session, cik: str, signature: str) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"ceri-sec-evidence:{cik}:{GUIDANCE}:{signature}"},
    )


def _seal(row, data: dict) -> dict:
    return {
        "cik": row.cik,
        "dataset": row.dataset,
        "processor_signature": row.processor_signature,
        "generation": row.generation,
        "ordinal": row.ordinal,
        "kind": row.kind,
        "ingestion_run_id": row.ingestion_run_id,
        "scope_id": row.scope_id,
        "recorded_at": Canonical.canonicalize(row.recorded_at),
        "data": data,
    }


def payload(row: CeriSecScopeEvidence) -> dict:
    data = json.loads(row.evidence)["data"]
    envelope = _seal(row, data)
    if row.evidence != Canonical.dumps(envelope) or row.evidence_sha256 != Canonical.fingerprint(
        envelope
    ):
        raise ScopeEvidenceError("SEC_SCOPE_EVIDENCE_SEAL_INVALID")
    return data


def _append(db, generation, ordinal, kind, scope_id, data):
    row = CeriSecScopeEvidence(
        cik=generation.cik,
        dataset=GUIDANCE,
        processor_signature=generation.signature,
        generation=generation.number,
        ordinal=ordinal,
        kind=kind,
        ingestion_run_id=generation.run_id,
        scope_id=scope_id,
        recorded_at=datetime.now(UTC),
    )
    envelope = _seal(row, data)
    row.evidence = Canonical.dumps(envelope)
    row.evidence_sha256 = Canonical.fingerprint(envelope)
    db.add(row)
    db.flush()
    return row


def _scope_run(db, run_id):
    table = CeriIngestionRun.__table__
    return db.execute(select(table).where(table.c.id == run_id)).mappings().one_or_none()


def open_generation(
    db: Session, run_id: int, ticker: str, cik: str, signature: str,
    *, preparation_sha256: str | None = None, approval_receipt_sha256: str | None = None,
    preparation_recovery: dict | None = None,
) -> ScopeGeneration:
    """Admit an owning SEC run, or resume its exact generation after interruption."""
    if signature != sec_guidance_processor_signature():
        raise ScopeEvidenceError("SEC_UNRECOGNIZED_SCOPE_PROCESSOR")
    _lock(db, cik, signature)
    owning_issuers = set(
        db.scalars(
            select(CeriCompany.cik).where(
                CeriCompany.ticker == ticker,
                CeriCompany.sec_applicability != "NOT_APPLICABLE",
            )
        )
    )
    run = _scope_run(db, run_id)
    parent = db.get(WorkScopeRecord, run.scope_id) if run and run.scope_id else None
    prepared_run = run is not None and run.request_key.startswith(PREPARATION_REQUEST_PREFIX)
    if prepared_run != (preparation_sha256 is not None) or (
        prepared_run and (
            not re.fullmatch("[a-f0-9]{64}", preparation_sha256)
            or not re.fullmatch("[a-f0-9]{64}", approval_receipt_sha256 or "")
            or not run.request_key.startswith(f"{PREPARATION_REQUEST_PREFIX}{preparation_sha256}:")
        )
    ):
        raise ScopeEvidenceError("SEC_PREPARATION_RUN_AUTHORITY_MISMATCH")
    refresh = (
        db.get(RefreshCycleRecord, run.refresh_cycle_id) if run and run.refresh_cycle_id else None
    )
    if (
        run is None
        or owning_issuers != {cik}
        or run.status != "RUNNING"
        or run.provider != "sec"
        or run.dataset != GUIDANCE
        or (run.scope_json or {}).get("backfill")
        or (run.scope_json or {}).get("ticker") != ticker
        or parent is None
        or parent.membership_policy != ScopeMembershipPolicy.FROZEN.value
        or parent.scope_kind
        not in {"ceri-source-acquisition", "full-pipeline-run", "retained-continuation"}
        or parent.acquisition_plan_id != run.acquisition_plan_id
        or refresh is None
        or refresh.scope_id != run.scope_id
        or not run.acquisition_plan_id
        or db.get(WorkScopeMember, (run.scope_id, "TICKER", ticker)) is None
    ):
        raise ScopeEvidenceError("SEC_SCOPE_RUN_AUTHORITY_REQUIRED")
    if prepared_run:
        candidate = db.get(CeriSecProcessorRelease, signature)
        if candidate is None or candidate.status != "DEPLOYED":
            raise ScopeEvidenceError("SEC_PREPARATION_PROCESSOR_NOT_DEPLOYED")
    row = CeriSecScopeEvidence
    latest = db.scalar(
        select(row)
        .where(
            row.cik == cik,
            row.dataset == GUIDANCE,
            row.processor_signature == signature,
            row.kind == OPEN,
        )
        .order_by(row.generation.desc())
        .limit(1)
    )
    if latest is not None:
        previous_opening = payload(latest)
        if run_id < latest.ingestion_run_id:
            raise ScopeEvidenceError("SEC_SCOPE_GENERATION_SUPERSEDED")
        if latest.ingestion_run_id == run_id:
            if latest.scope_id != run.scope_id or (
                previous_opening.get("preparation_sha256") != preparation_sha256
                or previous_opening.get("preparation_recovery") != preparation_recovery
            ):
                raise ScopeEvidenceError("SEC_SCOPE_RUN_AUTHORITY_CHANGED")
            return ScopeGeneration(cik, signature, latest.generation, run_id, run.scope_id)
        if (run.scope_json or {}).get("repair") and generation_ready(db, cik, signature):
            raise ScopeEvidenceError("SEC_REPAIR_CANNOT_SUPERSEDE_READY_GENERATION")
    generation = ScopeGeneration(
        cik, signature, 1 if latest is None else latest.generation + 1, run_id, run.scope_id
    )
    _append(
        db,
        generation,
        0,
        OPEN,
        run.scope_id,
        {
            "ticker": ticker,
            "refresh_cycle_id": run.refresh_cycle_id,
            "acquisition_plan_id": run.acquisition_plan_id,
            "contract": SEC_SCOPE_EVIDENCE_VERSION,
            **({
                "preparation_sha256": preparation_sha256,
                "approval_receipt_sha256": approval_receipt_sha256,
                "preparation_recovery": preparation_recovery,
            } if prepared_run else {}),
        },
    )
    return generation


def locked_events(db: Session, generation: ScopeGeneration) -> list[CeriSecScopeEvidence]:
    _lock(db, generation.cik, generation.signature)
    row = CeriSecScopeEvidence
    latest = db.scalar(select(func.max(row.generation)).where(*generation.predicate()))
    if latest != generation.number:
        raise ScopeEvidenceError("SEC_SCOPE_GENERATION_SUPERSEDED")
    events = list(
        db.scalars(
            select(row)
            .where(*generation.predicate(), row.generation == latest)
            .order_by(row.ordinal)
        )
    )
    if (
        not events
        or events[0].kind != OPEN
        or events[0].scope_id != generation.parent_id
        or any(event.ingestion_run_id != generation.run_id for event in events)
    ):
        raise ScopeEvidenceError("SEC_SCOPE_GENERATION_OWNERSHIP_CHANGED")
    if payload(events[0]).get("preparation_sha256"):
        candidate = db.get(CeriSecProcessorRelease, generation.signature, populate_existing=True)
        if candidate is None or candidate.status != "DEPLOYED":
            raise ScopeEvidenceError("SEC_PREPARATION_PROCESSOR_NOT_DEPLOYED")
    return events


def _members(required: list[dict]) -> tuple[ScopeMember, ...]:
    return tuple(ScopeMember("SEC_FILING", Canonical.fingerprint(filing)) for filing in required)


def retain_selection(db, generation, documents, *, discovery: dict) -> CeriSecScopeEvidence:
    events = locked_events(db, generation)
    required = sorted(
        (exact_filing(item, generation.cik) for item in documents),
        key=lambda item: (item["accession"], item["filename"]),
    )
    members = _members(required)
    if len({item.subject_id for item in members}) != len(required):
        raise ScopeEvidenceError("SEC_DUPLICATE_REQUIRED_FILING")
    selection = {"required": required, "discovery": discovery}
    if len(events) > 1:
        if payload(events[1]) != Canonical.canonicalize(selection):
            raise ScopeEvidenceError("SEC_FROZEN_SELECTION_CHANGED")
        return events[1]
    run = _scope_run(db, generation.run_id)
    scope = WorkScopeSnapshot(
        scope_kind="ceri-sec-guidance-selection",
        subject_kind="sec-filing",
        definition_version=SEC_SCOPE_EVIDENCE_VERSION,
        scope_definition={"cik": generation.cik, "discovery": discovery},
        selection_policy_identity=generation.signature,
        membership_policy=ScopeMembershipPolicy.FROZEN,
        members=members,
        selected_at=datetime.now(UTC),
        parent_scope_id=generation.parent_id,
        acquisition_plan_id=run.acquisition_plan_id,
    )
    return _append(
        db, generation, 1, SELECTED, ScopeIdentityStore.persist_scope(db, scope), selection
    )


def selected_event(db, generation) -> CeriSecScopeEvidence | None:
    events = locked_events(db, generation)
    return events[1] if len(events) > 1 else None


def record_outcome(db, generation, filing: dict, result: dict, *, kind: str = OUTCOME) -> None:
    events = locked_events(db, generation)
    if len(events) < 2 or filing not in payload(events[1])["required"]:
        raise ScopeEvidenceError("SEC_FILING_NOT_IN_FROZEN_SCOPE")
    selection = events[1]
    data = {"filing": filing, **result}
    current = _document_outcomes(events[2:]).get(Canonical.fingerprint(filing))
    if current == (kind, Canonical.canonicalize(data)):
        return
    _append(db, generation, events[-1].ordinal + 1, kind, selection.scope_id, data)


def _document_outcomes(events):
    outcomes = {}
    for event in events:
        result = payload(event)
        outcomes[Canonical.fingerprint(result["filing"])] = (event.kind, result)
    return outcomes


def body_version(row: CeriSecFilingDocument) -> dict:
    return {
        "document_id": row.id,
        "body_sha256": row.last_content_hash,
        "possessed_at": Canonical.canonicalize(row.last_downloaded_at),
    }


def _matching_body(db, filing, body):
    if not body or not body.get("body_sha256") or not body.get("possessed_at"):
        return False
    document = db.get(CeriSecFilingDocument, body["document_id"], populate_existing=True)
    return (
        document is not None
        and (
            document.cik,
            document.accession_number,
            document.document_name,
            document.form,
            document.filing_date,
        )
        == (
            filing["cik"],
            filing["accession"],
            filing["filename"],
            filing["form"],
            date.fromisoformat(filing["filing_date"]),
        )
        and body_version(document) == body
    )


def extraction_completion(db, extraction_id):
    row = db.get(CeriSecDocumentExtraction, extraction_id, populate_existing=True)
    if row is None:
        return None
    return {
        "id": row.id,
        "document_id": row.document_id,
        "processor_signature": row.processor_signature,
        "status": row.status,
        "attempt": row.attempt_count,
        "records": row.record_count,
        "completed_at": Canonical.canonicalize(row.completed_at),
    }


def _matching_prepared_completion(db, opening, body, result):
    completion = result.get("extraction")
    return (
        isinstance(completion, dict)
        and isinstance(completion.get("id"), int)
        and completion.get("document_id") == body["document_id"]
        and completion.get("processor_signature") == opening.processor_signature
        and completion.get("status") == result["status"]
        and completion.get("records") == len(result["sources"])
        and completion.get("completed_at") is not None
        and completion == extraction_completion(db, completion["id"])
    )


def _matches_sources(db, run_id, filing, result):
    sources = result["sources"]
    if result["status"] == "COMPLETED_NO_RECORDS":
        return not sources
    if not sources or len({item["id"] for item in sources}) != len(sources):
        return False
    retained = list(
        db.scalars(
            select(CeriSourceRecord)
            .join(
                CeriIngestionRunSourceRecord,
                CeriIngestionRunSourceRecord.source_record_id == CeriSourceRecord.id,
            )
            .where(
                CeriIngestionRunSourceRecord.ingestion_run_id == run_id,
                CeriSourceRecord.id.in_(item["id"] for item in sources),
                CeriSourceRecord.provider == "sec",
                CeriSourceRecord.dataset == GUIDANCE,
            )
        )
    )
    expected = {item["id"]: item["hash"] for item in sources}
    return len(retained) == len(sources) and all(
        row.content_hash == expected[row.id]
        and (row.company_hint_json or {}).get("cik") == filing["cik"]
        and row.provider_record_id.startswith(filing["accession"] + ":")
        and (row.source_reference or "").startswith(filing["accession"] + "/" + filing["filename"])
        for row in retained
    )


def reusable_outcome(db, generation, filing, body):
    """Only the same selection, source version and latest applicable success can be reused."""
    events = locked_events(db, generation)
    selected = payload(events[1])
    latest = _document_outcomes(events[2:]).get(Canonical.fingerprint(filing))
    run_id = generation.run_id
    if latest is None and generation.number > 1:
        row = CeriSecScopeEvidence
        previous = list(
            db.scalars(
                select(row)
                .where(*generation.predicate(), row.generation == generation.number - 1)
                .order_by(row.ordinal)
            )
        )
        if (
            len(previous) > 1 and not payload(previous[0]).get("preparation_sha256")
            and payload(previous[1])["required"] == selected["required"]
        ):
            run_id = previous[0].ingestion_run_id
            latest = _document_outcomes(previous[2:]).get(Canonical.fingerprint(filing))
    if latest is not None:
        kind, result = latest
        if (
            kind == OUTCOME
            and result["status"] in SUCCESS
            and not result.get("preparation")
            and result["body"] == body
            and _matches_sources(db, run_id, filing, result)
        ):
            return result
    return None


def generation_ready(
    db: Session, cik: str, signature: str, *,
    generation: ScopeGeneration | None = None, run_id: int | None = None,
) -> bool:
    """Read and certify the exact current generation, independent of legacy sync rows."""
    row = CeriSecScopeEvidence
    identity = (row.cik == cik, row.dataset == GUIDANCE, row.processor_signature == signature)
    latest = select(func.max(row.generation)).where(*identity).scalar_subquery()
    events = list(
        db.scalars(select(row).where(*identity, row.generation == latest).order_by(row.ordinal))
    )
    if len(events) < 2 or (generation and events[0].generation != generation.number):
        return False
    opening, selection, *documents = events
    if (
        opening.kind != OPEN or opening.ordinal != 0
        or selection.kind != SELECTED or selection.ordinal != 1
        or run_id is not None and opening.ingestion_run_id != run_id
    ):
        return False
    opened = payload(opening)
    contract = scope_evidence_contract(signature)
    if contract is None or (
        contract == SEC_SCOPE_EVIDENCE_VERSION and opened.get("contract") != contract
    ):
        return False
    selected = payload(selection)
    required = selected["required"]
    retained = db.get(WorkScopeRecord, selection.scope_id)
    members = set(
        db.scalars(
            select(WorkScopeMember.subject_id).where(
                WorkScopeMember.scope_id == selection.scope_id,
                WorkScopeMember.subject_type == "SEC_FILING",
            )
        )
    )
    if (
        retained is None
        or retained.parent_scope_id != opening.scope_id
        or retained.membership_policy != ScopeMembershipPolicy.FROZEN.value
        or retained.payload_json["selection_policy_identity"] != signature
        or members != {item.subject_id for item in _members(required)}
        or any(
            event.scope_id != selection.scope_id
            or event.ingestion_run_id != opening.ingestion_run_id
            for event in [selection, *documents]
        )
    ):
        return False
    outcomes = _document_outcomes(documents)
    for filing in required:
        kind, result = outcomes.get(Canonical.fingerprint(filing), (None, {}))
        if not _valid_processing_outcome(db, opening, selection, filing, kind, result):
            return False
    return len(outcomes) == len(required)


def preparation_owner(db, opening, selection):
    opened = payload(opening)
    grant = opened.get("preparation_sha256")
    run = _scope_run(db, opening.ingestion_run_id)
    release = db.get(CeriSecProcessorRelease, opening.processor_signature)
    if (
        not isinstance(grant, str)
        or not re.fullmatch("[a-f0-9]{64}", grant)
        or not re.fullmatch("[a-f0-9]{64}", opened.get("approval_receipt_sha256") or "")
        or scope_evidence_contract(opening.processor_signature) != SEC_SCOPE_EVIDENCE_VERSION
        or opened.get("contract") != SEC_SCOPE_EVIDENCE_VERSION
        or release is None
        or run is None
        or run.scope_id != opening.scope_id
        or not run.request_key.startswith(f"{PREPARATION_REQUEST_PREFIX}{grant}:")
    ):
        return None
    return PreparationSourceOwner(
        grant, opening.processor_signature, opening.ingestion_run_id,
        selection.scope_id, opening.generation,
    )


def retained_preparation_exclusion(db, opening, filing, body, anchor=None):
    """Resolve an independently verified exclusion for the exact retained body version."""
    row = CeriSecScopeEvidence
    events = db.scalars(select(row).where(
        row.cik == opening.cik,
        row.processor_signature == opening.processor_signature,
        row.kind == REFUSAL,
        row.generation < opening.generation,
    ).order_by(row.generation.desc(), row.ordinal.desc()))
    for event in events:
        if anchor is not None and event.evidence_sha256 != anchor:
            continue
        prior = payload(event)
        if (
            prior.get("status") != EXCLUDED_ATTRIBUTION or not prior.get("preparation")
            or prior.get("filing") != filing or prior.get("body") != body
        ):
            continue
        original = db.scalars(select(row).where(
            row.cik == event.cik, row.processor_signature == event.processor_signature,
            row.generation == event.generation, row.ordinal.in_((0, 1)),
        ).order_by(row.ordinal)).all()
        if (
            len(original) == 2 and original[0].kind == OPEN and original[1].kind == SELECTED
            and event.ingestion_run_id == original[0].ingestion_run_id
            and filing in payload(original[1])["required"]
            and _valid_processing_outcome(db, *original, filing, REFUSAL, prior)
        ):
            return event.evidence_sha256, prior["reason"], prior["custody"]
    return None


def _valid_processing_outcome(db, opening, selection, filing, kind, result):
    body = result.get("body")
    status = result.get("status")
    excluded = kind == REFUSAL and status == EXCLUDED_ATTRIBUTION
    if excluded or payload(opening).get("preparation_sha256") and status == "COMPLETED_NO_RECORDS":
        owner = preparation_owner(db, opening, selection)
        source_table = CeriSourceRecord.__table__
        members = CeriIngestionRunSourceRecord.__table__
        locator = f'{filing["accession"]}/{filing["filename"]}#paragraph-'
        associated = db.scalar(select(source_table.c.id).outerjoin(members).where(
            (members.c.ingestion_run_id == opening.ingestion_run_id)
            | (source_table.c.ingestion_run_id == opening.ingestion_run_id),
            source_table.c.source_reference.startswith(locator, autoescape=True),
        ).limit(1))
        if result.get("sources") != [] or associated is not None:
            return False
        if not excluded:
            return (
                owner is not None and kind == OUTCOME and _matching_body(db, filing, body)
                and _matching_prepared_completion(db, opening, body, result)
            )
        custody = result.get("custody") or {}
        origin = result.get("exclusion_origin")
        if origin is not None:
            if (
                owner is not None
                or scope_evidence_contract(opening.processor_signature)
                != SEC_SCOPE_EVIDENCE_VERSION
                or not _matching_body(db, filing, body)
            ):
                return False
            expected = retained_preparation_exclusion(db, opening, filing, body, anchor=origin)
            return expected == (origin, result["reason"], custody)
        return (
            owner is not None and result.get("preparation") == asdict(owner)
            and not has_unresolved_legacy_collision(db, filing)
            and (
                result.get("reason") == "SEC_REVIEWED_JOINT_FILING"
                and matches_reviewed_quarantine(filing, custody)
                or result.get("reason") in EXCLUDABLE_REFUSALS
                and _matching_body(db, filing, body)
                and custody.get("wire_sha256") == body["body_sha256"]
                and custody.get("filing") == filing
            )
        )
    if (
        kind != OUTCOME or result.get("status") not in SUCCESS
        or not _matching_body(db, filing, body)
    ):
        return False
    source_proof = result.get("preparation")
    if source_proof is None:
        return (
            not payload(opening).get("preparation_sha256")
            or result["status"] == "COMPLETED_NO_RECORDS"
        ) and _matches_sources(db, opening.ingestion_run_id, filing, result)
    owner = preparation_owner(db, opening, selection)
    return (
        owner is not None
        and source_proof == asdict(owner)
        and result["status"] == "COMPLETED_WITH_RECORDS"
        and _matching_prepared_completion(db, opening, body, result)
        and matches_prepared_sources(
            db, owner=owner, filing=filing, body=body, sources=result["sources"],
        )
    )


def reusable_preparation_outcome(db, events, filing, body):
    latest = _document_outcomes(events[2:]).get(Canonical.fingerprint(filing))
    if latest is None:
        return False
    kind, result = latest
    return result.get("body") == body and _valid_processing_outcome(
        db, events[0], events[1], filing, kind, result,
    )


def source_attribution_proved(db, source: CeriSourceRecord, company_cik: str | None) -> bool:
    """Require an original, exact successful ownership proof for a held source."""
    cik = (source.company_hint_json or {}).get("cik")
    if not cik or cik != company_cik or source.quarantine_reason or is_preparation_source(source):
        return False
    row = CeriSecScopeEvidence
    for event in db.scalars(
        select(row)
        .join(CeriIngestionRun, CeriIngestionRun.id == row.ingestion_run_id)
        .where(
            row.cik == cik,
            row.dataset == GUIDANCE,
            row.kind.in_((OUTCOME, REFUSAL)),
            row.processor_signature == sec_guidance_processor_signature(),
        )
        .order_by(row.generation.desc(), row.ordinal.desc())
    ):
        result = payload(event)
        if result["filing"]["accession"] != source.provider_record_id.partition(":")[0]:
            continue
        if result.get("preparation"):
            continue
        if result["status"] not in SUCCESS:
            return False
        if (
            result["status"] == "COMPLETED_WITH_RECORDS"
            and _matching_body(db, result["filing"], result["body"])
            and {"id": source.id, "hash": source.content_hash} in result["sources"]
            and _matches_sources(db, event.ingestion_run_id, result["filing"], result)
        ):
            return True
    return False
