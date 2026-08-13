from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CeriCompany(Base):
    __tablename__ = "ceri_companies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(32))
    company_name: Mapped[str | None] = mapped_column(Text)
    cik: Mapped[str | None] = mapped_column(String(32))
    current_provider_ids_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("ticker", "exchange", name="uq_ceri_companies_ticker_exchange"),
        Index("ix_ceri_companies_cik", "cik"),
    )


class CeriCompanyAlias(Base):
    __tablename__ = "ceri_company_aliases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    alias_type: Mapped[str] = mapped_column(String(64), nullable=False)
    alias_value: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(32))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="Normal")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "alias_type",
            "alias_value",
            "valid_from",
            "valid_to",
            name="uq_ceri_company_alias_validity",
        ),
        Index("ix_ceri_company_aliases_company", "company_id"),
        Index("ix_ceri_company_aliases_lookup", "provider", "alias_type", "alias_value"),
    )


class CeriIngestionRun(Base):
    __tablename__ = "ceri_ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_terms_version: Mapped[str | None] = mapped_column(Text)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    request_key: Mapped[str] = mapped_column(Text, nullable=False)
    config_version: Mapped[str | None] = mapped_column(Text)
    config_hash: Mapped[str | None] = mapped_column(Text)
    quota_state_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checkpoint_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deduplicated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    corrected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantined_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("request_key", name="uq_ceri_ingestion_runs_request_key"),
        Index("ix_ceri_ingestion_runs_provider_dataset_status", "provider", "dataset", "status"),
    )


class CeriProcessingRun(Base):
    __tablename__ = "ceri_processing_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    deterministic_request_key: Mapped[str] = mapped_column(Text, nullable=False)
    scope_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    config_version: Mapped[str | None] = mapped_column(Text)
    config_hash: Mapped[str | None] = mapped_column(Text)
    cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    read_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    normalized_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feature_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_snapshot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    change_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alert_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    counts_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    checkpoint_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    errors_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_token: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "deterministic_request_key",
            name="uq_ceri_processing_runs_request_key",
        ),
        Index("ix_ceri_processing_runs_status_checkpoint", "status", "job_type"),
        Index("ix_ceri_processing_runs_heartbeat", "heartbeat_at"),
    )


class CeriSourceRecord(Base):
    __tablename__ = "ceri_source_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_ingestion_runs.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_terms_version: Mapped[str | None] = mapped_column(Text)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_record_id: Mapped[str] = mapped_column(Text, nullable=False)
    company_hint_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    restricted_normalized_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    payload_remediation_version: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_hash: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    export_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="exportable")
    provider_retention_deadline: Mapped[date | None] = mapped_column(Date)
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_source_records.id", ondelete="SET NULL")
    )
    correction_type: Mapped[str | None] = mapped_column(String(64))
    quarantine_reason: Mapped[str | None] = mapped_column(Text)
    license_scope: Mapped[str | None] = mapped_column(Text)
    redistribution_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purge_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "dataset",
            "provider_record_id",
            "content_hash",
            name="uq_ceri_source_records_provider_record",
        ),
        UniqueConstraint("idempotency_key", name="uq_ceri_source_records_idempotency"),
        Index("ix_ceri_source_records_content_hash", "content_hash"),
        Index("ix_ceri_source_records_dataset_published", "dataset", "published_at"),
        Index("ix_ceri_source_records_quarantine", "quarantine_reason"),
        Index("ix_ceri_source_records_provider_retrieved", "provider", "retrieved_at"),
        Index(
            "ix_ceri_source_records_provider_source_timestamp",
            "provider",
            "source_timestamp",
        ),
    )


