from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.services.price_bar_repository import load_preferred_ohlcv_frames
from app.services.sector_rotation_dtos import (
    SectorEtfRotationMetrics,
    SectorUniverseMetrics,
)
from app.services.technical_indicators import (
    calculate_relative_strength_features,
    calculate_technical_features,
)


class SectorEtfRotationService:
    def build(
        self,
        db: Session,
        universe_rows: list[SectorUniverseMetrics],
        config: dict[str, Any],
    ) -> list[SectorEtfRotationMetrics]:
        if not bool(config.get("etf_score", {}).get("enabled", False)):
            return []

        benchmark_ticker = str(config["etf_score"]["benchmark_ticker"]).strip().upper()
        benchmark_price, _benchmark_volume = load_preferred_ohlcv_frames(db, benchmark_ticker)
        return [
            self._build_sector_metrics(
                db=db,
                universe=universe,
                benchmark_ticker=benchmark_ticker,
                benchmark_price=benchmark_price,
                config=config,
            )
            for universe in universe_rows
        ]

    def _build_sector_metrics(
        self,
        db: Session,
        universe: SectorUniverseMetrics,
        benchmark_ticker: str,
        benchmark_price: pd.DataFrame,
        config: dict[str, Any],
    ) -> SectorEtfRotationMetrics:
        proxy_ticker = str(config.get("sector_etf_proxies", {}).get(universe.sector) or "")
        warnings: list[str] = []
        if not proxy_ticker:
            return SectorEtfRotationMetrics(
                sector=universe.sector,
                sector_slug=universe.sector_slug,
                proxy_ticker="",
                benchmark_ticker=benchmark_ticker,
                as_of_date=None,
                etf_rotation_score=None,
                warnings=["missing_sector_etf_proxy"],
                debug={"missing_proxy": True},
            )

        price, volume = load_preferred_ohlcv_frames(db, proxy_ticker)
        if price.empty:
            return SectorEtfRotationMetrics(
                sector=universe.sector,
                sector_slug=universe.sector_slug,
                proxy_ticker=proxy_ticker,
                benchmark_ticker=benchmark_ticker,
                as_of_date=None,
                etf_rotation_score=None,
                warnings=[f"missing_{proxy_ticker.lower()}_etf_data"],
                debug={"missing_proxy_data": True},
            )

        feature_result = calculate_technical_features(price, volume, ticker=proxy_ticker)
        latest = feature_result.latest
        if feature_result.insufficient_data:
            warnings.append("insufficient_etf_history")
        if feature_result.missing_data.get("missing_columns"):
            warnings.append("missing_etf_ohlcv_columns")

        rs_features: dict[str, Any] = {}
        if benchmark_price.empty:
            warnings.append(f"missing_{benchmark_ticker.lower()}_benchmark_data")
        else:
            rs_features = calculate_relative_strength_features(price, benchmark_price)

        component_scores = _component_scores(latest, rs_features)
        score = _weighted_score(component_scores, config["etf_score"]["weights"])
        metrics = _metrics_payload(
            latest=latest,
            rs_features=rs_features,
            benchmark_ticker=benchmark_ticker,
        )
        if bool(metrics.get("risk_off")):
            warnings.append("sector_etf_risk_off")

        return SectorEtfRotationMetrics(
            sector=universe.sector,
            sector_slug=universe.sector_slug,
            proxy_ticker=proxy_ticker,
            benchmark_ticker=benchmark_ticker,
            as_of_date=_frame_as_of_date(price),
            etf_rotation_score=score,
            component_scores=component_scores,
            metrics=metrics,
            warnings=_unique(warnings),
            debug={
                "proxy_debug": feature_result.debug,
                "missing_data": feature_result.missing_data,
                "score_weights": dict(config["etf_score"]["weights"]),
            },
        )


def _component_scores(latest: dict[str, Any], rs_features: dict[str, Any]) -> dict[str, float]:
    return {
        "trend": _trend_score(latest),
        "relative_strength": _relative_strength_score(rs_features),
        "momentum": _momentum_score(latest),
        "breakout": _breakout_score(latest),
        "risk_control": _risk_control_score(latest),
    }


def _trend_score(latest: dict[str, Any]) -> float:
    close = _num(latest.get("close"))
    sma50 = _num(latest.get("sma50"))
    sma200 = _num(latest.get("sma200"))
    sma50_slope = _num(latest.get("sma50_slope_pct"))
    sma200_slope = _num(latest.get("sma200_slope_pct"))
    score = 0.0
    score += 2.5 if close is not None and sma200 is not None and close > sma200 else 0.0
    score += 2.0 if close is not None and sma50 is not None and close > sma50 else 0.0
    score += 2.0 if sma50 is not None and sma200 is not None and sma50 > sma200 else 0.0
    score += 2.0 if sma50_slope is not None and sma50_slope > 0 else 0.0
    score += 1.5 if sma200_slope is not None and sma200_slope >= 0 else 0.0
    return _clamp(score)


