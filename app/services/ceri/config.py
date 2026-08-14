from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from app.services.ceri.constants import (
    CERI_API_ERROR_CODES,
    CERI_DAILY_CUTOFF_TIMEZONE,
    CERI_EFFECTIVE_SESSION_POLICY,
    CERI_LICENSED_PURGE_REQUIRES_AUDIT,
    CERI_LICENSED_PURGE_REQUIRES_CONFIRMATION,
    CERI_LICENSED_PURGE_REQUIRES_PREVIEW,
    CERI_RUN_DELETION_POLICY,
)
from app.services.ceri.enums import (
    CatalystCategory,
    CatalystStatus,
    CeriChangeType,
    CeriDataset,
    CeriMetric,
    CeriPeriodType,
    CeriProvider,
    CeriProviderCapability,
    ExportPolicy,
)

CERI_CONFIG_PATH = Path("config/ceri.yaml")
CERI_TAXONOMY_PATH = Path("config/ceri_catalyst_taxonomy.yaml")

REQUIRED_CONFIG_SECTIONS = (
    "engine",
    "providers",
    "datasets",
    "metrics",
    "revision",
    "missing_values",
    "currency_conversion",
    "opportunity_weights",
    "event_risk",
    "confidence",
    "change_thresholds",
    "taxonomy",
    "backfill",
    "alerts",
    "posture",
    "exports",
    "retention",
    "price_response",
)

REQUIRED_TAXONOMY_CATEGORIES = frozenset(CatalystCategory)


class CeriConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EngineConfig:
    enabled: bool
    calculation_version: str
    config_version: str
    timezone: str
    daily_cutoff_time: time
    effective_session_policy: str


@dataclass(frozen=True)
class ProvidersConfig:
    priority: tuple[CeriProvider, ...]
    default_source_policy: str
    conflict_resolution: str
    terms_version_required: bool
    retention_metadata_required: bool
    capabilities: dict[CeriProvider, tuple[CeriProviderCapability, ...]]


@dataclass(frozen=True)
class DatasetPolicyConfig:
    enabled: bool
    max_stale_days: int
    export_policy: ExportPolicy


@dataclass(frozen=True)
class MetricsConfig:
    required: tuple[CeriMetric, ...]
    optional: tuple[CeriMetric, ...]
    period_types: tuple[CeriPeriodType, ...]


@dataclass(frozen=True)
class RevisionConfig:
    windows_days: tuple[int, ...]
    near_zero_threshold: float
    minimum_analyst_count: int
    minimum_component_coverage_pct: float
    baseline_tolerance_days: int
    pct_change_unit: str
    period_weights: dict[CeriPeriodType, float]


@dataclass(frozen=True)
class MissingValuesConfig:
    preserve_nulls: bool
    provider_zero_distinct_from_missing: bool
    forbid_zero_fill_defaults: bool


@dataclass(frozen=True)
class CurrencyConversionConfig:
    enabled: bool
    require_verified_basis: bool
    allowed_sources: tuple[str, ...]


@dataclass(frozen=True)
class ConfidenceConfig:
    high_min: float
    normal_min: float
    low_min: float
    weights: dict[str, float]


@dataclass(frozen=True)
class TaxonomyCategoryConfig:
    id: CatalystCategory
    label: str
    examples: tuple[str, ...]
    binary_risk: bool


@dataclass(frozen=True)
class CatalystTaxonomyConfig:
    version: str
    categories: dict[CatalystCategory, TaxonomyCategoryConfig]
    status_transitions: dict[CatalystStatus, tuple[CatalystStatus, ...]]
    config_hash: str


@dataclass(frozen=True)
class BackfillConfig:
    company_batch_size: int
    session_batch_size: int
    max_concurrent_jobs: int
    checkpoint_policy: str


@dataclass(frozen=True)
class AlertRuleConfig:
    rule_id: CeriChangeType
    enabled: bool
    severity: str
    source_change_type: CeriChangeType
    cooldown_sessions: int


