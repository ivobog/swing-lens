from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

SECTOR_ROTATION_CONFIG_PATH = Path("config/sector_rotation.yaml")

REQUIRED_SECTIONS = (
    "version",
    "defaults",
    "sector_taxonomy",
    "sector_etf_proxies",
    "universe_score",
    "etf_score",
    "combined_score",
    "rotation_states",
    "permissions",
)

VALID_MISSING_ETF_POLICIES = frozenset({"use_universe_only", "null_combined_score"})
REQUIRED_MARKET_BUCKETS = ("supportive", "choppy", "risk_off", "unknown")
REQUIRED_ROTATION_STATES = (
    "Leading",
    "Improving",
    "Neutral",
    "Fading",
    "Lagging",
    "Crowded risk",
    "Risk-off",
    "Insufficient data",
)


class SectorRotationConfigError(ValueError):
    pass


def load_sector_rotation_config(
    path: Path = SECTOR_ROTATION_CONFIG_PATH,
) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    if not isinstance(config, dict):
        raise SectorRotationConfigError("sector rotation config must be a mapping")

    _validate_config(config)
    return config


def sector_rotation_config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_config(config: dict[str, Any]) -> None:
    for section in REQUIRED_SECTIONS:
        if section not in config:
            raise SectorRotationConfigError(f"{section} is required")

    _required_text(config, "version")
    _validate_defaults(_mapping(config, "defaults"))
    _validate_taxonomy(_mapping(config, "sector_taxonomy"))
    _validate_etf_proxies(
        _mapping(config, "sector_etf_proxies"),
        _canonical_sectors(config),
    )
    _validate_universe_score(_mapping(config, "universe_score"))
    _validate_etf_score(_mapping(config, "etf_score"))
    _validate_combined_score(_mapping(config, "combined_score"))
    _validate_rotation_states(_mapping(config, "rotation_states"))
    _validate_permissions(_mapping(config, "permissions"))


def _validate_defaults(defaults: dict[str, Any]) -> None:
    _required_text(defaults, "default_ranking_profile")
    _required_text(defaults, "unknown_sector_label")

    cutoffs = defaults.get("top_candidate_cutoffs")
    if not isinstance(cutoffs, list) or not cutoffs:
        raise SectorRotationConfigError("defaults.top_candidate_cutoffs must be a non-empty list")
    for cutoff in cutoffs:
        if int(_number(cutoff, "defaults.top_candidate_cutoffs")) <= 0:
            raise SectorRotationConfigError(
                "defaults.top_candidate_cutoffs values must be positive"
            )

    normal = _number(
        defaults.get("min_tickers_for_normal_confidence"),
        "defaults.min_tickers_for_normal_confidence",
    )
    high = _number(
        defaults.get("min_tickers_for_high_confidence"),
        "defaults.min_tickers_for_high_confidence",
    )
    if normal < 0 or high < 0:
        raise SectorRotationConfigError("confidence ticker thresholds must be non-negative")
    if high < normal:
        raise SectorRotationConfigError(
            "defaults.min_tickers_for_high_confidence must be >= normal threshold"
        )


def _validate_taxonomy(taxonomy: dict[str, Any]) -> None:
    canonical = taxonomy.get("canonical")
    if not isinstance(canonical, list) or not canonical:
        raise SectorRotationConfigError("sector_taxonomy.canonical must be a non-empty list")

    canonical_labels = [_text(value) for value in canonical]
    if any(not value for value in canonical_labels):
        raise SectorRotationConfigError("sector_taxonomy.canonical values must be text")
    if len(set(canonical_labels)) != len(canonical_labels):
        raise SectorRotationConfigError("sector_taxonomy.canonical contains duplicates")
    if "Unknown" not in canonical_labels:
        raise SectorRotationConfigError("sector_taxonomy.canonical must include Unknown")

    aliases = taxonomy.get("aliases", {})
    if not isinstance(aliases, dict):
        raise SectorRotationConfigError("sector_taxonomy.aliases must be a mapping")
    canonical_set = set(canonical_labels)
    for alias, target in aliases.items():
        if not _text(alias):
            raise SectorRotationConfigError("sector_taxonomy.aliases keys must be text")
        target_text = _text(target)
        if target_text not in canonical_set:
            raise SectorRotationConfigError(
                f"sector_taxonomy.aliases.{alias} must resolve to a canonical sector"
            )

    tradingview_map = taxonomy.get("tradingview_map", {})
    if not isinstance(tradingview_map, dict):
        raise SectorRotationConfigError("sector_taxonomy.tradingview_map must be a mapping")
    for source, target in tradingview_map.items():
        if not _text(source):
            raise SectorRotationConfigError("sector_taxonomy.tradingview_map keys must be text")
        target_text = _text(target)
        if target_text not in canonical_set:
            raise SectorRotationConfigError(
                f"sector_taxonomy.tradingview_map.{source} must resolve to a canonical sector"
            )

    statuses = taxonomy.get("mapping_statuses", [])
    if statuses:
        status_set = set(_text_list(statuses, "sector_taxonomy.mapping_statuses"))
        required_statuses = {"mapped", "canonical", "missing", "unmapped"}
        if not required_statuses.issubset(status_set):
            raise SectorRotationConfigError(
                "sector_taxonomy.mapping_statuses must include mapped, canonical, "
                "missing, and unmapped"
            )


