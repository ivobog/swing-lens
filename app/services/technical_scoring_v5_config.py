from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

TECHNICAL_SCORING_V5_CONFIG_PATH = Path("config/technical_scoring_v5.yaml")


def load_technical_scoring_v5_config(
    path: Path = TECHNICAL_SCORING_V5_CONFIG_PATH,
) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    config = deepcopy(config)
    _validate(config)
    return config


def _validate(config: dict[str, Any]) -> None:
    if str(_mapping(config, "engine").get("version")) != "5.0.0":
        raise ValueError("engine.version must be 5.0.0")
    for path in (
        ("technical_strength", "weights"),
        ("leadership", "weights"),
        ("entry_quality", "weights"),
        ("execution", "weights"),
        ("sector_benchmarks", "benchmark_rs_mix"),
    ):
        _validate_weights(_nested(config, *path), ".".join(path))
    for key in ("bull_trend", "choppy", "risk_off"):
        _validate_weights(_nested(config, "composite", key), f"composite.{key}")
    trend = _mapping(config, "trend")
    if round(float(trend.get("local_weight", 0)) + float(trend.get("htf_weight", 0)), 8) != 1:
        raise ValueError("trend weights must sum to 1.0")
    momentum = _mapping(config, "momentum")
    if (
        round(
            float(momentum.get("base_weight", 0)) + float(momentum.get("acceleration_weight", 0)), 8
        )
        != 1
    ):
        raise ValueError("momentum weights must sum to 1.0")
    supported = set(_nested(config, "setup_quality", "supported_types"))
    required = {
        "pullback",
        "vcp",
        "breakout",
        "momentum_continuation",
        "extended_momentum",
        "trend_repair",
        "none",
    }
    if supported != required:
        raise ValueError("setup_quality.supported_types must contain the seven v5 setup types")
    if not 0 <= float(_nested(config, "risk", "secondary_risk_weight")) <= 1:
        raise ValueError("risk.secondary_risk_weight must be between 0 and 1")


def _validate_weights(weights: Any, path: str) -> None:
    if not isinstance(weights, dict) or not weights:
        raise ValueError(f"{path} must be a non-empty mapping")
    if any(float(value) < 0 for value in weights.values()):
        raise ValueError(f"{path} values must be non-negative")
    total = round(sum(float(value) for value in weights.values()), 8)
    if total != 1.0:
        raise ValueError(f"{path} must sum to 1.0, got {total}")


def _mapping(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value


def _nested(config: dict[str, Any], *keys: str) -> Any:
    value: Any = config
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"{'.'.join(keys)} is required")
        value = value[key]
    return value
