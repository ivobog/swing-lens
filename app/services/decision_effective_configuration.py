"""Decision authority built on the shared typed configuration contract.

Native adapters reconstruct retained values, never today's files or defaults.
Operational adapter fields are retained for native compatibility, but excluded
from each artifact's semantic identity.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache

from app.services.configuration_source_values import configuration_leaves
from app.services.contextual_effective_configuration import _restore
from app.services.core_effective_configuration import CoreEffectiveConfiguration, _decode
from app.services.effective_configuration import (
    ConfigurationClassification as Classification,
)
from app.services.effective_configuration import (
    ConfigurationEntry,
    ConfigurationFamily,
    ConfigurationResolution,
    ConfigurationSource,
    ConfigurationValueType,
    EffectiveConfigurationSnapshot,
    _secret_key,
)
from app.services.effective_configuration import (
    ConfigurationSourceKind as SourceKind,
)


@dataclass(frozen=True)
class DecisionEffectiveConfiguration(CoreEffectiveConfiguration):
    def require_family(self, namespace):
        if self.snapshot.family.namespace != namespace:
            raise ValueError("DECISION_CONFIGURATION_FAMILY_MISMATCH")
        if self.snapshot.family.schema_version != f"{namespace}-v1":
            raise ValueError("DECISION_CONFIGURATION_SCHEMA_MISMATCH")
        if any(
            e.source.kind is SourceKind.UNKNOWN
            for e in self.snapshot.entries
            if e.classification is not Classification.SECURITY_SECRET
        ):
            raise ValueError("DECISION_CONFIGURATION_UNKNOWN_AUTHORITY")

    def setup_config(self):
        from app.services.setup_lifecycle.config import SetupLifecycleConfig
        from app.services.setup_lifecycle.signal_registry import (
            SignalDefinition,
            SignalDefinitionRegistry,
        )

        values = self.values["native"]
        registry = SignalDefinitionRegistry(
            {row["key"]: _restore(SignalDefinition, row) for row in values["signal_registry"]}
        )
        values["signal_registry"] = None
        result = _restore(SetupLifecycleConfig, values)
        object.__setattr__(result, "signal_registry", registry)
        object.__setattr__(result, "_native_configuration_source", self._adapter_source())
        object.__setattr__(result, "_configuration_sources", self._adapter_sources())
        return result

    def _adapter_source(self):
        return next(
            e.source for e in self.snapshot.entries if e.key.startswith(("native.", "config."))
        )

    def _adapter_sources(self):
        return tuple(
            (e.key.removeprefix("native."), e.source)
            for e in self.snapshot.entries
            if e.key.startswith("native.")
        )

    def winner_config(self):
        from app.services.winner_probability.config import WinnerProbabilityConfig

        result = _restore(WinnerProbabilityConfig, self.values["native"])
        object.__setattr__(result, "_native_configuration_source", self._adapter_source())
        object.__setattr__(result, "_configuration_sources", self._adapter_sources())
        object.__setattr__(
            result, "_configuration_snapshots", {self.snapshot.family.namespace: self}
        )
        return result

    def ceri_decision_config(self):
        from dataclasses import fields

        from app.services.ceri.config import CeriConfig

        values = self.values["config"]
        result = _restore(
            CeriConfig, {field.name: values.get(field.name) for field in fields(CeriConfig)}
        )
        object.__setattr__(result, "_native_configuration_source", self._adapter_source())
        return result


def freeze_decision_configuration(
    namespace, values, behavioral_roots, *, source=None, sources=(), entry_sources=None
):
    values = json.loads(json.dumps(values, default=str))
    source = source or ConfigurationSource(SourceKind.PROFILE, namespace + "-native")
    source_map = {"native." + key: value for key, value in sources}
    source_map.update(entry_sources or {})
    entries = tuple(
        ConfigurationEntry(
            key,
            value,
            Classification.SECURITY_SECRET
            if _secret_key(key)
            else Classification.BEHAVIORAL
            if any(key == root or key.startswith(root + ".") for root in behavioral_roots)
            else Classification.OPERATIONAL,
            value_type={
                bool: ConfigurationValueType.BOOLEAN,
                int: ConfigurationValueType.INTEGER,
                float: ConfigurationValueType.REAL,
                str: ConfigurationValueType.STRING,
            }.get(type(value), ConfigurationValueType.STRUCTURE),
            source=ConfigurationSource(SourceKind.CODE_DEFAULT, namespace + "-code-policy")
            if key == "policy" or key.startswith("policy.") or key == "algorithm"
            else source_map.get(
                key, ConfigurationSource(SourceKind.DATABASE, "signal-alert-rule-authority")
            )
            if key == "rules" or key.startswith("rule_row_ids.")
            else source_map.get(key, source),
            defaulted=key in source_map and source_map[key].kind is SourceKind.CODE_DEFAULT,
        )
        for key, value in configuration_leaves(values)
    )
    family = ConfigurationFamily(
        namespace,
        f"{namespace}-v1",
        ConfigurationResolution(
            "decision_effective_configuration.resolve_" + namespace.replace(".", "_"),
            "1",
            (
                SourceKind.REQUEST,
                SourceKind.DATABASE,
                SourceKind.SETTINGS_MODEL,
                SourceKind.PROFILE,
                SourceKind.CODE_DEFAULT,
            ),
        ),
        tuple((entry.key, entry.classification) for entry in entries),
    )
    result = DecisionEffectiveConfiguration(EffectiveConfigurationSnapshot(family, entries))
    result.require_family(namespace)
    return result


def _setup_values(config):
    from app.services.setup_lifecycle.config import _normalized_data

    values = _normalized_data(config)
    values["config_hash"] = config.config_hash
    return json.loads(json.dumps(values, default=str))


def _native_source(config):
    return getattr(
        config,
        "_native_configuration_source",
        ConfigurationSource(SourceKind.REQUEST, "explicit-resolved-native-configuration"),
    )


def resolve_setup_configuration(config=None):
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return delivered_configuration("decision.setup")

    from app.services.setup_lifecycle.config import load_setup_lifecycle_config

    config = config or load_setup_lifecycle_config()
    return freeze_decision_configuration(
        "decision.setup",
        {"native": _setup_values(config), "policy": {"fresh_bar_grace_sessions": 3}},
        tuple(
            "native." + key
            for key in (
                "engine",
                "canonicalization",
                "confidence",
                "data_quality_labels",
                "signal_registry",
                "reconstructed_origin",
                "families",
                "phases",
                "actionability",
            )
        )
        + ("policy",),
        source=_native_source(config),
        sources=getattr(config, "_configuration_sources", ()),
    )


def resolve_lifecycle_configuration(config=None):
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return delivered_configuration("decision.lifecycle")

    from app.services.setup_lifecycle.config import load_setup_lifecycle_config

    config = config or load_setup_lifecycle_config()
    return freeze_decision_configuration(
        "decision.lifecycle",
        {"native": _setup_values(config), "policy": {"algorithm_version": "lifecycle-v1"}},
        tuple(
            "native." + key
            for key in (
                "engine",
                "states",
                "phases",
                "families",
                "episodes",
                "confidence",
                "actionability",
                "data_quality_labels",
                "signal_registry",
                "reconstructed_origin",
            )
        )
        + ("policy",),
        source=_native_source(config),
        sources=getattr(config, "_configuration_sources", ()),
    )


def resolve_alert_configuration(config=None, *, rules=()):
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return delivered_configuration("decision.alerts.setup")

    from app.services.setup_lifecycle.config import load_setup_lifecycle_config

    config = config or load_setup_lifecycle_config()
    rules = tuple(sorted(rules, key=lambda rule: rule.rule_id))
    rule_values = [
        {
            key: getattr(rule, key)
            for key in (
                "rule_id",
                "enabled",
                "severity",
                "scope",
                "setup_family",
                "cooldown_sessions",
                "minimum_confidence",
                "config_version",
                "condition_json",
                "market_restrictions_json",
            )
        }
        for rule in rules
    ]
    return freeze_decision_configuration(
        "decision.alerts.setup",
        {
            "native": _setup_values(config),
            "rules": rule_values,
            "rule_sources": {
                rule.rule_id: getattr(
                    rule,
                    "_configuration_rule_source",
                    ConfigurationSource(SourceKind.DATABASE, "signal-alert-rule-row"),
                ).as_dict()
                for rule in rules
            },
            "rule_row_ids": {rule.rule_id: rule.id for rule in rules},
            "policy": {
                "dedup": "source-event-semantic-key-v1",
                "cooldown": "us-trading-sessions-inclusive-history-v1",
            },
        },
        ("native.alerts", "native.reconstructed_origin", "rules", "policy"),
        source=_native_source(config),
        sources=getattr(config, "_configuration_sources", ()),
        entry_sources={
            "rules": ConfigurationSource(
                SourceKind.PIPELINE_CONTEXT, "selected-profile-database-rule-authority"
            )
        },
    )


WINNER_ROOTS = {
    "prediction": ("engine", "feature_schema", "filters", "episode", "entry_models"),
    "outcome": (
        "engine.calculation_version",
        "entry_models",
        "horizon",
        "outcome_definitions",
        "pending_outcomes",
    ),
    "cohort": (
        "engine.calculation_version",
        "feature_schema",
        "cohort",
        "evidence_grades",
        "cold_start",
        "filters",
        "outcome_definitions",
    ),
    "generation": (
        "engine.calculation_version",
        "feature_schema",
        "cohort",
        "evidence_grades",
        "cold_start",
        "filters",
        "outcome_definitions",
        "model_governance",
        "drift",
        "evidence_membership",
    ),
}


def current_code_policy(namespace):
    if namespace == "decision.setup":
        return {"fresh_bar_grace_sessions": 3}
    if namespace == "decision.lifecycle":
        return {"algorithm_version": "lifecycle-v1"}
    if namespace == "decision.alerts.setup":
        return {
            "dedup": "source-event-semantic-key-v1",
            "cooldown": "us-trading-sessions-inclusive-history-v1",
        }
    if namespace in {"decision.alerts.ceri", "decision.ceri.changes"}:
        family = "alerts" if namespace == "decision.alerts.ceri" else "changes"
        return {"algorithm_version": "ceri-" + family + "-v1"}
    if namespace.startswith("decision.winner."):
        family = namespace.rsplit(".", 1)[1]
        policy = {"algorithm_version": family + "-v1"}
        if family in {"cohort", "generation"}:
            from app.services.winner_probability.cohort_generation_service import (
                COHORT_ALGORITHM_VERSION,
            )

            policy["cohort_algorithm_version"] = COHORT_ALGORITHM_VERSION
        return policy
    return None


def validate_executable_configuration(configuration):
    """Retain history across software changes, but refuse unsupported execution."""
    current = current_code_policy(configuration.snapshot.family.namespace)
    if current is not None and configuration.values.get("policy") != current:
        raise ValueError("FROZEN_CONFIGURATION_CODE_POLICY_MISMATCH")


def resolve_winner_configuration(config=None, *, family="prediction"):
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is not None:
        return delivered_configuration("decision.winner." + family)
    from app.services.winner_probability.config import load_winner_probability_config

    config = config or load_winner_probability_config()
    if family not in WINNER_ROOTS:
        raise ValueError("unsupported Winner configuration family")
    retained = getattr(config, "_configuration_snapshots", {}).get("decision.winner." + family)
    if retained is not None:
        return retained
    values = json.loads(json.dumps(asdict(config), default=str))
    policy = {"algorithm_version": family + "-v1"}
    if family in {"cohort", "generation"}:
        from app.services.winner_probability.cohort_generation_service import (
            COHORT_ALGORITHM_VERSION,
        )

        policy["cohort_algorithm_version"] = COHORT_ALGORITHM_VERSION
    return freeze_decision_configuration(
        "decision.winner." + family,
        {"native": values, "policy": policy},
        tuple("native." + root for root in WINNER_ROOTS[family]) + ("policy",),
        source=_native_source(config),
        sources=getattr(config, "_configuration_sources", ()),
    )


def resolve_ceri_decision_configuration(config=None, *, family="alerts", enabled=True, rules=()):
    from app.services.ceri.config import load_ceri_config
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    namespace = "decision.alerts.ceri" if family == "alerts" else "decision.ceri.changes"
    if current_delivery() is not None:
        return delivered_configuration(namespace)
    config = config or load_ceri_config()
    rules = tuple(sorted(rules, key=lambda rule: rule.rule_id))
    roots = ("engine", "alerts", "revision") if family == "alerts" else ("change_thresholds",)
    values = asdict(config)
    selected = {key: values[key] for key in roots}
    selected = json.loads(json.dumps(selected, default=str))
    return freeze_decision_configuration(
        namespace,
        {
            "config": selected,
            "enabled": enabled,
            "rules": [
                {
                    key: getattr(rule, key)
                    for key in (
                        "rule_id",
                        "enabled",
                        "severity",
                        "cooldown_sessions",
                        "config_version",
                    )
                }
                for rule in rules
            ]
            if family == "alerts"
            else [],
            "policy": {"algorithm_version": "ceri-" + family + "-v1"},
        },
        ("config", "enabled", "policy", "rules"),
        source=_native_source(config),
        entry_sources={
            **{
                "config." + key: value
                for key, value in getattr(config, "_configuration_sources", ())
            },
            "enabled": ConfigurationSource(SourceKind.REQUEST, "ceri-downstream-enabled-policy"),
        },
    )


def winner_outcome_reference_configuration(base, reference_policy):
    """Freeze the selected auxiliary return rule without copying Sector authority."""
    values = {**base.values, "reference_policy": reference_policy}
    roots = tuple(
        e.key for e in base.snapshot.entries if e.classification is Classification.BEHAVIORAL
    )
    return freeze_decision_configuration(
        "decision.winner.outcome",
        values,
        roots + ("reference_policy",),
        source=base._adapter_source(),
        sources=base._adapter_sources(),
    )


def _configuration_from_payload(payload):
    """Decode and verify a retained T13A snapshot; no resolver invocation."""
    entries = tuple(
        ConfigurationEntry(
            item["key"],
            _decode(item["value"]) if item["value"] is not None else None,
            Classification(item["classification"]),
            ConfigurationValueType(item["value_type"]),
            source=ConfigurationSource(
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
            payload["resolution"]["resolver"],
            payload["resolution"]["contract_version"],
            tuple(SourceKind(kind) for kind in payload["resolution"]["precedence"]),
        ),
        tuple((entry.key, entry.classification) for entry in entries),
        payload["compatibility_policy"],
    )
    snapshot = EffectiveConfigurationSnapshot(family, entries)
    if (
        snapshot.semantic_hash != payload["semantic_hash"]
        or snapshot.resolution_hash != payload["resolution_hash"]
        or snapshot.identity.as_dict() != payload["identity"]
    ):
        raise ValueError("FROZEN_CONFIGURATION_INTEGRITY_MISMATCH")
    return DecisionEffectiveConfiguration(snapshot)


def configuration_from_payload(payload):
    try:
        return _configuration_from_payload(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("FROZEN_CONFIGURATION_INTEGRITY_MISMATCH") from exc


@lru_cache(maxsize=64)
def resolve_readiness_policy_configuration(policy):
    producer = getattr(policy, "producer", "TECHNICAL")
    module = getattr(policy, "module", None)
    namespace = "decision.readiness." + producer.lower() + "." + policy.consumer.lower()
    if module:
        namespace += "." + module.lower()
    return freeze_decision_configuration(
        namespace,
        {
            "policy": asdict(policy),
            "algorithm": "immutable-policy-version-strict-ready-blocked-degraded-unknown-v1",
        },
        ("policy", "algorithm"),
        source=ConfigurationSource(SourceKind.CODE_DEFAULT, "phase3-named-consumer-policy"),
    )


def validate_delivered_readiness_policy(policy):
    from app.services.configuration_delivery import current_delivery, delivered_configuration

    if current_delivery() is None:
        return
    current = resolve_readiness_policy_configuration(policy)
    retained = delivered_configuration(current.snapshot.family.namespace)
    if retained.snapshot.semantic_hash != current.snapshot.semantic_hash:
        raise ValueError("READINESS_POLICY_CONFIGURATION_ANCHOR_MISMATCH")
