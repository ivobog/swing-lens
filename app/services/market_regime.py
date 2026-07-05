from dataclasses import dataclass
from typing import Any

REGIME_BULL_TREND = "Bull trend"
REGIME_BULL_PULLBACK = "Bull pullback"
REGIME_RISK_ON_BREAKOUT = "Risk-on breakout"
REGIME_CHOPPY = "Choppy"
REGIME_DISTRIBUTION = "Distribution"
REGIME_CORRECTION = "Correction"
REGIME_BEAR_RALLY = "Bear rally"
REGIME_CRASH_RISK = "Crash risk"
REGIME_UNKNOWN = "Unknown"


@dataclass(frozen=True)
class MarketRegimeResult:
    regime: str
    score: float
    risk_off: bool
    gate_ok: bool
    confidence: str
    reasons: list[str]


def classify_market_regime(
    spy_features: dict[str, Any] | None,
    qqq_features: dict[str, Any] | None,
    params: dict[str, Any],
) -> MarketRegimeResult:
    if not params.get("enabled", True):
        return MarketRegimeResult(REGIME_UNKNOWN, 5.0, False, True, "low", ["disabled"])
    if not spy_features:
        return MarketRegimeResult(
            REGIME_UNKNOWN,
            0.0,
            True,
            False,
            "low",
            ["missing_spy_market_data"],
        )

    use_qqq = bool(params.get("use_qqq", True))
    qqq_missing = use_qqq and not qqq_features
    spy = _snapshot(spy_features)
    qqq = _snapshot(qqq_features or {}) if qqq_features else None

    score = _market_score(spy)
    if qqq is not None:
        score = round(score * 0.65 + _market_score(qqq) * 0.35, 4)

    reasons: list[str] = []
    if qqq_missing:
        reasons.append("missing_qqq_market_data")
    if spy["distribution_count"] >= 4:
        reasons.append("spy_distribution")
    if qqq is not None and qqq["distribution_count"] >= 4:
        reasons.append("qqq_distribution")

    regime = _regime_from_snapshots(spy, qqq)
    risk_off = regime in {
        REGIME_DISTRIBUTION,
        REGIME_CORRECTION,
        REGIME_CRASH_RISK,
    }
    gate_ok = not risk_off and regime != REGIME_UNKNOWN
    confidence = "normal"
    if qqq_missing and params.get("allow_unknown_market_low_confidence", True):
        confidence = "low"

    return MarketRegimeResult(
        regime=regime,
        score=max(0.0, min(10.0, score)),
        risk_off=risk_off,
        gate_ok=gate_ok,
        confidence=confidence,
        reasons=reasons,
    )


def _regime_from_snapshots(
    spy: dict[str, float | bool],
    qqq: dict[str, float | bool] | None,
) -> str:
    risk_proxy = qqq or spy
    if spy["below_sma200"] and spy["roc21"] <= -8 and spy["distribution_count"] >= 4:
        return REGIME_CRASH_RISK
    if spy["distribution_count"] >= 4 or risk_proxy["distribution_count"] >= 4:
        return REGIME_DISTRIBUTION
    if spy["below_sma50"] and spy["roc21"] < 0 and spy["distribution_count"] >= 3:
        return REGIME_CORRECTION
    if spy["below_sma200"] and spy["roc21"] > 0:
        return REGIME_BEAR_RALLY
    if spy["above_sma50"] and spy["above_sma200"] and risk_proxy["above_sma50"]:
        if spy["donchian_20_breakout"] or risk_proxy["donchian_20_breakout"]:
            return REGIME_RISK_ON_BREAKOUT
        return REGIME_BULL_TREND
    if spy["above_sma200"] and spy["below_sma50"] and spy["roc63"] > 0:
        return REGIME_BULL_PULLBACK
    return REGIME_CHOPPY


def _market_score(snapshot: dict[str, float | bool]) -> float:
    score = 0.0
    score += 2.5 if snapshot["above_sma200"] else 0.0
    score += 2.0 if snapshot["above_sma50"] else 0.0
    score += 1.5 if snapshot["sma50_above_sma200"] else 0.0
    score += 1.5 if snapshot["sma50_slope_pct"] > 0 else 0.0
    score += 1.0 if snapshot["roc21"] > 0 else 0.0
    score += 1.0 if snapshot["roc63"] > 0 else 0.0
    score -= 2.0 if snapshot["distribution_count"] >= 4 else 0.0
    return max(0.0, min(10.0, round(score, 4)))


def _snapshot(features: dict[str, Any]) -> dict[str, float | bool]:
    close = _num(features.get("close"))
    sma50 = _num(features.get("sma50"))
    sma200 = _num(features.get("sma200"))
    return {
        "above_sma50": close > sma50,
        "below_sma50": close < sma50,
        "above_sma200": close > sma200,
        "below_sma200": close < sma200,
        "sma50_above_sma200": sma50 > sma200,
        "sma50_slope_pct": _num(features.get("sma50_slope_pct")),
        "roc21": _num(features.get("roc21")),
        "roc63": _num(features.get("roc63")),
        "distribution_count": _num(features.get("distribution_count")),
        "donchian_20_breakout": bool(features.get("donchian_20_breakout")),
    }


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
