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
    ranking_results: Mapped[list["RankingResult"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    market_regime_snapshots: Mapped[list["MarketRegimeSnapshot"]] = relationship(
        back_populates="run",
    )
    sector_rotation_snapshots: Mapped[list["SectorRotationSnapshot"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    ib_fetch_runs: Mapped[list["IBFetchRun"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    pipeline_runs: Mapped[list["PipelineRun"]] = relationship(
        back_populates="upload_run",
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
    sector_canonical: Mapped[str | None] = mapped_column(Text)
    sector_taxonomy: Mapped[str | None] = mapped_column(Text)
    sector_mapping_status: Mapped[str | None] = mapped_column(Text)
    upcoming_earnings_date: Mapped[date | None] = mapped_column(Date)
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
        Index("idx_raw_company_rows_upcoming_earnings_date", "upcoming_earnings_date"),
        Index("idx_raw_company_rows_sector_canonical", "run_id", "sector_canonical"),
        Index(
            "idx_raw_company_rows_sector_mapping_status",
            "run_id",
            "sector_mapping_status",
        ),
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
    technical_engine_version: Mapped[str | None] = mapped_column(String(32))
    data_quality_score: Mapped[Decimal | None] = mapped_column(Numeric)
    stage: Mapped[str | None] = mapped_column(String(64))
    market_regime: Mapped[str | None] = mapped_column(String(64))
    leadership_score: Mapped[Decimal | None] = mapped_column(Numeric)
    vcp_score: Mapped[Decimal | None] = mapped_column(Numeric)
    box_tightness_score: Mapped[Decimal | None] = mapped_column(Numeric)
    breakout_quality_score: Mapped[Decimal | None] = mapped_column(Numeric)
    climax_risk_score: Mapped[Decimal | None] = mapped_column(Numeric)
    atr_percentile_252: Mapped[Decimal | None] = mapped_column(Numeric)
    volume_percentile_252: Mapped[Decimal | None] = mapped_column(Numeric)
    range_percentile_252: Mapped[Decimal | None] = mapped_column(Numeric)
    extension_percentile_252: Mapped[Decimal | None] = mapped_column(Numeric)
    feature_flags_json: Mapped[list[str] | None] = mapped_column(JSONB)
    warning_flags_json: Mapped[list[str] | None] = mapped_column(JSONB)
    sub_tags_json: Mapped[list[str] | None] = mapped_column(JSONB)
    v4_debug_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
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
    upcoming_earnings_date: Mapped[date | None] = mapped_column(Date)
    days_until_earnings: Mapped[int | None]
    earnings_risk_level: Mapped[str | None] = mapped_column(Text)
    earnings_warning_flags_json: Mapped[list[str]] = mapped_column(
        "earnings_warning_flags",
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
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
        Index("idx_combined_results_earnings_risk", "earnings_risk_level"),
    )


class RankingResult(Base):
    __tablename__ = "ranking_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    raw_row_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_company_rows.id", ondelete="SET NULL"),
        nullable=True,
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(Text)
    ranking_profile: Mapped[str] = mapped_column(Text, nullable=False)
    ranking_label: Mapped[str] = mapped_column(Text, nullable=False)
    profile_rank: Mapped[int] = mapped_column(nullable=False)
    profile_score: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    technical_profile_score: Mapped[Decimal | None] = mapped_column(Numeric)
    fundamental_score: Mapped[Decimal | None] = mapped_column(Numeric)
    base_technical_score: Mapped[Decimal | None] = mapped_column(Numeric)
    technical_classification: Mapped[str | None] = mapped_column(Text)
    fundamental_label: Mapped[str | None] = mapped_column(Text)
    decision_label: Mapped[str] = mapped_column(Text, nullable=False)
    position_size_hint: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    warning_flags_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    penalties_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    gates_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    component_scores_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    debug_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    upcoming_earnings_date: Mapped[date | None] = mapped_column(Date)
    days_until_earnings: Mapped[int | None]
    earnings_risk_level: Mapped[str | None] = mapped_column(Text)
    is_complete: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        server_default="true",
    )
    has_warning: Mapped[bool] = mapped_column(
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
    sort_bucket: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    run: Mapped[UploadRun] = relationship(back_populates="ranking_results")

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "ranking_profile",
            "ticker",
            name="uq_ranking_results_run_profile_ticker",
        ),
        Index("idx_ranking_results_run_id", "run_id"),
        Index("idx_ranking_results_ticker", "ticker"),
        Index("idx_ranking_results_profile", "ranking_profile"),
        Index(
            "idx_ranking_results_run_profile_rank",
            "run_id",
            "ranking_profile",
            "profile_rank",
        ),
        Index(
            "idx_ranking_results_run_profile_score",
            "run_id",
            "ranking_profile",
            "profile_score",
        ),
        Index("idx_ranking_results_earnings_risk", "earnings_risk_level"),
    )


class MarketRegimeSnapshot(Base):
    __tablename__ = "market_regime_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(32), nullable=False)
    config_version: Mapped[str | None] = mapped_column(String(64))
    regime: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_state: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float] = mapped_column(nullable=False)
    risk_off: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )
    gate_ok: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        server_default="true",
    )
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    action_summary: Mapped[str] = mapped_column(Text, nullable=False)
    position_size_multiplier: Mapped[float] = mapped_column(
        nullable=False,
        default=1.0,
        server_default="1.0",
    )
    preferred_profiles_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    allowed_profiles_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    reduced_profiles_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    blocked_profiles_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    allowed_setups_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    blocked_setups_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    input_symbols_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    index_health_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    universe_participation_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    sector_leadership_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    reasons_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    warnings_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    debug_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
    )

    run: Mapped[UploadRun | None] = relationship(back_populates="market_regime_snapshots")

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "as_of_date",
            "calculation_version",
            "config_version",
            name="uq_market_regime_snapshots_run_date_version",
        ),
        Index("idx_market_regime_snapshots_as_of_date", "as_of_date"),
        Index("idx_market_regime_snapshots_run_id", "run_id"),
        Index("idx_market_regime_snapshots_regime", "regime"),
        Index("idx_market_regime_snapshots_risk_state", "risk_state"),
    )