def _validate_etf_proxies(proxies: dict[str, Any], canonical_sectors: set[str]) -> None:
    if not proxies:
        raise SectorRotationConfigError("sector_etf_proxies must not be empty")
    for sector, ticker in proxies.items():
        if str(sector) not in canonical_sectors:
            raise SectorRotationConfigError(
                f"sector_etf_proxies.{sector} must be a canonical sector"
            )
        if not _text(ticker):
            raise SectorRotationConfigError(f"sector_etf_proxies.{sector} ticker is required")


def _validate_universe_score(universe_score: dict[str, Any]) -> None:
    _validate_weights(_mapping(universe_score, "weights"), "universe_score.weights")
    setup_labels = _mapping(universe_score, "setup_labels")
    for bucket in ("buyable", "watch", "danger"):
        _text_list(setup_labels.get(bucket), f"universe_score.setup_labels.{bucket}")

    warning_flags = _mapping(universe_score, "warning_flags")
    for bucket in ("danger", "caution"):
        _text_list(warning_flags.get(bucket), f"universe_score.warning_flags.{bucket}")


def _validate_etf_score(etf_score: dict[str, Any]) -> None:
    if not isinstance(etf_score.get("enabled", False), bool):
        raise SectorRotationConfigError("etf_score.enabled must be boolean")
    _required_text(etf_score, "benchmark_ticker")
    _validate_weights(_mapping(etf_score, "weights"), "etf_score.weights")


def _validate_combined_score(combined_score: dict[str, Any]) -> None:
    _validate_weights(_mapping(combined_score, "weights"), "combined_score.weights")
    policy = _required_text(combined_score, "missing_etf_policy")
    if policy not in VALID_MISSING_ETF_POLICIES:
        raise SectorRotationConfigError(
            "combined_score.missing_etf_policy must be one of "
            f"{sorted(VALID_MISSING_ETF_POLICIES)}"
        )


def _validate_rotation_states(rotation_states: dict[str, Any]) -> None:
    leading = _number(rotation_states.get("leading_min_score"), "rotation_states.leading_min_score")
    improving = _number(
        rotation_states.get("improving_min_score"),
        "rotation_states.improving_min_score",
    )
    lagging = _number(rotation_states.get("lagging_max_score"), "rotation_states.lagging_max_score")
    if not leading >= improving >= lagging:
        raise SectorRotationConfigError(
            "rotation state score thresholds must satisfy leading >= improving >= lagging"
        )

    for field_name in (
        "crowded_top25_share_min",
        "danger_share_risk_off_min",
        "score_change_improving_min",
        "score_change_fading_min",
    ):
        _number(rotation_states.get(field_name), f"rotation_states.{field_name}")


def _validate_permissions(permissions: dict[str, Any]) -> None:
    multipliers = _mapping(permissions, "position_size_multipliers")
    for permission, multiplier in multipliers.items():
        value = _number(multiplier, f"permissions.position_size_multipliers.{permission}")
        if value < 0 or value > 1:
            raise SectorRotationConfigError(
                f"permissions.position_size_multipliers.{permission} must be between 0 and 1"
            )

    market_buckets = _mapping(permissions, "market_buckets")
    missing_buckets = [bucket for bucket in REQUIRED_MARKET_BUCKETS if bucket not in market_buckets]
    if missing_buckets:
        raise SectorRotationConfigError(
            f"permissions.market_buckets missing bucket(s): {', '.join(missing_buckets)}"
        )

    for bucket in REQUIRED_MARKET_BUCKETS:
        rules = _mapping(market_buckets, bucket)
        missing_states = [state for state in REQUIRED_ROTATION_STATES if state not in rules]
        if missing_states:
            raise SectorRotationConfigError(
                f"permissions.market_buckets.{bucket} missing state(s): "
                f"{', '.join(missing_states)}"
            )
        for state, permission in rules.items():
            if state not in REQUIRED_ROTATION_STATES:
                raise SectorRotationConfigError(
                    f"permissions.market_buckets.{bucket}.{state} is not a supported state"
                )
            if permission not in multipliers:
                raise SectorRotationConfigError(
                    f"permissions.market_buckets.{bucket}.{state} uses unknown permission "
                    f"{permission}"
                )


def _validate_weights(weights: dict[str, Any], field_name: str) -> None:
    if not weights:
        raise SectorRotationConfigError(f"{field_name} must not be empty")
    for component, value in weights.items():
        if _number(value, f"{field_name}.{component}") < 0:
            raise SectorRotationConfigError(f"{field_name}.{component} must be non-negative")
    total = round(sum(float(value) for value in weights.values()), 6)
    if total != 1.0:
        raise SectorRotationConfigError(f"{field_name} must sum to 1.0, got {total}")


def _canonical_sectors(config: dict[str, Any]) -> set[str]:
    return {str(sector) for sector in _mapping(config, "sector_taxonomy")["canonical"]}


def _mapping(raw: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = raw.get(field_name)
    if not isinstance(value, dict):
        raise SectorRotationConfigError(f"{field_name} must be a mapping")
    return value


def _required_text(raw: dict[str, Any], field_name: str) -> str:
    value = raw.get(field_name.split(".")[-1])
    text = _text(value)
    if not text:
        raise SectorRotationConfigError(f"{field_name} is required")
    return text


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _text_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise SectorRotationConfigError(f"{field_name} must be a list")
    result = [_text(item) for item in value]
    if any(not item for item in result):
        raise SectorRotationConfigError(f"{field_name} values must be text")
    return result


def _number(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SectorRotationConfigError(f"{field_name} must be numeric") from exc
