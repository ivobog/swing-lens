"""Native contextual authority using the shared T13A snapshot/identity contract.

Only frozen tagged values are retained. Native objects are reconstructed without
files, environment, Settings or today's parser defaults on historical/retry reads.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import time
from enum import Enum
from typing import get_args, get_origin, get_type_hints

from app.services.configuration_source_values import configuration_leaves
from app.services.core_effective_configuration import (
    CoreEffectiveConfiguration,
    _decode,
    core_configuration_from_evidence,
    freeze_core_configuration,
)
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

CONTEXTUAL_NAMESPACES = {
    "contextual.regime",
    "contextual.sector",
    "contextual.ceri",
    *(
        f"contextual.ibmi.{name}"
        for name in ("liquidity", "short_pressure", "volatility", "options_activity", "histogram")
    ),
}

CERI_CALCULATION_FIELDS = (
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
    "enabled_categories",
    "price_response",
    "taxonomy",
    "config_hash",
)


def ceri_calculation_values(config):
    payload = json.loads(json.dumps(asdict(config), default=str))
    return {key: payload[key] for key in CERI_CALCULATION_FIELDS}


def _restore(kind, value):
    if value is None:
        return None
    origin, args = get_origin(kind), get_args(kind)
    if is_dataclass(kind):
        hints = get_type_hints(kind)
        return kind(**{f.name: _restore(hints[f.name], value[f.name]) for f in fields(kind)})
    if origin is dict:
        return {_restore(args[0], k): _restore(args[1], v) for k, v in value.items()}
    if origin in {tuple, list, set, frozenset}:
        items = [_restore(args[0], item) for item in value]
        return origin(items)
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if kind is time:
        return time.fromisoformat(value)
    if args and type(None) in args:
        return _restore(next(arg for arg in args if arg is not type(None)), value)
    return value


@dataclass(frozen=True)
class ContextualEffectiveConfiguration(CoreEffectiveConfiguration):
    def require_family(self, namespace: str) -> None:
        if namespace not in CONTEXTUAL_NAMESPACES or self.snapshot.family.namespace != namespace:
            raise ValueError("contextual configuration family mismatch")
        if self.snapshot.family.schema_version != f"{namespace}-v1":
            raise ValueError("unsupported contextual configuration schema")
        values = self.values
        required = {
            "contextual.regime": ("policy", "feature"),
            "contextual.sector": ("config", "selection"),
            "contextual.ceri": ("config", "native_policy", "consumer"),
        }.get(namespace, ("config",))
        if any(not isinstance(values.get(key), dict) or not values[key] for key in required):
            raise ValueError("incomplete contextual configuration")
        if any(
            entry.source.kind is SourceKind.UNKNOWN
            for entry in self.snapshot.entries
            if entry.classification is Classification.BEHAVIORAL
        ):
            raise ValueError("UNKNOWN contextual configuration provenance")
        if namespace == "contextual.regime":
            from app.services.market_regime_policy import (
                SUPPORTED_REGIMES,
                MarketRegimeCommandCenterConfig,
            )

            if set(values["policy"]) != {
                field.name for field in fields(MarketRegimeCommandCenterConfig)
            }:
                raise ValueError("incomplete Regime policy")
            if any(not values["feature"].get(key) for key in ("pine", "v4")):
                raise ValueError("incomplete Regime feature configuration")
            policy = values["policy"]
            policy_fields = {
                "position_size_multiplier",
                "preferred_profiles",
                "allowed_profiles",
                "reduced_profiles",
                "blocked_profiles",
                "allowed_setups",
                "blocked_setups",
                "minimum_score_adjustment",
                "summary",
                "warnings",
            }
            if any(
                regime not in policy["risk_state_mapping"]
                or not policy_fields.issubset(policy["policies"].get(regime, {}))
                for regime in SUPPORTED_REGIMES
            ):
                raise ValueError("incomplete Regime policy mappings or fallbacks")
            if any(
                entry.source.kind is SourceKind.UNKNOWN
                for entry in self.snapshot.entries
                if entry.classification is Classification.BEHAVIORAL
            ):
                raise ValueError("UNKNOWN Regime configuration provenance")
        if namespace == "contextual.ceri":
            if set(values["config"]) != set(CERI_CALCULATION_FIELDS):
                raise ValueError("incomplete CERI configuration")
            if set(values["native_policy"]) != {
                "posture",
                "event_risk",
                "confidence",
                "guidance",
                "provider",
            }:
                raise ValueError("incomplete CERI native policy")
            policy_fields = {
                "posture": {
                    "binary_risk_min",
                    "positive_min",
                    "improving_min",
                    "mixed_min",
                    "insufficient_label",
                    "labels",
                },
                "event_risk": {
                    "options_premium_cap",
                    "medium_earnings_days",
                    "blocked_score",
                    "high_score",
                    "medium_score",
                    "staleness_penalty_default",
                    "secondary_penalty_cap_default",
                },
                "confidence": {
                    "fresh_age_days",
                    "fresh_score",
                    "within_limit_score",
                    "analyst_high_multiple",
                    "analyst_high_score",
                    "analyst_normal_score",
                    "analyst_low_score",
                    "timestamp_unavailable_score",
                    "timestamp_missing_score",
                    "timestamp_verified_score",
                },
                "guidance": {
                    "action_scores",
                    "confidence_labels",
                    "rejected_quality_warnings",
                    "selection_order",
                },
                "provider": {"unknown_provider_priority", "selection_order"},
            }
            if any(
                not names.issubset(values["native_policy"][root])
                for root, names in policy_fields.items()
            ):
                raise ValueError("incomplete CERI native policy fields")
            if set(values["consumer"]) != {
                "run_capture",
                "revision_feature_config_hash",
                "ibmi_enabled",
                "volatility_enabled",
                "short_pressure_enabled",
                "volatility",
            }:
                raise ValueError("incomplete CERI consumer policy")
            if "ceri_risk_max_contribution" not in values["consumer"].get("volatility", {}):
                raise ValueError("incomplete CERI consumer cap")
        if namespace == "contextual.sector":
            from app.services.sector_rotation_config import sector_rotation_config_hash

            if values["selection"].get("prior_config_hash") != sector_rotation_config_hash(
                values["config"]
            ):
                raise ValueError("incomplete or inconsistent Sector prior selection")
        if namespace.startswith("contextual.ibmi."):
            module = namespace.rsplit(".", 1)[1]
            if any(not values["config"].get(key) for key in ("engine", "freshness", module)):
                raise ValueError("incomplete IBMI metric configuration")
            if not values.get("compatibility", {}).get("legacy_config_hash"):
                raise ValueError("incomplete IBMI compatibility metadata")
            if not set(IBMI_DEFAULTS[module]).issubset(values["config"][module]):
                raise ValueError("incomplete IBMI metric defaults")
            if not {"calculation_version", "config_version", "source_version"}.issubset(
                values["config"]["engine"]
            ):
                raise ValueError("incomplete IBMI versions")
            if not {"historical_max_age_days", "live_max_age_minutes"}.issubset(
                values["config"]["freshness"]
            ):
                raise ValueError("incomplete IBMI freshness defaults")
            if module == "liquidity" and not set(
                IBMI_DEFAULTS[module]["spread_grade_pct"]
            ).issubset(values["config"][module]["spread_grade_pct"]):
                raise ValueError("incomplete IBMI spread defaults")

    def regime_config(self):
        from app.services.market_regime_policy import MarketRegimeCommandCenterConfig

        self.require_family("contextual.regime")
        result = _restore(MarketRegimeCommandCenterConfig, self.values["policy"])
        object.__setattr__(result, "_effective_configuration", self)
        object.__setattr__(
            result,
            "_configuration_sources",
            tuple(
                (entry.key.removeprefix("policy."), entry.source)
                for entry in self.snapshot.entries
                if entry.key.startswith("policy.")
            ),
        )
        return result

    def ceri_config(self):
        from app.services.ceri.config import CeriConfig

        self.require_family("contextual.ceri")
        # Unused subsystem fields have no authority in this calculation adapter.
        # Their native loaders remain responsible for downstream T13D behavior.
        payload = {
            field.name: self.values["config"].get(field.name) for field in fields(CeriConfig)
        }
        result = _restore(CeriConfig, payload)
        object.__setattr__(result, "_effective_configuration", self)
        object.__setattr__(
            result,
            "_configuration_sources",
            tuple(
                (entry.key.removeprefix("config."), entry.source)
                for entry in self.snapshot.entries
                if entry.key.startswith("config.")
            ),
        )
        return result

    def ibmi_config(self, original=None):
        from app.services.ib_market_intelligence.config import IBMarketIntelligenceConfig

        self.require_family(self.snapshot.family.namespace)
        if not self.snapshot.family.namespace.startswith("contextual.ibmi."):
            raise ValueError("IBMI configuration family required")
        values = self.values
        raw = values["config"]
        engine = raw["engine"]
        result = IBMarketIntelligenceConfig(
            raw=raw,
            config_hash=values["compatibility"]["legacy_config_hash"],
            calculation_version=engine["calculation_version"],
            config_version=engine["config_version"],
            source_version=engine["source_version"],
            scanner_presets=(),
        )
        object.__setattr__(result, "_effective_configuration", self)
        object.__setattr__(
            result,
            "_configuration_sources",
            tuple(
                (entry.key.removeprefix("config."), entry.source)
                for entry in self.snapshot.entries
                if entry.key.startswith("config.")
            ),
        )
        return result


def _freeze(namespace, values, sources, excluded=(), display=()):
    frozen = freeze_core_configuration(namespace, values, sources=sources).snapshot
    entries = tuple(
        ConfigurationEntry(
            entry.key,
            _decode(json.loads(entry.canonical_value_json)),
            Classification.OBSERVABILITY
            if any(entry.key == key or entry.key.startswith(key + ".") for key in display)
            else Classification.OPERATIONAL,
            entry.value_type,
            entry.source,
            defaulted=entry.defaulted,
            overridden=entry.overridden,
        )
        if any(entry.key == key or entry.key.startswith(key + ".") for key in (*excluded, *display))
        else entry
        for entry in frozen.entries
    )
    family = ConfigurationFamily(
        namespace,
        f"{namespace}-v1",
        ConfigurationResolution(
            f"contextual_effective_configuration.resolve_{namespace.split('.')[1]}_configuration",
            "1",
            (
                SourceKind.REQUEST,
                SourceKind.SETTINGS_MODEL,
                SourceKind.ENVIRONMENT,
                SourceKind.DOTENV,
                SourceKind.PROFILE,
                SourceKind.CODE_DEFAULT,
            ),
        ),
        tuple((entry.key, entry.classification) for entry in entries),
    )
    return ContextualEffectiveConfiguration(EffectiveConfigurationSnapshot(family, entries))


def _sources(values, kind, identifier):
    return {key: ConfigurationSource(kind, identifier) for key, _ in configuration_leaves(values)}


def _native_sources(config, current, *, root, metadata, pointer):
    previous = getattr(config, pointer, None)
    old = dict(configuration_leaves(previous.values[root])) if previous is not None else None
    current_leaves = dict(configuration_leaves(current))
    return {
        f"{root}.{key}": source
        for key, source in getattr(config, metadata, ())
        if old is None
        or (
            key in old
            and key in current_leaves
            and type(old[key]) is type(current_leaves[key])
            and old[key] == current_leaves[key]
        )
    }


def _feature_display(feature):
    used = {
        "pine": {"trend", "momentum", "pullback_breakout", "risk", "stop_target", "market_rs"},
        "v4": {
            "adaptive_percentiles",
            "volatility_contraction",
            "donchian_darvas",
            "stage_analysis",
            "climax_risk",
        },
    }
    unused = []
    for group, params in feature.items():
        for root, value in params.items():
            if root not in used[group]:
                unused.append(f"feature.{group}.{root}")
            elif isinstance(value, dict) and value.get("enabled") is False:
                unused.extend(f"feature.{group}.{root}.{key}" for key in value if key != "enabled")
    return tuple(unused)


def contextual_configuration_from_evidence(evidence):
    config = core_configuration_from_evidence(evidence)
    if config is None:
        return None
    result = ContextualEffectiveConfiguration(config.snapshot)
    result.require_family(result.snapshot.family.namespace)
    return result


def contextual_configuration_for_row(row):
    if getattr(row, "evidence_id", None) is None:
        return None
    evidence = row.calculation_evidence
    if evidence is None or evidence.id != row.evidence_id:
        raise ValueError("exact contextual configuration evidence unavailable")
    return contextual_configuration_from_evidence(evidence)


def resolve_regime_configuration(config=None, *, pine=None, v4=None):
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return ContextualEffectiveConfiguration(
            delivered_configuration("contextual.regime").snapshot
        )

    from app.services.effective_configuration_families import snapshot_regime_configuration
    from app.services.market_regime_policy import load_market_regime_command_center_config
    from app.services.technical_indicators import load_pine_defaults
    from app.services.technical_scoring_config import load_technical_scoring_v4_config

    if config is None:
        return load_market_regime_command_center_config()._effective_configuration
    if pine is None and v4 is None and hasattr(config, "_effective_configuration"):
        existing = config._effective_configuration
        if asdict(config) == existing.values["policy"]:
            return existing
    pine = pine if pine is not None else load_pine_defaults()
    v4 = v4 if v4 is not None else load_technical_scoring_v4_config()
    representative = snapshot_regime_configuration(config, pine=pine, technical_v4=v4)
    decoded = CoreEffectiveConfiguration(representative).values
    feature = decoded.pop("feature")
    values = {"policy": decoded, "feature": feature}
    sources = _sources(values, SourceKind.REQUEST, "resolved-regime-request")
    native_keys = {key for key, _ in configuration_leaves(asdict(config))}
    sources.update(
        {
            f"policy.{key}": ConfigurationSource(
                SourceKind.CODE_DEFAULT, "regime-native-policy-defaults"
            )
            for key, _ in configuration_leaves(decoded)
            if key not in native_keys
        }
    )
    sources.update(
        _native_sources(
            config,
            decoded,
            root="policy",
            metadata="_configuration_sources",
            pointer="_effective_configuration",
        )
    )
    for group, params in (("pine", pine), ("v4", v4)):
        sources.update(
            {
                f"feature.{group}.{key}": source
                for key, source in getattr(params, "configuration_sources", ())
            }
        )
    return _freeze(
        "contextual.regime",
        values,
        sources,
        display=(
            "policy.enabled",
            "policy.market_regime_params.use_spy",
            "policy.symbols.optional_symbols",
            *_feature_display(feature),
        ),
    )


def resolve_sector_configuration(config=None, *, pine=None, v4=None):
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return ContextualEffectiveConfiguration(
            delivered_configuration("contextual.sector").snapshot
        )

    from app.services.sector_rotation_config import (
        load_sector_rotation_config,
        sector_rotation_config_hash,
    )
    from app.services.technical_indicators import load_pine_defaults
    from app.services.technical_scoring_config import load_technical_scoring_v4_config

    config = config if config is not None else load_sector_rotation_config()
    if pine is None and v4 is None and hasattr(config, "effective_configuration"):
        existing = config.effective_configuration
        if dict(config) == existing.values["config"]:
            return existing
    values = {
        "config": dict(config),
        "selection": {"prior_config_hash": sector_rotation_config_hash(config)},
    }
    sources = _sources(values, SourceKind.REQUEST, "resolved-sector-request")
    sources.update(
        _native_sources(
            config,
            values["config"],
            root="config",
            metadata="configuration_sources",
            pointer="effective_configuration",
        )
    )
    if config["etf_score"]["enabled"]:
        values["feature"] = {
            "pine": pine if pine is not None else load_pine_defaults(),
            "v4": v4 if v4 is not None else load_technical_scoring_v4_config(),
        }
        for group, params in values["feature"].items():
            sources.update(
                _sources(
                    {"feature": {group: params}},
                    SourceKind.REQUEST,
                    "resolved-sector-feature-request",
                )
            )
            sources.update(
                {
                    f"feature.{group}.{key}": source
                    for key, source in getattr(params, "configuration_sources", ())
                }
            )
    inactive = (
        ()
        if config["etf_score"]["enabled"]
        else tuple(f"config.etf_score.{key}" for key in config["etf_score"] if key != "enabled")
        + ("config.sector_etf_proxies",)
    )
    return _freeze(
        "contextual.sector",
        values,
        sources,
        display=(*inactive, *_feature_display(values.get("feature", {}))),
    )


def resolve_ceri_configuration(config=None, *, consumer=None, consumer_sources=None):
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return ContextualEffectiveConfiguration(delivered_configuration("contextual.ceri").snapshot)

    from app.services.ceri.config import load_ceri_config

    config = config if config is not None else load_ceri_config()
    existing = getattr(config, "_effective_configuration", None)
    if (
        existing is not None
        and consumer is None
        and (ceri_calculation_values(config) == existing.values["config"])
    ):
        return existing
    values = {
        "config": ceri_calculation_values(config),
        "native_policy": {
            "posture": {
                "binary_risk_min": 6.0,
                "positive_min": 7.0,
                "improving_min": 5.0,
                "mixed_min": 3.0,
                "insufficient_label": "Insufficient",
                "labels": {
                    "unrated": "Unrated",
                    "binary_risk": "Binary Risk",
                    "positive": "Positive",
                    "improving": "Improving",
                    "mixed": "Mixed",
                    "deteriorating": "Deteriorating",
                },
            },
            "event_risk": {
                "options_premium_cap": 1.5,
                "medium_earnings_days": 10,
                "blocked_score": 5.0,
                "high_score": 3.0,
                "medium_score": 1.5,
                "staleness_penalty_default": 1.0,
                "secondary_penalty_cap_default": 2.0,
            },
            "confidence": {
                "fresh_age_days": 1,
                "fresh_score": 10.0,
                "within_limit_score": 7.0,
                "analyst_high_multiple": 2,
                "analyst_high_score": 10.0,
                "analyst_normal_score": 7.0,
                "analyst_low_score": 3.0,
                "timestamp_unavailable_score": 4.0,
                "timestamp_missing_score": 6.0,
                "timestamp_verified_score": 9.0,
            },
            "guidance": {
                "action_scores": {
                    "RAISED": 8,
                    "INITIATED": 6,
                    "MAINTAINED": 5,
                    "NARROWED": 5,
                    "WIDENED": 4,
                    "LOWERED": 2,
                    "WITHDRAWN": 1,
                },
                "confidence_labels": ["HIGH", "NORMAL"],
                "rejected_quality_warnings": ["requires_review", "extraction_insufficient"],
                "selection_order": ["effective_timestamp", "id"],
            },
            "provider": {
                "unknown_provider_priority": 10000,
                "selection_order": [
                    "provider_priority",
                    "quality_penalty",
                    "freshness",
                    "source_record_id",
                ],
            },
        },
    }
    values["config"]["config_hash"] = config.config_hash
    if existing is not None:
        values["native_policy"] = existing.values["native_policy"]
    values["consumer"] = (
        consumer
        if consumer is not None
        else existing.values["consumer"]
        if existing is not None
        else {
            "run_capture": False,
            "revision_feature_config_hash": None,
            "ibmi_enabled": False,
            "volatility_enabled": False,
            "short_pressure_enabled": False,
            "volatility": {"ceri_risk_max_contribution": 1.5},
        }
    )
    sources = _sources(values, SourceKind.REQUEST, "resolved-ceri-request")
    sources.update(
        _native_sources(
            config,
            values["config"],
            root="config",
            metadata="_configuration_sources",
            pointer="_effective_configuration",
        )
    )
    sources.update(
        _sources(
            {"native_policy": values["native_policy"]},
            SourceKind.CODE_DEFAULT,
            "ceri-native-posture-policy",
        )
    )
    sources.update(
        _sources(
            {"consumer": values["consumer"]},
            SourceKind.REQUEST if consumer is not None else SourceKind.CODE_DEFAULT,
            "ceri-input-selection-resolution",
        )
    )
    sources.update(consumer_sources or {})
    if existing is not None:
        sources.update(
            {
                entry.key: entry.source
                for entry in existing.snapshot.entries
                if entry.key.startswith("native_policy.")
                or (consumer is None and entry.key.startswith("consumer."))
            }
        )
    return _freeze(
        "contextual.ceri",
        values,
        sources,
        excluded=(
            "config.taxonomy.config_hash",
            "config.providers.capabilities",
            "config.providers.terms_version_required",
            "config.providers.retention_metadata_required",
            *tuple(
                f"config.{key}"
                for key in (
                    "config_hash",
                    "backfill",
                    "alerts",
                    "exports",
                    "retention",
                    "api_error_codes",
                )
            ),
        ),
        display=(
            "config.engine.enabled",
            "config.providers.default_source_policy",
            "config.providers.conflict_resolution",
            "config.metrics.optional",
            "config.currency_conversion",
            *(
                f"config.datasets.{dataset}.{key}"
                for dataset in values["config"]["datasets"]
                for key in ("enabled", "export_policy")
            ),
        ),
    )


IBMI_DEFAULTS = {
    "liquidity": {
        "short_window_sessions": 5,
        "lookback_sessions": 20,
        "percentile_window_sessions": 60,
        "minimum_dollar_volume": 0,
        "spread_grade_pct": {
            "excellent_max": 0.10,
            "good_max": 0.25,
            "acceptable_max": 0.50,
            "poor_max": 1.0,
        },
    },
    "short_pressure": {
        "high_fee_rate_pct": 10,
        "extreme_fee_rate_pct": 25,
        "low_availability_shares": 100000,
        "very_low_availability_shares": 25000,
    },
    "volatility": {
        "iv_expansion_window": 20,
        "lookback_sessions": 252,
        "iv_hv_high_ratio": 1.5,
        "iv_hv_extreme_ratio": 2.0,
    },
    "options_activity": {
        "abnormal_activity_multiple": 2.0,
        "call_heavy_ratio_max": 0.7,
        "put_heavy_ratio_min": 1.3,
    },
    "histogram": {"high_activity_fraction": 0.70, "low_activity_percentile": 0.20},
}


def resolve_ibmi_configuration(config=None, module="liquidity"):
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    module = str(module).lower()
    if current_delivery() is not None:
        return ContextualEffectiveConfiguration(
            delivered_configuration("contextual.ibmi" + "." + module).snapshot
        )

    from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config

    config = config if config is not None else load_ib_market_intelligence_config()
    if module not in IBMI_DEFAULTS:
        raise ValueError("unsupported IBMI configuration module")
    cached = getattr(config, "_effective_configurations", {}).get(module)
    existing = getattr(config, "_effective_configuration", None)
    if existing is not None:
        existing.require_family(f"contextual.ibmi.{module}")
    values = {
        "config": {
            "engine": {
                "enabled": config.raw["engine"].get("enabled", True),
                "calculation_version": config.calculation_version,
                "config_version": config.config_version,
                "source_version": config.source_version,
            },
            module: {**IBMI_DEFAULTS[module], **config.section(module)},
            "freshness": {
                "historical_max_age_days": 5,
                "live_max_age_minutes": 30,
                **config.section("freshness"),
            },
        }
    }
    values["compatibility"] = {"legacy_config_hash": config.config_hash}
    if module == "liquidity":
        values["config"][module]["spread_grade_pct"] = {
            **IBMI_DEFAULTS[module]["spread_grade_pct"],
            **(config.section(module).get("spread_grade_pct") or {}),
        }
    if cached is not None and cached.values == values:
        return cached
    if existing is not None and existing.values == values:
        return existing
    sources = _sources(values, SourceKind.CODE_DEFAULT, "ibmi-native-metric-defaults")
    provided = {"config": {key: config.raw.get(key, {}) for key in ("engine", module, "freshness")}}
    sources.update(_sources(provided, SourceKind.REQUEST, "resolved-ibmi-metric-request"))
    previous = existing or cached
    old = dict(configuration_leaves(previous.values["config"])) if previous is not None else None
    current_leaves = dict(configuration_leaves(values["config"]))
    sources.update(
        {
            f"config.{key}": source
            for key, source in getattr(config, "_configuration_sources", ())
            if old is None
            or (
                key in old
                and key in current_leaves
                and type(old[key]) is type(current_leaves[key])
                and old[key] == current_leaves[key]
            )
        }
    )
    sources["compatibility.legacy_config_hash"] = ConfigurationSource(
        SourceKind.REQUEST, "ibmi-native-compatibility-hash"
    )
    unused = {
        "liquidity": ("enabled", "use_rth"),
        "short_pressure": ("enabled", "fee_rate_lookback_sessions", "live_shortable_shares"),
        "volatility": ("enabled", "ceri_risk_max_contribution"),
        "options_activity": ("enabled", "shortlist_only", "generic_ticks"),
        "histogram": ("enabled", "period", "use_rth", "shortlist_only"),
    }[module]
    freshness_unused = {
        "liquidity": ("live_max_age_minutes",),
        "short_pressure": (),
        "volatility": ("live_max_age_minutes",),
        "options_activity": ("historical_max_age_days",),
        "histogram": ("historical_max_age_days", "live_max_age_minutes"),
    }[module]
    return _freeze(
        f"contextual.ibmi.{module}",
        values,
        sources,
        excluded=(
            "compatibility",
            *(f"config.{module}.{key}" for key in unused),
            *(f"config.freshness.{key}" for key in freshness_unused),
            "config.engine.enabled",
            "config.freshness.histogram_max_age_days",
        ),
    )
