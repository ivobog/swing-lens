from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services.market_regime_policy import MarketRegimePolicyDto


@dataclass(frozen=True)
class IndexHealthDto:
    symbol: str
    latest_close: float | None
    as_of_date: date | None
    above_sma50: bool | None
    above_sma200: bool | None
    sma50_above_sma200: bool | None
    sma50_slope_pct: float | None
    roc21: float | None
    roc63: float | None
    distribution_count: float | None
    donchian_20_breakout: bool | None
    stale: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketParticipationDto:
    ticker_count: int = 0
    technical_count: int = 0
    average_technical_score: float | None = None
    clean_pullback_count: int = 0
    fresh_breakout_count: int = 0
    vcp_count: int = 0
    danger_count: int = 0
    market_risk_warning_count: int = 0
    above_sma50_pct: float | None = None
    above_sma200_pct: float | None = None
    ranking_profile_average_scores: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SectorLeadershipRow:
    sector: str
    ticker_count: int
    average_technical_score: float | None
    average_fundamental_score: float | None
    top_25_count: int
    clean_pullback_count: int
    breakout_count: int
    vcp_count: int
    danger_count: int
    leadership_score: float
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketRegimeCommandCenterDto:
    as_of_date: date
    run_id: int | None
    calculation_version: str
    config_version: str | None
    regime: str
    risk_state: str
    score: float
    risk_off: bool
    gate_ok: bool
    confidence: str
    reasons: list[str]
    warnings: list[str]
    action_summary: str
    policy: MarketRegimePolicyDto
    index_health: dict[str, IndexHealthDto]
    universe_participation: MarketParticipationDto | None
    sector_leadership: list[SectorLeadershipRow]
    debug: dict[str, Any]
