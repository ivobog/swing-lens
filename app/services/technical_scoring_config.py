from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

TECHNICAL_SCORING_V4_CONFIG_PATH = Path("config/technical_scoring_v4.yaml")

DEFAULT_TECHNICAL_SCORING_V4_CONFIG: dict[str, Any] = {
    "engine": {
        "version": "4.0.0",
        "base_engine_version": "3.2.0",
        "enabled": True,
        "keep_v3_debug": True,
    },
    "data_confidence": {
        "require_market_data": True,
        "require_benchmark_data": True,
        "require_htf_data": True,
        "low_confidence_on_missing_optional_data": True,
        "minimum_data_quality_score": 7.0,
    },
    "adaptive_percentiles": {
        "enabled": True,
        "long_lookback": 252,
        "medium_lookback": 126,
        "atr_contraction_percentile": 35,
        "atr_expansion_percentile": 80,
        "volume_breakout_percentile": 70,
        "volume_climax_percentile": 90,
        "range_climax_percentile": 90,
        "extension_danger_percentile": 90,
    },
    "volatility_contraction": {
        "enabled": True,
        "bb_length": 20,
        "bb_stddev": 2.0,
        "kc_length": 20,
        "kc_atr_multiple": 1.5,
        "squeeze_enabled": True,
        "vcp_min_score": 7.0,
        "tight_close_lookback_short": 5,
        "tight_close_lookback_long": 10,
        "tight_close_max_pct": 2.0,
    },
    "donchian_darvas": {
        "enabled": True,
        "donchian_short_len": 20,
        "donchian_long_len": 55,
        "box_lookback": 20,
        "max_box_width_pct": 15.0,
        "min_box_age": 7,
        "breakout_volume_percentile_min": 70,
        "breakout_close_ratio_min": 0.65,
    },
    "stage_analysis": {
        "enabled": True,
        "allow_prime_only_in_stage_2": True,
        "allow_stage_1_to_2_transition": True,
        "stage3_distribution_min_count": 3,
    },
    "relative_leadership": {
        "enabled": True,
        "benchmark_symbols": ["SPY", "QQQ"],
        "beta_adjusted_rs": True,
        "beta_lookbacks": [63, 126],
        "run_percentiles": True,
        "leadership_min_percentile": 70,
    },
    "market_regime_v4": {
        "enabled": True,
        "use_spy": True,
        "use_qqq": True,
        "allow_unknown_market_low_confidence": True,
    },
    "climax_risk": {
        "enabled": True,
        "rsi_warning": 75,
        "rsi_danger": 80,
        "vertical_move_3d_atr": 3.0,
        "vertical_move_5d_atr": 4.5,
        "climax_risk_threshold": 7.0,
    },
    "regime_weights": {
        "bull_trend": {
            "trend": 0.22,
            "momentum": 0.20,
            "setup": 0.20,
            "leadership": 0.14,
            "risk_control": 0.10,
            "market": 0.08,
            "execution": 0.06,
        },
        "choppy": {
            "trend": 0.18,
            "momentum": 0.10,
            "setup": 0.24,
            "leadership": 0.10,
            "risk_control": 0.22,
            "market": 0.10,
            "execution": 0.06,
        },
        "risk_off": {
            "trend": 0.10,
            "momentum": 0.05,
            "setup": 0.10,
            "leadership": 0.05,
            "risk_control": 0.40,
            "market": 0.25,
            "execution": 0.05,
        },
    },
    "classification_v4": {
        "danger_priority": [
            "Failed breakout",
            "Blowoff top",
            "Climax reversal risk",
            "Distribution risk",
            "Late-stage extension",
        ],
        "buyable_classifications": [
            "Prime clean pullback",
            "Clean bull pullback",
            "Fresh breakout",
            "Volatility contraction setup",
            "Tight base breakout",
            "RS leader pullback",
        ],
        "sub_tags": [
            "VCP",
            "Darvas box",
            "Donchian breakout",
            "Stage 2",
            "RS leader",
        ],
    },
}


def load_technical_scoring_v4_config(
    path: Path = TECHNICAL_SCORING_V4_CONFIG_PATH,
) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        file_config = yaml.safe_load(handle) or {}

    config = _deep_merge(DEFAULT_TECHNICAL_SCORING_V4_CONFIG, file_config)
    _validate_regime_weights(config)
    return config


def _deep_merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _validate_regime_weights(config: dict[str, Any]) -> None:
    regimes = config.get("regime_weights", {})
    if not isinstance(regimes, dict):
        raise ValueError("regime_weights must be a mapping")

    for regime, weights in regimes.items():
        if not isinstance(weights, dict):
            raise ValueError(f"regime_weights.{regime} must be a mapping")
        total = round(sum(float(value) for value in weights.values()), 6)
        if total != 1.0:
            raise ValueError(f"regime_weights.{regime} must sum to 1.0, got {total}")
