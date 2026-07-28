from pathlib import Path

import pytest
import yaml

from app.services.market_regime import (
    REGIME_BULL_TREND,
    REGIME_CHOPPY,
    REGIME_CRASH_RISK,
    REGIME_DISTRIBUTION,
    REGIME_UNKNOWN,
    MarketRegimeResult,
)
from app.services.market_regime_policy import (
    MarketDataFreshness,
    MarketRegimePolicyConfigError,
    MarketRegimePolicyService,
    load_market_regime_command_center_config,
)


def test_load_market_regime_command_center_config_covers_supported_regimes() -> None:
    config = load_market_regime_command_center_config()

    assert config.enabled is True
    assert config.calculation_version == "mrcc-1.0.0"
    assert config.symbols["primary_market"] == "SPY"
    assert config.symbols["risk_proxy"] == "QQQ"
    assert config.risk_state_mapping[REGIME_BULL_TREND] == "Green"
    assert config.risk_state_mapping[REGIME_UNKNOWN] == "Gray"
    assert REGIME_CRASH_RISK in config.policies


def test_bull_trend_maps_to_green_and_normal_size() -> None:
    policy = _policy_for(_result(REGIME_BULL_TREND, score=9.0))

    assert policy.regime == REGIME_BULL_TREND
    assert policy.risk_state == "Green"
    assert policy.position_size_multiplier == 1.0
    assert "momentum_swing" in policy.preferred_profiles
    assert policy.warnings == []


def test_choppy_maps_to_yellow_and_blocks_early_rocket() -> None:
    policy = _policy_for(_result(REGIME_CHOPPY, score=5.0))

    assert policy.risk_state == "Yellow"
    assert policy.position_size_multiplier == 0.5
    assert "early_rocket" in policy.blocked_profiles
    assert "defensive_quality" in policy.preferred_profiles


def test_distribution_adds_market_risk_off_warning() -> None:
    policy = _policy_for(
        _result(
            REGIME_DISTRIBUTION,
            score=3.0,
            risk_off=True,
            gate_ok=False,
            reasons=["spy_distribution"],
        )
    )

    assert policy.risk_state == "Orange"
    assert policy.position_size_multiplier == 0.25
    assert "market_risk_off" in policy.warnings
    assert "spy_distribution" in policy.warnings


def test_crash_risk_blocks_all_new_long_entries() -> None:
    policy = _policy_for(
        _result(REGIME_CRASH_RISK, score=0.0, risk_off=True, gate_ok=False)
    )

    assert policy.risk_state == "Red"
    assert policy.position_size_multiplier == 0.0
    assert policy.allowed_profiles == []
    assert policy.blocked_setups == ["*"]


def test_unknown_maps_to_gray_and_defensive_only() -> None:
    policy = _policy_for(
        _result(
            REGIME_UNKNOWN,
            score=0.0,
            gate_ok=False,
            confidence="low",
            reasons=["missing_spy_market_data"],
        )
    )

    assert policy.risk_state == "Gray"
    assert policy.position_size_multiplier == 0.25
    assert policy.allowed_profiles == ["defensive_quality"]
    assert "low_market_confidence" in policy.warnings
    assert "missing_spy_market_data" in policy.warnings


def test_unknown_regime_string_uses_unknown_policy() -> None:
    policy = _policy_for(_result("Odd weather", score=4.0))

    assert policy.regime == REGIME_UNKNOWN
    assert policy.risk_state == "Gray"
    assert policy.allowed_profiles == ["defensive_quality"]


def test_severely_stale_data_forces_configured_stale_risk_state() -> None:
    policy = _policy_for(
        _result(REGIME_BULL_TREND, score=9.0),
        freshness=MarketDataFreshness(stale=True, severely_stale=True),
    )

    assert policy.risk_state == "Gray"
    assert "stale_market_data" in policy.warnings
    assert "severely_stale_market_data" in policy.warnings


def test_bad_config_rejects_out_of_range_multiplier(tmp_path: Path) -> None:
    config = yaml.safe_load(
        Path("config/market_regime_command_center.yaml").read_text(encoding="utf-8")
    )
    config["policies"][REGIME_BULL_TREND]["position_size_multiplier"] = 1.5
    path = tmp_path / "market_regime_command_center.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(
        MarketRegimePolicyConfigError,
        match="position_size_multiplier must be between 0 and 1",
    ):
        load_market_regime_command_center_config(path)


def _policy_for(
    result: MarketRegimeResult,
    freshness: MarketDataFreshness | None = None,
):
    config = load_market_regime_command_center_config()
    return MarketRegimePolicyService().policy_for(result, config, freshness=freshness)


def _result(
    regime: str,
    score: float = 7.0,
    risk_off: bool = False,
    gate_ok: bool = True,
    confidence: str = "normal",
    reasons: list[str] | None = None,
) -> MarketRegimeResult:
    return MarketRegimeResult(
        regime=regime,
        score=score,
        risk_off=risk_off,
        gate_ok=gate_ok,
        confidence=confidence,
        reasons=reasons or [],
    )
