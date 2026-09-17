"""Opt-in examples. Production resolvers/consumers are unchanged in T13A."""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from app.services.effective_configuration import (
    ConfigurationClassification as Classification,
)
from app.services.effective_configuration import (
    ConfigurationEntry,
    ConfigurationFamily,
    ConfigurationResolution,
    ConfigurationSource,
    EffectiveConfigurationSnapshot,
)
from app.services.effective_configuration import (
    ConfigurationSourceKind as SourceKind,
)
from app.services.effective_configuration import (
    ConfigurationValueType as ValueType,
)
from app.services.market_regime_policy import (
    MarketRegimeCommandCenterConfig,
    load_market_regime_command_center_config,
)
from app.services.ranking_profile_config import (
    RANKING_PROFILES_CONFIG_PATH,
    RankingProfileConfig,
    RankingProfileConfigError,
    _parse_profile,
    _validate_profile,
)
from app.services.technical_scoring_config import load_technical_scoring_v4_config


def _dataclass_values(value: Any, prefix: str = "") -> dict[str, Any]:
    result = {}
    for field in fields(value):
        item = getattr(value, field.name)
        key = prefix + field.name
        if is_dataclass(item):
            result.update(_dataclass_values(item, key + "."))
        else:
            result[key] = item
    return result


RANKING_RESOLUTION = ConfigurationResolution(
    "ranking_profile_config._parse_profile",
    "ranking-profile-resolution-v1",
    (SourceKind.PROFILE, SourceKind.CODE_DEFAULT),
)
RANKING_KEYS = (
    "enabled",
    "technical_weight",
    "fundamental_weight",
    "technical_components",
    "missing_data_policy.rescale_available",
    "missing_data_policy.penalty",
    "thresholds.strong_candidate_min_score",
    "thresholds.candidate_min_score",
    "thresholds.watch_min_score",
    "penalties",
    "gates",
    "tradeability_overlay.enabled",
    "tradeability_overlay.poor_penalty",
    "tradeability_overlay.very_poor_penalty",
    "tradeability_overlay.maximum_penalty",
    "tradeability_overlay.minimum_dollar_volume",
)
RANKING_FAMILY = ConfigurationFamily(
    "ranking.profile",
    "ranking-profile-v1",
    RANKING_RESOLUTION,
    tuple((key, Classification.BEHAVIORAL) for key in RANKING_KEYS)
    + tuple((key, Classification.OBSERVABILITY) for key in ("name", "label", "description")),
)
REGIME_FAMILY = ConfigurationFamily(
    "regime",
    "regime-effective-v1",
    ConfigurationResolution(
        "market_regime_policy.load_market_regime_command_center_config",
        "regime-resolution-v1",
        (SourceKind.PROFILE, SourceKind.CODE_DEFAULT),
    ),
    tuple(
        (key, Classification.BEHAVIORAL)
        for key in (
            "enabled",
            "calculation_version",
            "config_version",
            "symbols",
            "freshness",
            "risk_state_mapping",
            "market_regime_params",
            "policies",
            "feature.pine",
            "feature.v4",
        )
    ),
)
READINESS_FAMILY = ConfigurationFamily(
    "readiness.consumer-policy",
    "readiness-consumer-policy-v1",
    ConfigurationResolution(
        "readiness.named-policy", "readiness-policy-resolution-v1", (SourceKind.POLICY_VERSION,)
    ),
    tuple(
        (key, Classification.BEHAVIORAL)
        for key in (
            "producer",
            "consumer",
            "policy_version",
            "module",
            "required_readiness_version",
        )
    ),
)
CONFIGURATION_FAMILIES = (RANKING_FAMILY, REGIME_FAMILY, READINESS_FAMILY)


def snapshot_ranking_profile(
    profile: RankingProfileConfig,
    *,
    sources: dict[str, ConfigurationSource] | None = None,
) -> EffectiveConfigurationSnapshot:
    values = _dataclass_values(profile)
    entries = []
    for key, classification in RANKING_FAMILY.keys:
        source = (sources or {}).get(key, ConfigurationSource())
        value_type = ValueType.REAL if isinstance(values[key], float) else ValueType.STRUCTURE
        entries.append(
            ConfigurationEntry(
                key,
                values[key],
                classification,
                value_type,
                source,
                defaulted=source.kind is SourceKind.CODE_DEFAULT,
                overridden=source.kind is SourceKind.PROFILE,
            )
        )
    return EffectiveConfigurationSnapshot(RANKING_FAMILY, tuple(entries))


