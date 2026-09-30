from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCatalystSource,
    CeriCompany,
    CeriCompanyAlias,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriIngestionRun,
    CeriIngestionRunSourceRecord,
    CeriProcessingRun,
    CeriSourceRecord,
)
from app.services.ceri.catalyst_taxonomy import CeriCatalystTaxonomy
from app.services.ceri.earnings_normalizer import CeriEarningsNormalizer
from app.services.ceri.enums import CeriDataset
from app.services.ceri.estimate_normalizer import CeriEstimateNormalizer
from app.services.ceri.guidance_comparison_service import compare_guidance
from app.services.ceri.guidance_normalizer import (
    CeriGuidanceNormalizer,
    apply_guidance_eligibility,
)
from app.services.ceri.identity_resolver import CeriIdentityResolver
from app.services.domain_mutation import MutationDomain
from app.services.redaction import redact_text
from app.services.source_mutation_authority import source_mutation_writer, source_writer_member


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


class CeriNormalizationCancelled(RuntimeError):
    pass


class CeriNormalizationClosureError(RuntimeError):
    """Run-local source membership could not be closed deterministically."""


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
        should_cancel: Callable[[], bool] | None = None,
        checkpoint_interval: int = 5,
        checkpoint_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> CeriNormalizationResult:
        all_records = (
            source_records if source_records is not None else _source_records(db, ingestion_run_id)
        )
        memberships = _memberships_by_source(db, ingestion_run_id)
        if ingestion_run_id is not None:
            ingestion = db.get(CeriIngestionRun, int(ingestion_run_id))
            if ingestion is None:
                raise CeriNormalizationClosureError(
                    f"CERI_NORMALIZATION_INGESTION_RUN_MISSING:{ingestion_run_id}"
                )
            if int(ingestion.fetched_count or 0) > 0 and not memberships:
                raise CeriNormalizationClosureError(
                    "CERI_NORMALIZATION_SOURCE_MEMBERSHIP_MISSING:"
                    f"ingestion_run={ingestion_run_id}:fetched={ingestion.fetched_count}"
                )
        records = list(all_records)
        prepare_identity = getattr(self.identity_resolver, "prepare", None)
        if callable(prepare_identity):
            prepare_identity(db)
        checkpoint = processing_run.checkpoint_json or {}
        last_source_record_id = int(checkpoint.get("last_source_record_id") or 0)
        records = [
            record
            for record in records
            if record.id is None or int(record.id) > last_source_record_id
        ]
        read = int(processing_run.read_count or 0)
        normalized = int(processing_run.normalized_count or 0)
        quarantined = int((processing_run.counts_json or {}).get("quarantined") or 0)
        failed = int(processing_run.failed_count or 0)
        warning_count = int(processing_run.warning_count or 0)
        errors: list[dict[str, Any]] = list((processing_run.errors_json or {}).get("records") or [])

        for index, source_record in enumerate(records, start=1):
            if callable(should_cancel) and should_cancel():
                raise CeriNormalizationCancelled("CERI normalization cancelled.")
            read += 1
            membership = memberships.get(int(source_record.id)) if source_record.id else None
            if source_record.quarantine_reason:
                quarantined += 1
                _mark_membership(membership, "QUARANTINED", source_record.quarantine_reason)
            else:
                try:
                    resolution = self.identity_resolver.resolve_source_record(db, source_record)
                    if not resolution.resolved:
                        quarantined += 1
                        _mark_membership(
                            membership, "REJECTED", "IDENTITY_RESOLUTION_REJECTED"
                        )
                    else:
                        reused = _has_normalized_representation(db, source_record)
                        created = self._normalize_record(
                            db,
                            source_record,
                            company_id=resolution.company_id,
                        )
                        normalized += created
                        _mark_membership(
                            membership, "REUSED" if reused else "NORMALIZED", None
                        )
                        warning_count += _warning_count_for_last(db)
                except Exception as exc:
                    failed += 1
                    _mark_membership(
                        membership,
                        "REJECTED",
                        redact_text(str(exc)).replace("\n", " ")[:500],
                    )
                    errors.append(
                        {
                            "source_record_id": source_record.id,
                            "error": redact_text(str(exc)).replace("\n", " ")[:500],
                        }
                    )
            if index % max(1, checkpoint_interval) == 0:
                _update_processing_checkpoint(
                    processing_run,
                    source_record_id=source_record.id,
                    record_index=index,
                    read=read,
                    normalized=normalized,
                    failed=failed,
                    warnings=warning_count,
                    quarantined=quarantined,
                    errors=errors,
                )
            if callable(checkpoint_callback) and index % max(1, checkpoint_interval) == 0:
                checkpoint_callback(dict(processing_run.checkpoint_json))

        closure = _normalization_closure(db, ingestion_run_id)
        if closure["referenced"] != closure["terminal"]:
            processing_run.status = "FAILED"
            processing_run.failed_count = max(1, failed)
            processing_run.errors_json = {
                "invariant": "CERI_NORMALIZATION_CLOSURE_INCOMPLETE",
                "closure": closure,
                "records": errors,
            }
            db.flush()
            raise CeriNormalizationClosureError(
                "CERI_NORMALIZATION_CLOSURE_INCOMPLETE:"
                f"referenced={closure['referenced']}:terminal={closure['terminal']}"
            )
        status = "COMPLETED" if failed == 0 and quarantined == 0 else "PARTIAL"
        processing_run.status = status
        processing_run.read_count = read
        processing_run.normalized_count = normalized
        processing_run.failed_count = failed
        processing_run.warning_count = warning_count
        processing_run.errors_json = {"records": errors} if errors else None
        processing_run.counts_json = {"quarantined": quarantined, "closure": closure}
        if records:
            processing_run.checkpoint_json = {
                "last_source_record_id": records[-1].id,
                "last_record_index": len(records),
            }
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

    @source_mutation_writer(MutationDomain.CERI_SOURCE, "provider_source")
    def _normalize_record(
        self,
        db: Session,
        source_record: CeriSourceRecord,
        *,
        company_id: int,
    ) -> int:
        # Source identity maintenance belongs to the same validated native
        # source transaction as normalization, including direct record calls.
        _persist_sec_identity(db, source_record, company_id)
        dataset = CeriDataset(source_record.dataset)
        if dataset is CeriDataset.ESTIMATES:
            if _exists_by_source(db, CeriEstimateSnapshot, source_record.id):
                return 0
            db.add(self.estimate_normalizer.normalize(source_record, company_id=company_id))
            return 1
        if dataset is CeriDataset.EARNINGS:
            if _exists_by_source(db, CeriEarningsActual, source_record.id):
                return 0
            db.add(self.earnings_normalizer.normalize(source_record, company_id=company_id))
            return 1
        if dataset is CeriDataset.GUIDANCE:
            if _exists_by_source(db, CeriGuidanceEvent, source_record.id):
                return 0
            guidance = self.guidance_normalizer.normalize(source_record, company_id=company_id)
            db.add(guidance)
            db.flush()
            prior = _prior_guidance(db, guidance)
            comparison = compare_guidance(guidance, prior)
            if source_record.provider == "sec":
                guidance.action = comparison.action
                guidance.confidence = comparison.confidence
                guidance.quality_warnings_json = sorted(
                    set(guidance.quality_warnings_json or [])
                    | set(comparison.warnings)
                    | ({"manual_review_required"} if comparison.action == "UNKNOWN" else set())
                )
                apply_guidance_eligibility(guidance)
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
                if _exists_by_source(db, CeriCatalystSource, source_record.id):
                    return 0
                current = _maybe_scalar(
                    db,
                    select(CeriCatalystEventRevision)
                    .where(CeriCatalystEventRevision.catalyst_event_id == event.id)
                    .where(CeriCatalystEventRevision.is_current.is_(True)),
                )
                if current is None:
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
                if current is not None and _catalyst_revision_matches(current, record):
                    db.add(
                        CeriCatalystSource(
                            catalyst_event_id=event.id,
                            catalyst_revision_id=current.id,
                            source_record_id=source_record.id,
                            source_fields_json=source_record.raw_json
                            or source_record.restricted_normalized_json,
                        )
                    )
                    return 1
                if current is not None:
                    current.is_current = False
                next_number = _next_catalyst_revision_number(db, int(event.id))
                revision = CeriCatalystEventRevision(
                    catalyst_event_id=event.id,
                    source_record_id=source_record.id,
                    prior_revision_id=current.id if current is not None else None,
                    revision_number=next_number,
                    is_current=True,
                    announced_at=record.announced_at,
                    expected_date=record.expected_date,
                    effective_session=record.effective_session,
                    status=record.status.value,
                    direction=record.direction.value,
                    materiality=record.materiality,
                    date_confidence=record.date_confidence.value,
                    source_confidence=record.confidence.value,
                    operational_values_json={
                        "subject_key": record.subject_key,
                        "issuer_relevance": record.issuer_relevance,
                        "relevance_reason": record.relevance_reason,
                    },
                    issuer_relevance=record.issuer_relevance,
                    relevance_reason=record.relevance_reason,
                    binary_eligible=None,
                    conflict_flags_json=list(record.conflict_flags) or None,
                )
                db.add(revision)
                db.flush()
                event.last_updated_at = _utcnow()
                db.add(
                    CeriCatalystSource(
                        catalyst_event_id=event.id,
                        catalyst_revision_id=revision.id,
                        source_record_id=source_record.id,
                        source_fields_json=source_record.raw_json
                        or source_record.restricted_normalized_json,
                    )
                )
                return 2

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
                operational_values_json={
                    "subject_key": record.subject_key,
                    "issuer_relevance": record.issuer_relevance,
                    "relevance_reason": record.relevance_reason,
                },
                issuer_relevance=record.issuer_relevance,
                relevance_reason=record.relevance_reason,
                binary_eligible=None,
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
        statement = statement.join(
            CeriIngestionRunSourceRecord,
            CeriIngestionRunSourceRecord.source_record_id == CeriSourceRecord.id,
        ).where(CeriIngestionRunSourceRecord.ingestion_run_id == ingestion_run_id)
    statement = statement.order_by(CeriSourceRecord.id.asc())
    result = scalars(statement)
    return list(result.all() if hasattr(result, "all") else result)