class CeriEstimateSnapshot(Base):
    __tablename__ = "ceri_estimate_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_record_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_source_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_period_slot: Mapped[str | None] = mapped_column(String(64))
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    fiscal_quarter: Mapped[int | None] = mapped_column(Integer)
    consensus: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    high: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    low: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    analyst_count: Mapped[int | None] = mapped_column(Integer)
    upward_count: Mapped[int | None] = mapped_column(Integer)
    downward_count: Mapped[int | None] = mapped_column(Integer)
    source_currency: Mapped[str | None] = mapped_column(String(16))
    source_scale: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    canonical_currency: Mapped[str | None] = mapped_column(String(16))
    currency_basis: Mapped[str | None] = mapped_column(Text)
    currency_verified: Mapped[bool | None] = mapped_column(Boolean)
    canonical_scale: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    conversion_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    conversion_source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_source_records.id", ondelete="SET NULL")
    )
    conversion_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    known_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_session: Mapped[date | None] = mapped_column(Date)
    provider_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trend_baseline_window_days: Mapped[int | None] = mapped_column(Integer)
    baseline_origin: Mapped[str | None] = mapped_column(String(64))
    current_observation_reference: Mapped[str | None] = mapped_column(Text)
    canonical_observation_key: Mapped[str] = mapped_column(Text, nullable=False)
    original_fields_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    quality_flags_json: Mapped[list[str] | None] = mapped_column(JSONB)
    normalization_version: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "source_record_id",
            name="uq_ceri_estimate_snapshots_source_record",
        ),
        Index(
            "ix_ceri_estimate_snapshots_canonical_observation",
            "company_id",
            "metric",
            "period_type",
            "fiscal_period_end",
            "canonical_observation_key",
        ),
        Index(
            "ix_ceri_estimate_snapshots_company_metric_effective",
            "company_id",
            "metric",
            "effective_at",
        ),
        Index("ix_ceri_estimate_snapshots_effective_session", "effective_session"),
        Index(
            "ix_ceri_estimates_trend_baseline_reference",
            "current_observation_reference",
            "trend_baseline_window_days",
        ),
        CheckConstraint(
            "analyst_count IS NULL OR analyst_count >= 0",
            name="ck_ceri_estimates_analysts_nonnegative",
        ),
    )


class CeriEarningsActual(Base):
    __tablename__ = "ceri_earnings_actuals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_record_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_source_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    period_type: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    report_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report_session: Mapped[date | None] = mapped_column(Date)
    actual_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    provider_consensus_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    provider_surprise_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    consensus_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_estimate_snapshots.id", ondelete="SET NULL")
    )
    consensus_selection_reason: Mapped[str | None] = mapped_column(Text)
    surprise_absolute: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    surprise_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    quality_warnings_json: Mapped[list[str] | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint(
            "source_record_id",
            "metric",
            "period_type",
            "fiscal_period_end",
            name="uq_ceri_earnings_actuals_source_metric_period",
        ),
        Index("ix_ceri_earnings_actuals_company_report", "company_id", "report_session"),
    )


class CeriGuidanceEvent(Base):
    __tablename__ = "ceri_guidance_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_record_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_source_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    metric: Mapped[str | None] = mapped_column(String(64))
    period_type: Mapped[str | None] = mapped_column(String(64))
    period_label: Mapped[str | None] = mapped_column(Text)
    low_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    high_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    point_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    unit: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(16))
    comparison_basis: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="Normal")
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_session: Mapped[date | None] = mapped_column(Date)
    evidence_locator: Mapped[str | None] = mapped_column(Text)
    filing_accession: Mapped[str | None] = mapped_column(String(64))
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_guidance_events.id", ondelete="SET NULL")
    )
    quality_warnings_json: Mapped[list[str] | None] = mapped_column(JSONB)
    accepted_for_scoring: Mapped[bool | None] = mapped_column(Boolean)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    normalization_version: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_ceri_guidance_events_company_effective", "company_id", "effective_session"),
        Index("ix_ceri_guidance_events_action", "action"),
        Index(
            "ix_ceri_guidance_events_source_accession",
            "source_record_id",
            "filing_accession",
        ),
    )


class CeriSecFilingDocument(Base):
    __tablename__ = "ceri_sec_filing_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String(32), nullable=False)
    accession_number: Mapped[str] = mapped_column(String(64), nullable=False)
    document_name: Mapped[str] = mapped_column(Text, nullable=False)
    ticker_hint: Mapped[str | None] = mapped_column(String(32))
    form: Mapped[str | None] = mapped_column(String(32))
    filing_date: Mapped[date | None] = mapped_column(Date)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_content_hash: Mapped[str | None] = mapped_column(String(128))
    last_content_bytes: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        UniqueConstraint(
            "cik",
            "accession_number",
            "document_name",
            name="uq_ceri_sec_filing_document_identity",
        ),
        Index("ix_ceri_sec_filing_documents_cik_date", "cik", "filing_date"),
        Index("ix_ceri_sec_filing_documents_accession", "accession_number"),
    )