def resolve_ranking_profile(
    name: str,
    path: Path = RANKING_PROFILES_CONFIG_PATH,
    *,
    source_identifier: str | None = None,
) -> tuple[RankingProfileConfig, EffectiveConfigurationSnapshot]:
    # Relative repository paths are safe source references. Absolute local paths
    # need a caller-approved logical identifier; do not invent one from a filename.
    source_identifier = source_identifier or (path.as_posix() if not path.is_absolute() else None)
    # Read once; use the existing parser/validator, including validation of disabled profiles.
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, dict):
        raise RankingProfileConfigError("profiles must be a mapping")
    profiles = [_parse_profile(key, raw) for key, raw in raw_profiles.items()]
    for profile in profiles:
        _validate_profile(profile)
    enabled = [profile for profile in profiles if profile.enabled]
    if not enabled:
        raise RankingProfileConfigError("No enabled ranking profiles found")
    profile = next((p for p in enabled if p.name == name.strip()), None)
    if profile is None:
        raise RankingProfileConfigError("unknown ranking profile")
    raw = raw_profiles[profile.name]
    sources = {}
    for key in _dataclass_values(profile):
        source_path = {
            "technical_weight": "weights.technical",
            "fundamental_weight": "weights.fundamental",
        }.get(key, key)
        cursor = raw
        for part in source_path.split("."):
            cursor = cursor.get(part) if isinstance(cursor, dict) else None
        present = cursor is not None or key == "name"
        if key in {"gates", "penalties"}:
            present = isinstance(cursor, dict)
        sources[key] = ConfigurationSource(
            SourceKind.PROFILE if present else SourceKind.CODE_DEFAULT,
            source_identifier if present else "ranking_profile_config.parser-defaults",
        )
    return profile, snapshot_ranking_profile(profile, sources=sources)


def snapshot_regime_configuration(
    config: MarketRegimeCommandCenterConfig,
    *,
    pine: dict[str, Any],
    technical_v4: dict[str, Any],
) -> EffectiveConfigurationSnapshot:
    # Capture the native consumer fallbacks as effective values. Classifier constants
    # remain algorithm identity; feature-engine dependencies must be supplied explicitly.
    values = asdict(config)
    values["symbols"] = {
        **config.symbols,
        "primary_market": str(config.symbols["primary_market"]).strip().upper(),
        "risk_proxy": str(config.symbols.get("risk_proxy") or "").strip().upper(),
        "use_risk_proxy": bool(config.symbols.get("use_risk_proxy", True))
        and bool(str(config.symbols.get("risk_proxy") or "").strip()),
    }
    values["freshness"] = {
        **config.freshness,
        "max_stale_trading_days": int(config.freshness.get("max_stale_trading_days", 3)),
        "stale_data_risk_state": str(
            config.freshness.get("stale_data_risk_state") or "Gray"
        ).strip(),
    }
    values["market_regime_params"] = {
        **config.market_regime_params,
        **{
            key: bool(config.market_regime_params.get(key, True))
            for key in (
                "enabled",
                "use_qqq",
                "allow_unknown_market_low_confidence",
            )
        },
    }
    list_fields = (
        "preferred_profiles",
        "allowed_profiles",
        "reduced_profiles",
        "blocked_profiles",
        "allowed_setups",
        "blocked_setups",
        "warnings",
    )
    values["policies"] = {
        regime: {
            **policy,
            **{key: [str(item) for item in policy.get(key, [])] for key in list_fields},
            **{
                key: float(policy.get(key, 0.0))
                for key in (
                    "position_size_multiplier",
                    "minimum_score_adjustment",
                )
            },
            "summary": str(policy.get("summary") or "").strip(),
        }
        for regime, policy in config.policies.items()
    }
    values.update({"feature.pine": pine, "feature.v4": technical_v4})
    # Already resolved objects cannot prove field-level origins (mixed defaults/YAML).
    return EffectiveConfigurationSnapshot(
        REGIME_FAMILY,
        tuple(
            ConfigurationEntry(key, values[key], classification)
            for key, classification in REGIME_FAMILY.keys
        ),
        producer_version=config.calculation_version,
    )


def resolve_regime_configuration(
    path: Path = Path("config/market_regime_command_center.yaml"),
) -> tuple[MarketRegimeCommandCenterConfig, EffectiveConfigurationSnapshot]:
    from app.services.technical_indicators import load_pine_defaults

    config = load_market_regime_command_center_config(path)
    return config, snapshot_regime_configuration(
        config,
        pine=load_pine_defaults(),
        technical_v4=load_technical_scoring_v4_config(),
    )


def snapshot_readiness_policy(policy: Any) -> EffectiveConfigurationSnapshot:
    # Only repository policy types are accepted. Version identifies the code-defined
    # state/reason matrix; none of these named policies accept behavioral overrides.
    from app.services.contextual_consumer_eligibility import ContextualConsumerPolicy
    from app.services.technical_consumer_eligibility import TechnicalConsumerPolicy

    if not isinstance(policy, (ContextualConsumerPolicy, TechnicalConsumerPolicy)):
        raise TypeError("unsupported readiness policy authority")
    values = {key: getattr(policy, key, None) for key, _ in READINESS_FAMILY.keys}
    if isinstance(policy, TechnicalConsumerPolicy):
        values["producer"] = "TECHNICAL"
    return EffectiveConfigurationSnapshot(
        READINESS_FAMILY,
        tuple(
            ConfigurationEntry(
                key,
                values[key],
                classification,
                source=ConfigurationSource(
                    SourceKind.POLICY_VERSION, "readiness.named-policy", policy.policy_version
                ),
            )
            for key, classification in READINESS_FAMILY.keys
        ),
    )
