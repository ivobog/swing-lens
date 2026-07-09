from dataclasses import asdict, replace
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tables import RawCompanyRow, TechnicalScore
from app.services.pine_replica_engine import PineReplicaScore, score_from_feature_result
from app.services.price_bar_repository import load_preferred_ohlcv_frames
from app.services.relative_leadership import calculate_beta_adjusted_rs, rank_technical_universe
from app.services.technical_explainability import add_leadership_to_explainability
from app.services.technical_indicators import (
    calculate_htf_trend_features,
    calculate_relative_strength_features,
    calculate_technical_features,
    load_pine_defaults,
)
from app.services.technical_score_v4 import (
    TechnicalScoreV4,
    technical_score_v4_from_base_score,
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
    pine_params = load_pine_defaults()
    benchmark_price = _load_price_frame(db, benchmark_ticker)
    market_features = _market_features(benchmark_price, benchmark_ticker)
    sector_price = _sector_benchmark_price(db, pine_params)
    qqq_market_features = _optional_market_features(db, "QQQ", v4_params)

    score_results: list[PineReplicaScore | TechnicalScore] = []
    for ticker in symbols:
        try:
            score = _score_ticker(
                db=db,
                ticker=ticker,
                benchmark_price=benchmark_price,
                sector_price=sector_price,
                market_features=market_features,
                qqq_market_features=qqq_market_features,
                v4_params=v4_params,
            )
            score_results.append(score)
        except Exception as exc:
            score_results.append(
                unavailable_technical_score(
                    run_id,
                    ticker,
                    str(exc),
                    v4_params=v4_params,
                )
            )

    scored = [
        result
        for result in score_results
        if isinstance(result, PineReplicaScore)
    ]
    leadership = rank_technical_universe(
        [_leadership_rank_input(score) for score in scored],
        v4_params.get("relative_leadership", {}),
    )
    scores = [
        build_technical_score(
            run_id=run_id,
            score=technical_score_v4_from_base_score(
                _with_leadership_debug(result, leadership),
                v4_params,
            ),
        )
        if isinstance(result, PineReplicaScore)
        else result
        for result in score_results
    ]

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
    v4_params: dict[str, Any] | None = None,
) -> TechnicalScore:
    error_payload = _v4_error_payload(reason, v4_params or {})
    return TechnicalScore(
        run_id=run_id,
        ticker=ticker.upper(),
        classification="No trade",
        action_bias="No data",
        technical_confidence="error",
        technical_engine_version=error_payload["engine_version"],
        data_quality_score=Decimal("0.0"),
        feature_flags_json=[],
        warning_flags_json=["technical_error"],
        sub_tags_json=[],
        v4_debug_json=error_payload,
        insufficient_data=True,
        missing_data_json={
            "unavailable": True,
            "reason": reason,
            "technical_error": True,
        },
        debug_json={
            "error": reason,
            "explainability": error_payload,
        },
    )


def build_technical_score(
    run_id: int,
    score: PineReplicaScore | TechnicalScoreV4,
) -> TechnicalScore:
    base_score = _base_score(score)
    debug = score.debug if isinstance(score, TechnicalScoreV4) else base_score.debug
    dual_score = (
        score.final_v4_score if isinstance(score, TechnicalScoreV4) else base_score.dual_score
    )
    classification = (
        score.final_v4_classification
        if isinstance(score, TechnicalScoreV4)
        else base_score.classification
    )
    action_bias = (
        score.final_v4_action if isinstance(score, TechnicalScoreV4) else base_score.action_bias
    )
    confidence = base_score.technical_confidence or (
        "low" if base_score.insufficient_data else "normal"
    )
    v4_fields = _v4_persistence_fields(debug)
    return TechnicalScore(
        run_id=run_id,
        ticker=base_score.ticker.upper(),
        trend_score=_to_decimal(base_score.trend_score),
        local_trend_score=_to_decimal(base_score.local_trend_score),
        momentum_score=_to_decimal(base_score.momentum_score),
        setup_score=_to_decimal(base_score.setup_score),
        risk_score=_to_decimal(base_score.risk_score),
        market_score=_to_decimal(base_score.market_score),
        relative_strength_score=_to_decimal(base_score.relative_strength_score),
        sector_relative_strength_score=_to_decimal(
            base_score.sector_relative_strength_score
        ),
        combined_relative_strength_score=_to_decimal(
            base_score.combined_relative_strength_score
        ),
        htf_score=_to_decimal(base_score.htf_score),
        dual_score=_to_decimal(dual_score),
        classification=classification,
        pullback_health=base_score.pullback_health,
        action_bias=action_bias,
        suggested_stop=_to_decimal(base_score.suggested_stop),
        suggested_target=_to_decimal(base_score.suggested_target),
        reward_risk=_to_decimal(base_score.reward_risk),
        entry_risk_pct=_to_decimal(base_score.entry_risk_pct),
        technical_confidence=confidence,
        **v4_fields,
        insufficient_data=base_score.insufficient_data,
        missing_data_json=base_score.missing_data,
        debug_json=debug,
    )