class CeriSecDocumentExtraction(Base):
    __tablename__ = "ceri_sec_document_extractions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_sec_filing_documents.id", ondelete="CASCADE"), nullable=False
    )
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    processor_signature: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    record_count: Mapped[int | None] = mapped_column(Integer)
    worker_id: Mapped[str | None] = mapped_column(Text)
    execution_token: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "dataset",
            "processor_signature",
            name="uq_ceri_sec_document_extraction_identity",
        ),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','COMPLETED_WITH_RECORDS',"
            "'COMPLETED_NO_RECORDS','FAILED_RETRYABLE','FAILED_PERMANENT','CANCELLED')",
            name="ck_ceri_sec_document_extractions_status",
        ),
        Index(
            "ix_ceri_sec_document_extractions_claim",
            "dataset",
            "processor_signature",
            "status",
            "lease_expires_at",
        ),
        Index("ix_ceri_sec_document_extractions_document", "document_id"),
    )


class CeriSecSyncState(Base):
    """Certification boundary preventing an ACTIVE cold sync from becoming a backfill."""

    __tablename__ = "ceri_sec_sync_states"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    processor_signature: Mapped[str] = mapped_column(Text, nullable=False)
    bootstrap_completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latest_filing_date: Mapped[date | None] = mapped_column(Date)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("cik", "dataset", "processor_signature", name="uq_ceri_sec_sync_state"),
        Index("ix_ceri_sec_sync_states_cik_dataset", "cik", "dataset"),
    )


class CeriCatalystEvent(Base):
    __tablename__ = "ceri_catalyst_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    subtype: Mapped[str | None] = mapped_column(String(128))
    subject_key: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_text: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "category",
            "subject_key",
            name="uq_ceri_catalyst_events_cluster",
        ),
        Index("ix_ceri_catalyst_events_company_category", "company_id", "category"),
    )


class CeriCatalystEventRevision(Base):
    __tablename__ = "ceri_catalyst_event_revisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    catalyst_event_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_catalyst_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_source_records.id", ondelete="SET NULL")
    )
    prior_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_catalyst_event_revisions.id", ondelete="SET NULL")
    )
    outcome_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_catalyst_event_revisions.id", ondelete="SET NULL")
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_date: Mapped[date | None] = mapped_column(Date)
    effective_session: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(64), nullable=False)
    materiality: Mapped[float | None] = mapped_column(Float)
    date_confidence: Mapped[str | None] = mapped_column(String(64))
    source_confidence: Mapped[str | None] = mapped_column(String(64))
    operational_values_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    conflict_flags_json: Mapped[list[str] | None] = mapped_column(JSONB)
    review_state: Mapped[str | None] = mapped_column(String(64))
    issuer_relevance: Mapped[bool | None] = mapped_column(Boolean)
    relevance_reason: Mapped[str | None] = mapped_column(Text)
    binary_eligible: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "catalyst_event_id",
            "revision_number",
            name="uq_ceri_catalyst_event_revision_number",
        ),
        Index(
            "uq_ceri_catalyst_event_current_revision",
            "catalyst_event_id",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
        Index("ix_ceri_catalyst_event_revisions_status", "status"),
    )


class CeriCatalystSource(Base):
    __tablename__ = "ceri_catalyst_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    catalyst_event_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_catalyst_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    catalyst_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_catalyst_event_revisions.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_source_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_fields_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "catalyst_event_id",
            "source_record_id",
            name="uq_ceri_catalyst_sources_event_source",
        ),
        Index("ix_ceri_catalyst_sources_source", "source_record_id"),
    )


