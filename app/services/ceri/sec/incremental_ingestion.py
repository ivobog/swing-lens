from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany, CeriIngestionRun, CeriSecFilingDocument
from app.services.ceri.dtos import GuidanceRequest
from app.services.ceri.enums import CeriDataset
from app.services.ceri.sec.attribution import SecAttributionRefused
from app.services.ceri.sec.content_cache import SecDocumentContentCache
from app.services.ceri.sec.issuer_succession import ownership_conflict
from app.services.ceri.sec.preparation_evidence import (
    EXCLUDED_ATTRIBUTION,
    PreparationAuthorization,
    PreparationExpired,
    archive_exclusion_proof,
    examine_attribution,
    has_unresolved_legacy_collision,
    matches_reviewed_quarantine,
)
from app.services.ceri.sec.preparation_sources import retain_prepared_sources
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.provider import SecCeriProvider, SecGuidanceDocument
from app.services.ceri.sec.readiness_service import (
    SecReadinessFacts,
    SecReadinessPolicy,
    SecReadinessService,
)
from app.services.ceri.sec.scope_evidence import (
    OUTCOME,
    REFUSAL,
    ScopeEvidenceError,
    ScopeGeneration,
    body_version,
    exact_filing,
    extraction_completion,
    generation_ready,
    locked_events,
    open_generation,
    payload,
    preparation_owner,
    record_outcome,
    retain_selection,
    retained_preparation_exclusion,
    reusable_outcome,
    reusable_preparation_outcome,
    selected_event,
)
from app.services.ceri.sec.state_service import (
    SecDocumentIdentity,
    SecDocumentStateService,
)
from app.services.ceri.source_record_service import CeriSourceRecordService
from app.services.domain_mutation import MutationDomain, MutationSemanticMode
from app.services.redaction import redact_text
from app.services.source_mutation_authority import source_mutation_writer
from app.settings import SecDocumentIncrementalMode, Settings


class SecBootstrapRequired(RuntimeError):
    pass


class SecIncrementalCancelled(RuntimeError):
    pass


@dataclass
class SecIncrementalOutcome:
    requested: int = 0
    fetched: int = 0
    inserted: int = 0
    deduplicated: int = 0
    corrected: int = 0
    quarantined: int = 0
    failed: int = 0
    documents_discovered: int = 0
    documents_downloaded: int = 0
    documents_cache_reused: int = 0
    documents_skipped: int = 0
    documents_would_skip: int = 0
    documents_zero_records: int = 0
    documents_excluded_attribution: int = 0
    documents_claimed_elsewhere: int = 0
    stale_leases_recovered: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    readiness_state: str | None = None
    run_evidence_status: str | None = None
    readiness_reason: str | None = None

    def telemetry(self) -> dict[str, int]:
        return {
            key: int(getattr(self, key))
            for key in (
                "documents_discovered",
                "documents_downloaded",
                "documents_cache_reused",
                "documents_skipped",
                "documents_would_skip",
                "documents_zero_records",
                "documents_excluded_attribution",
                "documents_claimed_elsewhere",
                "stale_leases_recovered",
            )
        }


