from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from app.services.setup_lifecycle.constants import (
    SLSE_API_ERROR_CODES,
    SLSE_API_P95_TARGET_MS,
    SLSE_CAPTURE_EVALUATION_TARGET_SECONDS,
    SLSE_PERFORMANCE_FIXTURE_MIN_SNAPSHOTS,
)
from app.services.setup_lifecycle.enums import (
    DataQualityLabel,
    EventSeverity,
    LifecycleState,
    SetupFamily,
)
from app.services.setup_lifecycle.signal_registry import (
    SignalDefinitionRegistry,
    SignalRegistryError,
)

SETUP_LIFECYCLE_CONFIG_PATH = Path("config/setup_lifecycle.yaml")

REQUIRED_SECTIONS = (
    "engine",
    "canonicalization",
    "states",
    "phases",
    "families",
    "episodes",
    "confidence",
    "data_quality_labels",
    "actionability",
    "signals",
    "alerts",
    "api",
    "replay",
    "retention",
    "reconstructed_origin",
)


class SetupLifecycleConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EngineConfig:
    enabled: bool
    version: str
    schema_version: str
    config_version: str
    timeframe: str
    origin_mode: str
    trigger_authority: str
    diagnostic_high_cross_enabled: bool


@dataclass(frozen=True)
class CanonicalizationConfig:
    required_context: tuple[str, ...]
    prefer_market_regime: bool
    prefer_sector_rotation: bool
    precedence: tuple[str, ...]


@dataclass(frozen=True)
class StatesConfig:
    supported: tuple[LifecycleState, ...]
    terminal: tuple[LifecycleState, ...]
    transition_precedence: tuple[LifecycleState, ...]


@dataclass(frozen=True)
class FamilyPolicyConfig:
    enabled: bool
    tracking_score_min: float
    ready_score_min: float
    observation_gap_sessions: int
    max_age_sessions: int
    failed_rearm_cooldown_sessions: int
    parameters: dict[str, Any]


@dataclass(frozen=True)
class GenericFallbackConfig:
    enabled: bool
    prevent_shadowing_supported_family: bool
    min_supported_family_confidence_to_block_generic: int


@dataclass(frozen=True)
class FamiliesConfig:
    precedence: tuple[SetupFamily, ...]
    generic_fallback: GenericFallbackConfig
    policies: dict[SetupFamily, FamilyPolicyConfig]


@dataclass(frozen=True)
class EpisodesConfig:
    one_active_per_family: bool
    default_max_age_sessions: int
    observation_gap_sessions: int
    failed_rearm_cooldown_sessions: int


@dataclass(frozen=True)
class ConfidenceConfig:
    high_min: int
    normal_min: int
    low_min: int
    weights: dict[str, float]


@dataclass(frozen=True)
class DataQualityRuleConfig:
    label: DataQualityLabel
    required_feature_coverage_min: float | None
    fresh_completed_bar_required: bool
    context_required: bool
    allows_inferred_required_feature: bool
    allows_missing_context: bool
    allows_near_stale_data: bool
    optional_omissions_allowed: bool
    hard_required_absent: bool
    stale_beyond_hard_limit: bool


@dataclass(frozen=True)
class AlertRuleConfig:
    rule_id: str
    enabled: bool
    severity: EventSeverity
    source: str
    cooldown_sessions: int
    minimum_confidence: int
    filters: dict[str, Any]


@dataclass(frozen=True)
class AlertsConfig:
    built_in_rules_enabled: bool
    default_cooldown_sessions: int
    minimum_confidence: int
    reconstructed_origin_excluded: bool
    rules: dict[str, AlertRuleConfig]


@dataclass(frozen=True)
class ApiConfig:
    max_page_size: int
    default_page_size: int
    capture_evaluation_target_seconds: int
    p95_target_ms: int
    performance_fixture_min_snapshots: int
    error_codes: tuple[str, ...]


@dataclass(frozen=True)
class ReplayConfig:
    enabled: bool
    output_authoritative_by_default: bool
    promotion_requires_explicit_admin_action: bool
    promotion_requires_confirmation: bool
    persisted_replay_creates_parallel_version: bool


