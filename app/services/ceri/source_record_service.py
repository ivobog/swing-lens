from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriIngestionRun, CeriSourceRecord
from app.services.ceri.dtos import RawProviderRecord
from app.services.ceri.observability import ceri_log_event, ceri_metrics


@dataclass(frozen=True)
class SourceRecordWriteResult:
    source_record: CeriSourceRecord
    inserted: bool
    deduplicated: bool
    corrected: bool
    quarantined: bool


class CeriSourceRecordService:
    def create_ingestion_run(
        self,
        db: Session,
        *,
        provider: str,
        provider_terms_version: str | None,
        dataset: str,
        scope: dict[str, Any],
        request_key: str,
        config_version: str | None,
        config_hash: str | None,
    ) -> CeriIngestionRun:
        existing = _maybe_scalar(
            db,
            select(CeriIngestionRun).where(CeriIngestionRun.request_key == request_key),
        )
        if existing is not None:
            return existing

        run = CeriIngestionRun(
            provider=provider,
            provider_terms_version=provider_terms_version,
            dataset=dataset,
            scope_json=scope,
            status="RUNNING",
            request_key=request_key,
            config_version=config_version,
            config_hash=config_hash,
            started_at=_utcnow(),
        )
        db.add(run)
        db.flush()
        ceri_metrics.increment(
            "ceri_ingestion_started_total",
            provider=provider,
            dataset=dataset,
        )
        ceri_log_event(
            "ingestion_started",
            ingestion_run_id=run.id,
            provider=provider,
            dataset=dataset,
            request_key=request_key,
            config_hash=config_hash,
        )
        return run

    def store_source_record(
        self,
        db: Session,
        *,
        ingestion_run_id: int | None,
        record: RawProviderRecord,
        raw_payload_allowed: bool,
    ) -> SourceRecordWriteResult:
        content_hash = source_record_content_hash(record.payload)
        idempotency_key = source_record_idempotency_key(
            record.provider,
            record.dataset.value,
            record.provider_record_id,
            content_hash,
        )
        existing = _maybe_scalar(
            db,
            select(CeriSourceRecord).where(CeriSourceRecord.idempotency_key == idempotency_key),
        )
        if existing is not None:
            ceri_metrics.increment(
                "ceri_ingestion_deduplicated_total",
                provider=record.provider,
                dataset=record.dataset.value,
            )
            ceri_log_event(
                "source_record_deduplicated",
                ingestion_run_id=ingestion_run_id,
                provider=record.provider,
                dataset=record.dataset.value,
                source_record_id=existing.id,
            )
            return SourceRecordWriteResult(
                source_record=existing,
                inserted=False,
                deduplicated=True,
                corrected=False,
                quarantined=bool(existing.quarantine_reason),
            )

        prior_provider_record = _maybe_scalar(
            db,
            select(CeriSourceRecord)
            .where(CeriSourceRecord.provider == record.provider)
            .where(CeriSourceRecord.dataset == record.dataset.value)
            .where(CeriSourceRecord.provider_record_id == record.provider_record_id)
            .order_by(CeriSourceRecord.ingested_at.desc(), CeriSourceRecord.id.desc())
            .limit(1),
        )
        if prior_provider_record is not None and prior_provider_record.content_hash == content_hash:
            ceri_metrics.increment(
                "ceri_ingestion_deduplicated_total",
                provider=record.provider,
                dataset=record.dataset.value,
            )
            ceri_log_event(
                "source_record_deduplicated",
                ingestion_run_id=ingestion_run_id,
                provider=record.provider,
                dataset=record.dataset.value,
                source_record_id=prior_provider_record.id,
            )
            return SourceRecordWriteResult(
                source_record=prior_provider_record,
                inserted=False,
                deduplicated=True,
                corrected=False,
                quarantined=bool(prior_provider_record.quarantine_reason),
            )

        quarantine_reason = _quarantine_reason(record)
        source = CeriSourceRecord(
            ingestion_run_id=ingestion_run_id,
            provider=record.provider,
            provider_terms_version=_optional_payload_text(record, "provider_terms_version"),
            dataset=record.dataset.value,
            provider_record_id=record.provider_record_id,
            company_hint_json=_company_hint(record.payload),
            published_at=record.published_at,
            observed_at=record.observed_at,
            source_url=record.source_url,
            source_reference=_optional_payload_text(record, "source_reference"),
            raw_json=record.payload if raw_payload_allowed and not quarantine_reason else None,
            restricted_normalized_json=record.payload
            if (not raw_payload_allowed or quarantine_reason)
            else None,
            content_hash=content_hash,
            idempotency_key=idempotency_key,
            export_policy=record.export_policy,
            supersedes_id=getattr(prior_provider_record, "id", None),
            correction_type="CORRECTION" if prior_provider_record is not None else None,
            quarantine_reason=quarantine_reason,
        )
        db.add(source)
        db.flush()
        if quarantine_reason:
            ceri_metrics.increment(
                "ceri_ingestion_quarantined_total",
                provider=record.provider,
                dataset=record.dataset.value,
            )
            ceri_log_event(
                "source_record_quarantined",
                ingestion_run_id=ingestion_run_id,
                provider=record.provider,
                dataset=record.dataset.value,
                source_record_id=source.id,
                quarantine_reason=quarantine_reason,
            )
        else:
            metric = (
                "ceri_ingestion_corrected_total"
                if prior_provider_record is not None
                else "ceri_ingestion_inserted_total"
            )
            ceri_metrics.increment(metric, provider=record.provider, dataset=record.dataset.value)
            ceri_log_event(
                (
                    "source_record_corrected"
                    if prior_provider_record is not None
                    else "source_record_inserted"
                ),
                ingestion_run_id=ingestion_run_id,
                provider=record.provider,
                dataset=record.dataset.value,
                source_record_id=source.id,
                supersedes_id=getattr(prior_provider_record, "id", None),
            )
        return SourceRecordWriteResult(
            source_record=source,
            inserted=True,
            deduplicated=False,
            corrected=prior_provider_record is not None and quarantine_reason is None,
            quarantined=bool(quarantine_reason),
        )

    def finish_ingestion_run(
        self,
        db: Session,
        run: CeriIngestionRun,
        *,
        status: str,
        requested_count: int,
        fetched_count: int,
        inserted_count: int,
        deduplicated_count: int,
        corrected_count: int,
        quarantined_count: int,
        failed_count: int,
        warning_count: int,
        quota_state: dict[str, Any] | None = None,
        checkpoint: dict[str, Any] | None = None,
        errors: dict[str, Any] | None = None,
        warnings: dict[str, Any] | None = None,
    ) -> CeriIngestionRun:
        now = _utcnow()
        run.status = status
        run.requested_count = requested_count
        run.fetched_count = fetched_count
        run.inserted_count = inserted_count
        run.deduplicated_count = deduplicated_count
        run.corrected_count = corrected_count
        run.quarantined_count = quarantined_count
        run.failed_count = failed_count
        run.warning_count = warning_count
        run.quota_state_json = quota_state
        run.checkpoint_json = checkpoint
        run.errors_json = errors
        run.warnings_json = warnings
        run.completed_at = now
        if run.started_at is not None:
            run.duration_ms = int((now - run.started_at).total_seconds() * 1000)
        db.flush()
        ceri_metrics.increment(
            "ceri_ingestion_completed_total",
            provider=run.provider,
            dataset=run.dataset,
            status=status,
        )
        if run.duration_ms is not None:
            ceri_metrics.observe(
                "ceri_ingestion_duration_ms",
                float(run.duration_ms),
                provider=run.provider,
                dataset=run.dataset,
                status=status,
            )
        ceri_log_event(
            "ingestion_completed",
            ingestion_run_id=run.id,
            provider=run.provider,
            dataset=run.dataset,
            request_key=run.request_key,
            config_hash=run.config_hash,
            status=status,
            counts={
                "requested": requested_count,
                "fetched": fetched_count,
                "inserted": inserted_count,
                "deduplicated": deduplicated_count,
                "corrected": corrected_count,
                "quarantined": quarantined_count,
                "failed": failed_count,
            },
        )
        return run


def source_record_content_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def source_record_idempotency_key(
    provider: str,
    dataset: str,
    provider_record_id: str,
    content_hash: str,
) -> str:
    return f"{provider}:{dataset}:{provider_record_id}:{content_hash}"


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _quarantine_reason(record: RawProviderRecord) -> str | None:
    value = record.payload.get("_ceri_quarantine_reason")
    if value:
        return str(value)
    if not record.provider_record_id:
        return "missing_provider_record_id"
    return None


def _company_hint(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in ("ticker", "exchange", "provider_company_id", "cik", "company_name")
        if payload.get(key) not in (None, "")
    }


def _optional_payload_text(record: RawProviderRecord, key: str) -> str | None:
    value = record.payload.get(key)
    if value in (None, ""):
        return None
    return str(value)


def _maybe_scalar(db: Session, statement):
    scalar = getattr(db, "scalar", None)
    if callable(scalar):
        return scalar(statement)
    return None


def _utcnow() -> datetime:
    return datetime.now(UTC)
