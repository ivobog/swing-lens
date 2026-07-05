from app.services.market_regime import (
    REGIME_BULL_PULLBACK,
    REGIME_BULL_TREND,
    REGIME_CRASH_RISK,
    REGIME_DISTRIBUTION,
    REGIME_RISK_ON_BREAKOUT,
    REGIME_UNKNOWN,
    classify_market_regime,
)


def test_classify_market_regime_unknown_without_spy() -> None:
    result = classify_market_regime(None, None, _params())

    assert result.regime == REGIME_UNKNOWN
    assert result.confidence == "low"
    assert result.gate_ok is False
    assert result.reasons == ["missing_spy_market_data"]


def test_classify_market_regime_bull_trend() -> None:
    result = classify_market_regime(_features(), _features(), _params())

    assert result.regime == REGIME_BULL_TREND
    assert result.score >= 8.0
    assert result.risk_off is False
    assert result.gate_ok is True


def test_classify_market_regime_risk_on_breakout() -> None:
    result = classify_market_regime(
        _features(donchian_20_breakout=True),
        _features(),
        _params(),
    )

    assert result.regime == REGIME_RISK_ON_BREAKOUT


def test_classify_market_regime_bull_pullback_with_missing_qqq_low_confidence() -> None:
    result = classify_market_regime(
        _features(close=105, sma50=110, sma200=100, roc21=-2, roc63=5),
        None,
        _params(),
    )

    assert result.regime == REGIME_BULL_PULLBACK
    assert result.confidence == "low"
    assert result.reasons == ["missing_qqq_market_data"]


def test_classify_market_regime_distribution_and_crash_risk() -> None:
    distribution = classify_market_regime(
        _features(distribution_count=4),
        _features(),
        _params(),
    )
    crash = classify_market_regime(
        _features(close=80, sma50=95, sma200=100, roc21=-10, distribution_count=4),
        _features(),
        _params(),
    )

    assert distribution.regime == REGIME_DISTRIBUTION
    assert distribution.risk_off is True
    assert crash.regime == REGIME_CRASH_RISK
    assert crash.gate_ok is False


def _params() -> dict:
    return {
        "enabled": True,
        "use_spy": True,
        "use_qqq": True,
        "allow_unknown_market_low_confidence": True,
    }


def _features(
    close: float = 120,
    sma50: float = 110,
    sma200: float = 100,
    sma50_slope_pct: float = 2,
    roc21: float = 3,
    roc63: float = 8,
    distribution_count: float = 0,
    donchian_20_breakout: bool = False,
) -> dict:
    return {
        "close": close,
        "sma50": sma50,
        "sma200": sma200,
        "sma50_slope_pct": sma50_slope_pct,
        "roc21": roc21,
        "roc63": roc63,
        "distribution_count": distribution_count,
        "donchian_20_breakout": donchian_20_breakout,
    }