@dataclass(frozen=True)
class RetentionConfig:
    retain_immutable_evidence_indefinitely: bool
    purge_enabled: bool
    purge_preview_required: bool
    purge_confirmation_required: bool
    purge_audit_required: bool


@dataclass(frozen=True)
class ReconstructedOriginConfig:
    enabled: bool
    exclude_from_live_alerts: bool
    exclude_from_live_alert_statistics: bool
    exclude_from_owpe_export: bool


@dataclass(frozen=True)
class SetupLifecycleConfig:
    engine: EngineConfig
    canonicalization: CanonicalizationConfig
    states: StatesConfig
    phases: dict[SetupFamily, tuple[str, ...]]
    families: FamiliesConfig
    episodes: EpisodesConfig
    confidence: ConfidenceConfig
    data_quality_labels: dict[DataQualityLabel, DataQualityRuleConfig]
    actionability: dict[str, Any]
    signal_registry: SignalDefinitionRegistry
    alerts: AlertsConfig
    api: ApiConfig
    replay: ReplayConfig
    retention: RetentionConfig
    reconstructed_origin: ReconstructedOriginConfig
    config_hash: str


def load_setup_lifecycle_config(
    path: Path = SETUP_LIFECYCLE_CONFIG_PATH,
) -> SetupLifecycleConfig:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise SetupLifecycleConfigError("setup lifecycle config must be a mapping")
    for section in REQUIRED_SECTIONS:
        if section not in raw:
            raise SetupLifecycleConfigError(f"{section} is required")

    try:
        registry = SignalDefinitionRegistry.from_config(_mapping(raw, "signals"))
    except SignalRegistryError as exc:
        raise SetupLifecycleConfigError(str(exc)) from exc

    parsed = SetupLifecycleConfig(
        engine=_parse_engine(_mapping(raw, "engine")),
        canonicalization=_parse_canonicalization(_mapping(raw, "canonicalization")),
        states=_parse_states(_mapping(raw, "states")),
        phases=_parse_phases(_mapping(raw, "phases")),
        families=_parse_families(_mapping(raw, "families")),
        episodes=_parse_episodes(_mapping(raw, "episodes")),
        confidence=_parse_confidence(_mapping(raw, "confidence")),
        data_quality_labels=_parse_data_quality_labels(_mapping(raw, "data_quality_labels")),
        actionability=dict(_mapping(raw, "actionability")),
        signal_registry=registry,
        alerts=_parse_alerts(_mapping(raw, "alerts"), registry),
        api=_parse_api(_mapping(raw, "api")),
        replay=_parse_replay(_mapping(raw, "replay")),
        retention=_parse_retention(_mapping(raw, "retention")),
        reconstructed_origin=_parse_reconstructed_origin(_mapping(raw, "reconstructed_origin")),
        config_hash="",
    )
    _validate_cross_section_rules(parsed)
    return replace(parsed, config_hash=setup_lifecycle_config_hash(parsed))


def setup_lifecycle_config_hash(config: SetupLifecycleConfig | dict[str, Any]) -> str:
    data = _normalized_data(config)
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generic_fallback_allowed(
    config: SetupLifecycleConfig,
    supported_family: SetupFamily | None,
    supported_family_confidence: int | None,
) -> bool:
    policy = config.families.generic_fallback
    if not policy.enabled:
        return False
    if supported_family is None:
        return True
    if not policy.prevent_shadowing_supported_family:
        return True
    confidence = supported_family_confidence or 0
    return confidence < policy.min_supported_family_confidence_to_block_generic


def data_quality_label_for(
    config: SetupLifecycleConfig,
    *,
    required_feature_coverage: float,
    fresh_completed_bar: bool,
    context_complete: bool,
    inferred_required_feature: bool = False,
    near_stale_data: bool = False,
    hard_required_absent: bool = False,
    stale_beyond_hard_limit: bool = False,
) -> DataQualityLabel:
    if hard_required_absent or stale_beyond_hard_limit:
        return DataQualityLabel.INSUFFICIENT
    if (
        required_feature_coverage >= 1.0
        and fresh_completed_bar
        and context_complete
        and not inferred_required_feature
        and not near_stale_data
    ):
        return DataQualityLabel.HIGH
    if (
        required_feature_coverage >= 0.90
        and fresh_completed_bar
        and not inferred_required_feature
        and not near_stale_data
    ):
        return DataQualityLabel.NORMAL
    if required_feature_coverage >= 0.50:
        return DataQualityLabel.LOW
    return DataQualityLabel.INSUFFICIENT