class SecGuidanceIncrementalIngestionService:
    """Coordinates durable filing claims around existing record-level idempotency."""

    def __init__(
        self,
        *,
        settings: Settings,
        source_records: CeriSourceRecordService,
        state: SecDocumentStateService | None = None,
        content_cache: SecDocumentContentCache | None = None,
        processor_signature: str | None = None,
    ) -> None:
        self.settings = settings
        self.source_records = source_records
        self.state = state or SecDocumentStateService()
        self.content_cache = content_cache or SecDocumentContentCache(settings.cache_dir)
        self.processor_signature = processor_signature or sec_guidance_processor_signature()

    def ingest(
        self,
        db: Session,
        *,
        provider: SecCeriProvider,
        ingestion_run: CeriIngestionRun,
        ticker: str,
        start: date | None,
        end: date | None,
        raw_payload_allowed: bool,
        historical_backfill: bool,
        should_cancel=None,
        worker_id: str | None = None,
        reuse_completed_documents: bool = False,
        preparation: PreparationAuthorization | None = None,
    ) -> SecIncrementalOutcome:
        outcome = SecIncrementalOutcome()
        mode = self.settings.sec_document_incremental_mode
        cik = self._resolve_and_persist_cik(db, provider=provider, ticker=ticker)
        if cik is None:
            readiness = SecReadinessService().assess(
                SecReadinessFacts(cik=None),
                policy=SecReadinessPolicy(self.settings.sec_readiness_policy.value),
            )
            outcome.readiness_state = readiness.state.value
            outcome.run_evidence_status = readiness.run_evidence_status
            outcome.readiness_reason = readiness.reason
            return outcome
        if mode is SecDocumentIncrementalMode.ACTIVE and not historical_backfill:
            certified = self.state.is_bootstrap_certified(
                db,
                cik=cik,
                dataset=CeriDataset.GUIDANCE.value,
                processor_signature=self.processor_signature,
            )
            readiness = SecReadinessService().assess(
                SecReadinessFacts(
                    cik=cik,
                    active=True,
                    bootstrap_certified=certified,
                    prior_signature_certified=self.state.has_prior_bootstrap_signature(
                        db,
                        cik=cik,
                        dataset=CeriDataset.GUIDANCE.value,
                        processor_signature=self.processor_signature,
                    ),
                ),
                policy=SecReadinessPolicy(self.settings.sec_readiness_policy.value),
            )
            outcome.readiness_state = readiness.state.value
            outcome.run_evidence_status = readiness.run_evidence_status
            outcome.readiness_reason = readiness.reason
            if not readiness.may_ingest:
                raise SecBootstrapRequired(
                    f"{readiness.reason}: SEC ACTIVE sync requires a successful "
                    f"bootstrap for CIK {cik} and processor signature "
                    f"{self.processor_signature}."
                )
        if historical_backfill:
            raise ScopeEvidenceError("SEC_HISTORICAL_RUN_CANNOT_PUBLISH_CURRENT_READINESS")
        run_id = ingestion_run.id
        if preparation is not None and mode is not SecDocumentIncrementalMode.SHADOW:
            raise ScopeEvidenceError("SEC_PREPARATION_REQUIRES_SHADOW")
        generation = open_generation(
            db, run_id, ticker.upper(), cik, self.processor_signature,
            preparation_sha256=preparation.scope_sha256 if preparation else None,
            approval_receipt_sha256=preparation.signed_approval_sha256 if preparation else None,
            preparation_recovery=preparation.recovery if preparation else None,
        )
        db.commit()  # Current readiness is blocked before discovery starts.
        try:
            selected = selected_event(db, generation)
            if selected is not None:
                documents = tuple(
                    SecGuidanceDocument(
                        ticker.upper(),
                        item["cik"],
                        item["accession"],
                        item["filename"],
                        item["form"],
                        item["filing_date"],
                        (
                            datetime.fromisoformat(item["accepted_at"])
                            if item.get("accepted_at") else None
                        ),
                    )
                    for item in payload(selected)["required"]
                )
                db.commit()
            else:
                db.commit()  # Do not hold the generation lock during SEC I/O.
                upper = end or datetime.now(UTC).date()
                selection = provider.select_guidance_documents(
                    GuidanceRequest(company_id=None, ticker=ticker, start=start, end=upper),
                    cik=cik,
                )
                documents = selection.documents
                retain_selection(db, generation, documents, discovery=selection.discovery)
                db.commit()
            outcome.documents_discovered = len(documents)
            for document in documents:
                if _cancelled(should_cancel):
                    raise SecIncrementalCancelled("CERI SEC ingestion cancelled")
                self._process_document(
                    db,
                    provider=provider,
                    generation=generation,
                    document=document,
                    raw_payload_allowed=raw_payload_allowed,
                    outcome=outcome,
                    should_cancel=should_cancel,
                    worker_id=worker_id or self.settings.job_worker_id,
                    preparation=preparation,
                )
            locked_events(db, generation)
            certified = generation_ready(db, cik, self.processor_signature, generation=generation)
        except SecIncrementalCancelled:
            raise
        except ScopeEvidenceError as exc:
            db.rollback()
            outcome.failed += 1
            outcome.errors.append({"error": str(exc)})
            certified = False
        readiness = SecReadinessService().assess(
            SecReadinessFacts(
                cik=cik,
                bootstrap_certified=certified,
                failed=bool(outcome.failed or outcome.quarantined),
                bootstrap_in_progress=not certified,
                terminal_documents_reused=outcome.documents_skipped,
                documents_downloaded=outcome.documents_downloaded,
                documents_extracted=outcome.documents_discovered - outcome.documents_skipped,
            ),
            policy=SecReadinessPolicy(self.settings.sec_readiness_policy.value),
        )
        outcome.readiness_state = readiness.state.value
        outcome.run_evidence_status = readiness.run_evidence_status
        outcome.readiness_reason = readiness.reason
        if not certified:
            outcome.failed = max(outcome.failed, 1)
        # The caller finalizes the run in this transaction under the same generation lock.
        return outcome

    def _process_document(
        self,
        db: Session,
        *,
        provider: SecCeriProvider,
        generation: ScopeGeneration,
        document: SecGuidanceDocument,
        raw_payload_allowed: bool,
        outcome: SecIncrementalOutcome,
        should_cancel,
        worker_id: str,
        preparation: PreparationAuthorization | None,
    ) -> None:
        dataset = CeriDataset.GUIDANCE.value
        filing = exact_filing(document, generation.cik)
        events = locked_events(db, generation)
        prepared = payload(events[0]).get("preparation_sha256") is not None
        owner = preparation_owner(db, events[0], events[1]) if prepared else None
        if prepared and (owner is None or preparation is None):
            raise ScopeEvidenceError("SEC_PREPARATION_OWNER_INVALID")
        if preparation is not None:
            preparation.check_window()
            if not preparation.has_filing(filing):
                raise ScopeEvidenceError("SEC_PREPARATION_FILING_NOT_AUTHORIZED")
            if preparation.quarantined is not None:
                quarantined_text, custody = preparation.quarantined
                if matches_reviewed_quarantine(filing, custody):
                    if (
                        hashlib.sha256(quarantined_text.encode("utf-8")).hexdigest()
                        != custody["wire_sha256"]
                        or examine_attribution(document, quarantined_text)
                        != "SEC_REVIEWED_JOINT_FILING"
                        or has_unresolved_legacy_collision(db, filing)
                    ):
                        raise ScopeEvidenceError("SEC_PREPARATION_QUARANTINE_PROOF_INVALID")
                    record_outcome(db, generation, filing, {
                        "status": EXCLUDED_ATTRIBUTION, "sources": [],
                        "reason": "SEC_REVIEWED_JOINT_FILING", "custody": custody,
                        "preparation": asdict(owner),
                    }, kind=REFUSAL)
                    outcome.documents_excluded_attribution += 1
                    db.commit()
                    return

        if prepared:
            registered = db.scalar(select(CeriSecFilingDocument).where(
                CeriSecFilingDocument.cik == filing["cik"],
                CeriSecFilingDocument.accession_number == filing["accession"],
                CeriSecFilingDocument.document_name == filing["filename"],
                CeriSecFilingDocument.form == filing["form"],
                CeriSecFilingDocument.filing_date == date.fromisoformat(filing["filing_date"]),
            ))
            if registered is None or not preparation.matches_body(filing, body_version(registered)):
                record_outcome(db, generation, filing, {
                    "status": "FAILED_RETRYABLE", "reason": "SEC_PREPARATION_CUSTODY_UNAVAILABLE",
                })
                outcome.failed += 1
                db.commit()
                return
        else:
            registered = self.state.register_document(
                db,
                SecDocumentIdentity(
                    cik=document.cik,
                    accession_number=document.accession_number,
                    document_name=document.document_name,
                    ticker_hint=document.ticker,
                    form=document.form,
                    filing_date=date.fromisoformat(filing["filing_date"]),
                ),
            )
        document_id = registered.id
        version = body_version(registered)
        cached = (
            self.content_cache.load(document, expected_hash=version["body_sha256"])
            if version["body_sha256"] and version["possessed_at"]
            else None
        )
        if prepared and cached is not None and reusable_preparation_outcome(
            db, events, filing, version,
        ):
            outcome.documents_skipped += 1
            outcome.documents_cache_reused += 1
            db.commit()
            return
        prior = (
            reusable_outcome(db, generation, filing, version)
            if cached is not None and not prepared else None
        )
        if prior is not None:
            outcome.deduplicated += len(prior["sources"])
            self.source_records.retain_reused_sources(
                db,
                ingestion_run_id=generation.run_id,
                source_ids=[item["id"] for item in prior["sources"]],
            )
            record_outcome(
                db,
                generation,
                filing,
                {key: value for key, value in prior.items() if key != "filing"},
            )
            outcome.documents_would_skip += 1
            outcome.documents_skipped += 1
            outcome.documents_cache_reused += 1
            db.commit()
            return
        claim = self.state.claim(
            db,
            document_id=document_id,
            dataset=dataset,
            processor_signature=self.processor_signature,
            worker_id=worker_id,
            lease_seconds=self.settings.sec_document_lease_seconds,
            reprocess_completed=True,
        )
        if not claim.acquired:
            outcome.documents_claimed_elsewhere += 1
            # The owning run and OPEN/SELECTED events are already durable. A
            # lease held elsewhere or a retry backoff is incomplete coverage.
            db.commit()
            return
        outcome.stale_leases_recovered += int(claim.stale_recovered)
        db.commit()
        try:
            filing_text = cached
            retrieved_at = None
            if filing_text is None:
                if prepared:
                    raise ValueError("SEC_PREPARATION_CACHE_MISS")
                filing_text = provider.download_guidance_document(document)
                possessed_at = datetime.now(UTC)
                retrieved_at = possessed_at
                body_bytes = filing_text.encode("utf-8")
                locked_events(db, generation)
                self.state.heartbeat(
                    db,
                    extraction_id=claim.extraction_id,
                    execution_token=claim.execution_token,
                    lease_seconds=self.settings.sec_document_lease_seconds,
                )
                self.content_cache.store(document, filing_text)
                self.state.record_downloaded_content(
                    db,
                    document_id=document_id,
                    possessed_at=possessed_at,
                    content_hash=hashlib.sha256(body_bytes).hexdigest(),
                    content_bytes=len(body_bytes),
                )
                version = body_version(
                    db.get(CeriSecFilingDocument, document_id, populate_existing=True)
                )
                outcome.documents_downloaded += 1
                db.commit()
            else:
                outcome.documents_cache_reused += 1
            active_origin = (
                retained_preparation_exclusion(db, events[0], filing, version)
                if not prepared and cached is not None
                and self.settings.sec_document_incremental_mode is SecDocumentIncrementalMode.ACTIVE
                else None
            )
            refusal = (
                examine_attribution(document, filing_text) if prepared or active_origin else None
            )
            exclusion = None
            if refusal is not None:
                if active_origin is not None and active_origin[1] == refusal:
                    custody = active_origin[2]
                else:
                    custody = preparation.proof_for(filing) if prepared else None
                exclusion = archive_exclusion_proof(filing, version, filing_text, custody, refusal)
                if exclusion is None or has_unresolved_legacy_collision(db, filing):
                    raise SecAttributionRefused(refusal)
                records = ()
            else:
                records = provider.extract_guidance_document(
                    document, text=filing_text, require_financial_attribution=True,
                    retrieved_at=retrieved_at,
                )
            cancelled = _cancelled(should_cancel)
            locked_events(db, generation)
            self.state.heartbeat(
                db,
                extraction_id=claim.extraction_id,
                execution_token=claim.execution_token,
                lease_seconds=self.settings.sec_document_lease_seconds,
            )
            if cancelled:
                self.state.cancel(
                    db, extraction_id=claim.extraction_id, execution_token=claim.execution_token
                )
                db.commit()
                raise SecIncrementalCancelled("CERI SEC ingestion cancelled")
            if (
                body_version(db.get(CeriSecFilingDocument, document_id, populate_existing=True))
                != version
            ):
                raise ScopeEvidenceError("SEC_PROCESSING_BODY_VERSION_CHANGED")
            if preparation is not None:
                preparation.check_window()
            if exclusion is not None:
                self.state.fail_retryable(
                    db,
                    extraction_id=claim.extraction_id,
                    execution_token=claim.execution_token,
                    error_code=refusal,
                    message="Examined filing is excluded from financial attribution.",
                    retry_base_seconds=0,
                )
                record_outcome(
                    db, generation, filing,
                    {
                        "status": EXCLUDED_ATTRIBUTION, "body": version, "sources": [],
                        "reason": refusal, "custody": exclusion,
                        **({"preparation": asdict(owner)} if prepared else {
                            "exclusion_origin": active_origin[0],
                        }),
                    },
                    kind=REFUSAL,
                )
                outcome.documents_excluded_attribution += 1
                db.commit()
                return
            prepared_sources = (
                retain_prepared_sources(
                    db, owner=owner, filing=filing, body=version, records=records,
                )
                if prepared else []
            )
            sources = {}
            for record in (() if prepared else records):
                if (
                    record.provider != "sec"
                    or record.dataset is not CeriDataset.GUIDANCE
                    or record.payload.get("cik") != filing["cik"]
                    or record.payload.get("filing_accession") != filing["accession"]
                    or not str(record.payload.get("source_reference", "")).startswith(
                        filing["accession"] + "/" + filing["filename"] + "#paragraph-"
                    )
                ):
                    raise SecAttributionRefused("SEC_PROJECTED_SOURCE_OWNERSHIP_MISMATCH")
                outcome.requested += 1
                outcome.fetched += 1
                write = self.source_records.store_source_record(
                    db,
                    ingestion_run_id=generation.run_id,
                    record=record,
                    raw_payload_allowed=raw_payload_allowed,
                )
                if write.quarantined:
                    raise SecAttributionRefused("SEC_PROJECTED_SOURCE_QUARANTINED")
                sources[write.source_record.id] = write.source_record.content_hash
                outcome.inserted += int(write.inserted)
                outcome.deduplicated += int(write.deduplicated)
                outcome.corrected += int(write.corrected)
            if not records:
                outcome.documents_zero_records += 1
            self.state.complete(
                db,
                extraction_id=claim.extraction_id,
                execution_token=claim.execution_token,
                record_count=len(records),
                content_hash=version["body_sha256"],
                content_bytes=len(filing_text.encode("utf-8")),
                downloaded=False,
                record_registry_use=not prepared,
            )
            record_outcome(
                db,
                generation,
                filing,
                {
                    "status": "COMPLETED_WITH_RECORDS" if records else "COMPLETED_NO_RECORDS",
                    "body": version,
                    "sources": prepared_sources if prepared else [
                        {"id": key, "hash": value} for key, value in sorted(sources.items())
                    ],
                    **({"extraction": extraction_completion(db, claim.extraction_id)}
                       if prepared else {}),
                    **({"preparation": asdict(owner)} if prepared_sources else {}),
                },
            )
            db.commit()
        except PreparationExpired:
            db.rollback()
            locked_events(db, generation)
            self.state.cancel(
                db, extraction_id=claim.extraction_id, execution_token=claim.execution_token,
            )
            db.commit()
            raise
        except (SecIncrementalCancelled, ScopeEvidenceError):
            raise
        except Exception as exc:
            db.rollback()
            locked_events(db, generation)
            self._record_failure(db, claim=claim, exc=exc, outcome=outcome)
            record_outcome(
                db,
                generation,
                filing,
                {
                    "status": "FAILED_RETRYABLE",
                    "body": version,
                    "reason": str(exc)
                    if isinstance(exc, SecAttributionRefused)
                    else type(exc).__name__,
                },
                kind=REFUSAL if isinstance(exc, SecAttributionRefused) else OUTCOME,
            )
            db.commit()
            if isinstance(exc, SQLAlchemyError):
                raise

    def _record_failure(self, db: Session, *, claim, exc: Exception, outcome) -> None:
        outcome.failed += 1
        outcome.errors.append(
            {
                "error": redact_text(str(exc)).replace("\n", " ")[:500],
                "type": type(exc).__name__,
            }
        )
        if claim and claim.acquired and claim.execution_token:
            self.state.fail_retryable(
                db,
                extraction_id=claim.extraction_id,
                execution_token=claim.execution_token,
                error_code=type(exc).__name__.upper()[:64],
                message=redact_text(str(exc)),
                retry_base_seconds=self.settings.sec_document_retry_base_seconds,
            )

    @staticmethod
    @source_mutation_writer(
        MutationDomain.CERI_SOURCE, "provider_source", mode=MutationSemanticMode.MAINTENANCE
    )
    def _resolve_and_persist_cik(
        db: Session, *, provider: SecCeriProvider, ticker: str
    ) -> str | None:
        rows = list(
            db.scalars(select(CeriCompany).where(CeriCompany.ticker == ticker.upper())).all()
        )
        known = {str(row.cik).zfill(10) for row in rows if row.cik}
        if len(known) > 1:
            raise RuntimeError(f"Conflicting stored CIK values for ticker {ticker.upper()}")
        if known:
            return next(iter(known))
        resolved = provider.resolve_cik(ticker)
        if resolved is None:
            return None
        normalized = str(resolved).zfill(10)
        conflict = ownership_conflict(db, ticker.upper(), normalized, writing=True)
        if conflict:
            raise RuntimeError(conflict)
        if rows:
            for row in rows:
                if not row.cik:
                    row.cik = normalized
        else:
            db.add(CeriCompany(ticker=ticker.upper(), exchange="US", cik=normalized))
        db.flush()
        return normalized


def _cancelled(callback) -> bool:
    return bool(callable(callback) and callback())