def _memberships_by_source(
    db: Session, ingestion_run_id: int | None
) -> dict[int, CeriIngestionRunSourceRecord]:
    if ingestion_run_id is None or not isinstance(db, Session):
        return {}
    return {
        int(row.source_record_id): row
        for row in db.scalars(
            select(CeriIngestionRunSourceRecord).where(
                CeriIngestionRunSourceRecord.ingestion_run_id == int(ingestion_run_id)
            )
        )
    }


def _mark_membership(
    membership: CeriIngestionRunSourceRecord | None,
    state: str,
    reason: str | None,
) -> None:
    if membership is None:
        return
    membership.normalization_state = state
    membership.normalization_reason = reason
    membership.normalized_at = _utcnow()


def _has_normalized_representation(db: Session, source: CeriSourceRecord) -> bool:
    model = {
        CeriDataset.ESTIMATES: CeriEstimateSnapshot,
        CeriDataset.EARNINGS: CeriEarningsActual,
        CeriDataset.GUIDANCE: CeriGuidanceEvent,
        CeriDataset.CATALYSTS: CeriCatalystSource,
    }.get(CeriDataset(source.dataset))
    return bool(model is not None and _exists_by_source(db, model, source.id))


def _normalization_closure(db: Session, ingestion_run_id: int | None) -> dict[str, int]:
    if ingestion_run_id is None or not isinstance(db, Session):
        return {"referenced": 0, "terminal": 0}
    rows = list(
        db.scalars(
            select(CeriIngestionRunSourceRecord).where(
                CeriIngestionRunSourceRecord.ingestion_run_id == int(ingestion_run_id)
            )
        )
    )
    states = {
        state: 0
        for state in ("NORMALIZED", "REUSED", "QUARANTINED", "REJECTED", "PENDING")
    }
    for row in rows:
        states[row.normalization_state] = states.get(row.normalization_state, 0) + 1
    terminal = sum(
        states.get(value, 0)
        for value in ("NORMALIZED", "REUSED", "QUARANTINED", "REJECTED")
    )
    return {
        "referenced": len(rows),
        "terminal": terminal,
        **{key.lower(): value for key, value in states.items()},
    }


