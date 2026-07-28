from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import RankingResult, RawCompanyRow, TechnicalScore
from app.services.combined_decision import DANGER_CLASSIFICATIONS
from app.services.market_regime_dtos import MarketParticipationDto

CLEAN_PULLBACK_CLASSIFICATIONS = {
    "Prime clean pullback",
    "Clean bull pullback",
}
FRESH_BREAKOUT_CLASSIFICATIONS = {
    "Fresh breakout",
    "Tight base breakout",
}
VCP_CLASSIFICATIONS = {
    "Volatility contraction setup",
}
VCP_SCORE_MIN = 7.0


class MarketParticipationService:
    def build(self, db: Session, run_id: int) -> MarketParticipationDto:
        raw_rows = _raw_rows_for_run(db, run_id)
        technicals = _technicals_for_run(db, run_id)
        rankings = _ranking_results_for_run(db, run_id)
        ticker_count = _ticker_count(raw_rows, technicals)

        notes: list[str] = []
        if ticker_count == 0:
            notes.append("No run universe rows are available.")
        if not technicals:
            notes.append("No technical scores are available.")

        above_sma50_values = [_above_sma(technical, "sma50") for technical in technicals]
        above_sma200_values = [_above_sma(technical, "sma200") for technical in technicals]
        above_sma50_pct = _pct_true(above_sma50_values)
        above_sma200_pct = _pct_true(above_sma200_values)
        if technicals and above_sma50_pct is None:
            notes.append("SMA50 participation is unavailable in technical debug data.")
        if technicals and above_sma200_pct is None:
            notes.append("SMA200 participation is unavailable in technical debug data.")

        return MarketParticipationDto(
            ticker_count=ticker_count,
            technical_count=len(technicals),
            average_technical_score=_average([technical.dual_score for technical in technicals]),
            clean_pullback_count=sum(_is_clean_pullback(technical) for technical in technicals),
            fresh_breakout_count=sum(_is_fresh_breakout(technical) for technical in technicals),
            vcp_count=sum(_is_vcp(technical) for technical in technicals),
            danger_count=sum(_is_danger(technical) for technical in technicals),
            market_risk_warning_count=sum(
                _has_warning(technical.warning_flags_json, "market_risk_off")
                for technical in technicals
            ),
            above_sma50_pct=above_sma50_pct,
            above_sma200_pct=above_sma200_pct,
            ranking_profile_average_scores=_ranking_profile_averages(rankings),
            notes=notes,
        )


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


def _ranking_results_for_run(db: Session, run_id: int) -> list[RankingResult]:
    return list(db.scalars(select(RankingResult).where(RankingResult.run_id == run_id)))


def _ticker_count(
    raw_rows: list[RawCompanyRow],
    technicals: list[TechnicalScore],
) -> int:
    raw_tickers = {row.ticker.upper() for row in raw_rows if row.ticker}
    if raw_tickers:
        return len(raw_tickers)
    return len({technical.ticker.upper() for technical in technicals if technical.ticker})


def _is_clean_pullback(technical: TechnicalScore) -> bool:
    return technical.classification in CLEAN_PULLBACK_CLASSIFICATIONS


def _is_fresh_breakout(technical: TechnicalScore) -> bool:
    return technical.classification in FRESH_BREAKOUT_CLASSIFICATIONS


def _is_vcp(technical: TechnicalScore) -> bool:
    if technical.classification in VCP_CLASSIFICATIONS:
        return True
    if _score(technical.vcp_score) >= VCP_SCORE_MIN:
        return True
    flags = technical.feature_flags_json or []
    return "vcp_detected" in flags


def _is_danger(technical: TechnicalScore) -> bool:
    return technical.classification in DANGER_CLASSIFICATIONS


def _has_warning(flags: list[str] | None, warning: str) -> bool:
    return warning in (flags or [])


def _above_sma(technical: TechnicalScore, sma_key: str) -> bool | None:
    derived = _derived_debug(technical)
    explicit = derived.get(f"above_{sma_key}")
    if explicit is not None:
        return bool(explicit)

    close = _float_or_none(derived.get("close"))
    sma = _float_or_none(derived.get(sma_key))
    if close is None or sma is None:
        return None
    return close > sma


def _derived_debug(technical: TechnicalScore) -> dict[str, Any]:
    debug = technical.debug_json if isinstance(technical.debug_json, dict) else {}
    derived = debug.get("derived")
    if isinstance(derived, dict):
        return derived

    v4_debug = technical.v4_debug_json if isinstance(technical.v4_debug_json, dict) else {}
    derived = v4_debug.get("derived")
    return derived if isinstance(derived, dict) else {}


def _ranking_profile_averages(rankings: list[RankingResult]) -> dict[str, float]:
    grouped: dict[str, list[Any]] = {}
    for ranking in rankings:
        grouped.setdefault(ranking.ranking_profile, []).append(ranking.profile_score)
    return {
        profile: average
        for profile, values in grouped.items()
        if (average := _average(values)) is not None
    }


def _pct_true(values: list[bool | None]) -> float | None:
    available = [value for value in values if value is not None]
    if not available:
        return None
    return round(sum(bool(value) for value in available) / len(available) * 100, 2)


def _average(values: list[Any]) -> float | None:
    numbers = [_float_or_none(value) for value in values]
    available = [value for value in numbers if value is not None]
    if not available:
        return None
    return round(sum(available) / len(available), 4)


def _score(value: Any) -> float:
    return _float_or_none(value) or 0.0


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