class SectorRotationSnapshot(Base):
    __tablename__ = "sector_rotation_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    market_regime_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_regime_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(32), nullable=False)
    config_version: Mapped[str | None] = mapped_column(String(32))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    default_ranking_profile: Mapped[str | None] = mapped_column(String(64))
    benchmark_ticker: Mapped[str | None] = mapped_column(String(16))
    sector_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    ticker_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    leading_sector: Mapped[str | None] = mapped_column(String(128))
    weakest_sector: Mapped[str | None] = mapped_column(String(128))
    riskiest_sector: Mapped[str | None] = mapped_column(String(128))
    summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    warning_flags_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    debug_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
    )

    run: Mapped[UploadRun | None] = relationship(back_populates="sector_rotation_snapshots")
    rows: Mapped[list["SectorRotationRow"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="SectorRotationRow.current_rank",
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "as_of_date",
            "calculation_version",
            "config_hash",
            "mode",
            name="uq_sector_rotation_snapshots_run_date_version_mode",
        ),
        Index("idx_sector_rotation_snapshot_run_date", "run_id", "as_of_date"),
        Index("idx_sector_rotation_snapshot_date", "as_of_date"),
    )


class SectorRotationRow(Base):
    __tablename__ = "sector_rotation_rows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("sector_rotation_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    sector: Mapped[str] = mapped_column(String(128), nullable=False)
    sector_slug: Mapped[str] = mapped_column(String(160), nullable=False)
    sector_proxy_ticker: Mapped[str | None] = mapped_column(String(16))
    ticker_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    universe_share: Mapped[float | None] = mapped_column(Float)
    average_fundamental_score: Mapped[float | None] = mapped_column(Float)
    average_technical_score: Mapped[float | None] = mapped_column(Float)
    average_final_score: Mapped[float | None] = mapped_column(Float)
    average_profile_score: Mapped[float | None] = mapped_column(Float)
    top_10_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    top_25_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    top_50_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    top_25_share: Mapped[float | None] = mapped_column(Float)
    buyable_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    watch_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    danger_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    buyable_share: Mapped[float | None] = mapped_column(Float)
    watch_share: Mapped[float | None] = mapped_column(Float)
    danger_share: Mapped[float | None] = mapped_column(Float)
    clean_pullback_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    breakout_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    vcp_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    tight_base_breakout_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    extended_or_overheated_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    missing_fundamental_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    missing_technical_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    universe_leadership_score: Mapped[float | None] = mapped_column(Float)
    etf_rotation_score: Mapped[float | None] = mapped_column(Float)
    sector_final_score: Mapped[float | None] = mapped_column(Float)
    rotation_state: Mapped[str] = mapped_column(String(64), nullable=False)
    sector_permission: Mapped[str] = mapped_column(String(64), nullable=False)
    position_size_multiplier: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_rank: Mapped[int | None]
    current_rank: Mapped[int | None]
    rank_change: Mapped[int | None]
    score_change: Mapped[float | None] = mapped_column(Float)
    profile_distribution_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    setup_distribution_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    warning_distribution_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    etf_metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    component_scores_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    reason_codes_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    warning_flags_json: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    debug_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    snapshot: Mapped[SectorRotationSnapshot] = relationship(back_populates="rows")

    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "sector_slug",
            name="uq_sector_rotation_rows_snapshot_sector_slug",
        ),
        Index("idx_sector_rotation_rows_snapshot_rank", "snapshot_id", "current_rank"),
        Index("idx_sector_rotation_rows_sector_slug", "sector_slug"),
    )