def _relative_strength_score(rs_features: dict[str, Any]) -> float:
    if not rs_features:
        return 0.0
    rs_line = _num(rs_features.get("benchmark_rs_line"))
    rs_sma = _num(rs_features.get("benchmark_rs_sma"))
    score = 0.0
    score += 3.0 if rs_line is not None and rs_sma is not None and rs_line > rs_sma else 0.0
    score += 2.0 if _positive(rs_features.get("benchmark_rs_roc21")) else 0.0
    score += 2.0 if _positive(rs_features.get("benchmark_rs_roc63")) else 0.0
    score += 1.5 if _positive(rs_features.get("benchmark_rs_roc126")) else 0.0
    score += 1.5 if bool(rs_features.get("benchmark_rs_new_high")) else 0.0
    return _clamp(score)


def _momentum_score(latest: dict[str, Any]) -> float:
    score = 0.0
    score += 3.0 if _positive(latest.get("roc21")) else 0.0
    score += 3.0 if _positive(latest.get("roc63")) else 0.0
    score += 2.0 if _positive(latest.get("roc126")) else 0.0
    rsi = _num(latest.get("rsi14"))
    score += 2.0 if rsi is not None and rsi >= 50 else 0.0
    return _clamp(score)


def _breakout_score(latest: dict[str, Any]) -> float:
    score = 0.0
    score += 5.0 if bool(latest.get("donchian_20_breakout")) else 0.0
    score += 5.0 if bool(latest.get("donchian_55_breakout")) else 0.0
    return _clamp(score)


def _risk_control_score(latest: dict[str, Any]) -> float:
    score = 10.0
    distribution = _num(latest.get("distribution_count")) or 0.0
    atr_pct = _num(latest.get("atr_pct")) or 0.0
    if distribution >= 4:
        score -= 4.0
    elif distribution >= 3:
        score -= 2.0
    if bool(latest.get("distribution_risk")):
        score -= 2.0
    if bool(latest.get("failed_breakout")):
        score -= 2.0
    if atr_pct >= 8:
        score -= 1.0
    return _clamp(score)


def _weighted_score(component_scores: dict[str, float], weights: dict[str, Any]) -> float:
    return _clamp(
        sum(
            float(component_scores.get(name, 0.0)) * float(weight)
            for name, weight in weights.items()
        )
    )


def _metrics_payload(
    latest: dict[str, Any],
    rs_features: dict[str, Any],
    benchmark_ticker: str,
) -> dict[str, Any]:
    close = _num(latest.get("close"))
    sma50 = _num(latest.get("sma50"))
    sma200 = _num(latest.get("sma200"))
    distribution = _num(latest.get("distribution_count"))
    roc21 = _num(latest.get("roc21"))
    return {
        "close": close,
        "sma50": sma50,
        "sma200": sma200,
        "above_sma50": _greater_than(close, sma50),
        "above_sma200": _greater_than(close, sma200),
        "sma50_above_sma200": _greater_than(sma50, sma200),
        "sma50_slope_pct": _num(latest.get("sma50_slope_pct")),
        "sma200_slope_pct": _num(latest.get("sma200_slope_pct")),
        "roc21": roc21,
        "roc63": _num(latest.get("roc63")),
        "roc126": _num(latest.get("roc126")),
        "atr_pct": _num(latest.get("atr_pct")),
        "distribution_count": distribution,
        "donchian_20_breakout": _bool_or_none(latest.get("donchian_20_breakout")),
        "donchian_55_breakout": _bool_or_none(latest.get("donchian_55_breakout")),
        "relative_strength_benchmark": benchmark_ticker,
        "rs_line": _num(rs_features.get("benchmark_rs_line")),
        "rs_sma": _num(rs_features.get("benchmark_rs_sma")),
        "rs_roc21": _num(rs_features.get("benchmark_rs_roc21")),
        "rs_roc63": _num(rs_features.get("benchmark_rs_roc63")),
        "rs_roc126": _num(rs_features.get("benchmark_rs_roc126")),
        "rs_new_high": _bool_or_none(rs_features.get("benchmark_rs_new_high")),
        "risk_off": bool(
            (close is not None and sma200 is not None and close < sma200 and (roc21 or 0.0) < 0)
            or (distribution is not None and distribution >= 4)
        ),
    }


def _frame_as_of_date(frame: pd.DataFrame) -> str | None:
    if frame.empty or "date" not in frame:
        return None
    return pd.to_datetime(frame["date"].iloc[-1]).date().isoformat()


def _greater_than(left: float | None, right: float | None) -> bool | None:
    if left is None or right is None:
        return None
    return left > right


def _positive(value: Any) -> bool:
    number = _num(value)
    return bool(number is not None and number > 0)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))
