"""Persistence models for the read-only IBKR Market Intelligence extension."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class IBIntelligenceRun(Base):
    __tablename__ = "ib_intelligence_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    background_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="SET NULL")
    )
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    module: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    deterministic_request_key: Mapped[str] = mapped_column(Text, nullable=False)
    scope_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    counts_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    warning_flags_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_ib_intelligence_runs_module_started", "module", "started_at"),
        Index("ix_ib_intelligence_runs_request_key", "deterministic_request_key"),
    )


class IBIntelligenceRequestItem(Base):
    __tablename__ = "ib_intelligence_request_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    intelligence_run_id: Mapped[int] = mapped_column(
        ForeignKey("ib_intelligence_runs.id", ondelete="CASCADE"), nullable=False
    )
    deterministic_request_key: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str | None] = mapped_column(Text)
    ib_conid: Mapped[int | None] = mapped_column(BigInteger)
    request_family: Mapped[str] = mapped_column(Text, nullable=False)
    request_type: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    availability_status: Mapped[str] = mapped_column(Text, nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_counts_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "intelligence_run_id",
            "deterministic_request_key",
            name="uq_ib_intelligence_request_item_key",
        ),
        Index("ix_ib_intelligence_request_item_status", "status", "priority", "started_at"),
    )


class IBHistoricalMetricBar(Base):
    __tablename__ = "ib_historical_metric_bars"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    intelligence_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ib_intelligence_runs.id", ondelete="SET NULL")
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    ib_conid: Mapped[int | None] = mapped_column(BigInteger)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_session: Mapped[date] = mapped_column(Date, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    metric_type: Mapped[str] = mapped_column(Text, nullable=False)
    open_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    high_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    low_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    close_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    source: Mapped[str] = mapped_column(Text, nullable=False, default="IBKR")
    source_semantic_type: Mapped[str] = mapped_column(Text, nullable=False)
    requested_range: Mapped[str | None] = mapped_column(Text)
    availability_status: Mapped[str] = mapped_column(Text, nullable=False)
    capability_reason: Mapped[str | None] = mapped_column(Text)
    data_hash: Mapped[str] = mapped_column(Text, nullable=False)
    revision_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    warning_flags_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "session_date",
            "timeframe",
            "metric_type",
            name="uq_ib_historical_metric_bar_identity",
        ),
        Index("ix_ib_historical_metric_ticker_type_date", "ticker", "metric_type", "session_date"),
    )


class IBHistoricalMetricRevision(Base):
    __tablename__ = "ib_historical_metric_revisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    metric_bar_id: Mapped[int] = mapped_column(
        ForeignKey("ib_historical_metric_bars.id", ondelete="CASCADE"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_data_hash: Mapped[str] = mapped_column(Text, nullable=False)
    new_data_hash: Mapped[str] = mapped_column(Text, nullable=False)
    previous_values_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    new_values_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("metric_bar_id", "revision_number", name="uq_ib_metric_revision"),
    )


class IBMarketIntelligenceSnapshot(Base):
    __tablename__ = "ib_market_intelligence_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    intelligence_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ib_intelligence_runs.id", ondelete="SET NULL")
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    ib_conid: Mapped[int | None] = mapped_column(BigInteger)
    effective_session: Mapped[date] = mapped_column(Date, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_type: Mapped[str] = mapped_column(Text, nullable=False)
    values_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    availability_status: Mapped[str] = mapped_column(Text, nullable=False)
    capability_reason: Mapped[str | None] = mapped_column(Text)
    evidence_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_request_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    warning_flags_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        UniqueConstraint("evidence_hash", name="uq_ib_market_snapshot_evidence_hash"),
        Index("ix_ib_market_snapshot_latest", "ticker", "snapshot_type", "observed_at"),
    )


class IBIntelligenceFeature(Base):
    __tablename__ = "ib_intelligence_features"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    intelligence_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ib_intelligence_runs.id", ondelete="SET NULL")
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    ib_conid: Mapped[int | None] = mapped_column(BigInteger)
    as_of_session: Mapped[date] = mapped_column(Date, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    module: Mapped[str] = mapped_column(Text, nullable=False)
    classification: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    freshness_status: Mapped[str] = mapped_column(Text, nullable=False)
    coverage_status: Mapped[str] = mapped_column(Text, nullable=False)
    components_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    warnings_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_evidence_hashes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_version: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    input_signature: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "as_of_session",
            "module",
            "calculation_version",
            "config_hash",
            "input_signature",
            name="uq_ib_intelligence_feature_version",
        ),
        Index("ix_ib_intelligence_feature_latest", "ticker", "module", "as_of_session"),
    )


class IBScannerParameterCache(Base):
    __tablename__ = "ib_scanner_parameter_cache"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    xml_payload: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IBScannerRun(Base):
    __tablename__ = "ib_scanner_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    intelligence_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ib_intelligence_runs.id", ondelete="SET NULL")
    )
    scanner_name: Mapped[str] = mapped_column(Text, nullable=False)
    scanner_version: Mapped[str] = mapped_column(Text, nullable=False)
    instrument: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    scan_code: Mapped[str] = mapped_column(Text, nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False)
    filters_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_ib_scanner_runs_started", "started_at"),)


class IBScannerCandidate(Base):
    __tablename__ = "ib_scanner_candidates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scanner_run_id: Mapped[int] = mapped_column(
        ForeignKey("ib_scanner_runs.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    ib_conid: Mapped[int | None] = mapped_column(BigInteger)
    contract_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    scanner_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    universe_source: Mapped[str] = mapped_column(Text, nullable=False, default="IBKR_SCANNER")
    enrichment_status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    promoted_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("scanner_run_id", "ib_conid", name="uq_ib_scanner_candidate_conid"),
        Index("ix_ib_scanner_candidates_ticker", "ticker"),
    )


class IBHistogramSnapshot(Base):
    __tablename__ = "ib_histogram_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    intelligence_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ib_intelligence_runs.id", ondelete="SET NULL")
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    ib_conid: Mapped[int | None] = mapped_column(BigInteger)
    requested_period: Mapped[str] = mapped_column(Text, nullable=False)
    use_rth: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    availability_status: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source_semantics: Mapped[str] = mapped_column(
        Text, nullable=False, default="IBKR_HISTOGRAM_PRICE_LEVEL_ACTIVITY"
    )
    warnings_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (Index("ix_ib_histogram_latest", "ticker", "observed_at"),)


class IBHistogramBin(Base):
    __tablename__ = "ib_histogram_bins"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    histogram_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("ib_histogram_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    activity_count: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    activity_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    density_percentile: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)

    __table_args__ = (
        UniqueConstraint("histogram_snapshot_id", "price", name="uq_ib_histogram_bin_price"),
    )


class IBFlexImportRun(Base):
    __tablename__ = "ib_flex_import_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    intelligence_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ib_intelligence_runs.id", ondelete="SET NULL")
    )
    query_type: Mapped[str] = mapped_column(Text, nullable=False)
    query_id_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    reference_code_hash: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    output_format: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    corrected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_ib_flex_import_content_hash", "content_hash"),
        Index("ix_ib_flex_import_started", "started_at"),
    )


class IBExecutionFill(Base):
    __tablename__ = "ib_execution_fills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flex_import_run_id: Mapped[int] = mapped_column(
        ForeignKey("ib_flex_import_runs.id", ondelete="RESTRICT"), nullable=False
    )
    external_execution_id: Mapped[str | None] = mapped_column(Text)
    account_hash: Mapped[str | None] = mapped_column(Text)
    account_masked_label: Mapped[str | None] = mapped_column(Text)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    conid: Mapped[int | None] = mapped_column(BigInteger)
    asset_class: Mapped[str | None] = mapped_column(Text)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    execution_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    currency: Mapped[str | None] = mapped_column(Text)
    exchange: Mapped[str | None] = mapped_column(Text)
    commission: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=0)
    fees: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False, default=0)
    broker_realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    order_reference: Mapped[str | None] = mapped_column(Text)
    raw_record_hash: Mapped[str] = mapped_column(Text, nullable=False)
    supersedes_fill_id: Mapped[int | None] = mapped_column(
        ForeignKey("ib_execution_fills.id", ondelete="SET NULL")
    )
    is_superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_ib_execution_external_id", "external_execution_id"),
        Index("ix_ib_execution_symbol_time", "symbol", "execution_time"),
        Index("uq_ib_execution_raw_hash", "raw_record_hash", unique=True),
        Index(
            "uq_ib_execution_active_external",
            "external_execution_id",
            unique=True,
            postgresql_where=text(
                "external_execution_id IS NOT NULL AND is_superseded = false"
            ),
        ),
    )


class IBTradeEpisode(Base):
    __tablename__ = "ib_trade_episodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    episode_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    exit_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    average_entry_price: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    average_exit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    deployed_entry_capital: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    gross_pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    broker_realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    commissions: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    net_pnl: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    return_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    holding_seconds: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    matching_policy: Mapped[str] = mapped_column(Text, nullable=False, default="FIFO_POSITION_V1")
    fill_ids_json: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    is_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_ib_trade_episode_ticker_opened", "ticker", "opened_at"),)


class IBTradeResearchLink(Base):
    __tablename__ = "ib_trade_research_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_episode_id: Mapped[int] = mapped_column(
        ForeignKey("ib_trade_episodes.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    upload_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="SET NULL")
    )
    combined_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("combined_results.id", ondelete="SET NULL")
    )
    technical_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("technical_scores.id", ondelete="SET NULL")
    )
    fundamental_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("fundamental_scores.id", ondelete="SET NULL")
    )
    matching_status: Mapped[str] = mapped_column(Text, nullable=False)
    matching_policy: Mapped[str] = mapped_column(Text, nullable=False)
    decision_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    leakage_check: Mapped[str] = mapped_column(Text, nullable=False)
    ambiguity_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