class IBFetchRun(Base):
    __tablename__ = "ib_fetch_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("upload_runs.id", ondelete="CASCADE"))
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


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    upload_run_id: Mapped[int] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_step: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    message: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    upload_run: Mapped[UploadRun] = relationship(back_populates="pipeline_runs")
    steps: Mapped[list["PipelineStep"]] = relationship(
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
        order_by="PipelineStep.step_order",
    )

    __table_args__ = (
        Index("idx_pipeline_runs_upload_run_id", "upload_run_id"),
        Index("idx_pipeline_runs_status", "status"),
        Index("idx_pipeline_runs_created_at", "created_at"),
    )


class PipelineStep(Base):
    __tablename__ = "pipeline_steps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_name: Mapped[str] = mapped_column(Text, nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    pipeline_run: Mapped[PipelineRun] = relationship(back_populates="steps")

    __table_args__ = (
        Index("idx_pipeline_steps_pipeline_run_id", "pipeline_run_id"),
        UniqueConstraint(
            "pipeline_run_id",
            "step_name",
            name="uq_pipeline_steps_pipeline_step",
        ),
    )


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    related_run_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default="3",
    )
    requested_cancel: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    worker_id: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    execution_token: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operational_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    __table_args__ = (
        Index(
            "idx_background_jobs_status_priority",
            "status",
            "priority",
            "run_after",
            "created_at",
        ),
        Index("idx_background_jobs_related_run_id", "related_run_id"),
        Index("idx_background_jobs_locked_at", "locked_at"),
        Index("idx_background_jobs_lease_expires_at", "lease_expires_at"),
        Index("idx_background_jobs_execution_token", "execution_token"),
    )


class PredictionEligibility:
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED = "EXCLUDED"
    INSUFFICIENT = "INSUFFICIENT"


class EntryScheduleStatus:
    PLANNED = "PLANNED"
    READY = "READY"
    MISSING_SESSION = "MISSING_SESSION"
    CANCELLED = "CANCELLED"


class EntryDataStatus:
    PENDING = "PENDING"
    READY = "READY"
    MISSING_BAR = "MISSING_BAR"
    INVALID_BAR = "INVALID_BAR"


class OutcomeStatus:
    PENDING = "PENDING"
    MATURED = "MATURED"
    EXCLUDED = "EXCLUDED"
    REVISED = "REVISED"


class EntryModel:
    NEXT_OPEN = "NEXT_OPEN"
    SIGNAL_CLOSE_DIAGNOSTIC = "SIGNAL_CLOSE_DIAGNOSTIC"


class FirstEvent:
    TARGET_FIRST = "TARGET_FIRST"
    STOP_FIRST = "STOP_FIRST"
    SAME_BAR_CONFLICT = "SAME_BAR_CONFLICT"
    NEITHER = "NEITHER"
    UNKNOWN = "UNKNOWN"


class EstimateKind:
    DECISION_TIME = "DECISION_TIME"
    LATEST_RESCORE = "LATEST_RESCORE"
    AS_OF_REPLAY = "AS_OF_REPLAY"