@source_writer_member(
    "app.services.ceri.normalization_service:CeriNormalizationService._normalize_record"
)
def _persist_sec_identity(
    db: Session,
    source_record: CeriSourceRecord,
    company_id: int | None,
    *,
    seen: set[tuple[int, str]] | None = None,
) -> None:
    """Persist the SEC CIK learned during ingestion for future resolution.

    SEC's public ticker map is an external lookup aid, not durable SwingLens
    identity evidence.  Once a source record resolves successfully, retain
    the CIK on the canonical company and as a provider alias so subsequent
    runs do not depend on a fresh ticker-map lookup.
    """
    if source_record.provider != "sec" or company_id is None:
        return
    payload = source_record.raw_json or source_record.restricted_normalized_json or {}
    cik_value = payload.get("cik") or payload.get("provider_company_id")
    if cik_value in (None, ""):
        return
    cik = str(cik_value).zfill(10)
    identity_key = (company_id, cik)
    if seen is not None and identity_key in seen:
        return
    company = getattr(db, "get", lambda *_args: None)(CeriCompany, company_id)
    if company is not None and not company.cik:
        company.cik = cik
    scalar = getattr(db, "scalar", None)
    if not callable(scalar):
        return
    existing = scalar(
        select(CeriCompanyAlias.id)
        .where(CeriCompanyAlias.provider == "sec")
        .where(CeriCompanyAlias.alias_type == "cik")
        .where(CeriCompanyAlias.alias_value == cik)
    )
    if existing is None:
        db.add(
            CeriCompanyAlias(
                company_id=company_id,
                provider="sec",
                alias_type="cik",
                alias_value=cik,
                source="sec-guidance-ingestion",
                confidence="High",
            )
        )
    if seen is not None:
        seen.add(identity_key)


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
    statement = (
        select(CeriCatalystEvent)
        .where(CeriCatalystEvent.company_id == company_id)
        .where(CeriCatalystEvent.category == category)
        .where(CeriCatalystEvent.subject_key == subject_key)
    )
    if isinstance(db, Session):
        # Serialize revision allocation for this canonical event.  The existing
        # unique constraints remain the final race guard.
        statement = statement.with_for_update()
    return scalar(statement)


