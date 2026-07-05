from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tables import RawCompanyRow, TechnicalScore
from app.services.pine_replica_engine import PineReplicaScore, score_from_feature_result
from app.services.price_bar_repository import load_preferred_ohlcv_frames
from app.services.relative_leadership import calculate_beta_adjusted_rs
from app.services.technical_indicators import (
    calculate_htf_trend_features,
    calculate_relative_strength_features,
    calculate_technical_features,
)
from app.services.technical_scoring_config import load_technical_scoring_v4_config


class TechnicalScoringError(ValueError):
    pass


def score_run_technicals(
    db: Session,
    run_id: int,
    tickers: list[str] | None = None,
    benchmark_ticker: str = "SPY",
) -> list[TechnicalScore]:
    symbols = _normalize_tickers(tickers or _tickers_for_run(db, run_id))
    v4_params = load_technical_scoring_v4_config()
    benchmark_price = _load_price_frame(db, benchmark_ticker)
    market_features = _market_features(benchmark_price, benchmark_ticker)
    qqq_market_features = _optional_market_features(db, "QQQ", v4_params)

    scores: list[TechnicalScore] = []
    for ticker in symbols:
        try:
            score = _score_ticker(
                db=db,
                ticker=ticker,
                benchmark_price=benchmark_price,
                market_features=market_features,
                qqq_market_features=qqq_market_features,
                v4_params=v4_params,
            )
            scores.append(build_technical_score(run_id=run_id, score=score))
        except Exception as exc:
            scores.append(unavailable_technical_score(run_id, ticker, str(exc)))

    if symbols:
        db.execute(
            delete(TechnicalScore).where(
                TechnicalScore.run_id == run_id,
                TechnicalScore.ticker.in_(symbols),
            )
        )
    db.add_all(scores)
    db.flush()
    return scores


def unavailable_technical_score(
    run_id: int,
    ticker: str,
    reason: str,
) -> TechnicalScore:
    return TechnicalScore(
        run_id=run_id,
        ticker=ticker.upper(),
        classification="No trade",
        action_bias="No data",
        technical_confidence="error",
        insufficient_data=True,
        missing_data_json={
            "unavailable": True,
            "reason": reason,
        },
        debug_json={
            "error": reason,
        },
    )


def build_technical_score(run_id: int, score: PineReplicaScore) -> TechnicalScore:
    confidence = score.technical_confidence or ("low" if score.insufficient_data else "normal")
    return TechnicalScore(
        run_id=run_id,
        ticker=score.ticker.upper(),
        trend_score=_to_decimal(score.trend_score),
        local_trend_score=_to_decimal(score.local_trend_score),
        momentum_score=_to_decimal(score.momentum_score),
        setup_score=_to_decimal(score.setup_score),
        risk_score=_to_decimal(score.risk_score),
        market_score=_to_decimal(score.market_score),
        relative_strength_score=_to_decimal(score.relative_strength_score),
        sector_relative_strength_score=_to_decimal(score.sector_relative_strength_score),
        combined_relative_strength_score=_to_decimal(
            score.combined_relative_strength_score
        ),
        htf_score=_to_decimal(score.htf_score),
        dual_score=_to_decimal(score.dual_score),
        classification=score.classification,
        pullback_health=score.pullback_health,
        action_bias=score.action_bias,
        suggested_stop=_to_decimal(score.suggested_stop),
        suggested_target=_to_decimal(score.suggested_target),
        reward_risk=_to_decimal(score.reward_risk),
        entry_risk_pct=_to_decimal(score.entry_risk_pct),
        technical_confidence=confidence,
        insufficient_data=score.insufficient_data,
        missing_data_json=score.missing_data,
        debug_json=score.debug,
    )


def _score_ticker(
    db: Session,
    ticker: str,
    benchmark_price: pd.DataFrame,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any] | None = None,
    v4_params: dict[str, Any] | None = None,
) -> PineReplicaScore:
    v4_params = v4_params or load_technical_scoring_v4_config()
    price, trades = load_preferred_ohlcv_frames(db, ticker)
    if price.empty:
        raise TechnicalScoringError(
            f"No cached OHLCV bars for {ticker.upper()}. Fetch IB data first."
        )

    features = calculate_technical_features(price, trades, ticker=ticker)
    htf_features = calculate_htf_trend_features(price) if not price.empty else {}
    relative_strength_features = _relative_strength_features(
        price,
        benchmark_price,
        v4_params.get("relative_leadership", {}),
    )

    return score_from_feature_result(
        features,
        htf_features=htf_features,
        relative_strength_features=relative_strength_features,
        market_features=market_features,
        qqq_market_features=qqq_market_features,
        v4_params=v4_params,
    )


def _relative_strength_features(
    price: pd.DataFrame,
    benchmark_price: pd.DataFrame,
    relative_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if price.empty or benchmark_price.empty:
        return {}
    features = calculate_relative_strength_features(price, benchmark_price)
    relative_params = relative_params or {}
    if relative_params.get("beta_adjusted_rs", False):
        features.update(calculate_beta_adjusted_rs(price, benchmark_price, relative_params))
    return features


def _market_features(price: pd.DataFrame, ticker: str) -> dict[str, Any]:
    if price.empty:
        return {}
    return calculate_technical_features(price, ticker=ticker).latest


def _load_price_frame(db: Session, ticker: str) -> pd.DataFrame:
    price, _ = load_preferred_ohlcv_frames(db, ticker)
    return price


def _optional_market_features(
    db: Session,
    ticker: str,
    v4_params: dict[str, Any],
) -> dict[str, Any]:
    market_regime_params = v4_params.get("market_regime_v4", {})
    if ticker.upper() == "QQQ" and not market_regime_params.get("use_qqq", True):
        return {}
    price = _load_price_frame(db, ticker)
    return _market_features(price, ticker)


def _tickers_for_run(db: Session, run_id: int) -> list[str]:
    return list(
        db.scalars(
            select(RawCompanyRow.ticker)
            .where(RawCompanyRow.run_id == run_id)
            .order_by(RawCompanyRow.row_number)
        )
    )


def _normalize_tickers(tickers: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for ticker in tickers:
        symbol = ticker.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            normalized.append(symbol)
    return normalized


def _to_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 4)))