class EstimateSource:
    COHORT = "COHORT"
    MODEL = "MODEL"
    INSUFFICIENT = "INSUFFICIENT"
    SIMILARITY = "SIMILARITY"


class EvidenceGrade:
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INSUFFICIENT = "Insufficient"


class ModelStatus:
    CANDIDATE = "CANDIDATE"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class ProcessingStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class LifecycleEventType:
    CREATED = "CREATED"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"
    RESTORED = "RESTORED"


class WinnerPredictionEpisode(Base):
    __tablename__ = "winner_prediction_episodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    setup_family: Mapped[str | None] = mapped_column(Text)
    trigger_state: Mapped[str | None] = mapped_column(Text)
    episode_key: Mapped[str] = mapped_column(Text, nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date | None] = mapped_column(Date)
    cooldown_sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    dependency_group_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    predictions: Mapped[list["WinnerPredictionSnapshot"]] = relationship(
        back_populates="episode",
    )

    __table_args__ = (
        UniqueConstraint("episode_key", name="uq_winner_prediction_episodes_key"),
        Index("idx_winner_prediction_episodes_ticker_dates", "ticker", "starts_on", "ends_on"),
        Index("idx_winner_prediction_episodes_group", "dependency_group_hash"),
    )


class WinnerOutcomeDefinition(Base):
    __tablename__ = "winner_outcome_definitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    definition_id: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    entry_model: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    target_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    stop_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    same_bar_conflict_policy: Mapped[str | None] = mapped_column(Text)
    calculation_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    target_stop_outcomes: Mapped[list["WinnerTargetStopOutcome"]] = relationship(
        back_populates="outcome_definition",
    )
    probability_estimates: Mapped[list["WinnerProbabilityEstimate"]] = relationship(
        back_populates="outcome_definition",
    )

    __table_args__ = (
        UniqueConstraint(
            "definition_id",
            "calculation_version",
            name="uq_winner_outcome_definitions_definition_version",
        ),
        Index("idx_winner_outcome_definitions_entry_model", "entry_model"),
        Index(
            "idx_winner_outcome_definitions_active",
            "definition_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class WinnerPredictionSnapshot(Base):
    __tablename__ = "winner_prediction_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("upload_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    raw_row_id: Mapped[int | None] = mapped_column(ForeignKey("raw_company_rows.id"))
    combined_result_id: Mapped[int | None] = mapped_column(ForeignKey("combined_results.id"))
    ranking_result_id: Mapped[int | None] = mapped_column(ForeignKey("ranking_results.id"))
    market_regime_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_regime_snapshots.id")
    )
    sector_rotation_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("sector_rotation_snapshots.id")
    )
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("winner_prediction_episodes.id"))
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    prediction_as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_data_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    planned_entry_session: Mapped[date | None] = mapped_column(Date)
    entry_schedule_status: Mapped[str] = mapped_column(Text, nullable=False)
    entry_data_status: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_status: Mapped[str] = mapped_column(Text, nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    setup_family: Mapped[str | None] = mapped_column(Text)
    setup_classification: Mapped[str | None] = mapped_column(Text)
    ranking_profile: Mapped[str | None] = mapped_column(Text)
    fundamental_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    technical_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    combined_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    market_regime: Mapped[str | None] = mapped_column(Text)
    market_risk_state: Mapped[str | None] = mapped_column(Text)
    sector_state: Mapped[str | None] = mapped_column(Text)
    sector_rank: Mapped[int | None] = mapped_column(Integer)
    suggested_target_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    suggested_stop_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    reward_risk: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    upcoming_earnings_date: Mapped[date | None] = mapped_column(Date)
    days_until_earnings: Mapped[int | None] = mapped_column(Integer)
    earnings_risk_level: Mapped[str | None] = mapped_column(Text)
    technical_data_quality: Mapped[str | None] = mapped_column(Text)
    fundamental_coverage: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    universe_provenance: Mapped[str | None] = mapped_column(Text)
    screener_provenance: Mapped[str | None] = mapped_column(Text)
    feature_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    feature_vector_hash: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_version: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feature_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_ids_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    warning_flags_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    lineage_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    reconstruction_method: Mapped[str | None] = mapped_column(Text)
    retention_class: Mapped[str] = mapped_column(
        Text, nullable=False, default="permanent", server_default="permanent"
    )

    run: Mapped[UploadRun] = relationship()
    episode: Mapped[WinnerPredictionEpisode | None] = relationship(back_populates="predictions")
    forward_outcomes: Mapped[list["WinnerForwardOutcome"]] = relationship(
        back_populates="prediction",
    )
    target_stop_outcomes: Mapped[list["WinnerTargetStopOutcome"]] = relationship(
        back_populates="prediction",
    )
    probability_estimates: Mapped[list["WinnerProbabilityEstimate"]] = relationship(
        back_populates="prediction",
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "ticker",
            "prediction_as_of_date",
            "feature_schema_version",
            "revision",
            name="uq_winner_prediction_snapshots_natural_revision",
        ),
        Index("idx_winner_prediction_snapshots_run_ticker", "run_id", "ticker"),
        Index("idx_winner_prediction_snapshots_ticker_as_of", "ticker", "prediction_as_of_date"),
        Index("idx_winner_prediction_snapshots_eligibility", "eligibility_status"),
        Index("idx_winner_prediction_snapshots_profile", "ranking_profile"),
        Index("idx_winner_prediction_snapshots_regime_sector", "market_risk_state", "sector_state"),
        Index(
            "idx_winner_prediction_snapshots_earnings_quality",
            "earnings_risk_level",
            "technical_data_quality",
        ),
        Index(
            "idx_winner_prediction_snapshots_active_revision",
            "run_id",
            "ticker",
            "prediction_as_of_date",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
    )


class WinnerForwardOutcome(Base):
    __tablename__ = "winner_forward_outcomes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("winner_prediction_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    entry_model: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_session: Mapped[date | None] = mapped_column(Date)
    due_session: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_current_revision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    close_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    spy_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    excess_spy_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    sector_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    excess_sector_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    mfe_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    mae_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    sessions_to_mfe: Mapped[int | None] = mapped_column(Integer)
    sessions_to_mae: Mapped[int | None] = mapped_column(Integer)
    positive_return: Mapped[bool | None] = mapped_column(Boolean)
    beat_spy: Mapped[bool | None] = mapped_column(Boolean)
    beat_sector: Mapped[bool | None] = mapped_column(Boolean)
    source_bar_lineage_hash: Mapped[str | None] = mapped_column(Text)
    source_revision_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    prediction: Mapped[WinnerPredictionSnapshot] = relationship(back_populates="forward_outcomes")
    evidence_members: Mapped[list["WinnerEstimateEvidenceMember"]] = relationship(
        back_populates="outcome",
    )

    __table_args__ = (
        UniqueConstraint(
            "prediction_id",
            "entry_model",
            "horizon_sessions",
            "revision",
            name="uq_winner_forward_outcomes_prediction_entry_horizon_revision",
        ),
        Index("idx_winner_forward_outcomes_status_due", "status", "due_session"),
        Index(
            "idx_winner_forward_outcomes_prediction_entry_horizon",
            "prediction_id",
            "entry_model",
            "horizon_sessions",
        ),
        Index(
            "idx_winner_forward_outcomes_current_revision",
            "prediction_id",
            "entry_model",
            "horizon_sessions",
            unique=True,
            postgresql_where=text("is_current_revision"),
        ),
        Index(
            "idx_winner_forward_outcomes_bar_lineage_current",
            "source_bar_lineage_hash",
            "is_current_revision",
        ),
    )


class WinnerTargetStopOutcome(Base):
    __tablename__ = "winner_target_stop_outcomes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("winner_prediction_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    outcome_definition_id: Mapped[int] = mapped_column(
        ForeignKey("winner_outcome_definitions.id"),
        nullable=False,
    )
    forward_outcome_id: Mapped[int | None] = mapped_column(ForeignKey("winner_forward_outcomes.id"))
    entry_model: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_sessions: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_current_revision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    target_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    stop_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    target_hit: Mapped[bool | None] = mapped_column(Boolean)
    stop_hit: Mapped[bool | None] = mapped_column(Boolean)
    first_event: Mapped[str | None] = mapped_column(Text)
    event_session: Mapped[date | None] = mapped_column(Date)
    same_bar_conflict: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    primary_winner: Mapped[bool | None] = mapped_column(Boolean)
    optimistic_winner: Mapped[bool | None] = mapped_column(Boolean)
    conservative_winner: Mapped[bool | None] = mapped_column(Boolean)
    source_bar_lineage_hash: Mapped[str | None] = mapped_column(Text)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    prediction: Mapped[WinnerPredictionSnapshot] = relationship(
        back_populates="target_stop_outcomes"
    )
    outcome_definition: Mapped[WinnerOutcomeDefinition] = relationship(
        back_populates="target_stop_outcomes"
    )

    __table_args__ = (
        UniqueConstraint(
            "prediction_id",
            "outcome_definition_id",
            "revision",
            name="uq_winner_target_stop_outcomes_prediction_definition_revision",
        ),
        Index("idx_winner_target_stop_outcomes_status", "status"),
        Index(
            "idx_winner_target_stop_outcomes_prediction_definition",
            "prediction_id",
            "outcome_definition_id",
        ),
        Index(
            "idx_winner_target_stop_outcomes_current_revision",
            "prediction_id",
            "outcome_definition_id",
            unique=True,
            postgresql_where=text("is_current_revision"),
        ),
    )


class WinnerCohortDefinition(Base):
    __tablename__ = "winner_cohort_definitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cohort_key: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_definition_id: Mapped[int] = mapped_column(
        ForeignKey("winner_outcome_definitions.id"), nullable=False
    )
    entry_model: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    statistics: Mapped[list["WinnerCohortStatistic"]] = relationship(
        back_populates="cohort_definition"
    )
    probability_estimates: Mapped[list["WinnerProbabilityEstimate"]] = relationship(
        back_populates="cohort_definition"
    )

    __table_args__ = (
        UniqueConstraint(
            "cohort_key",
            "outcome_definition_id",
            "source_version",
            name="uq_winner_cohort_definitions_key_outcome_version",
        ),
        Index("idx_winner_cohort_definitions_level", "level"),
        Index("idx_winner_cohort_definitions_status", "status"),
    )


