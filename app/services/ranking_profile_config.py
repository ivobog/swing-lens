from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RANKING_PROFILES_CONFIG_PATH = Path("config/ranking_profiles.yaml")

SUPPORTED_TECHNICAL_COMPONENTS = frozenset(
    {
        "momentum_strength",
        "momentum_health",
        "momentum_danger",
        "trend_quality",
        "setup_quality",
        "breakout_quality",
        "vcp_quality",
        "box_tightness",
        "breakout_or_vcp_quality",
        "pullback_health",
        "relative_strength",
        "relative_strength_acceleration",
        "volume_expansion",
        "trend_repair",
        "risk_control",
        "market_regime_alignment",
    }
)


class RankingProfileConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MissingDataPolicy:
    rescale_available: bool = True
    penalty: float = 1.0


@dataclass(frozen=True)
class RankingThresholds:
    strong_candidate_min_score: float = 8.0
    candidate_min_score: float = 6.8
    watch_min_score: float = 5.5


@dataclass(frozen=True)
class TradeabilityOverlayConfig:
    enabled: bool = False
    poor_penalty: float = 0.0
    very_poor_penalty: float = 0.0
    maximum_penalty: float = 0.0
    minimum_dollar_volume: float | None = None


@dataclass(frozen=True)
class RankingProfileConfig:
    name: str
    enabled: bool
    label: str
    description: str
    technical_weight: float
    fundamental_weight: float
    technical_components: dict[str, float]
    missing_data_policy: MissingDataPolicy
    thresholds: RankingThresholds
    penalties: dict[str, float]
    gates: dict[str, Any]
    tradeability_overlay: TradeabilityOverlayConfig = field(
        default_factory=TradeabilityOverlayConfig
    )


def load_ranking_profiles(
    path: Path = RANKING_PROFILES_CONFIG_PATH,
) -> list[RankingProfileConfig]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, dict):
        raise RankingProfileConfigError("profiles must be a mapping")

    profiles = [_parse_profile(name, raw) for name, raw in raw_profiles.items()]
    for profile in profiles:
        _validate_profile(profile)

    enabled_profiles = [profile for profile in profiles if profile.enabled]
    if not enabled_profiles:
        raise RankingProfileConfigError("No enabled ranking profiles found")
    return enabled_profiles


def get_ranking_profile(
    profile_name: str,
    path: Path = RANKING_PROFILES_CONFIG_PATH,
) -> RankingProfileConfig:
    normalized_name = profile_name.strip()
    for profile in load_ranking_profiles(path):
        if profile.name == normalized_name:
            return profile
    raise RankingProfileConfigError(f"{normalized_name}: unknown ranking profile")


def _parse_profile(name: str, raw: Any) -> RankingProfileConfig:
    if not isinstance(raw, dict):
        raise RankingProfileConfigError(f"{name}: profile must be a mapping")

    weights = _required_mapping(name, raw, "weights")
    components = _required_mapping(name, raw, "technical_components")
    missing_data_policy = _optional_mapping(raw, "missing_data_policy")
    thresholds = _optional_mapping(raw, "thresholds")
    penalties = _optional_mapping(raw, "penalties")
    gates = _optional_mapping(raw, "gates")
    tradeability = _optional_mapping(raw, "tradeability_overlay")

    return RankingProfileConfig(
        name=name,
        enabled=bool(raw.get("enabled", True)),
        label=_required_text(name, raw, "label"),
        description=_required_text(name, raw, "description"),
        technical_weight=_required_float(name, weights, "weights.technical", "technical"),
        fundamental_weight=_required_float(
            name,
            weights,
            "weights.fundamental",
            "fundamental",
        ),
        technical_components={
            component: _float(name, value, f"technical_components.{component}")
            for component, value in components.items()
        },
        missing_data_policy=MissingDataPolicy(
            rescale_available=bool(missing_data_policy.get("rescale_available", True)),
            penalty=_float(
                name,
                missing_data_policy.get("penalty", 1.0),
                "missing_data_policy.penalty",
            ),
        ),
        thresholds=RankingThresholds(
            strong_candidate_min_score=_float(
                name,
                thresholds.get("strong_candidate_min_score", 8.0),
                "thresholds.strong_candidate_min_score",
            ),
            candidate_min_score=_float(
                name,
                thresholds.get("candidate_min_score", 6.8),
                "thresholds.candidate_min_score",
            ),
            watch_min_score=_float(
                name,
                thresholds.get("watch_min_score", 5.5),
                "thresholds.watch_min_score",
            ),
        ),
        penalties={
            penalty: _float(name, value, f"penalties.{penalty}")
            for penalty, value in penalties.items()
        },
        gates=dict(gates),
        tradeability_overlay=TradeabilityOverlayConfig(
            enabled=bool(tradeability.get("enabled", False)),
            poor_penalty=_float(
                name, tradeability.get("poor_penalty", 0.0),
                "tradeability_overlay.poor_penalty",
            ),
            very_poor_penalty=_float(
                name, tradeability.get("very_poor_penalty", 0.0),
                "tradeability_overlay.very_poor_penalty",
            ),
            maximum_penalty=_float(
                name, tradeability.get("maximum_penalty", 0.0),
                "tradeability_overlay.maximum_penalty",
            ),
            minimum_dollar_volume=(
                _float(
                    name, tradeability["minimum_dollar_volume"],
                    "tradeability_overlay.minimum_dollar_volume",
                )
                if tradeability.get("minimum_dollar_volume") is not None
                else None
            ),
        ),
    )


