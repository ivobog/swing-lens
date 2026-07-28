from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SectorUniverseMetrics:
    sector: str
    sector_slug: str
    ticker_count: int
    universe_share: float | None
    average_fundamental_score: float | None
    average_technical_score: float | None
    average_final_score: float | None
    average_profile_score: float | None
    top_counts: dict[str, int]
    setup_distribution: dict[str, int]
    warning_distribution: dict[str, int]
    buyable_count: int
    watch_count: int
    danger_count: int
    buyable_share: float | None
    watch_share: float | None
    danger_share: float | None
    clean_pullback_count: int
    breakout_count: int
    vcp_count: int
    tight_base_breakout_count: int
    extended_or_overheated_count: int
    missing_fundamental_count: int
    missing_technical_count: int
    raw_sector_distribution: dict[str, int] = field(default_factory=dict)
    sector_mapping_status_counts: dict[str, int] = field(default_factory=dict)
    profile_distribution: dict[str, Any] = field(default_factory=dict)
    component_scores: dict[str, float] = field(default_factory=dict)
    universe_leadership_score: float | None = None
    confidence: str = "unscored"
    reason_codes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SectorRotationDecision:
    sector: str
    sector_slug: str
    final_score: float | None
    universe_score: float | None
    etf_score: float | None
    rotation_state: str
    permission: str
    position_size_multiplier: float
    confidence: str
    rank: int | None
    previous_rank: int | None
    rank_change: int | None
    score_change: float | None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SectorEtfRotationMetrics:
    sector: str
    sector_slug: str
    proxy_ticker: str
    benchmark_ticker: str
    as_of_date: str | None
    etf_rotation_score: float | None
    component_scores: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SectorTickerDrilldownRow:
    ticker: str
    company_name: str | None
    sector: str
    final_rank: int | None
    final_score: float | None
    profile_rank: int | None
    profile_score: float | None
    technical_score: float | None
    fundamental_score: float | None
    technical_classification: str | None
    warning_flags: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SectorRotationSnapshotDto:
    run_id: int | None
    as_of_date: str
    mode: str
    calculation_version: str
    config_version: str | None
    config_hash: str | None
    default_ranking_profile: str | None
    rows: list[SectorRotationDecision]
    market_regime_snapshot_id: int | None = None
    benchmark_ticker: str | None = None
    universe_rows: list[SectorUniverseMetrics] = field(default_factory=list)
    etf_rows: list[SectorEtfRotationMetrics] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)