def _score_ticker(
    db: Session,
    ticker: str,
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
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
        sector_price,
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
    sector_price: pd.DataFrame | None = None,
    relative_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if price.empty or benchmark_price.empty:
        return {}
    features = calculate_relative_strength_features(price, benchmark_price, sector_price)
    relative_params = relative_params or {}
    if relative_params.get("beta_adjusted_rs", False):
        features.update(calculate_beta_adjusted_rs(price, benchmark_price, relative_params))
    return features


def _sector_benchmark_price(
    db: Session,
    pine_params: dict[str, Any],
) -> pd.DataFrame | None:
    market_rs = pine_params.get("market_rs", {})
    if not market_rs.get("useSectorBenchmark", False):
        return None

    sector_symbol = str(market_rs.get("sectorSymbol") or "").strip().upper()
    if not sector_symbol:
        return None
    return _load_price_frame(db, sector_symbol)


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


def _leadership_rank_input(score: PineReplicaScore) -> dict[str, Any]:
    derived = score.debug.get("derived", {}) if score.debug else {}
    return {
        "ticker": score.ticker,
        "roc21": derived.get("stock_roc_short"),
        "roc63": derived.get("stock_roc_medium"),
        "roc126": derived.get("stock_roc_long"),
        "benchmark_rs_score": score.relative_strength_score,
        "dual_score": score.dual_score,
        "setup_score": score.setup_score,
    }


def _with_leadership_debug(
    score: PineReplicaScore,
    leadership: dict[str, Any],
) -> PineReplicaScore:
    leadership_result = leadership.get(score.ticker.upper())
    if leadership_result is None:
        return score
    debug = {
        **(score.debug or {}),
        "leadership": asdict(leadership_result),
    }
    debug = add_leadership_to_explainability(debug, leadership_result)
    return replace(score, debug=debug)


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


def _base_score(score: PineReplicaScore | TechnicalScoreV4) -> PineReplicaScore:
    return score.base_score if isinstance(score, TechnicalScoreV4) else score


def _v4_persistence_fields(debug: dict[str, Any] | None) -> dict[str, Any]:
    explainability = _dict((debug or {}).get("explainability"))
    adaptive = _dict(explainability.get("adaptive"))
    contraction = _dict(explainability.get("contraction"))
    box = _dict(explainability.get("box"))
    stage = _dict(explainability.get("stage"))
    regime = _dict(explainability.get("regime"))
    leadership = _dict(explainability.get("leadership"))
    climax = _dict(explainability.get("climax"))
    data_readiness = _dict(explainability.get("data_readiness"))

    return {
        "technical_engine_version": explainability.get("engine_version"),
        "data_quality_score": _to_decimal(data_readiness.get("data_quality_score")),
        "stage": stage.get("stage"),
        "market_regime": regime.get("regime"),
        "leadership_score": _to_decimal(leadership.get("leadership_score")),
        "vcp_score": _to_decimal(contraction.get("vcp_score")),
        "box_tightness_score": _to_decimal(box.get("box_tightness_score")),
        "breakout_quality_score": _to_decimal(box.get("breakout_quality_score")),
        "climax_risk_score": _to_decimal(climax.get("climax_risk_score")),
        "atr_percentile_252": _to_decimal(adaptive.get("atr_percentile_252")),
        "volume_percentile_252": _to_decimal(adaptive.get("volume_percentile_252")),
        "range_percentile_252": _to_decimal(adaptive.get("range_percentile_252")),
        "extension_percentile_252": _to_decimal(
            adaptive.get("extension_percentile_252")
        ),
        "feature_flags_json": _list_or_none(explainability.get("feature_flags")),
        "warning_flags_json": _list_or_none(explainability.get("warning_flags")),
        "sub_tags_json": _list_or_none(explainability.get("sub_tags")),
        "v4_debug_json": explainability or None,
    }


def _v4_error_payload(reason: str, v4_params: dict[str, Any]) -> dict[str, Any]:
    engine = _dict(v4_params.get("engine"))
    engine_version = str(engine.get("version") or "4.0.0")
    return {
        "engine_version": engine_version,
        "data_readiness": {
            "confidence": "error",
            "data_quality_score": 0.0,
            "missing_reasons": ["technical_error"],
        },
        "adaptive": {},
        "contraction": {},
        "box": {},
        "stage": {"stage": "Unknown"},
        "regime": {"regime": "Unknown"},
        "leadership": None,
        "climax": {},
        "feature_flags": [],
        "warning_flags": ["technical_error"],
        "sub_tags": [],
        "final_v4_score": None,
        "final_v4_classification": "No trade",
        "final_v4_action": "No data",
        "error": {"reason": reason},
        "debug": {
            "score_source": "technical_error",
            "error": reason,
        },
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_or_none(value: Any) -> list[Any] | None:
    return value if isinstance(value, list) else None