class WinnerCohortStatistic(Base):
    __tablename__ = "winner_cohort_statistics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cohort_definition_id: Mapped[int] = mapped_column(
        ForeignKey("winner_cohort_definitions.id", ondelete="CASCADE"), nullable=False
    )
    outcome_definition_id: Mapped[int] = mapped_column(
        ForeignKey("winner_outcome_definitions.id"), nullable=False
    )
    statistic_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    training_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_n: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_n: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    wins: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    raw_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    posterior_probability: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    lower_bound: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    upper_bound: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    median_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    median_mfe_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    median_mae_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    evidence_grade: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_manifest_hash: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    cohort_definition: Mapped[WinnerCohortDefinition] = relationship(back_populates="statistics")

    __table_args__ = (
        UniqueConstraint(
            "cohort_definition_id",
            "outcome_definition_id",
            "training_cutoff_at",
            name="uq_winner_cohort_statistics_definition_cutoff",
        ),
        Index("idx_winner_cohort_statistics_cutoff", "training_cutoff_at"),
        Index("idx_winner_cohort_statistics_grade_n", "evidence_grade", "effective_n"),
    )


class WinnerEvidenceManifest(Base):
    __tablename__ = "winner_evidence_manifests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    hash_algorithm: Mapped[str] = mapped_column(Text, nullable=False)
    content_encoding: Mapped[str] = mapped_column(Text, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    compressed_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    artifact_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    probability_estimates: Mapped[list["WinnerProbabilityEstimate"]] = relationship(
        back_populates="evidence_manifest"
    )

    __table_args__ = (
        UniqueConstraint("manifest_hash", name="uq_winner_evidence_manifests_hash"),
        Index("idx_winner_evidence_manifests_created_at", "created_at"),
    )


class WinnerModelVersion(Base):
    __tablename__ = "winner_model_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_key: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_definition_id: Mapped[int] = mapped_column(
        ForeignKey("winner_outcome_definitions.id"), nullable=False
    )
    entry_model: Mapped[str] = mapped_column(Text, nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    training_window_start: Mapped[date | None] = mapped_column(Date)
    training_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hyperparameters_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    preprocessing_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    calibration_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    artifact_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_format: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_path: Mapped[str | None] = mapped_column(Text)
    artifact_hash: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    dependency_versions_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    probability_estimates: Mapped[list["WinnerProbabilityEstimate"]] = relationship(
        back_populates="model_version"
    )

    __table_args__ = (
        UniqueConstraint("model_key", name="uq_winner_model_versions_model_key"),
        Index("idx_winner_model_versions_status", "status"),
        Index("idx_winner_model_versions_outcome_status", "outcome_definition_id", "status"),
        CheckConstraint("artifact_hash <> ''", name="ck_winner_model_versions_artifact_hash"),
        CheckConstraint(
            "artifact_schema_version <> ''", name="ck_winner_model_versions_schema_version"
        ),
    )


class WinnerProbabilityEstimate(Base):
    __tablename__ = "winner_probability_estimates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("winner_prediction_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    outcome_definition_id: Mapped[int] = mapped_column(
        ForeignKey("winner_outcome_definitions.id"), nullable=False
    )
    estimate_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_version: Mapped[str] = mapped_column(Text, nullable=False)
    cohort_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("winner_cohort_definitions.id")
    )
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("winner_model_versions.id"))
    evidence_manifest_id: Mapped[int | None] = mapped_column(
        ForeignKey("winner_evidence_manifests.id")
    )
    training_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    point_probability: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    lower_bound: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    upper_bound: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    interval_width: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    sample_n: Mapped[int | None] = mapped_column(Integer)
    effective_n: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    evidence_grade: Mapped[str] = mapped_column(Text, nullable=False)
    insufficient_reasons_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    expected_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    median_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    median_mfe_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    median_mae_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    target_first_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_manifest_hash: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    prediction: Mapped[WinnerPredictionSnapshot] = relationship(
        back_populates="probability_estimates"
    )
    outcome_definition: Mapped[WinnerOutcomeDefinition] = relationship(
        back_populates="probability_estimates"
    )
    cohort_definition: Mapped[WinnerCohortDefinition | None] = relationship(
        back_populates="probability_estimates"
    )
    model_version: Mapped[WinnerModelVersion | None] = relationship(
        back_populates="probability_estimates"
    )
    evidence_manifest: Mapped[WinnerEvidenceManifest | None] = relationship(
        back_populates="probability_estimates"
    )
    evidence_members: Mapped[list["WinnerEstimateEvidenceMember"]] = relationship(
        back_populates="estimate"
    )

    __table_args__ = (
        UniqueConstraint(
            "prediction_id",
            "outcome_definition_id",
            "estimate_kind",
            "source_version",
            "training_cutoff_at",
            name="uq_winner_probability_estimates_identity",
        ),
        Index(
            "idx_winner_probability_estimates_prediction_outcome_kind",
            "prediction_id",
            "outcome_definition_id",
            "estimate_kind",
        ),
        Index("idx_winner_probability_estimates_model_created", "model_version_id", "created_at"),
        Index("idx_winner_probability_estimates_probability", "point_probability"),
        Index("idx_winner_probability_estimates_lower_bound", "lower_bound"),
        Index("idx_winner_probability_estimates_grade_n", "evidence_grade", "effective_n"),
    )


