import json
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict, replace
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tables import RawCompanyRow, TechnicalScore
from app.services.operational_metrics import operational_metrics
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
from app.services.technical_work import (
    TechnicalWorkItem,
    TechnicalWorkResult,
    build_technical_work_item,
    execute_technical_work_item,
)
from app.settings import Settings, get_settings


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

    settings = get_settings()
    if settings.technical_process_pool_enabled:
        score_results = _score_tickers_process_pool(
            db=db,
            symbols=symbols,
            benchmark_price=benchmark_price,
            sector_price=sector_price,
            market_features=market_features,
            qqq_market_features=qqq_market_features,
            pine_params=pine_params,
            v4_params=v4_params,
            settings=settings,
            run_id=run_id,
        )
    elif (
        settings.technical_pure_boundary_enabled
        or settings.technical_pure_boundary_shadow_compare_enabled
    ):
        score_results = _score_tickers_pure_sequential(
            db=db,
            symbols=symbols,
            benchmark_price=benchmark_price,
            sector_price=sector_price,
            market_features=market_features,
            qqq_market_features=qqq_market_features,
            pine_params=pine_params,
            v4_params=v4_params,
            shadow_compare=settings.technical_pure_boundary_shadow_compare_enabled,
            run_id=run_id,
        )
    else:
        score_results = _score_tickers_legacy(
            db=db,
            symbols=symbols,
            benchmark_price=benchmark_price,
            sector_price=sector_price,
            market_features=market_features,
            qqq_market_features=qqq_market_features,
            v4_params=v4_params,
            run_id=run_id,
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


def _score_tickers_legacy(
    *,
    db: Session,
    symbols: list[str],
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any],
    v4_params: dict[str, Any],
    run_id: int,
) -> list[PineReplicaScore | TechnicalScore]:
    score_results: list[PineReplicaScore | TechnicalScore] = []
    for ticker in symbols:
        try:
            score_results.append(
                _score_ticker(
                    db=db,
                    ticker=ticker,
                    benchmark_price=benchmark_price,
                    sector_price=sector_price,
                    market_features=market_features,
                    qqq_market_features=qqq_market_features,
                    v4_params=v4_params,
                )
            )
        except Exception as exc:
            score_results.append(
                unavailable_technical_score(run_id, ticker, str(exc), v4_params=v4_params)
            )
    operational_metrics.increment(
        "swinglens_technical_scoring_runs_total",
        mode="legacy",
    )
    return score_results


def _score_tickers_pure_sequential(
    *,
    db: Session,
    symbols: list[str],
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any],
    pine_params: dict[str, Any],
    v4_params: dict[str, Any],
    shadow_compare: bool,
    run_id: int,
) -> list[PineReplicaScore | TechnicalScore]:
    results: list[PineReplicaScore | TechnicalScore] = []
    for ticker in symbols:
        try:
            price, trades = load_preferred_ohlcv_frames(db, ticker)
            item = _build_work_item(
                ticker=ticker,
                price=price,
                trades=trades,
                benchmark_price=benchmark_price,
                sector_price=sector_price,
                market_features=market_features,
                qqq_market_features=qqq_market_features,
                pine_params=pine_params,
                v4_params=v4_params,
            )
            pure_result = execute_technical_work_item(item)
            pure_score = _score_from_work_result(pure_result)
            if shadow_compare:
                legacy_score = _legacy_score_or_error(
                    db=db,
                    ticker=ticker,
                    benchmark_price=benchmark_price,
                    sector_price=sector_price,
                    market_features=market_features,
                    qqq_market_features=qqq_market_features,
                    v4_params=v4_params,
                    run_id=run_id,
                )
                if _technical_score_fingerprint(pure_score) != _technical_score_fingerprint(
                    legacy_score
                ):
                    operational_metrics.increment(
                        "swinglens_technical_pure_boundary_shadow_mismatches_total"
                    )
                    pure_score = legacy_score
            results.append(pure_score)
        except Exception as exc:
            results.append(
                unavailable_technical_score(run_id, ticker, str(exc), v4_params=v4_params)
            )
    operational_metrics.increment(
        "swinglens_technical_scoring_runs_total",
        mode="pure_sequential_shadow" if shadow_compare else "pure_sequential",
    )
    return results


