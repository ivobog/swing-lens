from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany, CeriIngestionRun
from app.services.ceri.dtos import GuidanceRequest
from app.services.ceri.enums import CeriDataset
from app.services.ceri.observability import ceri_metrics
from app.services.ceri.sec.content_cache import SecDocumentContentCache
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.provider import SecCeriProvider, SecGuidanceDocument
from app.services.ceri.sec.readiness_service import (
    SecReadinessFacts,
    SecReadinessPolicy,
    SecReadinessService,
    SecReadinessState,
)
from app.services.ceri.sec.state_service import (
    COMPLETED_STATUSES,
    SecDocumentIdentity,
    SecDocumentStateService,
)
from app.services.ceri.source_record_service import CeriSourceRecordService
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
        documents = provider.discover_guidance_documents(
            GuidanceRequest(
                company_id=None,
                ticker=ticker,
                start=start,
                end=end,
            ),
            cik=cik,
        )
        outcome.documents_discovered = len(documents)
        for document in documents:
            if _cancelled(should_cancel):
                raise SecIncrementalCancelled("CERI SEC ingestion cancelled")
            if not self._process_document(
                db,
                provider=provider,
                ingestion_run=ingestion_run,
                document=document,
                raw_payload_allowed=raw_payload_allowed,
                outcome=outcome,
                should_cancel=should_cancel,
                worker_id=worker_id or self.settings.job_worker_id,
                reuse_completed_documents=reuse_completed_documents,
            ):
                break
        if (
            mode is SecDocumentIncrementalMode.SHADOW
            and not historical_backfill
            and not outcome.failed
            and not outcome.documents_claimed_elsewhere
        ):
            filing_dates = [_parse_date(item.filing_date) for item in documents]
            self.state.certify_bootstrap(
                db,
                cik=cik,
                dataset=CeriDataset.GUIDANCE.value,
                processor_signature=self.processor_signature,
                document_count=len(documents),
                latest_filing_date=max((value for value in filing_dates if value), default=None),
            )
            db.commit()
        if outcome.documents_skipped and not outcome.documents_downloaded:
            outcome.readiness_state = SecReadinessState.READY_TERMINAL_REUSE.value
            outcome.run_evidence_status = "READY"
            outcome.readiness_reason = "TERMINAL_EXTRACTION_REUSED"
        elif outcome.readiness_state is None:
            outcome.readiness_state = SecReadinessState.READY.value
            outcome.run_evidence_status = "READY"
            outcome.readiness_reason = "SEC_INGESTION_COMPLETED"
        return outcome

    def _process_document(
        self,
        db: Session,
        *,
        provider: SecCeriProvider,
        ingestion_run: CeriIngestionRun,
        document: SecGuidanceDocument,
        raw_payload_allowed: bool,
        outcome: SecIncrementalOutcome,
        should_cancel,
        worker_id: str,
        reuse_completed_documents: bool,
    ) -> bool:
        registered = self.state.register_document(
            db,
            SecDocumentIdentity(
                cik=document.cik,
                accession_number=document.accession_number,
                document_name=document.document_name,
                ticker_hint=document.ticker,
                form=document.form,
                filing_date=_parse_date(document.filing_date),
            ),
        )
        current = self.state.extraction(
            db,
            document_id=registered.id,
            dataset=CeriDataset.GUIDANCE.value,
            processor_signature=self.processor_signature,
        )
        completed = current is not None and current.status in COMPLETED_STATUSES
        if completed:
            outcome.documents_would_skip += 1
            ceri_metrics.increment(
                "ceri_ingestion_sec_documents_would_skip_total",
                dataset=CeriDataset.GUIDANCE.value,
            )
            if self.settings.sec_document_incremental_mode is SecDocumentIncrementalMode.ACTIVE:
                outcome.documents_skipped += 1
                ceri_metrics.increment(
                    "ceri_ingestion_sec_documents_skipped_total",
                    dataset=CeriDataset.GUIDANCE.value,
                )
                return True
            if reuse_completed_documents:
                outcome.documents_skipped += 1
                ceri_metrics.increment(
                    "ceri_ingestion_sec_documents_skipped_total",
                    dataset=CeriDataset.GUIDANCE.value,
                )
                return True

        claim = None
        if not completed:
            claim = self.state.claim(
                db,
                document_id=registered.id,
                dataset=CeriDataset.GUIDANCE.value,
                processor_signature=self.processor_signature,
                worker_id=worker_id,
                lease_seconds=self.settings.sec_document_lease_seconds,
            )
            if not claim.acquired:
                outcome.documents_claimed_elsewhere += 1
                if self.settings.sec_document_incremental_mode is SecDocumentIncrementalMode.ACTIVE:
                    db.rollback()
                    return True
            else:
                outcome.stale_leases_recovered += int(claim.stale_recovered)
                # The claim must be durable before synchronous SEC I/O begins.
                db.commit()
        try:
            filing_text = (
                self.content_cache.load(
                    document,
                    expected_hash=registered.last_content_hash,
                )
                if registered.last_content_hash
                else None
            )
            downloaded = filing_text is None
            if filing_text is None:
                filing_text = provider.download_guidance_document(document)
                self.content_cache.store(document, filing_text)
                outcome.documents_downloaded += 1
                content_bytes = len(filing_text.encode("utf-8"))
                self.state.record_downloaded_content(
                    db,
                    document_id=registered.id,
                    content_hash=hashlib.sha256(filing_text.encode("utf-8")).hexdigest(),
                    content_bytes=content_bytes,
                )
                db.commit()
            else:
                outcome.documents_cache_reused += 1
            records = provider.extract_guidance_document(document, text=filing_text)
            if _cancelled(should_cancel):
                if claim and claim.acquired and claim.execution_token:
                    self.state.cancel(
                        db,
                        extraction_id=claim.extraction_id,
                        execution_token=claim.execution_token,
                    )
                    db.commit()
                raise SecIncrementalCancelled("CERI SEC ingestion cancelled")
            if claim and claim.acquired and claim.execution_token:
                self.state.heartbeat(
                    db,
                    extraction_id=claim.extraction_id,
                    execution_token=claim.execution_token,
                    lease_seconds=self.settings.sec_document_lease_seconds,
                )
                db.commit()
            for record in records:
                outcome.requested += 1
                outcome.fetched += 1
                write = self.source_records.store_source_record(
                    db,
                    ingestion_run_id=ingestion_run.id,
                    record=record,
                    raw_payload_allowed=raw_payload_allowed,
                )
                outcome.inserted += int(write.inserted)
                outcome.deduplicated += int(write.deduplicated)
                outcome.corrected += int(write.corrected)
                outcome.quarantined += int(write.quarantined)
            if not records:
                outcome.documents_zero_records += 1
            if claim and claim.acquired and claim.execution_token:
                content_bytes = len(filing_text.encode("utf-8"))
                self.state.complete(
                    db,
                    extraction_id=claim.extraction_id,
                    execution_token=claim.execution_token,
                    record_count=len(records),
                    content_hash=hashlib.sha256(filing_text.encode("utf-8")).hexdigest(),
                    content_bytes=content_bytes,
                    downloaded=downloaded,
                )
            # Source records and the authoritative successful state commit together.
            db.commit()
            return True
        except SecIncrementalCancelled:
            raise
        except SQLAlchemyError as exc:
            db.rollback()
            self._record_failure(db, claim=claim, exc=exc, outcome=outcome)
            raise
        except Exception as exc:
            db.rollback()
            self._record_failure(db, claim=claim, exc=exc, outcome=outcome)
            return False

    def _record_failure(self, db: Session, *, claim, exc: Exception, outcome) -> None:
        outcome.failed += 1
        outcome.errors.append(
            {"error": str(exc).replace("\n", " ")[:500], "type": type(exc).__name__}
        )
        if claim and claim.acquired and claim.execution_token:
            self.state.fail_retryable(
                db,
                extraction_id=claim.extraction_id,
                execution_token=claim.execution_token,
                error_code=type(exc).__name__.upper()[:64],
                message=str(exc),
                retry_base_seconds=self.settings.sec_document_retry_base_seconds,
            )
            db.commit()

    @staticmethod
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


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