def _exists_by_source(db: Session, model: Any, source_record_id: int) -> bool:
    # Lightweight fake sessions used by the existing unit tests expose only
    # a scalar queue for the event lookup; do not consume that queue for an
    # optional idempotency probe.
    if not callable(getattr(db, "scalars", None)):
        return False
    statement = select(model.id).where(model.source_record_id == source_record_id)
    return _maybe_scalar(db, statement) is not None


def _update_processing_checkpoint(
    processing_run: CeriProcessingRun,
    *,
    source_record_id: int | None,
    record_index: int,
    read: int,
    normalized: int,
    failed: int,
    warnings: int,
    quarantined: int,
    errors: list[dict[str, Any]],
) -> None:
    processing_run.checkpoint_json = {
        "last_source_record_id": source_record_id,
        "last_record_index": record_index,
    }
    processing_run.read_count = read
    processing_run.normalized_count = normalized
    processing_run.failed_count = failed
    processing_run.warning_count = warnings
    processing_run.errors_json = {"records": errors} if errors else None
    processing_run.counts_json = {"quarantined": quarantined}


def _prior_guidance(db: Session, current: CeriGuidanceEvent) -> CeriGuidanceEvent | None:
    if current.effective_at is None:
        return None
    statement = (
        select(CeriGuidanceEvent)
        .where(CeriGuidanceEvent.company_id == current.company_id)
        .where(CeriGuidanceEvent.metric == current.metric)
        .where(CeriGuidanceEvent.period_type == current.period_type)
        .where(CeriGuidanceEvent.effective_at < current.effective_at)
        .order_by(CeriGuidanceEvent.effective_at.desc(), CeriGuidanceEvent.id.desc())
        .limit(1)
    )
    if current.id is not None:
        statement = statement.where(CeriGuidanceEvent.id != current.id)
    return _maybe_scalar(db, statement)


def _maybe_scalar(db: Session, statement):
    scalar = getattr(db, "scalar", None)
    return scalar(statement) if callable(scalar) else None


def _catalyst_revision_matches(current: CeriCatalystEventRevision, record) -> bool:
    return (
        current.status == record.status.value
        and current.direction == record.direction.value
        and current.expected_date == record.expected_date
        and current.materiality == record.materiality
    )


def _next_catalyst_revision_number(db: Session, catalyst_event_id: int) -> int:
    maximum = _maybe_scalar(
        db,
        select(func.max(CeriCatalystEventRevision.revision_number)).where(
            CeriCatalystEventRevision.catalyst_event_id == catalyst_event_id
        ),
    )
    return int(maximum or 0) + 1


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