class WinnerEstimateEvidenceMember(Base):
    __tablename__ = "winner_estimate_evidence_members"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    estimate_id: Mapped[int] = mapped_column(
        ForeignKey("winner_probability_estimates.id", ondelete="CASCADE"), nullable=False
    )
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("winner_prediction_snapshots.id"), nullable=False
    )
    outcome_id: Mapped[int] = mapped_column(
        ForeignKey("winner_forward_outcomes.id"), nullable=False
    )
    outcome_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("winner_prediction_episodes.id"))
    inclusion_weight: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=1, server_default="1"
    )
    included_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    inclusion_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    estimate: Mapped[WinnerProbabilityEstimate] = relationship(back_populates="evidence_members")
    outcome: Mapped[WinnerForwardOutcome] = relationship(back_populates="evidence_members")

    __table_args__ = (
        UniqueConstraint(
            "estimate_id",
            "outcome_id",
            "outcome_revision",
            name="uq_winner_estimate_evidence_members_estimate_outcome_revision",
        ),
        Index("idx_winner_estimate_evidence_members_estimate_outcome", "estimate_id", "outcome_id"),
        Index("idx_winner_estimate_evidence_members_estimate_episode", "estimate_id", "episode_id"),
        Index(
            "idx_winner_estimate_evidence_members_prediction_asof",
            "prediction_id",
            "included_as_of",
        ),
    )


