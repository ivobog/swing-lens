from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCatalystSource,
    CeriProcessingRun,
    CeriSourceRecord,
)
from app.services.ceri.catalyst_taxonomy import CeriCatalystTaxonomy
from app.services.ceri.earnings_normalizer import CeriEarningsNormalizer
from app.services.ceri.enums import CeriDataset
from app.services.ceri.estimate_normalizer import CeriEstimateNormalizer
from app.services.ceri.guidance_normalizer import CeriGuidanceNormalizer
from app.services.ceri.identity_resolver import CeriIdentityResolver


@dataclass(frozen=True)
class CeriNormalizationResult:
    processing_run_id: int | None
    status: str
    read: int
    normalized: int
    quarantined: int
    failed: int
    warnings: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "processing_run_id": self.processing_run_id,
            "status": self.status,
            "read": self.read,
            "normalized": self.normalized,
            "quarantined": self.quarantined,
            "failed": self.failed,
            "warnings": self.warnings,
        }


class CeriNormalizationService:
    def __init__(
        self,
        *,
        identity_resolver: CeriIdentityResolver | None = None,
        estimate_normalizer: CeriEstimateNormalizer | None = None,
        earnings_normalizer: CeriEarningsNormalizer | None = None,
        guidance_normalizer: CeriGuidanceNormalizer | None = None,
        catalyst_taxonomy: CeriCatalystTaxonomy | None = None,
    ) -> None:
        self.identity_resolver = identity_resolver or CeriIdentityResolver()
        self.estimate_normalizer = estimate_normalizer or CeriEstimateNormalizer()
        self.earnings_normalizer = earnings_normalizer or CeriEarningsNormalizer()
        self.guidance_normalizer = guidance_normalizer or CeriGuidanceNormalizer()
        self.catalyst_taxonomy = catalyst_taxonomy or CeriCatalystTaxonomy()

    def normalize(
        self,
        db: Session,
        *,
        processing_run: CeriProcessingRun,
        ingestion_run_id: int | None = None,
        source_records: list[CeriSourceRecord] | None = None,
    ) -> CeriNormalizationResult:
        records = (
            source_records
            if source_records is not None
            else _source_records(db, ingestion_run_id)
        )
        read = normalized = quarantined = failed = warning_count = 0
        errors: list[dict[str, Any]] = []

        for index, source_record in enumerate(records, start=1):
            read += 1
            if source_record.quarantine_reason:
                quarantined += 1
                continue
            try:
                resolution = self.identity_resolver.resolve_source_record(db, source_record)
                if not resolution.resolved:
                    quarantined += 1
                    continue
                created = self._normalize_record(
                    db,
                    source_record,
                    company_id=resolution.company_id,
                )
                normalized += created
                warning_count += _warning_count_for_last(db)
                processing_run.checkpoint_json = {
                    "last_source_record_id": source_record.id,
                    "last_record_index": index,
                }
            except Exception as exc:
                failed += 1
                errors.append(
                    {
                        "source_record_id": source_record.id,
                        "error": str(exc).replace("\n", " ")[:500],
                    }
                )

        status = "COMPLETED" if failed == 0 and quarantined == 0 else "PARTIAL"
        processing_run.status = status
        processing_run.read_count = read
        processing_run.normalized_count = normalized
        processing_run.failed_count = failed
        processing_run.warning_count = warning_count
        processing_run.errors_json = {"records": errors} if errors else None
        processing_run.counts_json = {"quarantined": quarantined}
        processing_run.completed_at = _utcnow()
        if processing_run.started_at is not None:
            processing_run.duration_ms = _duration_ms(
                processing_run.started_at,
                processing_run.completed_at,
            )
        db.flush()
        return CeriNormalizationResult(
            processing_run_id=processing_run.id,
            status=status,
            read=read,
            normalized=normalized,
            quarantined=quarantined,
            failed=failed,
            warnings=warning_count,
        )

    def _normalize_record(
        self,
        db: Session,
        source_record: CeriSourceRecord,
        *,
        company_id: int,
    ) -> int:
        dataset = CeriDataset(source_record.dataset)
        if dataset is CeriDataset.ESTIMATES:
            db.add(self.estimate_normalizer.normalize(source_record, company_id=company_id))
            return 1
        if dataset is CeriDataset.EARNINGS:
            db.add(self.earnings_normalizer.normalize(source_record, company_id=company_id))
            return 1
        if dataset is CeriDataset.GUIDANCE:
            db.add(self.guidance_normalizer.normalize(source_record, company_id=company_id))
            return 1
        if dataset is CeriDataset.CATALYSTS:
            record = self.catalyst_taxonomy.normalize(source_record, company_id=company_id)
            event = _find_catalyst_event(
                db,
                company_id=company_id,
                category=record.category.value,
                subject_key=record.subject_key,
            )
            if event is not None:
                db.add(
                    CeriCatalystSource(
                        catalyst_event_id=event.id,
                        catalyst_revision_id=None,
                        source_record_id=source_record.id,
                        source_fields_json=source_record.raw_json
                        or source_record.restricted_normalized_json,
                    )
                )
                return 1

            event = CeriCatalystEvent(
                company_id=company_id,
                category=record.category.value,
                subtype=record.subtype,
                subject_key=record.subject_key,
                canonical_text=record.canonical_text,
            )
            db.add(event)
            db.flush()
            revision = CeriCatalystEventRevision(
                catalyst_event_id=event.id,
                source_record_id=source_record.id,
                revision_number=1,
                is_current=True,
                announced_at=record.announced_at,
                expected_date=record.expected_date,
                effective_session=record.effective_session,
                status=record.status.value,
                direction=record.direction.value,
                materiality=record.materiality,
                date_confidence=record.date_confidence.value,
                source_confidence=record.confidence.value,
                operational_values_json={"subject_key": record.subject_key},
                conflict_flags_json=list(record.conflict_flags) or None,
            )
            db.add(revision)
            db.flush()
            db.add(
                CeriCatalystSource(
                    catalyst_event_id=event.id,
                    catalyst_revision_id=revision.id,
                    source_record_id=source_record.id,
                    source_fields_json=source_record.raw_json
                    or source_record.restricted_normalized_json,
                )
            )
            return 3
        raise ValueError(f"Unsupported CERI dataset for normalization: {source_record.dataset}")