def _parse_engine(raw: dict[str, Any]) -> EngineConfig:
    enabled = raw.get("enabled")
    diagnostic = raw.get("diagnostic_high_cross_enabled")
    if not isinstance(enabled, bool):
        raise SetupLifecycleConfigError("engine.enabled must be boolean")
    if not isinstance(diagnostic, bool):
        raise SetupLifecycleConfigError("engine.diagnostic_high_cross_enabled must be boolean")
    trigger_authority = _required_text(raw, "engine.trigger_authority")
    if trigger_authority != "COMPLETED_DAILY_CLOSE":
        raise SetupLifecycleConfigError("engine.trigger_authority must be COMPLETED_DAILY_CLOSE")
    origin_mode = _required_text(raw, "engine.origin_mode")
    if origin_mode != "forward_only":
        raise SetupLifecycleConfigError("engine.origin_mode must be forward_only")
    return EngineConfig(
        enabled=enabled,
        version=_required_text(raw, "engine.version"),
        schema_version=_required_text(raw, "engine.schema_version"),
        config_version=str(_required_text(raw, "engine.config_version")),
        timeframe=_required_text(raw, "engine.timeframe"),
        origin_mode=origin_mode,
        trigger_authority=trigger_authority,
        diagnostic_high_cross_enabled=diagnostic,
    )


def _parse_canonicalization(raw: dict[str, Any]) -> CanonicalizationConfig:
    prefer_market = raw.get("prefer_market_regime")
    prefer_sector = raw.get("prefer_sector_rotation")
    if not isinstance(prefer_market, bool) or not isinstance(prefer_sector, bool):
        raise SetupLifecycleConfigError("canonicalization preference values must be boolean")
    precedence = tuple(_text_list(raw.get("precedence"), "canonicalization.precedence"))
    if len(set(precedence)) != len(precedence):
        raise SetupLifecycleConfigError("canonicalization.precedence contains duplicates")
    return CanonicalizationConfig(
        required_context=tuple(_text_list(raw.get("required_context"), "required_context")),
        prefer_market_regime=prefer_market,
        prefer_sector_rotation=prefer_sector,
        precedence=precedence,
    )


def _parse_states(raw: dict[str, Any]) -> StatesConfig:
    supported = tuple(_enum_list(raw.get("supported"), LifecycleState, "states.supported"))
    terminal = tuple(_enum_list(raw.get("terminal"), LifecycleState, "states.terminal"))
    precedence = tuple(
        _enum_list(raw.get("transition_precedence"), LifecycleState, "states.transition_precedence")
    )
    if set(supported) != set(LifecycleState):
        raise SetupLifecycleConfigError("states.supported must include every lifecycle state")
    if set(terminal) != {LifecycleState.FAILED, LifecycleState.EXPIRED}:
        raise SetupLifecycleConfigError("states.terminal must be FAILED and EXPIRED")
    if set(precedence) != set(supported) or len(precedence) != len(supported):
        raise SetupLifecycleConfigError("states.transition_precedence must cover states once")
    return StatesConfig(supported=supported, terminal=terminal, transition_precedence=precedence)


def _parse_phases(raw: dict[str, Any]) -> dict[SetupFamily, tuple[str, ...]]:
    phases: dict[SetupFamily, tuple[str, ...]] = {}
    for family in SetupFamily:
        key = family.value.lower()
        rows = tuple(_text_list(raw.get(key), f"phases.{key}"))
        if not rows:
            raise SetupLifecycleConfigError(f"phases.{key} must not be empty")
        phases[family] = rows
    return phases