class WinnerCalibrationBin(Base):
    __tablename__ = "winner_calibration_bins"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("winner_model_versions.id"))
    outcome_definition_id: Mapped[int] = mapped_column(
        ForeignKey("winner_outcome_definitions.id"), nullable=False
    )
    estimate_kind: Mapped[str] = mapped_column(Text, nullable=False)
    bin_floor: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    bin_ceiling: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    sample_n: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_n: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    mean_prediction: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    observed_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    lower_bound: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    upper_bound: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    error: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    segment_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    __table_args__ = (
        Index("idx_winner_calibration_bins_model", "model_version_id"),
        Index("idx_winner_calibration_bins_outcome_kind", "outcome_definition_id", "estimate_kind"),
    )


class WinnerDriftMetric(Base):
    __tablename__ = "winner_drift_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("winner_model_versions.id"))
    outcome_definition_id: Mapped[int] = mapped_column(
        ForeignKey("winner_outcome_definitions.id"), nullable=False
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    threshold_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    breached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    sample_n: Mapped[int | None] = mapped_column(Integer)
    comparison_window: Mapped[str] = mapped_column(Text, nullable=False)
    segment_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    sufficient_sample: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "outcome_definition_id",
            "as_of_date",
            "metric_name",
            "comparison_window",
            name="uq_winner_drift_metrics_identity",
        ),
        Index("idx_winner_drift_metrics_as_of", "as_of_date"),
        Index("idx_winner_drift_metrics_breached", "breached"),
    )