class CeriRevisionFeature(Base):
    __tablename__ = "ceri_revision_features"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    period_key: Mapped[str] = mapped_column(Text, nullable=False)
    period_slot: Mapped[str | None] = mapped_column(String(64))
    as_of_session: Mapped[date] = mapped_column(Date, nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_estimate_snapshots.id", ondelete="SET NULL")
    )
    current_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_estimate_snapshots.id", ondelete="SET NULL")
    )
    actual_elapsed_days: Mapped[int | None] = mapped_column(Integer)
    absolute_change: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    pct_change_unit: Mapped[str | None] = mapped_column(String(32))
    upward_count: Mapped[int | None] = mapped_column(Integer)
    downward_count: Mapped[int | None] = mapped_column(Integer)
    net_breadth: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    dispersion: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    acceleration: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    acceleration_unit: Mapped[str | None] = mapped_column(String(64))
    baseline_origin: Mapped[str | None] = mapped_column(String(64))
    revision_confidence_score: Mapped[float | None] = mapped_column(Float)
    revision_confidence_label: Mapped[str | None] = mapped_column(String(32))
    warnings_json: Mapped[list[str] | None] = mapped_column(JSONB)
    source_observation_ids_json: Mapped[list[int] | None] = mapped_column(JSONB)
    provider_selection_reason: Mapped[str | None] = mapped_column(Text)
    unavailable_reason: Mapped[str | None] = mapped_column(Text)
    evidence_hash: Mapped[str | None] = mapped_column(Text)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_version: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "company_id", "metric", "period_key", "as_of_session", "window_days",
            "config_hash", "calculation_version", name="uq_ceri_revision_features_identity",
        ),
        Index("ix_ceri_revision_features_company_session", "company_id", "as_of_session"),
    )


class CeriPriceResponseFeature(Base):
    __tablename__ = "ceri_price_response_features"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_companies.id", ondelete="RESTRICT"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[int | None] = mapped_column(BigInteger)
    event_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_effective_session: Mapped[date | None] = mapped_column(Date)
    reaction_session: Mapped[date | None] = mapped_column(Date)
    benchmark: Mapped[str | None] = mapped_column(String(32))
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reasons_json: Mapped[list[str] | None] = mapped_column(JSONB)
    warnings_json: Mapped[list[str] | None] = mapped_column(JSONB)
    price_bar_ids_json: Mapped[list[int] | None] = mapped_column(JSONB)
    evidence_hash: Mapped[str] = mapped_column(Text, nullable=False)
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("event_key", name="uq_ceri_price_response_event_key"),
        Index("ix_ceri_price_response_company_session", "company_id", "reaction_session"),
    )


class CeriDerivedFeature(Base):
    __tablename__ = "ceri_derived_features"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_companies.id", ondelete="RESTRICT"), nullable=False
    )
    feature_family: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_key: Mapped[str] = mapped_column(Text, nullable=False)
    as_of_session: Mapped[date] = mapped_column(Date, nullable=False)
    value_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_ids_json: Mapped[list[int] | None] = mapped_column(JSONB)
    evidence_hash: Mapped[str] = mapped_column(Text, nullable=False)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id", "feature_family", "feature_key", "as_of_session",
            "config_hash", "calculation_version", name="uq_ceri_derived_features_identity"
        ),
        Index("ix_ceri_derived_features_company_session", "company_id", "as_of_session"),
    )


class CeriScoreSnapshot(Base):
    __tablename__ = "ceri_score_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("upload_runs.id", ondelete="SET NULL"))
    source_run_id_text: Mapped[str | None] = mapped_column(Text)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of_session: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opportunity_score: Mapped[float | None] = mapped_column(Float)
    opportunity_coverage_pct: Mapped[float | None] = mapped_column(Float)
    opportunity_unrated_reason: Mapped[str | None] = mapped_column(Text)
    event_risk_score: Mapped[float | None] = mapped_column(Float)
    data_confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    coverage_pct: Mapped[float] = mapped_column(Float, nullable=False)
    posture: Mapped[str] = mapped_column(String(64), nullable=False)
    earnings_proximity_risk: Mapped[float | None] = mapped_column(Float)
    alignment_flags_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    alignment_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence_lineage_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    top_positive_contributors_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    top_negative_contributors_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    component_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    opportunity_ledger_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    confidence_ledger_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    event_risk_ledger_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reasons_json: Mapped[list[str] | None] = mapped_column(JSONB)
    warnings_json: Mapped[list[str] | None] = mapped_column(JSONB)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_version: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(Text, nullable=False)
    hash_schema_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "company_id",
            "config_hash",
            "calculation_version",
            name="uq_ceri_score_snapshots_run_company_version",
        ),
        Index("ix_ceri_score_snapshots_run_ticker", "run_id", "ticker"),
        Index("ix_ceri_score_snapshots_scores", "opportunity_score", "event_risk_score"),
        Index("ix_ceri_score_snapshots_confidence", "data_confidence"),
    )


