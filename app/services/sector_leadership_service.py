from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    FundamentalScore,
    RankingResult,
    RawCompanyRow,
    TechnicalScore,
)
from app.services.combined_decision import DANGER_CLASSIFICATIONS
from app.services.market_participation_service import (
    CLEAN_PULLBACK_CLASSIFICATIONS,
    FRESH_BREAKOUT_CLASSIFICATIONS,
    _is_vcp,
)
from app.services.market_regime_dtos import SectorLeadershipRow


class SectorLeadershipService:
    def build(self, db: Session, run_id: int) -> list[SectorLeadershipRow]:
        raw_rows = _raw_rows_for_run(db, run_id)
        technicals = _technicals_for_run(db, run_id)
        fundamentals = _fundamentals_for_run(db, run_id)
        rankings = _ranking_results_for_run(db, run_id)
        sector_by_ticker = _sector_by_ticker(raw_rows, technicals, fundamentals, rankings)

        buckets: dict[str, _SectorBucket] = {}
        for ticker, sector in sector_by_ticker.items():
            buckets.setdefault(sector, _SectorBucket(sector=sector)).tickers.add(ticker)

        for technical in technicals:
            bucket = _bucket_for(buckets, sector_by_ticker, technical.ticker)
            bucket.technical_scores.append(technical.dual_score)
            if technical.classification in CLEAN_PULLBACK_CLASSIFICATIONS:
                bucket.clean_pullback_count += 1
            if technical.classification in FRESH_BREAKOUT_CLASSIFICATIONS:
                bucket.breakout_count += 1
            if _is_vcp(technical):
                bucket.vcp_count += 1
            if technical.classification in DANGER_CLASSIFICATIONS:
                bucket.danger_count += 1

        for fundamental in fundamentals:
            bucket = _bucket_for(buckets, sector_by_ticker, fundamental.ticker)
            bucket.fundamental_scores.append(fundamental.fundamental_score)

        for ranking in rankings:
            if ranking.profile_rank <= 25:
                bucket = _bucket_for(buckets, sector_by_ticker, ranking.ticker)
                bucket.top_25_tickers.add(ranking.ticker.upper())

        rows = [_to_row(bucket) for bucket in buckets.values()]
        return sorted(rows, key=lambda row: (-row.leadership_score, row.sector))


@dataclass
class _SectorBucket:
    sector: str
    tickers: set[str] = field(default_factory=set)
    technical_scores: list[Any] = field(default_factory=list)
    fundamental_scores: list[Any] = field(default_factory=list)
    top_25_tickers: set[str] = field(default_factory=set)
    clean_pullback_count: int = 0
    breakout_count: int = 0
    vcp_count: int = 0
    danger_count: int = 0


def _raw_rows_for_run(db: Session, run_id: int) -> list[RawCompanyRow]:
    return list(
        db.scalars(
            select(RawCompanyRow)
            .where(RawCompanyRow.run_id == run_id)
            .order_by(RawCompanyRow.row_number)
        )
    )


def _technicals_for_run(db: Session, run_id: int) -> list[TechnicalScore]:
    return list(db.scalars(select(TechnicalScore).where(TechnicalScore.run_id == run_id)))


def _fundamentals_for_run(db: Session, run_id: int) -> list[FundamentalScore]:
    return list(
        db.scalars(select(FundamentalScore).where(FundamentalScore.run_id == run_id))
    )


def _ranking_results_for_run(db: Session, run_id: int) -> list[RankingResult]:
    return list(db.scalars(select(RankingResult).where(RankingResult.run_id == run_id)))


def _sector_by_ticker(
    raw_rows: list[RawCompanyRow],
    technicals: list[TechnicalScore],
    fundamentals: list[FundamentalScore],
    rankings: list[RankingResult],
) -> dict[str, str]:
    sectors: dict[str, str] = {}
    for raw in raw_rows:
        sectors[raw.ticker.upper()] = _sector(raw.sector)
    for ranking in rankings:
        sectors.setdefault(ranking.ticker.upper(), _sector(ranking.sector))
    for technical in technicals:
        sectors.setdefault(technical.ticker.upper(), "Unknown")
    for fundamental in fundamentals:
        sectors.setdefault(fundamental.ticker.upper(), "Unknown")
    return sectors


def _bucket_for(
    buckets: dict[str, _SectorBucket],
    sector_by_ticker: dict[str, str],
    ticker: str,
) -> _SectorBucket:
    sector = sector_by_ticker.get(ticker.upper(), "Unknown")
    bucket = buckets.setdefault(sector, _SectorBucket(sector=sector))
    bucket.tickers.add(ticker.upper())
    return bucket


def _to_row(bucket: _SectorBucket) -> SectorLeadershipRow:
    ticker_count = len(bucket.tickers)
    average_technical = _average(bucket.technical_scores)
    average_fundamental = _average(bucket.fundamental_scores)
    leadership_score = _leadership_score(
        ticker_count=ticker_count,
        average_technical=average_technical,
        average_fundamental=average_fundamental,
        top_25_count=len(bucket.top_25_tickers),
        setup_count=(
            bucket.clean_pullback_count
            + bucket.breakout_count
            + bucket.vcp_count
        ),
        danger_count=bucket.danger_count,
    )
    warnings = []
    if bucket.sector == "Unknown":
        warnings.append("missing_sector")

    return SectorLeadershipRow(
        sector=bucket.sector,
        ticker_count=ticker_count,
        average_technical_score=average_technical,
        average_fundamental_score=average_fundamental,
        top_25_count=len(bucket.top_25_tickers),
        clean_pullback_count=bucket.clean_pullback_count,
        breakout_count=bucket.breakout_count,
        vcp_count=bucket.vcp_count,
        danger_count=bucket.danger_count,
        leadership_score=leadership_score,
        warnings=warnings,
    )


def _leadership_score(
    ticker_count: int,
    average_technical: float | None,
    average_fundamental: float | None,
    top_25_count: int,
    setup_count: int,
    danger_count: int,
) -> float:
    if ticker_count <= 0:
        return 0.0
    technical_component = average_technical if average_technical is not None else 0.0
    fundamental_component = average_fundamental if average_fundamental is not None else 0.0
    top_25_share_score = _clamp(top_25_count / ticker_count * 10)
    setup_density_score = _clamp(setup_count / ticker_count * 10)
    risk_control_score = _clamp((1 - danger_count / ticker_count) * 10)
    return _clamp(
        technical_component * 0.35
        + fundamental_component * 0.20
        + top_25_share_score * 0.20
        + setup_density_score * 0.15
        + risk_control_score * 0.10
    )


def _sector(value: str | None) -> str:
    text = str(value or "").strip()
    return text or "Unknown"


def _average(values: list[Any]) -> float | None:
    numbers = [_float_or_none(value) for value in values]
    available = [value for value in numbers if value is not None]
    if not available:
        return None
    return round(sum(available) / len(available), 4)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))