@dataclass(frozen=True)
class AlertsConfig:
    enabled: bool
    default_cooldown_sessions: int
    acknowledgement_required: bool
    dedup_scope: str
    rules: dict[CeriChangeType, AlertRuleConfig]


@dataclass(frozen=True)
class PostureConfig:
    labels: tuple[str, ...]
    alignment_flags: tuple[str, ...]


@dataclass(frozen=True)
class ExportsConfig:
    default_view_fields: dict[str, ExportPolicy]
    purge_policy: dict[str, bool]


@dataclass(frozen=True)
class RetentionConfig:
    run_deletion_policy: str
    retain_source_evidence_indefinitely: bool
    provider_license_purge_enabled: bool
    provider_terms_version: str


@dataclass(frozen=True)
class PriceResponseConfig:
    benchmark: str
    windows: tuple[int, ...]
    trailing_volume_sessions: int
    positive_relative_return_threshold: float
    strong_relative_return_threshold: float
    volume_confirmation_threshold: float


@dataclass(frozen=True)
class CeriConfig:
    engine: EngineConfig
    providers: ProvidersConfig
    datasets: dict[CeriDataset, DatasetPolicyConfig]
    metrics: MetricsConfig
    revision: RevisionConfig
    missing_values: MissingValuesConfig
    currency_conversion: CurrencyConversionConfig
    opportunity_weights: dict[str, float]
    event_risk: dict[str, float | int]
    confidence: ConfidenceConfig
    change_thresholds: dict[str, float]
    enabled_categories: tuple[CatalystCategory, ...]
    backfill: BackfillConfig
    alerts: AlertsConfig
    posture: PostureConfig
    exports: ExportsConfig
    retention: RetentionConfig
    price_response: PriceResponseConfig
    taxonomy: CatalystTaxonomyConfig
    api_error_codes: tuple[str, ...]
    config_hash: str


def load_ceri_config(
    path: Path = CERI_CONFIG_PATH,
    taxonomy_path: Path = CERI_TAXONOMY_PATH,
) -> CeriConfig:
    raw = _load_yaml(path, "ceri config")
    for section in REQUIRED_CONFIG_SECTIONS:
        if section not in raw:
            raise CeriConfigError(f"{section} is required")

    taxonomy = load_ceri_taxonomy(taxonomy_path)
    parsed = CeriConfig(
        engine=_parse_engine(_mapping(raw, "engine")),
        providers=_parse_providers(_mapping(raw, "providers")),
        datasets=_parse_datasets(_mapping(raw, "datasets")),
        metrics=_parse_metrics(_mapping(raw, "metrics")),
        revision=_parse_revision(_mapping(raw, "revision")),
        missing_values=_parse_missing_values(_mapping(raw, "missing_values")),
        currency_conversion=_parse_currency_conversion(_mapping(raw, "currency_conversion")),
        opportunity_weights=_parse_weights(_mapping(raw, "opportunity_weights"), "opportunity"),
        event_risk=_parse_event_risk(_mapping(raw, "event_risk")),
        confidence=_parse_confidence(_mapping(raw, "confidence")),
        change_thresholds=_parse_change_thresholds(_mapping(raw, "change_thresholds")),
        enabled_categories=_parse_enabled_categories(_mapping(raw, "taxonomy")),
        backfill=_parse_backfill(_mapping(raw, "backfill")),
        alerts=_parse_alerts(_mapping(raw, "alerts")),
        posture=_parse_posture(_mapping(raw, "posture")),
        exports=_parse_exports(_mapping(raw, "exports")),
        retention=_parse_retention(_mapping(raw, "retention")),
        price_response=_parse_price_response(_mapping(raw, "price_response")),
        taxonomy=taxonomy,
        api_error_codes=tuple(sorted(CERI_API_ERROR_CODES)),
        config_hash="",
    )
    _validate_cross_section_rules(parsed)
    return replace(parsed, config_hash=ceri_config_hash(parsed))