def _parse_families(raw: dict[str, Any]) -> FamiliesConfig:
    precedence = tuple(_enum_list(raw.get("precedence"), SetupFamily, "families.precedence"))
    if set(precedence) != set(SetupFamily) or len(precedence) != len(SetupFamily):
        raise SetupLifecycleConfigError("families.precedence must cover every setup family once")
    fallback = _parse_generic_fallback(_mapping(raw, "generic_fallback"))
    policies = {
        family: _parse_family_policy(_mapping(raw, family.value.lower()), family)
        for family in SetupFamily
    }
    return FamiliesConfig(precedence=precedence, generic_fallback=fallback, policies=policies)


def _parse_generic_fallback(raw: dict[str, Any]) -> GenericFallbackConfig:
    enabled = _required_bool(raw, "generic_fallback.enabled")
    prevent_shadowing = _required_bool(
        raw,
        "generic_fallback.prevent_shadowing_supported_family",
    )
    confidence = int(
        _number(
            raw.get("min_supported_family_confidence_to_block_generic"),
            "generic_fallback.min_supported_family_confidence_to_block_generic",
        )
    )
    if confidence < 0 or confidence > 100:
        raise SetupLifecycleConfigError("generic fallback confidence must be between 0 and 100")
    return GenericFallbackConfig(
        enabled=enabled,
        prevent_shadowing_supported_family=prevent_shadowing,
        min_supported_family_confidence_to_block_generic=confidence,
    )


def _parse_family_policy(raw: dict[str, Any], family: SetupFamily) -> FamilyPolicyConfig:
    enabled = _required_bool(raw, f"families.{family.value.lower()}.enabled")
    tracking = _nonnegative_number(raw.get("tracking_score_min"), "tracking_score_min")
    ready = _nonnegative_number(raw.get("ready_score_min"), "ready_score_min")
    if ready < tracking:
        raise SetupLifecycleConfigError(f"families.{family.value.lower()} ready score < tracking")
    gap = _positive_int(raw.get("observation_gap_sessions"), "observation_gap_sessions")
    max_age = _positive_int(raw.get("max_age_sessions"), "max_age_sessions")
    cooldown = _positive_int(
        raw.get("failed_rearm_cooldown_sessions"),
        "failed_rearm_cooldown_sessions",
    )
    parameters = {
        key: value
        for key, value in raw.items()
        if key
        not in {
            "enabled",
            "tracking_score_min",
            "ready_score_min",
            "observation_gap_sessions",
            "max_age_sessions",
            "failed_rearm_cooldown_sessions",
        }
    }
    return FamilyPolicyConfig(
        enabled=enabled,
        tracking_score_min=tracking,
        ready_score_min=ready,
        observation_gap_sessions=gap,
        max_age_sessions=max_age,
        failed_rearm_cooldown_sessions=cooldown,
        parameters=parameters,
    )


def _parse_episodes(raw: dict[str, Any]) -> EpisodesConfig:
    return EpisodesConfig(
        one_active_per_family=_required_bool(raw, "episodes.one_active_per_family"),
        default_max_age_sessions=_positive_int(raw.get("default_max_age_sessions"), "episodes"),
        observation_gap_sessions=_positive_int(raw.get("observation_gap_sessions"), "episodes"),
        failed_rearm_cooldown_sessions=_positive_int(
            raw.get("failed_rearm_cooldown_sessions"),
            "episodes",
        ),
    )


def _parse_confidence(raw: dict[str, Any]) -> ConfidenceConfig:
    high = _bounded_int(raw.get("high_min"), "confidence.high_min", 0, 100)
    normal = _bounded_int(raw.get("normal_min"), "confidence.normal_min", 0, 100)
    low = _bounded_int(raw.get("low_min"), "confidence.low_min", 0, 100)
    if not high >= normal >= low:
        raise SetupLifecycleConfigError("confidence thresholds must satisfy high >= normal >= low")
    weights = {
        key: _nonnegative_number(value, f"confidence.weights.{key}")
        for key, value in _mapping(raw, "weights").items()
    }
    total = round(sum(weights.values()), 6)
    if total != 1.0:
        raise SetupLifecycleConfigError(f"confidence.weights must sum to 1.0, got {total}")
    return ConfidenceConfig(high_min=high, normal_min=normal, low_min=low, weights=weights)


