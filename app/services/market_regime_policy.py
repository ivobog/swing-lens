from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.services.market_regime import (
    REGIME_BEAR_RALLY,
    REGIME_BULL_PULLBACK,
    REGIME_BULL_TREND,
    REGIME_CHOPPY,
    REGIME_CORRECTION,
    REGIME_CRASH_RISK,
    REGIME_DISTRIBUTION,
    REGIME_RISK_ON_BREAKOUT,
    REGIME_UNKNOWN,
    MarketRegimeResult,
)

MARKET_REGIME_COMMAND_CENTER_CONFIG_PATH = Path(
    "config/market_regime_command_center.yaml"
)

SUPPORTED_REGIMES = (
    REGIME_BULL_TREND,
    REGIME_RISK_ON_BREAKOUT,
    REGIME_BULL_PULLBACK,
    REGIME_CHOPPY,
    REGIME_BEAR_RALLY,
    REGIME_DISTRIBUTION,
    REGIME_CORRECTION,
    REGIME_CRASH_RISK,
    REGIME_UNKNOWN,
)
VALID_RISK_STATES = frozenset({"Green", "Yellow", "Orange", "Red", "Gray"})


class MarketRegimePolicyConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MarketRegimeCommandCenterConfig:
    enabled: bool
    calculation_version: str
    config_version: str | None
    symbols: dict[str, Any]
    freshness: dict[str, Any]
    risk_state_mapping: dict[str, str]
    market_regime_params: dict[str, Any]
    policies: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class MarketDataFreshness:
    stale: bool = False
    severely_stale: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketRegimePolicyDto:
    regime: str
    risk_state: str
    position_size_multiplier: float
    preferred_profiles: list[str]
    allowed_profiles: list[str]
    reduced_profiles: list[str]
    blocked_profiles: list[str]
    allowed_setups: list[str]
    blocked_setups: list[str]
    minimum_score_adjustment: float
    summary: str
    warnings: list[str]


class MarketRegimePolicyService:
    def policy_for(
        self,
        regime_result: MarketRegimeResult,
        config: MarketRegimeCommandCenterConfig,
        freshness: MarketDataFreshness | None = None,
    ) -> MarketRegimePolicyDto:
        regime = regime_result.regime
        raw_policy = config.policies.get(regime) or config.policies[REGIME_UNKNOWN]
        risk_state = config.risk_state_mapping.get(
            regime,
            config.risk_state_mapping[REGIME_UNKNOWN],
        )

        warnings = _unique_list(_list(raw_policy.get("warnings")))
        for reason in regime_result.reasons:
            warnings = _append_unique(warnings, reason)
        if regime_result.risk_off:
            warnings = _append_unique(warnings, "market_risk_off")
        if regime_result.confidence == "low":
            warnings = _append_unique(warnings, "low_market_confidence")

        if freshness is not None:
            for warning in freshness.warnings:
                warnings = _append_unique(warnings, warning)
            if freshness.stale:
                warnings = _append_unique(warnings, "stale_market_data")
            if freshness.severely_stale:
                warnings = _append_unique(warnings, "severely_stale_market_data")
                risk_state = _configured_stale_risk_state(config)

        return MarketRegimePolicyDto(
            regime=regime if regime in config.policies else REGIME_UNKNOWN,
            risk_state=risk_state,
            position_size_multiplier=_float(raw_policy, "position_size_multiplier"),
            preferred_profiles=_list(raw_policy.get("preferred_profiles")),
            allowed_profiles=_list(raw_policy.get("allowed_profiles")),
            reduced_profiles=_list(raw_policy.get("reduced_profiles")),
            blocked_profiles=_list(raw_policy.get("blocked_profiles")),
            allowed_setups=_list(raw_policy.get("allowed_setups")),
            blocked_setups=_list(raw_policy.get("blocked_setups")),
            minimum_score_adjustment=_float(raw_policy, "minimum_score_adjustment"),
            summary=str(raw_policy.get("summary") or "").strip(),
            warnings=warnings,
        )


def load_market_regime_command_center_config(
    path: Path = MARKET_REGIME_COMMAND_CENTER_CONFIG_PATH,
) -> MarketRegimeCommandCenterConfig:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    config = MarketRegimeCommandCenterConfig(
        enabled=bool(_mapping(data, "engine").get("enabled", True)),
        calculation_version=_required_text(_mapping(data, "engine"), "engine.version"),
        config_version=_optional_text(_mapping(data, "engine").get("config_version")),
        symbols=_mapping(data, "symbols"),
        freshness=_mapping(data, "freshness"),
        risk_state_mapping=_risk_state_mapping(data),
        market_regime_params=_mapping(data, "market_regime_v4"),
        policies=_policies(data),
    )
    _validate_config(config)
    return config