class WinnerProcessingRun(Base):
    __tablename__ = "winner_processing_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    background_job_id: Mapped[int | None] = mapped_column(ForeignKey("background_jobs.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("upload_runs.id"))
    process_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str | None] = mapped_column(Text)
    source_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counts_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    __table_args__ = (
        Index("idx_winner_processing_runs_type_status", "process_type", "status"),
        Index("idx_winner_processing_runs_run_id", "run_id"),
        Index("idx_winner_processing_runs_job_id", "background_job_id"),
    )


class WinnerModelTrainingRun(Base):
    __tablename__ = "winner_model_training_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    background_job_id: Mapped[int | None] = mapped_column(ForeignKey("background_jobs.id"))
    candidate_model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("winner_model_versions.id")
    )
    outcome_definition_id: Mapped[int] = mapped_column(
        ForeignKey("winner_outcome_definitions.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm: Mapped[str] = mapped_column(Text, nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    training_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fold_plan_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    preprocessing_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    warnings_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    artifact_hash: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_winner_model_training_runs_status", "status"),
        Index("idx_winner_model_training_runs_candidate", "candidate_model_version_id"),
    )


class WinnerModelLifecycleEvent(Base):
    __tablename__ = "winner_model_lifecycle_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("winner_model_versions.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    old_status: Mapped[str | None] = mapped_column(Text)
    new_status: Mapped[str | None] = mapped_column(Text)
    replacement_model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("winner_model_versions.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    __table_args__ = (
        Index("idx_winner_model_lifecycle_events_model", "model_version_id"),
        Index("idx_winner_model_lifecycle_events_type", "event_type"),
    )


class WinnerSimilarityLink(Base):
    __tablename__ = "winner_similarity_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("winner_prediction_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    neighbor_prediction_id: Mapped[int] = mapped_column(
        ForeignKey("winner_prediction_snapshots.id"), nullable=False
    )
    outcome_definition_id: Mapped[int] = mapped_column(
        ForeignKey("winner_outcome_definitions.id"), nullable=False
    )
    outcome_id: Mapped[int | None] = mapped_column(ForeignKey("winner_forward_outcomes.id"))
    outcome_revision: Mapped[int | None] = mapped_column(Integer)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    distance: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    similarity_coverage: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    contribution_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    cache_version: Mapped[str] = mapped_column(Text, nullable=False)
    source_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "prediction_id",
            "outcome_definition_id",
            "cache_version",
            "rank",
            name="uq_winner_similarity_links_prediction_definition_cache_rank",
        ),
        Index("idx_winner_similarity_links_prediction", "prediction_id"),
        Index("idx_winner_similarity_links_neighbor", "neighbor_prediction_id"),
    )