class CeriChangeEvent(Base):
    __tablename__ = "ceri_change_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("ceri_companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_score_snapshots.id", ondelete="SET NULL")
    )
    to_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_score_snapshots.id", ondelete="SET NULL")
    )
    catalyst_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_catalyst_event_revisions.id", ondelete="SET NULL")
    )
    change_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    delta_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_ceri_change_events_dedup_key"),
        Index("ix_ceri_change_events_company_created", "company_id", "created_at"),
        Index("ix_ceri_change_events_type", "change_type"),
    )


class CeriManualReview(Base):
    __tablename__ = "ceri_manual_reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prior_value_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reviewer: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    prior_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_manual_reviews.id", ondelete="SET NULL")
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "uq_ceri_manual_reviews_active_target",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
        Index("ix_ceri_manual_reviews_target", "target_type", "target_id"),
    )


class CeriAlertRule(Base):
    __tablename__ = "ceri_alert_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    thresholds_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    scope_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cooldown_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_types_json: Mapped[list[str] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (UniqueConstraint("rule_id", name="uq_ceri_alert_rules_rule_id"),)


class CeriAlertEvent(Base):
    __tablename__ = "ceri_alert_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_alert_rules.id", ondelete="SET NULL")
    )
    source_change_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_change_events.id", ondelete="SET NULL")
    )
    source_catalyst_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("ceri_catalyst_event_revisions.id", ondelete="SET NULL")
    )
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNREAD")
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("event_key", name="uq_ceri_alert_events_event_key"),
        Index("ix_ceri_alert_events_status_created", "status", "created_at"),
        Index("ix_ceri_alert_events_ticker", "ticker"),
    )


class CeriPurgeAudit(Base):
    __tablename__ = "ceri_purge_audits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    license_scope: Mapped[str] = mapped_column(Text, nullable=False)
    preview_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation_token_hash: Mapped[str | None] = mapped_column(Text)
    affected_counts_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    invalidated_derivatives_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PREVIEWED")
    previewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "license_scope",
            "preview_manifest_hash",
            name="uq_ceri_purge_audits_preview_scope",
        ),
        Index("ix_ceri_purge_audits_status", "status"),
    )


class CeriProviderRequestTelemetry(Base):
    __tablename__ = "ceri_provider_request_telemetry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str | None] = mapped_column(String(64))
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    request_key: Mapped[str | None] = mapped_column(Text)
    scope_hash: Mapped[str | None] = mapped_column(String(128))
    status_code: Mapped[int | None] = mapped_column(Integer)
    call_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_ceri_provider_telemetry_provider_observed", "provider", "observed_at"),
        Index("ix_ceri_provider_telemetry_endpoint_observed", "endpoint", "observed_at"),
    )


CERI_TABLES = (
    CeriCompany.__table__,
    CeriCompanyAlias.__table__,
    CeriIngestionRun.__table__,
    CeriProcessingRun.__table__,
    CeriSourceRecord.__table__,
    CeriEstimateSnapshot.__table__,
    CeriEarningsActual.__table__,
    CeriGuidanceEvent.__table__,
    CeriCatalystEvent.__table__,
    CeriCatalystEventRevision.__table__,
    CeriCatalystSource.__table__,
    CeriRevisionFeature.__table__,
    CeriPriceResponseFeature.__table__,
    CeriDerivedFeature.__table__,
    CeriScoreSnapshot.__table__,
    CeriChangeEvent.__table__,
    CeriManualReview.__table__,
    CeriAlertRule.__table__,
    CeriAlertEvent.__table__,
    CeriPurgeAudit.__table__,
)
