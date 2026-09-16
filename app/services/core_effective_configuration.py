"""Core-stage configuration authority. Resolution happens once, before scoring.

The shared snapshot is the only retained state. Each consumer gets a fresh native
value tree; retries can receive the same object or rehydrate verified evidence.
No Settings object, environment value, absolute filename or credential is stored.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.services.calculation_identity import CalculationIdentity, IdentityDimension, IdentityState
from app.services.configuration_source_values import configuration_leaves as _leaves
from app.services.effective_configuration import (
    CONFIGURATION_PAYLOAD_KEY,
    ConfigurationCompatibilityStatus,
    ConfigurationEntry,
    ConfigurationFamily,
    ConfigurationResolution,
    ConfigurationSource,
    ConfigurationValueType,
    EffectiveConfigurationSnapshot,
    bind_configuration,
    compare_configuration,
    configuration_from_evidence,
)
from app.services.effective_configuration import (
    ConfigurationClassification as Classification,
)
from app.services.effective_configuration import (
    ConfigurationSourceKind as SourceKind,
)


def _decode(node: dict[str, Any]) -> Any:
    kind, value = node["type"], node["value"]
    if kind in {"null", "boolean", "integer", "string"}:
        return value
    if kind == "real":
        return float(value)
    if kind == "mapping":
        return {key: _decode(item) for key, item in value.items()}
    if kind == "list":
        return [_decode(item) for item in value]
    raise ValueError("core configuration requires JSON-native values")


@dataclass(frozen=True)
class CoreEffectiveConfiguration:
    snapshot: EffectiveConfigurationSnapshot

    def require_family(self, namespace: str) -> None:
        if self.snapshot.family.namespace != namespace:
            raise ValueError("core configuration family mismatch")
        required = {
            "core.fundamental": {
                "model_version",
                "weights",
                "missing_data",
                "thresholds",
                "field_priorities",
                "coverage_only_fields",
                "components",
            },
            "core.technical": {
                "pine",
                "v4",
                "v5",
                "benchmark_ticker",
                "v5_enabled",
                "v5_shadow_compare_enabled",
                "v5_persist_shadow_results",
            },
            "core.combined": {"combined_score", "penalties", "labels", "earnings_risk_gate"},
            "core.ranking": {"profile", "earnings_risk_gate"},
        }
        values = self.values
        if namespace not in required or not required[namespace] <= values.keys():
            raise ValueError("incomplete core effective configuration")
        if namespace == "core.technical" and (not values["pine"] or not values["v4"]):
            raise ValueError("Technical requires resolved Pine and v4 parameters")

    @property
    def values(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for entry in self.snapshot.entries:
            if entry.classification is Classification.SECURITY_SECRET:
                continue
            target = result
            parts = entry.key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = _decode(json.loads(entry.canonical_value_json))
        return result

    def bind(self, identity: CalculationIdentity) -> CalculationIdentity:
        return bind_configuration(identity, self.snapshot)

    def ranking_profile(self):
        from app.services.ranking_profile_config import (
            MissingDataPolicy,
            RankingProfileConfig,
            RankingThresholds,
            TradeabilityOverlayConfig,
        )

        self.require_family("core.ranking")
        values = self.values["profile"]
        values["missing_data_policy"] = MissingDataPolicy(**values["missing_data_policy"])
        values["thresholds"] = RankingThresholds(**values["thresholds"])
        values["tradeability_overlay"] = TradeabilityOverlayConfig(**values["tradeability_overlay"])
        return RankingProfileConfig(**values)

    def require_retry_identity(self, identity: CalculationIdentity) -> None:
        comparison = compare_configuration(
            identity.configuration.effective_configuration,
            IdentityDimension.known(self.snapshot.identity),
        )
        if comparison.status is not ConfigurationCompatibilityStatus.EXACT:
            raise ValueError("CORE_CONFIGURATION_RETRY_MISMATCH")


def freeze_core_configuration(
    namespace: str,
    values: dict[str, Any],
    *,
    sources: dict[str, ConfigurationSource] | None = None,
) -> CoreEffectiveConfiguration:
    """Freeze resolved values; absent provenance remains explicitly UNKNOWN."""
    display_keys = {"profile.name", "profile.label", "profile.description"}
    if namespace == "core.technical":
        display_keys.update(
            {
                "pine.engine.pine_version",
                "pine.engine.python_port_version",
                "pine.market_rs.marketSymbol",
                "pine.market_rs.benchmarkSymbol",
                "v4.engine.keep_v3_debug",
                "v4.engine.enabled",
                "v4.engine.base_engine_version",
                "v4.classification_v4.danger_priority",
            }
        )
        if values.get("v5_enabled"):
            display_keys.update({"v5_shadow_compare_enabled", "v5_persist_shadow_results"})
        elif not values.get("v5_shadow_compare_enabled"):
            display_keys.add("v5_persist_shadow_results")
    entries = tuple(
        ConfigurationEntry(
            key,
            value,
            Classification.OBSERVABILITY if key in display_keys else Classification.BEHAVIORAL,
            source=(sources or {}).get(key, ConfigurationSource()),
            defaulted=(sources or {}).get(key, ConfigurationSource()).kind
            is SourceKind.CODE_DEFAULT,
            overridden=(sources or {}).get(key, ConfigurationSource()).kind is SourceKind.PROFILE,
        )
        for key, value in _leaves(values)
    )
    for entry in entries:
        replayed = ConfigurationEntry(
            entry.key,
            _decode(json.loads(entry.canonical_value_json)),
            entry.classification,
            entry.value_type,
        )
        if replayed.canonical_value_json != entry.canonical_value_json:
            raise ValueError("core configuration cannot be replayed without numeric loss")
    family = ConfigurationFamily(
        namespace,
        f"{namespace}-v1",
        ConfigurationResolution(
            f"core_effective_configuration.resolve_{namespace.split('.')[-1]}_configuration",
            "1",
            (
                SourceKind.REQUEST,
                SourceKind.SETTINGS_MODEL,
                SourceKind.ENVIRONMENT,
                SourceKind.DOTENV,
                SourceKind.PROFILE,
                SourceKind.CODE_DEFAULT,
            )
            if namespace == "core.technical"
            else (SourceKind.REQUEST, SourceKind.PROFILE, SourceKind.CODE_DEFAULT),
        ),
        tuple((entry.key, entry.classification) for entry in entries),
    )
    return CoreEffectiveConfiguration(EffectiveConfigurationSnapshot(family, entries))


def core_configuration_from_evidence(evidence: Any) -> CoreEffectiveConfiguration | None:
    """Historical/retry reader. Verify evidence before decoding; no live resolver."""
    dimension = configuration_from_evidence(evidence)
    if dimension.state is not IdentityState.KNOWN:
        return None
    payload = evidence.payload_json[CONFIGURATION_PAYLOAD_KEY]
    resolution = payload["resolution"]
    entries = tuple(
        ConfigurationEntry(
            item["key"],
            _decode(item["value"]),
            Classification(item["classification"]),
            ConfigurationValueType(item["value_type"]),
            ConfigurationSource(
                SourceKind(item["source"]["kind"]),
                item["source"]["identifier"],
                item["source"]["version"],
            ),
            defaulted=item["defaulted"],
            overridden=item["overridden"],
        )
        for item in payload["entries"]
    )
    family = ConfigurationFamily(
        payload["semantic"]["namespace"],
        payload["semantic"]["schema_version"],
        ConfigurationResolution(
            resolution["resolver"],
            resolution["contract_version"],
            tuple(SourceKind(kind) for kind in resolution["precedence"]),
        ),
        tuple((entry.key, entry.classification) for entry in entries),
        payload["compatibility_policy"],
    )
    snapshot = EffectiveConfigurationSnapshot(family, entries)
    if (
        snapshot.semantic_hash != payload["semantic_hash"]
        or snapshot.resolution_hash != payload["resolution_hash"]
    ):
        raise ValueError("core configuration rehydration mismatch")
    return CoreEffectiveConfiguration(snapshot)


def core_configuration_for_row(row: Any) -> CoreEffectiveConfiguration | None:
    if getattr(row, "evidence_id", None) is None:
        return None
    evidence = row.calculation_evidence
    if evidence is None or evidence.id != row.evidence_id:
        raise ValueError("exact core configuration evidence unavailable")
    return core_configuration_from_evidence(evidence)


def _file_sources(values: dict[str, Any], identifier: str | None) -> dict[str, ConfigurationSource]:
    return {key: ConfigurationSource(SourceKind.PROFILE, identifier) for key, _ in _leaves(values)}


def resolve_fundamental_configuration(
    path: Path = Path("config/fundamentals_v2.yaml"),
    *,
    source_identifier: str | None = None,
) -> CoreEffectiveConfiguration:
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return CoreEffectiveConfiguration(delivered_configuration("core.fundamental").snapshot)

    from app.services.fundamental_ranker_v2 import load_fundamentals_v2_config

    values = load_fundamentals_v2_config(path).data
    return freeze_core_configuration(
        "core.fundamental",
        values,
        sources=_file_sources(
            values, source_identifier or (path.as_posix() if not path.is_absolute() else None)
        ),
    )


def resolve_combined_configuration(
    config: dict[str, Any] | None = None,
) -> CoreEffectiveConfiguration:
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return CoreEffectiveConfiguration(delivered_configuration("core.combined").snapshot)

    from app.services.combined_decision import _load_scoring_config
    from app.services.earnings_risk_service import _merged_config

    supplied = config is not None
    raw = config if supplied else _load_scoring_config()
    values = {key: raw[key] for key in ("combined_score", "penalties", "labels")}
    values["earnings_risk_gate"] = _merged_config(raw.get("earnings_risk_gate", {"enabled": False}))
    sources = dict(getattr(raw, "configuration_sources", ()))
    for key, _ in _leaves(values):
        if key not in dict(_leaves(raw)):
            sources[key] = ConfigurationSource(SourceKind.CODE_DEFAULT, "earnings-risk-defaults")
    return freeze_core_configuration("core.combined", values, sources=sources)


def resolve_ranking_configuration(
    profile: Any, config: dict[str, Any]
) -> CoreEffectiveConfiguration:
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return CoreEffectiveConfiguration(
            delivered_configuration("core.ranking", scope=profile.name).snapshot
        )

    from app.services.earnings_risk_service import _merged_config

    values = {
        "profile": asdict(profile),
        "earnings_risk_gate": _merged_config(config.get("earnings_risk_gate", {"enabled": False})),
    }
    sources = {
        f"profile.{key}": source for key, source in getattr(profile, "_configuration_sources", ())
    }
    sources.update(dict(getattr(config, "configuration_sources", ())))
    for key, _ in _leaves({"earnings_risk_gate": values["earnings_risk_gate"]}):
        if key not in dict(_leaves(config)):
            sources[key] = ConfigurationSource(SourceKind.CODE_DEFAULT, "earnings-risk-defaults")
    return freeze_core_configuration("core.ranking", values, sources=sources)


def resolve_technical_configuration(
    *,
    pine: dict[str, Any],
    v4: dict[str, Any],
    v5: dict[str, Any],
    settings: Any,
    benchmark_ticker: str = "SPY",
) -> CoreEffectiveConfiguration:
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return CoreEffectiveConfiguration(delivered_configuration("core.technical").snapshot)

    from app.services.technical_score_v4 import _danger_priority
    from app.services.technical_scoring_config import (
        DEFAULT_TECHNICAL_SCORING_V4_CONFIG,
        _deep_merge,
    )

    if not pine:
        raise ValueError("Technical requires resolved Pine parameters")
    merged_v4 = _deep_merge(DEFAULT_TECHNICAL_SCORING_V4_CONFIG, v4)

    resolved_v4 = {
        **merged_v4,
        "classification_v4": {
            **merged_v4.get("classification_v4", {}),
            "effective_danger_priority": _danger_priority(merged_v4),
        },
    }
    flags = {
        "v5_enabled": bool(getattr(settings, "technical_v5_enabled", False)),
        "v5_shadow_compare_enabled": bool(
            getattr(settings, "technical_v5_shadow_compare_enabled", True)
        ),
        "v5_persist_shadow_results": bool(
            getattr(settings, "technical_v5_persist_shadow_results", True)
        ),
    }
    sources = {
        f"{namespace}.{key}": source
        for namespace, config in (("pine", pine), ("v4", v4), ("v5", v5))
        for key, source in getattr(config, "configuration_sources", ())
    }
    sources.update(
        {
            key: ConfigurationSource(SourceKind.SETTINGS_MODEL, "resolved-technical-settings")
            for key in flags
        }
    )
    for key, kind, resolved_value in getattr(settings, "_core_configuration_sources", ()):
        flag = key.removeprefix("technical_")
        if flag in flags and flags[flag] == resolved_value:
            sources[flag] = ConfigurationSource(SourceKind(kind), "settings-native-resolution")
    sources["v4.classification_v4.effective_danger_priority"] = ConfigurationSource(
        SourceKind.CODE_DEFAULT,
        "technical-v4-priority-resolution",
    )
    sources["benchmark_ticker"] = ConfigurationSource(
        SourceKind.REQUEST, "technical-benchmark-request"
    )
    for key, _ in _leaves(resolved_v4):
        if key not in dict(_leaves(v4)):
            sources[f"v4.{key}"] = ConfigurationSource(
                SourceKind.CODE_DEFAULT, "technical-v4-defaults"
            )
    # Inactive v5 rules cannot affect this artifact. Flags still explain selection.
    active_v5 = flags["v5_enabled"] or flags["v5_shadow_compare_enabled"]
    return freeze_core_configuration(
        "core.technical",
        {
            "pine": pine,
            "v4": resolved_v4,
            "v5": v5 if active_v5 else {},
            "benchmark_ticker": benchmark_ticker.strip().upper(),
            **flags,
        },
        sources=sources,
    )