def _validate_config(config: MarketRegimeCommandCenterConfig) -> None:
    _required_text(config.symbols, "symbols.primary_market")
    if not isinstance(config.symbols.get("use_risk_proxy", True), bool):
        raise MarketRegimePolicyConfigError("symbols.use_risk_proxy must be boolean")

    max_stale = config.freshness.get("max_stale_trading_days")
    if max_stale is None or _number(max_stale, "freshness.max_stale_trading_days") < 0:
        raise MarketRegimePolicyConfigError(
            "freshness.max_stale_trading_days must be non-negative"
        )

    stale_state = _configured_stale_risk_state(config)
    if stale_state not in VALID_RISK_STATES:
        raise MarketRegimePolicyConfigError(
            f"freshness.stale_data_risk_state must be one of {sorted(VALID_RISK_STATES)}"
        )

    missing_mappings = [
        regime for regime in SUPPORTED_REGIMES if regime not in config.risk_state_mapping
    ]
    if missing_mappings:
        raise MarketRegimePolicyConfigError(
            f"risk_state_mapping missing regime(s): {', '.join(missing_mappings)}"
        )

    missing_policies = [regime for regime in SUPPORTED_REGIMES if regime not in config.policies]
    if missing_policies:
        raise MarketRegimePolicyConfigError(
            f"policies missing regime(s): {', '.join(missing_policies)}"
        )

    for regime, risk_state in config.risk_state_mapping.items():
        if risk_state not in VALID_RISK_STATES:
            raise MarketRegimePolicyConfigError(
                f"risk_state_mapping.{regime} must be one of {sorted(VALID_RISK_STATES)}"
            )

    for regime, policy in config.policies.items():
        multiplier = _number(
            policy.get("position_size_multiplier"),
            f"policies.{regime}.position_size_multiplier",
        )
        if multiplier < 0 or multiplier > 1:
            raise MarketRegimePolicyConfigError(
                f"policies.{regime}.position_size_multiplier must be between 0 and 1"
            )

        minimum_score_adjustment = _number(
            policy.get("minimum_score_adjustment"),
            f"policies.{regime}.minimum_score_adjustment",
        )
        if minimum_score_adjustment < 0:
            raise MarketRegimePolicyConfigError(
                f"policies.{regime}.minimum_score_adjustment must be non-negative"
            )

        if not str(policy.get("summary") or "").strip():
            raise MarketRegimePolicyConfigError(f"policies.{regime}.summary is required")

        for field_name in (
            "preferred_profiles",
            "allowed_profiles",
            "reduced_profiles",
            "blocked_profiles",
            "allowed_setups",
            "blocked_setups",
        ):
            if not isinstance(policy.get(field_name, []), list):
                raise MarketRegimePolicyConfigError(
                    f"policies.{regime}.{field_name} must be a list"
                )


def _risk_state_mapping(data: dict[str, Any]) -> dict[str, str]:
    mapping = _mapping(data, "risk_state_mapping")
    return {str(regime): _risk_state(str(state), regime) for regime, state in mapping.items()}


def _policies(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    policies = _mapping(data, "policies")
    return {str(regime): dict(_mapping(policies, regime)) for regime in policies}


def _mapping(raw: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = raw.get(field_name)
    if not isinstance(value, dict):
        raise MarketRegimePolicyConfigError(f"{field_name} must be a mapping")
    return value


def _required_text(raw: dict[str, Any], field_name: str) -> str:
    key = field_name.split(".")[-1]
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MarketRegimePolicyConfigError(f"{field_name} is required")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


def _risk_state(value: str, regime: str) -> str:
    value = value.strip()
    if value not in VALID_RISK_STATES:
        raise MarketRegimePolicyConfigError(
            f"risk_state_mapping.{regime} must be one of {sorted(VALID_RISK_STATES)}"
        )
    return value


def _configured_stale_risk_state(config: MarketRegimeCommandCenterConfig) -> str:
    return str(config.freshness.get("stale_data_risk_state") or "Gray").strip()


def _number(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise MarketRegimePolicyConfigError(f"{field_name} must be numeric") from exc


def _float(raw: dict[str, Any], key: str) -> float:
    return float(raw.get(key, 0.0))


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _unique_list(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result = _append_unique(result, value)
    return result


def _append_unique(values: list[str], value: str) -> list[str]:
    cleaned = str(value).strip()
    if cleaned and cleaned not in values:
        return [*values, cleaned]
    return values
