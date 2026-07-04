from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class UploadRun(Base):
    __tablename__ = "upload_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_count: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    pine_engine_version: Mapped[str | None] = mapped_column(Text)
    python_engine_version: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    raw_company_rows: Mapped[list["RawCompanyRow"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    fundamental_scores: Mapped[list["FundamentalScore"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    technical_scores: Mapped[list["TechnicalScore"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    combined_results: Mapped[list["CombinedResult"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    ib_fetch_runs: Mapped[list["IBFetchRun"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    engine_parameters: Mapped[list["EngineParameters"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class RawCompanyRow(Base):
    __tablename__ = "raw_company_rows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    row_number: Mapped[int] = mapped_column(nullable=False)
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[UploadRun] = relationship(back_populates="raw_company_rows")

    __table_args__ = (
        Index("idx_raw_company_rows_run_id", "run_id"),
        Index("idx_raw_company_rows_ticker", "ticker"),
    )


class IBContract(Base):
    __tablename__ = "ib_contracts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    ib_conid: Mapped[int | None] = mapped_column(BigInteger)
    symbol: Mapped[str | None] = mapped_column(Text)
    exchange: Mapped[str | None] = mapped_column(Text)
    primary_exchange: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    sec_type: Mapped[str | None] = mapped_column(Text)
    local_symbol: Mapped[str | None] = mapped_column(Text)
    trading_class: Mapped[str | None] = mapped_column(Text)
    resolution_status: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    last_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PriceBar(Base):
    __tablename__ = "price_bars"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    bar_date: Mapped[date] = mapped_column(Date, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Numeric)
    high: Mapped[Decimal | None] = mapped_column(Numeric)
    low: Mapped[Decimal | None] = mapped_column(Numeric)
    close: Mapped[Decimal | None] = mapped_column(Numeric)
    volume: Mapped[Decimal | None] = mapped_column(Numeric)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    what_to_show: Mapped[str] = mapped_column(Text, nullable=False)
    adjustment_type: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    revised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    data_hash: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "ticker",
            "bar_date",
            "timeframe",
            "what_to_show",
            name="uq_price_bars_ticker_date_timeframe_what_to_show",
        ),
        Index("idx_price_bars_ticker_date", "ticker", "bar_date"),
    )


class FundamentalScore(Base):
    __tablename__ = "fundamental_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    growth_score: Mapped[Decimal | None] = mapped_column(Numeric)
    profitability_score: Mapped[Decimal | None] = mapped_column(Numeric)
    fcf_score: Mapped[Decimal | None] = mapped_column(Numeric)
    balance_sheet_score: Mapped[Decimal | None] = mapped_column(Numeric)
    valuation_score: Mapped[Decimal | None] = mapped_column(Numeric)
    momentum_score: Mapped[Decimal | None] = mapped_column(Numeric)
    dilution_score: Mapped[Decimal | None] = mapped_column(Numeric)
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric)
    growth_quality_score: Mapped[Decimal | None] = mapped_column(Numeric)
    profitability_quality_score: Mapped[Decimal | None] = mapped_column(Numeric)
    fcf_quality_score: Mapped[Decimal | None] = mapped_column(Numeric)
    earnings_quality_score: Mapped[Decimal | None] = mapped_column(Numeric)
    capital_efficiency_score: Mapped[Decimal | None] = mapped_column(Numeric)
    balance_sheet_quality_score: Mapped[Decimal | None] = mapped_column(Numeric)
    valuation_quality_score: Mapped[Decimal | None] = mapped_column(Numeric)
    forward_quality_score: Mapped[Decimal | None] = mapped_column(Numeric)
    shareholder_quality_score: Mapped[Decimal | None] = mapped_column(Numeric)
    liquidity_risk_score: Mapped[Decimal | None] = mapped_column(Numeric)
    data_coverage_score: Mapped[Decimal | None] = mapped_column(Numeric)
    scoring_model_version: Mapped[str | None] = mapped_column(Text)
    v2_warning_flags_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    missing_data_penalty: Mapped[Decimal | None] = mapped_column(Numeric)
    fundamental_score: Mapped[Decimal | None] = mapped_column(Numeric)
    fundamental_label: Mapped[str | None] = mapped_column(Text)
    trap_flags_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    explanation: Mapped[str | None] = mapped_column(Text)
    debug_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[UploadRun] = relationship(back_populates="fundamental_scores")

    __table_args__ = (
        UniqueConstraint("run_id", "ticker", name="uq_fundamental_scores_run_ticker"),
        Index("idx_fundamental_scores_run_id", "run_id"),
    )


class TechnicalScore(Base):
    __tablename__ = "technical_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    trend_score: Mapped[Decimal | None] = mapped_column(Numeric)
    local_trend_score: Mapped[Decimal | None] = mapped_column(Numeric)
    momentum_score: Mapped[Decimal | None] = mapped_column(Numeric)
    setup_score: Mapped[Decimal | None] = mapped_column(Numeric)
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric)
    market_score: Mapped[Decimal | None] = mapped_column(Numeric)
    relative_strength_score: Mapped[Decimal | None] = mapped_column(Numeric)
    sector_relative_strength_score: Mapped[Decimal | None] = mapped_column(Numeric)
    combined_relative_strength_score: Mapped[Decimal | None] = mapped_column(Numeric)
    htf_score: Mapped[Decimal | None] = mapped_column(Numeric)
    dual_score: Mapped[Decimal | None] = mapped_column(Numeric)
    classification: Mapped[str | None] = mapped_column(Text)
    pullback_health: Mapped[str | None] = mapped_column(Text)
    action_bias: Mapped[str | None] = mapped_column(Text)
    suggested_stop: Mapped[Decimal | None] = mapped_column(Numeric)
    suggested_target: Mapped[Decimal | None] = mapped_column(Numeric)
    reward_risk: Mapped[Decimal | None] = mapped_column(Numeric)
    entry_risk_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    technical_confidence: Mapped[str | None] = mapped_column(Text)
    insufficient_data: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )
    missing_data_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    debug_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[UploadRun] = relationship(back_populates="technical_scores")

    __table_args__ = (
        UniqueConstraint("run_id", "ticker", name="uq_technical_scores_run_ticker"),
        Index("idx_technical_scores_run_id", "run_id"),
    )


class CombinedResult(Base):
    __tablename__ = "combined_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(Text)
    final_rank: Mapped[int | None]
    final_score: Mapped[Decimal | None] = mapped_column(Numeric)
    fundamental_score: Mapped[Decimal | None] = mapped_column(Numeric)
    fundamental_label: Mapped[str | None] = mapped_column(Text)
    technical_classification: Mapped[str | None] = mapped_column(Text)
    dual_score: Mapped[Decimal | None] = mapped_column(Numeric)
    combined_decision: Mapped[str | None] = mapped_column(Text)
    position_size_hint: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    warning_flags_json: Mapped[list[str] | None] = mapped_column(JSONB)
    is_complete: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )
    has_fundamental: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )
    has_technical: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )
    has_warning: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )
    sort_bucket: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[UploadRun] = relationship(back_populates="combined_results")

    __table_args__ = (
        UniqueConstraint("run_id", "ticker", name="uq_combined_results_run_ticker"),
        Index("idx_combined_results_run_id", "run_id"),
        Index("idx_combined_results_run_rank", "run_id", "final_rank"),
    )


class IBFetchRun(Base):
    __tablename__ = "ib_fetch_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="CASCADE")
    )
    requested_tickers: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    symbols_including_benchmarks: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    include_benchmarks: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        server_default="true",
    )
    force_refresh: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )
    force_full_backfill: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )
    planned_request_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    executed_request_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    skipped_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    success_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    inserted_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    updated_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    revised_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    unchanged_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    failure_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    message: Mapped[str | None] = mapped_column(Text)

    run: Mapped[UploadRun | None] = relationship(back_populates="ib_fetch_runs")
    items: Mapped[list["IBFetchItem"]] = relationship(
        back_populates="fetch_run",
        cascade="all, delete-orphan",
    )


class IBFetchItem(Base):
    __tablename__ = "ib_fetch_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fetch_run_id: Mapped[int] = mapped_column(
        ForeignKey("ib_fetch_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    what_to_show: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[str | None] = mapped_column(Text)
    bar_size: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="1 day",
        server_default="1 day",
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    current_bar_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    fetched: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    inserted: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    updated: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    revised: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    unchanged: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    attempt_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    fetch_run: Mapped[IBFetchRun] = relationship(back_populates="items")

    __table_args__ = (
        Index("idx_ib_fetch_items_fetch_run_id", "fetch_run_id"),
        Index("idx_ib_fetch_items_ticker", "ticker"),
        Index("idx_ib_fetch_items_status", "status"),
    )


class EngineParameters(Base):
    __tablename__ = "engine_parameters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[UploadRun] = relationship(back_populates="engine_parameters")

    __table_args__ = (Index("idx_engine_parameters_run_id", "run_id"),)