def _score_tickers_process_pool(
    *,
    db: Session,
    symbols: list[str],
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any],
    pine_params: dict[str, Any],
    v4_params: dict[str, Any],
    settings: Settings,
    run_id: int,
) -> list[PineReplicaScore | TechnicalScore]:
    items: list[tuple[int, TechnicalWorkItem]] = []
    results: list[PineReplicaScore | TechnicalScore | None] = [None] * len(symbols)
    for index, ticker in enumerate(symbols):
        try:
            price, trades = load_preferred_ohlcv_frames(db, ticker)
            items.append(
                (
                    index,
                    _build_work_item(
                        ticker=ticker,
                        price=price,
                        trades=trades,
                        benchmark_price=benchmark_price,
                        sector_price=sector_price,
                        market_features=market_features,
                        qqq_market_features=qqq_market_features,
                        pine_params=pine_params,
                        v4_params=v4_params,
                    ),
                )
            )
        except Exception as exc:
            results[index] = unavailable_technical_score(
                run_id, ticker, str(exc), v4_params=v4_params
            )

    try:
        workers = _technical_worker_count(settings)
        in_flight_limit = min(settings.technical_max_in_flight, workers * 2)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            pending = {}
            next_item = iter(items)
            while len(pending) < in_flight_limit:
                try:
                    index, item = next(next_item)
                except StopIteration:
                    break
                pending[executor.submit(execute_technical_work_item, item)] = (index, item)

            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    index, item = pending.pop(future)
                    try:
                        results[index] = _score_from_work_result(future.result())
                    except Exception as exc:
                        results[index] = unavailable_technical_score(
                            run_id,
                            item.ticker,
                            str(exc),
                            v4_params=v4_params,
                        )
                    try:
                        next_index, next_item_value = next(next_item)
                    except StopIteration:
                        continue
                    pending[executor.submit(execute_technical_work_item, next_item_value)] = (
                        next_index,
                        next_item_value,
                    )
    except Exception as exc:
        operational_metrics.increment(
            "swinglens_technical_process_pool_fallback_total",
            reason=type(exc).__name__,
        )
        return _score_tickers_pure_sequential(
            db=db,
            symbols=symbols,
            benchmark_price=benchmark_price,
            sector_price=sector_price,
            market_features=market_features,
            qqq_market_features=qqq_market_features,
            pine_params=pine_params,
            v4_params=v4_params,
            shadow_compare=False,
            run_id=run_id,
        )

    operational_metrics.increment(
        "swinglens_technical_scoring_runs_total",
        mode="process_pool",
        workers=_technical_worker_count(settings),
    )
    return [
        result
        if result is not None
        else unavailable_technical_score(
            run_id, symbols[index], "Technical worker returned no result.", v4_params=v4_params
        )
        for index, result in enumerate(results)
    ]


def _build_work_item(
    *,
    ticker: str,
    price: pd.DataFrame,
    trades: pd.DataFrame | None,
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any],
    pine_params: dict[str, Any],
    v4_params: dict[str, Any],
) -> TechnicalWorkItem:
    return build_technical_work_item(
        ticker=ticker,
        price=price,
        trades=trades,
        benchmark_price=benchmark_price,
        sector_price=sector_price,
        technical_config=v4_params,
        pine_config=pine_params,
        relative_config=v4_params.get("relative_leadership", {}),
        market_features=market_features,
        qqq_market_features=qqq_market_features,
    )


def _score_from_work_result(result: TechnicalWorkResult) -> PineReplicaScore:
    if result.score is None:
        raise TechnicalScoringError(result.error or "Technical worker failed.")
    return result.score


def _legacy_score_or_error(
    *,
    db: Session,
    ticker: str,
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any],
    v4_params: dict[str, Any],
    run_id: int,
) -> PineReplicaScore | TechnicalScore:
    try:
        return _score_ticker(
            db=db,
            ticker=ticker,
            benchmark_price=benchmark_price,
            sector_price=sector_price,
            market_features=market_features,
            qqq_market_features=qqq_market_features,
            v4_params=v4_params,
        )
    except Exception as exc:
        return unavailable_technical_score(run_id, ticker, str(exc), v4_params=v4_params)


def _technical_score_fingerprint(score: PineReplicaScore | TechnicalScore) -> str:
    if isinstance(score, TechnicalScore):
        payload = {
            key: getattr(score, key)
            for key in (
                "ticker",
                "trend_score",
                "local_trend_score",
                "momentum_score",
                "setup_score",
                "risk_score",
                "market_score",
                "relative_strength_score",
                "sector_relative_strength_score",
                "combined_relative_strength_score",
                "htf_score",
                "dual_score",
                "classification",
                "action_bias",
                "pullback_health",
                "suggested_stop",
                "suggested_target",
                "reward_risk",
                "entry_risk_pct",
                "technical_confidence",
                "technical_engine_version",
                "data_quality_score",
                "stage",
                "market_regime",
                "leadership_score",
                "vcp_score",
                "box_tightness_score",
                "breakout_quality_score",
                "climax_risk_score",
                "atr_percentile_252",
                "volume_percentile_252",
                "range_percentile_252",
                "extension_percentile_252",
                "feature_flags_json",
                "warning_flags_json",
                "sub_tags_json",
                "v4_debug_json",
                "insufficient_data",
                "missing_data_json",
                "debug_json",
            )
        }
    else:
        payload = asdict(score)
    return json.dumps(payload, default=str, sort_keys=True)


def _technical_worker_count(settings: Settings) -> int:
    return settings.technical_worker_processes


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