def _parse_data_quality_labels(
    raw: dict[str, Any],
) -> dict[DataQualityLabel, DataQualityRuleConfig]:
    rules = {
        label: _parse_data_quality_rule(label, _mapping(raw, label.value))
        for label in DataQualityLabel
    }
    thresholds = [
        rules[label].required_feature_coverage_min
        for label in (DataQualityLabel.HIGH, DataQualityLabel.NORMAL, DataQualityLabel.LOW)
    ]
    if any(value is None for value in thresholds):
        raise SetupLifecycleConfigError("data quality HIGH/NORMAL/LOW need coverage thresholds")
    if not thresholds[0] >= thresholds[1] >= thresholds[2]:
        raise SetupLifecycleConfigError("data quality coverage thresholds must be ordered")
    return rules


def _parse_data_quality_rule(
    label: DataQualityLabel,
    raw: dict[str, Any],
) -> DataQualityRuleConfig:
    coverage = raw.get("required_feature_coverage_min")
    return DataQualityRuleConfig(
        label=label,
        required_feature_coverage_min=None
        if coverage is None
        else _ratio(coverage, f"data_quality_labels.{label}.required_feature_coverage_min"),
        fresh_completed_bar_required=bool(raw.get("fresh_completed_bar_required", False)),
        context_required=bool(raw.get("context_required", False)),
        allows_inferred_required_feature=bool(
            raw.get("allows_inferred_required_feature", False)
        ),
        allows_missing_context=bool(raw.get("allows_missing_context", False)),
        allows_near_stale_data=bool(raw.get("allows_near_stale_data", False)),
        optional_omissions_allowed=bool(raw.get("optional_omissions_allowed", False)),
        hard_required_absent=bool(raw.get("hard_required_absent", False)),
        stale_beyond_hard_limit=bool(raw.get("stale_beyond_hard_limit", False)),
    )


def _parse_alerts(raw: dict[str, Any], registry: SignalDefinitionRegistry) -> AlertsConfig:
    rules_raw = _mapping(raw, "rules")
    rules = {
        rule_id: _parse_alert_rule(rule_id, rule_raw, registry)
        for rule_id, rule_raw in rules_raw.items()
    }
    required_rules = {
        "NEW_READY",
        "NEW_TRIGGER",
        "NEW_CONFIRMATION",
        "NEW_FAILURE",
        "NEW_EXTENSION",
        "SCORE_ACCELERATION",
        "SECTOR_ACCELERATION",
        "GATE_BLOCKED",
        "DATA_DEGRADED",
    }
    missing = sorted(required_rules - set(rules))
    if missing:
        raise SetupLifecycleConfigError(f"alerts.rules missing: {', '.join(missing)}")
    return AlertsConfig(
        built_in_rules_enabled=_required_bool(raw, "alerts.built_in_rules_enabled"),
        default_cooldown_sessions=_positive_int(
            raw.get("default_cooldown_sessions"),
            "alerts.default_cooldown_sessions",
        ),
        minimum_confidence=_bounded_int(raw.get("minimum_confidence"), "alerts.minimum", 0, 100),
        reconstructed_origin_excluded=_required_bool(
            raw,
            "alerts.reconstructed_origin_excluded",
        ),
        rules=rules,
    )


def _parse_alert_rule(
    rule_id: str,
    raw: Any,
    registry: SignalDefinitionRegistry,
) -> AlertRuleConfig:
    if not isinstance(raw, dict):
        raise SetupLifecycleConfigError(f"alerts.rules.{rule_id} must be a mapping")
    severity = _enum_value(raw.get("severity"), EventSeverity, f"alerts.rules.{rule_id}.severity")
    signal_key = raw.get("signal_key")
    if signal_key is not None and str(signal_key) not in registry:
        raise SetupLifecycleConfigError(f"alerts.rules.{rule_id} references unknown signal")
    known_filters = {
        "enabled",
        "severity",
        "source",
        "cooldown_sessions",
        "minimum_confidence",
    }
    return AlertRuleConfig(
        rule_id=rule_id,
        enabled=_required_bool(raw, f"alerts.rules.{rule_id}.enabled"),
        severity=severity,
        source=_required_text(raw, f"alerts.rules.{rule_id}.source"),
        cooldown_sessions=_positive_int(
            raw.get("cooldown_sessions"),
            f"alerts.rules.{rule_id}.cooldown_sessions",
        ),
        minimum_confidence=_bounded_int(
            raw.get("minimum_confidence"),
            f"alerts.rules.{rule_id}.minimum_confidence",
            0,
            100,
        ),
        filters={key: value for key, value in raw.items() if key not in known_filters},
    )