def _validate_profile(profile: RankingProfileConfig) -> None:
    _require_approx_sum(
        profile.name,
        "weights",
        [profile.technical_weight, profile.fundamental_weight],
    )
    _require_approx_sum(
        profile.name,
        "technical_components",
        list(profile.technical_components.values()),
    )

    unknown_components = sorted(
        set(profile.technical_components) - SUPPORTED_TECHNICAL_COMPONENTS
    )
    if unknown_components:
        joined = ", ".join(unknown_components)
        raise RankingProfileConfigError(
            f"{profile.name}: unknown technical component(s): {joined}"
        )

    if not (
        profile.thresholds.strong_candidate_min_score
        >= profile.thresholds.candidate_min_score
        >= profile.thresholds.watch_min_score
    ):
        raise RankingProfileConfigError(
            f"{profile.name}: thresholds must be ordered strong >= candidate >= watch"
        )

    if profile.missing_data_policy.penalty < 0:
        raise RankingProfileConfigError(
            f"{profile.name}: missing_data_policy.penalty must be non-negative"
        )

    for penalty_name, penalty_value in profile.penalties.items():
        if penalty_value < 0:
            raise RankingProfileConfigError(
                f"{profile.name}: penalties.{penalty_name} must be non-negative"
            )
    overlay = profile.tradeability_overlay
    overlay_values = (
        overlay.poor_penalty,
        overlay.very_poor_penalty,
        overlay.maximum_penalty,
    )
    if any(value < 0 for value in overlay_values):
        raise RankingProfileConfigError(
            f"{profile.name}: tradeability overlay penalties must be non-negative"
        )
    if overlay.minimum_dollar_volume is not None and overlay.minimum_dollar_volume < 0:
        raise RankingProfileConfigError(
            f"{profile.name}: tradeability_overlay.minimum_dollar_volume must be non-negative"
        )
    if max(overlay.poor_penalty, overlay.very_poor_penalty) > overlay.maximum_penalty:
        raise RankingProfileConfigError(
            f"{profile.name}: tradeability penalties must not exceed maximum_penalty"
        )


def _required_mapping(profile_name: str, raw: dict[str, Any], field: str) -> dict[str, Any]:
    value = raw.get(field)
    if not isinstance(value, dict):
        raise RankingProfileConfigError(f"{profile_name}: {field} must be a mapping")
    if not value:
        raise RankingProfileConfigError(f"{profile_name}: {field} must not be empty")
    return value


def _optional_mapping(raw: dict[str, Any], field: str) -> dict[str, Any]:
    value = raw.get(field, {})
    return value if isinstance(value, dict) else {}


def _required_text(profile_name: str, raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RankingProfileConfigError(f"{profile_name}: {field} is required")
    return value.strip()


def _required_float(
    profile_name: str,
    raw: dict[str, Any],
    error_field: str,
    key: str,
) -> float:
    if key not in raw:
        raise RankingProfileConfigError(f"{profile_name}: {error_field} is required")
    return _float(profile_name, raw[key], error_field)


def _float(profile_name: str, value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RankingProfileConfigError(
            f"{profile_name}: {field} must be numeric"
        ) from exc


def _require_approx_sum(profile_name: str, field: str, values: list[float]) -> None:
    total = round(sum(values), 6)
    if total != 1.0:
        raise RankingProfileConfigError(f"{profile_name}: {field} must sum to 1.0, got {total}")