def _source_records(db: Session, ingestion_run_id: int | None) -> list[CeriSourceRecord]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    statement = select(CeriSourceRecord)
    if ingestion_run_id is not None:
        statement = statement.where(CeriSourceRecord.ingestion_run_id == ingestion_run_id)
    result = scalars(statement)
    return list(result.all() if hasattr(result, "all") else result)


def _find_catalyst_event(
    db: Session,
    *,
    company_id: int,
    category: str,
    subject_key: str,
) -> CeriCatalystEvent | None:
    scalar = getattr(db, "scalar", None)
    if not callable(scalar):
        return None
    return scalar(
        select(CeriCatalystEvent)
        .where(CeriCatalystEvent.company_id == company_id)
        .where(CeriCatalystEvent.category == category)
        .where(CeriCatalystEvent.subject_key == subject_key)
    )


def _warning_count_for_last(db: Session) -> int:
    added = getattr(db, "added", None)
    if not added:
        return 0
    row = added[-1]
    for attr in ("quality_flags_json", "quality_warnings_json", "conflict_flags_json"):
        value = getattr(row, attr, None)
        if value:
            return len(value)
    return 0


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _duration_ms(started_at: datetime, completed_at: datetime) -> int:
    if started_at.tzinfo is None and completed_at.tzinfo is not None:
        started_at = started_at.replace(tzinfo=completed_at.tzinfo)
    if completed_at.tzinfo is None and started_at.tzinfo is not None:
        completed_at = completed_at.replace(tzinfo=started_at.tzinfo)
    return int((completed_at - started_at).total_seconds() * 1000)