def load_ceri_taxonomy(path: Path = CERI_TAXONOMY_PATH) -> CatalystTaxonomyConfig:
    raw = _load_yaml(path, "ceri taxonomy")
    version = _required_text(raw, "version")
    rows = raw.get("categories")
    if not isinstance(rows, list) or not rows:
        raise CeriConfigError("taxonomy categories must be a non-empty list")

    categories = tuple(_parse_taxonomy_category(row, index) for index, row in enumerate(rows))
    by_id = {category.id: category for category in categories}
    if len(by_id) != len(categories):
        raise CeriConfigError("taxonomy category ids must be unique")
    missing = sorted(category.value for category in REQUIRED_TAXONOMY_CATEGORIES - set(by_id))
    if missing:
        raise CeriConfigError(f"taxonomy missing categories: {', '.join(missing)}")

    transitions = _parse_status_transitions(_mapping(raw, "status_transitions"))
    parsed = CatalystTaxonomyConfig(
        version=version,
        categories=by_id,
        status_transitions=transitions,
        config_hash="",
    )
    return replace(parsed, config_hash=ceri_config_hash(parsed))


def ceri_config_hash(config: CeriConfig | CatalystTaxonomyConfig | dict[str, Any]) -> str:
    payload = json.dumps(
        _normalized_data(config),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_engine(raw: dict[str, Any]) -> EngineConfig:
    timezone = _required_text(raw, "engine.timezone")
    if timezone != CERI_DAILY_CUTOFF_TIMEZONE:
        raise CeriConfigError(f"engine.timezone must be {CERI_DAILY_CUTOFF_TIMEZONE}")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise CeriConfigError("engine.timezone is not a valid IANA timezone") from exc

    policy = _required_text(raw, "engine.effective_session_policy")
    if policy != CERI_EFFECTIVE_SESSION_POLICY:
        raise CeriConfigError(
            f"engine.effective_session_policy must be {CERI_EFFECTIVE_SESSION_POLICY}"
        )
    return EngineConfig(
        enabled=_required_bool(raw, "engine.enabled"),
        calculation_version=_required_text(raw, "engine.calculation_version"),
        config_version=str(_required_text(raw, "engine.config_version")),
        timezone=timezone,
        daily_cutoff_time=_parse_time(_required_text(raw, "engine.daily_cutoff_time")),
        effective_session_policy=policy,
    )


def _parse_providers(raw: dict[str, Any]) -> ProvidersConfig:
    priority = tuple(_enum_list(raw.get("priority"), CeriProvider, "providers.priority"))
    if len(priority) != len(set(priority)):
        raise CeriConfigError("providers.priority contains duplicates")
    if CeriProvider.MANUAL not in priority:
        raise CeriConfigError("providers.priority must include manual")
    default_policy = _required_text(raw, "providers.default_source_policy")
    if default_policy != "provider_priority":
        raise CeriConfigError("providers.default_source_policy must be provider_priority")
    conflict = _required_text(raw, "providers.conflict_resolution")
    if conflict != "preserve_all_observations":
        raise CeriConfigError("providers.conflict_resolution must preserve all observations")

    capabilities_raw = _mapping(raw, "capabilities")
    capabilities: dict[CeriProvider, tuple[CeriProviderCapability, ...]] = {}
    for provider in priority:
        values = tuple(
            _enum_list(
                capabilities_raw.get(provider.value),
                CeriProviderCapability,
                f"providers.capabilities.{provider.value}",
            )
        )
        if CeriProviderCapability.HEALTH not in values:
            raise CeriConfigError(f"{provider.value} provider must expose health")
        capabilities[provider] = values

    return ProvidersConfig(
        priority=priority,
        default_source_policy=default_policy,
        conflict_resolution=conflict,
        terms_version_required=_required_bool(raw, "providers.terms_version_required"),
        retention_metadata_required=_required_bool(
            raw,
            "providers.retention_metadata_required",
        ),
        capabilities=capabilities,
    )


def _parse_datasets(raw: dict[str, Any]) -> dict[CeriDataset, DatasetPolicyConfig]:
    policies = {
        dataset: _parse_dataset_policy(_mapping(raw, dataset.value), dataset)
        for dataset in CeriDataset
    }
    if not any(policy.enabled for policy in policies.values()):
        raise CeriConfigError("at least one dataset must be enabled")
    return policies


def _parse_dataset_policy(
    raw: dict[str, Any],
    dataset: CeriDataset,
) -> DatasetPolicyConfig:
    max_stale = _positive_int(raw.get("max_stale_days"), f"datasets.{dataset}.max_stale_days")
    return DatasetPolicyConfig(
        enabled=_required_bool(raw, f"datasets.{dataset}.enabled"),
        max_stale_days=max_stale,
        export_policy=_enum_value(
            raw.get("export_policy"),
            ExportPolicy,
            f"datasets.{dataset}.export_policy",
        ),
    )


def _parse_metrics(raw: dict[str, Any]) -> MetricsConfig:
    required = tuple(_enum_list(raw.get("required"), CeriMetric, "metrics.required"))
    optional = tuple(_enum_list(raw.get("optional"), CeriMetric, "metrics.optional"))
    period_types = tuple(
        _enum_list(raw.get("period_types"), CeriPeriodType, "metrics.period_types")
    )
    if CeriMetric.EPS_DILUTED not in required or CeriMetric.REVENUE not in required:
        raise CeriConfigError("metrics.required must include EPS_DILUTED and REVENUE")
    if set(required) & set(optional):
        raise CeriConfigError("metrics.required and metrics.optional must not overlap")
    required_periods = {
        CeriPeriodType.CURRENT_QUARTER,
        CeriPeriodType.NEXT_QUARTER,
        CeriPeriodType.CURRENT_FISCAL_YEAR,
        CeriPeriodType.NEXT_FISCAL_YEAR,
    }
    if not required_periods <= set(period_types):
        raise CeriConfigError("metrics.period_types must include current/next quarter/year")
    return MetricsConfig(required=required, optional=optional, period_types=period_types)


def _parse_revision(raw: dict[str, Any]) -> RevisionConfig:
    windows = tuple(
        int(_number(value, "revision.windows_days")) for value in _list(raw, "windows_days")
    )
    if windows != tuple(sorted(set(windows))) or any(window <= 0 for window in windows):
        raise CeriConfigError("revision.windows_days must be unique positive ascending values")
    near_zero = _positive_number(raw.get("near_zero_threshold"), "revision.near_zero_threshold")
    coverage = _ratio_pct(
        raw.get("minimum_component_coverage_pct"),
        "revision.minimum_component_coverage_pct",
    )
    period_weights = {
        CeriPeriodType(key): value
        for key, value in _parse_weights(
            _mapping(raw, "period_weights"), "revision.period_weights"
        ).items()
    }
    required_slots = {
        CeriPeriodType.CURRENT_QUARTER,
        CeriPeriodType.NEXT_QUARTER,
        CeriPeriodType.CURRENT_FISCAL_YEAR,
        CeriPeriodType.NEXT_FISCAL_YEAR,
    }
    if set(period_weights) != required_slots:
        raise CeriConfigError("revision.period_weights must define exactly CQ/NQ/CFY/NFY")
    pct_change_unit = _required_text(raw, "revision.pct_change_unit")
    if pct_change_unit != "PERCENTAGE_POINTS":
        raise CeriConfigError("revision.pct_change_unit must be PERCENTAGE_POINTS")
    return RevisionConfig(
        windows_days=windows,
        near_zero_threshold=near_zero,
        minimum_analyst_count=_positive_int(
            raw.get("minimum_analyst_count"),
            "revision.minimum_analyst_count",
        ),
        minimum_component_coverage_pct=coverage,
        baseline_tolerance_days=_positive_int(
            raw.get("baseline_tolerance_days"),
            "revision.baseline_tolerance_days",
        ),
        pct_change_unit=pct_change_unit,
        period_weights=period_weights,
    )


def _parse_missing_values(raw: dict[str, Any]) -> MissingValuesConfig:
    config = MissingValuesConfig(
        preserve_nulls=_required_bool(raw, "missing_values.preserve_nulls"),
        provider_zero_distinct_from_missing=_required_bool(
            raw,
            "missing_values.provider_zero_distinct_from_missing",
        ),
        forbid_zero_fill_defaults=_required_bool(
            raw,
            "missing_values.forbid_zero_fill_defaults",
        ),
    )
    if not (
        config.preserve_nulls
        and config.provider_zero_distinct_from_missing
        and config.forbid_zero_fill_defaults
    ):
        raise CeriConfigError("missing-value policy must reject zero-as-null ambiguity")
    return config


def _parse_currency_conversion(raw: dict[str, Any]) -> CurrencyConversionConfig:
    sources = tuple(_text_list(raw.get("allowed_sources"), "currency_conversion.allowed_sources"))
    if not sources:
        raise CeriConfigError("currency_conversion.allowed_sources must not be empty")
    config = CurrencyConversionConfig(
        enabled=_required_bool(raw, "currency_conversion.enabled"),
        require_verified_basis=_required_bool(
            raw,
            "currency_conversion.require_verified_basis",
        ),
        allowed_sources=sources,
    )
    if config.enabled and not config.require_verified_basis:
        raise CeriConfigError("currency conversion must require a verified basis")
    return config


def _parse_event_risk(raw: dict[str, Any]) -> dict[str, float | int]:
    risk = {key: _nonnegative_number(value, f"event_risk.{key}") for key, value in raw.items()}
    for field in ("earnings_block_trading_days", "earnings_high_risk_trading_days"):
        risk[field] = _positive_int(raw.get(field), f"event_risk.{field}")
    if risk["earnings_block_trading_days"] > risk["earnings_high_risk_trading_days"]:
        raise CeriConfigError("earnings block window must not exceed high-risk window")
    return risk


def _parse_confidence(raw: dict[str, Any]) -> ConfidenceConfig:
    high = _score(raw.get("high_min"), "confidence.high_min")
    normal = _score(raw.get("normal_min"), "confidence.normal_min")
    low = _score(raw.get("low_min"), "confidence.low_min")
    if not high >= normal >= low:
        raise CeriConfigError("confidence thresholds must satisfy high >= normal >= low")
    return ConfidenceConfig(
        high_min=high,
        normal_min=normal,
        low_min=low,
        weights=_parse_weights(_mapping(raw, "weights"), "confidence"),
    )


def _parse_change_thresholds(raw: dict[str, Any]) -> dict[str, float]:
    thresholds = {
        key: _positive_number(value, f"change_thresholds.{key}") for key, value in raw.items()
    }
    for field in (
        "score_delta",
        "opportunity_upgrade_threshold",
        "revision_pct_points",
        "risk_escalation_delta",
    ):
        if field not in thresholds:
            raise CeriConfigError(f"change_thresholds.{field} is required")
    return thresholds


def _parse_enabled_categories(raw: dict[str, Any]) -> tuple[CatalystCategory, ...]:
    categories = tuple(
        _enum_list(raw.get("enabled_categories"), CatalystCategory, "taxonomy.enabled_categories")
    )
    if len(categories) != len(set(categories)):
        raise CeriConfigError("taxonomy.enabled_categories contains duplicates")
    missing = sorted(category.value for category in REQUIRED_TAXONOMY_CATEGORIES - set(categories))
    if missing:
        raise CeriConfigError(f"taxonomy.enabled_categories missing: {', '.join(missing)}")
    return categories


def _parse_backfill(raw: dict[str, Any]) -> BackfillConfig:
    return BackfillConfig(
        company_batch_size=_positive_int(raw.get("company_batch_size"), "backfill.company"),
        session_batch_size=_positive_int(raw.get("session_batch_size"), "backfill.session"),
        max_concurrent_jobs=_positive_int(raw.get("max_concurrent_jobs"), "backfill.jobs"),
        checkpoint_policy=_required_text(raw, "backfill.checkpoint_policy"),
    )


def _parse_alerts(raw: dict[str, Any]) -> AlertsConfig:
    rules_raw = _mapping(raw, "rules")
    rules = {
        _enum_value(rule_id, CeriChangeType, f"alerts.rules.{rule_id}"): _parse_alert_rule(
            rule_id,
            rule_raw,
        )
        for rule_id, rule_raw in rules_raw.items()
    }
    if not rules:
        raise CeriConfigError("alerts.rules must not be empty")
    return AlertsConfig(
        enabled=_required_bool(raw, "alerts.enabled"),
        default_cooldown_sessions=_positive_int(
            raw.get("default_cooldown_sessions"),
            "alerts.default_cooldown_sessions",
        ),
        acknowledgement_required=_required_bool(raw, "alerts.acknowledgement_required"),
        dedup_scope=_required_text(raw, "alerts.dedup_scope"),
        rules=rules,
    )


def _parse_alert_rule(rule_id: str, raw: Any) -> AlertRuleConfig:
    if not isinstance(raw, dict):
        raise CeriConfigError(f"alerts.rules.{rule_id} must be a mapping")
    change_type = _enum_value(rule_id, CeriChangeType, f"alerts.rules.{rule_id}")
    source = _enum_value(
        raw.get("source_change_type"),
        CeriChangeType,
        f"alerts.rules.{rule_id}.source_change_type",
    )
    if source != change_type:
        raise CeriConfigError(f"alerts.rules.{rule_id} source_change_type must match rule id")
    return AlertRuleConfig(
        rule_id=change_type,
        enabled=_required_bool(raw, f"alerts.rules.{rule_id}.enabled"),
        severity=_required_text(raw, f"alerts.rules.{rule_id}.severity"),
        source_change_type=source,
        cooldown_sessions=_positive_int(
            raw.get("cooldown_sessions"),
            f"alerts.rules.{rule_id}.cooldown_sessions",
        ),
    )


def _parse_posture(raw: dict[str, Any]) -> PostureConfig:
    labels = tuple(_text_list(raw.get("labels"), "posture.labels"))
    flags = tuple(_text_list(raw.get("alignment_flags"), "posture.alignment_flags"))
    required_labels = {"Positive", "Improving", "Mixed", "Deteriorating", "Binary Risk", "Unrated"}
    missing = sorted(required_labels - set(labels))
    if missing:
        raise CeriConfigError(f"posture.labels missing: {', '.join(missing)}")
    if not flags:
        raise CeriConfigError("posture.alignment_flags must not be empty")
    return PostureConfig(labels=labels, alignment_flags=flags)


def _parse_exports(raw: dict[str, Any]) -> ExportsConfig:
    fields = {
        key: _enum_value(value, ExportPolicy, f"exports.default_view_fields.{key}")
        for key, value in _mapping(raw, "default_view_fields").items()
    }
    if "raw_payload" not in fields or fields["raw_payload"] != ExportPolicy.RESTRICTED:
        raise CeriConfigError("exports.default_view_fields.raw_payload must be restricted")
    purge = {
        key: _required_bool(_mapping(raw, "purge_policy"), f"exports.purge_policy.{key}")
        for key in (
            "licensed_data_requires_preview",
            "confirmation_required",
            "audit_required",
        )
    }
    return ExportsConfig(default_view_fields=fields, purge_policy=purge)


def _parse_retention(raw: dict[str, Any]) -> RetentionConfig:
    return RetentionConfig(
        run_deletion_policy=_required_text(raw, "retention.run_deletion_policy"),
        retain_source_evidence_indefinitely=_required_bool(
            raw,
            "retention.retain_source_evidence_indefinitely",
        ),
        provider_license_purge_enabled=_required_bool(
            raw,
            "retention.provider_license_purge_enabled",
        ),
        provider_terms_version=_required_text(raw, "retention.provider_terms_version"),
    )


def _parse_price_response(raw: dict[str, Any]) -> PriceResponseConfig:
    windows = tuple(int(_number(item, "price_response.windows")) for item in _list(raw, "windows"))
    if not windows or any(item <= 0 for item in windows):
        raise CeriConfigError("price_response.windows must contain positive values")
    return PriceResponseConfig(
        benchmark=_required_text(raw, "price_response.benchmark").upper(),
        windows=windows,
        trailing_volume_sessions=_positive_int(
            raw.get("trailing_volume_sessions"), "price_response.trailing_volume_sessions"
        ),
        positive_relative_return_threshold=_number(
            raw.get("positive_relative_return_threshold"),
            "price_response.positive_relative_return_threshold",
        ),
        strong_relative_return_threshold=_number(
            raw.get("strong_relative_return_threshold"),
            "price_response.strong_relative_return_threshold",
        ),
        volume_confirmation_threshold=_positive_number(
            raw.get("volume_confirmation_threshold"),
            "price_response.volume_confirmation_threshold",
        ),
    )


def _parse_taxonomy_category(raw: Any, index: int) -> TaxonomyCategoryConfig:
    if not isinstance(raw, dict):
        raise CeriConfigError(f"taxonomy.categories[{index}] must be a mapping")
    return TaxonomyCategoryConfig(
        id=_enum_value(raw.get("id"), CatalystCategory, f"taxonomy.categories[{index}].id"),
        label=_required_text(raw, f"taxonomy.categories[{index}].label"),
        examples=tuple(_text_list(raw.get("examples"), f"taxonomy.categories[{index}].examples")),
        binary_risk=_required_bool(raw, f"taxonomy.categories[{index}].binary_risk"),
    )


def _parse_status_transitions(
    raw: dict[str, Any],
) -> dict[CatalystStatus, tuple[CatalystStatus, ...]]:
    transitions = {
        status: tuple(
            _enum_list(raw.get(status.value), CatalystStatus, f"status_transitions.{status}")
        )
        for status in CatalystStatus
    }
    for status in (CatalystStatus.CANCELLED, CatalystStatus.OUTCOME_KNOWN):
        if transitions[status]:
            raise CeriConfigError(f"{status.value} must be terminal in taxonomy transitions")
    return transitions


def _validate_cross_section_rules(config: CeriConfig) -> None:
    if config.retention.run_deletion_policy != CERI_RUN_DELETION_POLICY:
        raise CeriConfigError(f"retention.run_deletion_policy must be {CERI_RUN_DELETION_POLICY}")
    if not config.retention.retain_source_evidence_indefinitely:
        raise CeriConfigError("retention must keep source evidence indefinitely")
    if config.retention.provider_license_purge_enabled:
        raise CeriConfigError("provider-license purge must default to disabled")
    purge = config.exports.purge_policy
    if purge["licensed_data_requires_preview"] != CERI_LICENSED_PURGE_REQUIRES_PREVIEW:
        raise CeriConfigError("licensed purge preview policy is not aligned")
    if purge["confirmation_required"] != CERI_LICENSED_PURGE_REQUIRES_CONFIRMATION:
        raise CeriConfigError("licensed purge confirmation policy is not aligned")
    if purge["audit_required"] != CERI_LICENSED_PURGE_REQUIRES_AUDIT:
        raise CeriConfigError("licensed purge audit policy is not aligned")
    missing_categories = set(config.enabled_categories) - set(config.taxonomy.categories)
    missing = sorted(category.value for category in missing_categories)
    if missing:
        raise CeriConfigError(f"enabled taxonomy categories missing definitions: {missing}")
    # Providers are deliberately allowed to expose partial capabilities.  For
    # example SEC supplies first-party guidance/catalysts while EODHD supplies
    # estimates/earnings/news.  The registry and orchestration select a
    # provider explicitly; requiring every provider to implement every dataset
    # would make the provider-neutral protocol impossible to extend.
    for dataset in CeriDataset:
        capability = CeriProviderCapability(dataset.value)
        if not any(
            capability in config.providers.capabilities[provider]
            for provider in config.providers.priority
        ):
            raise CeriConfigError(f"no configured provider supports {dataset.value}")


def _parse_weights(raw: dict[str, Any], section_name: str) -> dict[str, float]:
    weights = {
        key: _nonnegative_number(value, f"{section_name}.{key}") for key, value in raw.items()
    }
    total = round(sum(weights.values()), 6)
    if total != 1.0:
        raise CeriConfigError(f"{section_name} weights must sum to 1.0, got {total}")
    return weights


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise CeriConfigError(f"{label} must be a mapping")
    return raw


def _normalized_data(
    config: CeriConfig | CatalystTaxonomyConfig | dict[str, Any],
) -> dict[str, Any]:
    if isinstance(config, CeriConfig):
        return _strip_config_hashes(asdict(config))
    if isinstance(config, CatalystTaxonomyConfig):
        return _strip_config_hashes(asdict(config))
    return _strip_config_hashes(dict(config))


def _strip_config_hashes(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_config_hashes(item) for key, item in value.items() if key != "config_hash"
        }
    if isinstance(value, list):
        return [_strip_config_hashes(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_config_hashes(item) for item in value)
    return value


def _mapping(raw: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = raw.get(field_name)
    if not isinstance(value, dict):
        raise CeriConfigError(f"{field_name} must be a mapping")
    return value


def _list(raw: dict[str, Any], field_name: str) -> list[Any]:
    value = raw.get(field_name)
    if not isinstance(value, list):
        raise CeriConfigError(f"{field_name} must be a list")
    return value


def _text_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise CeriConfigError(f"{field_name} must be a list")
    result = [str(item).strip() for item in value]
    if any(not item for item in result):
        raise CeriConfigError(f"{field_name} values must be text")
    return result


def _enum_list(value: Any, enum_type: Any, field_name: str) -> list[Any]:
    return [_enum_value(item, enum_type, field_name) for item in _text_list(value, field_name)]


def _enum_value(value: Any, enum_type: Any, field_name: str) -> Any:
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise CeriConfigError(f"{field_name} is not supported") from exc


def _required_text(raw: dict[str, Any], field_name: str) -> str:
    key = field_name.split(".")[-1]
    value = raw.get(key)
    if value is None:
        raise CeriConfigError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise CeriConfigError(f"{field_name} is required")
    return text


def _required_bool(raw: dict[str, Any], field_name: str) -> bool:
    key = field_name.split(".")[-1]
    value = raw.get(key)
    if not isinstance(value, bool):
        raise CeriConfigError(f"{field_name} must be boolean")
    return value


def _number(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CeriConfigError(f"{field_name} must be numeric") from exc


def _positive_number(value: Any, field_name: str) -> float:
    number = _number(value, field_name)
    if number <= 0:
        raise CeriConfigError(f"{field_name} must be positive")
    return number


def _nonnegative_number(value: Any, field_name: str) -> float:
    number = _number(value, field_name)
    if number < 0:
        raise CeriConfigError(f"{field_name} must be non-negative")
    return number


def _positive_int(value: Any, field_name: str) -> int:
    number = int(_number(value, field_name))
    if number <= 0:
        raise CeriConfigError(f"{field_name} must be positive")
    return number


def _ratio_pct(value: Any, field_name: str) -> float:
    number = _number(value, field_name)
    if number < 0 or number > 100:
        raise CeriConfigError(f"{field_name} must be between 0 and 100")
    return number


def _score(value: Any, field_name: str) -> float:
    number = _number(value, field_name)
    if number < 0 or number > 10:
        raise CeriConfigError(f"{field_name} must be between 0 and 10")
    return number


def _parse_time(value: str) -> time:
    try:
        hour, minute = value.split(":", 1)
        return time(hour=int(hour), minute=int(minute))
    except (TypeError, ValueError) as exc:
        raise CeriConfigError("engine.daily_cutoff_time must use HH:MM") from exc