def _parse_api(raw: dict[str, Any]) -> ApiConfig:
    max_page_size = _positive_int(raw.get("max_page_size"), "api.max_page_size")
    default_page_size = _positive_int(raw.get("default_page_size"), "api.default_page_size")
    if default_page_size > max_page_size:
        raise SetupLifecycleConfigError("api.default_page_size must be <= max_page_size")
    error_codes = tuple(_text_list(raw.get("error_codes"), "api.error_codes"))
    missing_errors = sorted(SLSE_API_ERROR_CODES - set(error_codes))
    if missing_errors:
        raise SetupLifecycleConfigError(f"api.error_codes missing: {', '.join(missing_errors)}")
    return ApiConfig(
        max_page_size=max_page_size,
        default_page_size=default_page_size,
        capture_evaluation_target_seconds=_positive_int(
            raw.get("capture_evaluation_target_seconds"),
            "api.capture_evaluation_target_seconds",
        ),
        p95_target_ms=_positive_int(raw.get("p95_target_ms"), "api.p95_target_ms"),
        performance_fixture_min_snapshots=_positive_int(
            raw.get("performance_fixture_min_snapshots"),
            "api.performance_fixture_min_snapshots",
        ),
        error_codes=error_codes,
    )


def _parse_replay(raw: dict[str, Any]) -> ReplayConfig:
    return ReplayConfig(
        enabled=_required_bool(raw, "replay.enabled"),
        output_authoritative_by_default=_required_bool(
            raw,
            "replay.output_authoritative_by_default",
        ),
        promotion_requires_explicit_admin_action=_required_bool(
            raw,
            "replay.promotion_requires_explicit_admin_action",
        ),
        promotion_requires_confirmation=_required_bool(
            raw,
            "replay.promotion_requires_confirmation",
        ),
        persisted_replay_creates_parallel_version=_required_bool(
            raw,
            "replay.persisted_replay_creates_parallel_version",
        ),
    )


def _parse_retention(raw: dict[str, Any]) -> RetentionConfig:
    return RetentionConfig(
        retain_immutable_evidence_indefinitely=_required_bool(
            raw,
            "retention.retain_immutable_evidence_indefinitely",
        ),
        purge_enabled=_required_bool(raw, "retention.purge_enabled"),
        purge_preview_required=_required_bool(raw, "retention.purge_preview_required"),
        purge_confirmation_required=_required_bool(
            raw,
            "retention.purge_confirmation_required",
        ),
        purge_audit_required=_required_bool(raw, "retention.purge_audit_required"),
    )


def _parse_reconstructed_origin(raw: dict[str, Any]) -> ReconstructedOriginConfig:
    return ReconstructedOriginConfig(
        enabled=_required_bool(raw, "reconstructed_origin.enabled"),
        exclude_from_live_alerts=_required_bool(
            raw,
            "reconstructed_origin.exclude_from_live_alerts",
        ),
        exclude_from_live_alert_statistics=_required_bool(
            raw,
            "reconstructed_origin.exclude_from_live_alert_statistics",
        ),
        exclude_from_owpe_export=_required_bool(
            raw,
            "reconstructed_origin.exclude_from_owpe_export",
        ),
    )


