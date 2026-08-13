from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriSecDocumentExtraction,
    CeriSecFilingDocument,
    CeriSecSyncState,
)


class SecExtractionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED_WITH_RECORDS = "COMPLETED_WITH_RECORDS"
    COMPLETED_NO_RECORDS = "COMPLETED_NO_RECORDS"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    CANCELLED = "CANCELLED"


COMPLETED_STATUSES = {
    SecExtractionStatus.COMPLETED_WITH_RECORDS.value,
    SecExtractionStatus.COMPLETED_NO_RECORDS.value,
}


@dataclass(frozen=True)
class SecDocumentIdentity:
    cik: str
    accession_number: str
    document_name: str
    ticker_hint: str | None = None
    form: str | None = None
    filing_date: date | None = None


@dataclass(frozen=True)
class SecDocumentClaim:
    acquired: bool
    extraction_id: int
    status: str
    execution_token: str | None
    stale_recovered: bool = False


class SecDocumentStateService:
    def register_document(
        self, db: Session, identity: SecDocumentIdentity
    ) -> CeriSecFilingDocument:
        now = _utcnow()
        values = {
            "cik": _normalize_cik(identity.cik),
            "accession_number": identity.accession_number.strip(),
            "document_name": identity.document_name.strip(),
            "ticker_hint": identity.ticker_hint.upper() if identity.ticker_hint else None,
            "form": identity.form,
            "filing_date": identity.filing_date,
            "last_seen_at": now,
        }
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            statement = (
                pg_insert(CeriSecFilingDocument)
                .values(**values)
                .on_conflict_do_update(
                    constraint="uq_ceri_sec_filing_document_identity",
                    set_={
                        "ticker_hint": values["ticker_hint"],
                        "form": values["form"],
                        "filing_date": values["filing_date"],
                        "last_seen_at": now,
                    },
                )
                .returning(CeriSecFilingDocument.id)
            )
            document_id = db.scalar(statement)
            document = db.get(CeriSecFilingDocument, document_id)
            assert document is not None
            return document
        document = db.scalar(
            select(CeriSecFilingDocument).where(
                CeriSecFilingDocument.cik == values["cik"],
                CeriSecFilingDocument.accession_number == values["accession_number"],
                CeriSecFilingDocument.document_name == values["document_name"],
            )
        )
        if document is None:
            document = CeriSecFilingDocument(**values)
            db.add(document)
            db.flush()
        else:
            document.ticker_hint = values["ticker_hint"]
            document.form = values["form"]
            document.filing_date = values["filing_date"]
            document.last_seen_at = now
        return document

    def extraction(
        self, db: Session, *, document_id: int, dataset: str, processor_signature: str
    ) -> CeriSecDocumentExtraction | None:
        return db.scalar(
            select(CeriSecDocumentExtraction).where(
                CeriSecDocumentExtraction.document_id == document_id,
                CeriSecDocumentExtraction.dataset == dataset,
                CeriSecDocumentExtraction.processor_signature == processor_signature,
            )
        )

    def claim(
        self,
        db: Session,
        *,
        document_id: int,
        dataset: str,
        processor_signature: str,
        worker_id: str,
        lease_seconds: int,
    ) -> SecDocumentClaim:
        now = _utcnow()
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                pg_insert(CeriSecDocumentExtraction)
                .values(
                    document_id=document_id,
                    dataset=dataset,
                    processor_signature=processor_signature,
                    status=SecExtractionStatus.PENDING.value,
                )
                .on_conflict_do_nothing(constraint="uq_ceri_sec_document_extraction_identity")
            )
        elif (
            self.extraction(
                db,
                document_id=document_id,
                dataset=dataset,
                processor_signature=processor_signature,
            )
            is None
        ):
            db.add(
                CeriSecDocumentExtraction(
                    document_id=document_id,
                    dataset=dataset,
                    processor_signature=processor_signature,
                    status=SecExtractionStatus.PENDING.value,
                )
            )
            db.flush()
        current = self.extraction(
            db,
            document_id=document_id,
            dataset=dataset,
            processor_signature=processor_signature,
        )
        assert current is not None
        if (
            current.status in COMPLETED_STATUSES
            or current.status == SecExtractionStatus.FAILED_PERMANENT
        ):
            return SecDocumentClaim(False, current.id, current.status, None)
        stale = current.status == SecExtractionStatus.RUNNING and (
            current.lease_expires_at is None or current.lease_expires_at <= now
        )
        token = uuid4().hex
        eligible = or_(
            CeriSecDocumentExtraction.status.in_(
                [
                    SecExtractionStatus.PENDING.value,
                    SecExtractionStatus.CANCELLED.value,
                ]
            ),
            and_(
                CeriSecDocumentExtraction.status == SecExtractionStatus.FAILED_RETRYABLE.value,
                or_(
                    CeriSecDocumentExtraction.next_retry_at.is_(None),
                    CeriSecDocumentExtraction.next_retry_at <= now,
                ),
            ),
            and_(
                CeriSecDocumentExtraction.status == SecExtractionStatus.RUNNING.value,
                or_(
                    CeriSecDocumentExtraction.lease_expires_at.is_(None),
                    CeriSecDocumentExtraction.lease_expires_at <= now,
                ),
            ),
        )
        result = db.execute(
            update(CeriSecDocumentExtraction)
            .where(CeriSecDocumentExtraction.id == current.id, eligible)
            .values(
                status=SecExtractionStatus.RUNNING.value,
                attempt_count=CeriSecDocumentExtraction.attempt_count + 1,
                worker_id=worker_id,
                execution_token=token,
                started_at=now,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                next_retry_at=None,
                completed_at=None,
                last_error_code=None,
                last_error_message=None,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            db.expire_all()
            current = self.extraction(
                db,
                document_id=document_id,
                dataset=dataset,
                processor_signature=processor_signature,
            )
            assert current is not None
            return SecDocumentClaim(False, current.id, current.status, None)
        return SecDocumentClaim(True, current.id, SecExtractionStatus.RUNNING.value, token, stale)

    def complete(
        self,
        db: Session,
        *,
        extraction_id: int,
        execution_token: str,
        record_count: int,
        content_hash: str,
        content_bytes: int,
    ) -> None:
        now = _utcnow()
        status = (
            SecExtractionStatus.COMPLETED_WITH_RECORDS.value
            if record_count
            else SecExtractionStatus.COMPLETED_NO_RECORDS.value
        )
        result = db.execute(
            update(CeriSecDocumentExtraction)
            .where(
                CeriSecDocumentExtraction.id == extraction_id,
                CeriSecDocumentExtraction.status == SecExtractionStatus.RUNNING.value,
                CeriSecDocumentExtraction.execution_token == execution_token,
            )
            .values(
                status=status,
                record_count=record_count,
                completed_at=now,
                heartbeat_at=now,
                lease_expires_at=None,
                worker_id=None,
                execution_token=None,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("SEC extraction lease was lost before completion")
        extraction = db.get(CeriSecDocumentExtraction, extraction_id)
        assert extraction is not None
        db.execute(
            update(CeriSecFilingDocument)
            .where(CeriSecFilingDocument.id == extraction.document_id)
            .values(
                last_downloaded_at=now,
                last_content_hash=content_hash,
                last_content_bytes=content_bytes,
                last_seen_at=now,
            )
        )

    def heartbeat(
        self,
        db: Session,
        *,
        extraction_id: int,
        execution_token: str,
        lease_seconds: int,
    ) -> None:
        now = _utcnow()
        result = db.execute(
            update(CeriSecDocumentExtraction)
            .where(
                CeriSecDocumentExtraction.id == extraction_id,
                CeriSecDocumentExtraction.status == SecExtractionStatus.RUNNING.value,
                CeriSecDocumentExtraction.execution_token == execution_token,
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("SEC extraction lease was lost during heartbeat")

    def fail_retryable(
        self,
        db: Session,
        *,
        extraction_id: int,
        execution_token: str,
        error_code: str,
        message: str,
        retry_base_seconds: int,
    ) -> None:
        extraction = db.get(CeriSecDocumentExtraction, extraction_id)
        attempt = max(1, int(extraction.attempt_count if extraction else 1))
        delay = min(3600, retry_base_seconds * (2 ** min(attempt - 1, 7)))
        self._finish_unsuccessful(
            db,
            extraction_id=extraction_id,
            execution_token=execution_token,
            status=SecExtractionStatus.FAILED_RETRYABLE.value,
            error_code=error_code,
            message=message,
            next_retry_at=_utcnow() + timedelta(seconds=delay),
        )

    def cancel(self, db: Session, *, extraction_id: int, execution_token: str) -> None:
        self._finish_unsuccessful(
            db,
            extraction_id=extraction_id,
            execution_token=execution_token,
            status=SecExtractionStatus.CANCELLED.value,
            error_code="CANCELLED",
            message="Cooperative cancellation requested",
            next_retry_at=None,
        )

    def _finish_unsuccessful(
        self,
        db: Session,
        *,
        extraction_id: int,
        execution_token: str,
        status: str,
        error_code: str,
        message: str,
        next_retry_at: datetime | None,
    ) -> None:
        now = _utcnow()
        result = db.execute(
            update(CeriSecDocumentExtraction)
            .where(
                CeriSecDocumentExtraction.id == extraction_id,
                CeriSecDocumentExtraction.status == SecExtractionStatus.RUNNING.value,
                CeriSecDocumentExtraction.execution_token == execution_token,
            )
            .values(
                status=status,
                worker_id=None,
                execution_token=None,
                heartbeat_at=now,
                lease_expires_at=None,
                next_retry_at=next_retry_at,
                last_error_code=error_code[:64],
                last_error_message=message.replace("\n", " ")[:500],
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("SEC extraction lease was lost before state transition")

    def is_bootstrap_certified(
        self, db: Session, *, cik: str, dataset: str, processor_signature: str
    ) -> bool:
        return (
            db.scalar(
                select(CeriSecSyncState.id).where(
                    CeriSecSyncState.cik == _normalize_cik(cik),
                    CeriSecSyncState.dataset == dataset,
                    CeriSecSyncState.processor_signature == processor_signature,
                )
            )
            is not None
        )

    def certify_bootstrap(
        self,
        db: Session,
        *,
        cik: str,
        dataset: str,
        processor_signature: str,
        document_count: int,
        latest_filing_date: date | None,
    ) -> None:
        now = _utcnow()
        values = {
            "cik": _normalize_cik(cik),
            "dataset": dataset,
            "processor_signature": processor_signature,
            "bootstrap_completed_at": now,
            "last_discovered_at": now,
            "latest_filing_date": latest_filing_date,
            "document_count": document_count,
        }
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                pg_insert(CeriSecSyncState)
                .values(**values)
                .on_conflict_do_update(
                    constraint="uq_ceri_sec_sync_state",
                    set_={key: value for key, value in values.items() if key != "cik"},
                )
            )
            return
        current = db.scalar(
            select(CeriSecSyncState).where(
                CeriSecSyncState.cik == values["cik"],
                CeriSecSyncState.dataset == dataset,
                CeriSecSyncState.processor_signature == processor_signature,
            )
        )
        if current is None:
            db.add(CeriSecSyncState(**values))
        else:
            for key, value in values.items():
                setattr(current, key, value)


def _normalize_cik(value: str) -> str:
    return (str(value).lstrip("0") or "0").zfill(10)


def _utcnow() -> datetime:
    return datetime.now(UTC)