def _validate_cross_section_rules(config: SetupLifecycleConfig) -> None:
    if config.api.capture_evaluation_target_seconds != SLSE_CAPTURE_EVALUATION_TARGET_SECONDS:
        raise SetupLifecycleConfigError("api.capture_evaluation_target_seconds must be 60")
    if config.api.p95_target_ms != SLSE_API_P95_TARGET_MS:
        raise SetupLifecycleConfigError("api.p95_target_ms must be 500")
    if config.api.performance_fixture_min_snapshots != SLSE_PERFORMANCE_FIXTURE_MIN_SNAPSHOTS:
        raise SetupLifecycleConfigError("api.performance_fixture_min_snapshots must be 100000")
    if not config.retention.retain_immutable_evidence_indefinitely:
        raise SetupLifecycleConfigError("retention must keep immutable evidence indefinitely")
    if config.retention.purge_enabled:
        raise SetupLifecycleConfigError("retention.purge_enabled must be false in Phase 1")
    if config.replay.output_authoritative_by_default:
        raise SetupLifecycleConfigError("replay output must be non-authoritative by default")
    if not config.replay.promotion_requires_explicit_admin_action:
        raise SetupLifecycleConfigError("replay promotion must require explicit admin action")
    if not config.reconstructed_origin.exclude_from_live_alerts:
        raise SetupLifecycleConfigError("reconstructed origin must be excluded from alerts")
    if not config.reconstructed_origin.exclude_from_owpe_export:
        raise SetupLifecycleConfigError("reconstructed origin must be excluded from OWPE export")
    if not generic_fallback_allowed(config, None, None):
        raise SetupLifecycleConfigError("generic fallback must be available when no family matches")
    close_signal = config.signal_registry.require("close_trigger_cross")
    high_signal = config.signal_registry.require("intraday_high_trigger_cross_diagnostic")
    if not close_signal.is_close_authoritative_trigger:
        raise SetupLifecycleConfigError("close_trigger_cross must be close authoritative")
    if not high_signal.is_diagnostic_high_cross:
        raise SetupLifecycleConfigError("high-cross signal must be diagnostic only")


def _normalized_data(config: SetupLifecycleConfig | dict[str, Any]) -> dict[str, Any]:
    if isinstance(config, SetupLifecycleConfig):
        data = asdict(config)
        data["signal_registry"] = [
            asdict(definition) for definition in config.signal_registry.definitions()
        ]
        data.pop("config_hash", None)
        return data
    data = dict(config)
    data.pop("config_hash", None)
    return data


def _mapping(raw: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = raw.get(field_name)
    if not isinstance(value, dict):
        raise SetupLifecycleConfigError(f"{field_name} must be a mapping")
    return value


def _text_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise SetupLifecycleConfigError(f"{field_name} must be a list")
    result = [str(item).strip() for item in value]
    if any(not item for item in result):
        raise SetupLifecycleConfigError(f"{field_name} values must be text")
    return result


def _enum_list(value: Any, enum_type: Any, field_name: str) -> list[Any]:
    return [_enum_value(item, enum_type, field_name) for item in _text_list(value, field_name)]


def _enum_value(value: Any, enum_type: Any, field_name: str) -> Any:
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise SetupLifecycleConfigError(f"{field_name} is not supported") from exc


def _required_text(raw: dict[str, Any], field_name: str) -> str:
    key = field_name.split(".")[-1]
    value = raw.get(key)
    if value is None:
        raise SetupLifecycleConfigError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise SetupLifecycleConfigError(f"{field_name} is required")
    return text


def _required_bool(raw: dict[str, Any], field_name: str) -> bool:
    key = field_name.split(".")[-1]
    value = raw.get(key)
    if not isinstance(value, bool):
        raise SetupLifecycleConfigError(f"{field_name} must be boolean")
    return value


def _number(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SetupLifecycleConfigError(f"{field_name} must be numeric") from exc


def _nonnegative_number(value: Any, field_name: str) -> float:
    number = _number(value, field_name)
    if number < 0:
        raise SetupLifecycleConfigError(f"{field_name} must be non-negative")
    return number


def _positive_int(value: Any, field_name: str) -> int:
    number = int(_number(value, field_name))
    if number <= 0:
        raise SetupLifecycleConfigError(f"{field_name} must be positive")
    return number


def _bounded_int(value: Any, field_name: str, low: int, high: int) -> int:
    number = int(_number(value, field_name))
    if number < low or number > high:
        raise SetupLifecycleConfigError(f"{field_name} must be between {low} and {high}")
    return number


def _ratio(value: Any, field_name: str) -> float:
    number = _number(value, field_name)
    if number < 0 or number > 1:
        raise SetupLifecycleConfigError(f"{field_name} must be between 0 and 1")
    return number
