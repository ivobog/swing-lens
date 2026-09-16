"""Deliver an already resolved compositional bundle across durable boundaries.

Execution tokens are lease ownership. This context is calculation authority and
does not change when a job is retried or reclaimed.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.tables import (
    EffectiveConfigurationRecord,
    ExecutionConfigurationAnchor,
    ExecutionConfigurationBinding,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.decision_effective_configuration import (
    configuration_from_payload,
    validate_executable_configuration,
)

ANCHOR_KEY = "effective_configuration_anchor"
CONTRACT = "pipeline-effective-configuration-anchor-v1"
_DELIVERY = ContextVar("effective_configuration_delivery", default=None)
_RUNTIME_SETTINGS = ContextVar("delivered_runtime_settings", default=None)


@dataclass(frozen=True)
class ConfigurationDelivery:
    anchor: dict
    configurations: object


def current_delivery():
    return _DELIVERY.get()


def anchored_job_configuration(calculation):
    """Also preserve authority when a native handler is invoked directly.

    Unanchored direct legacy APIs are current-rules entry points; the durable
    worker independently rejects unanchored business jobs before invoking them.
    """

    @wraps(calculation)
    def execute(db, job, *args, **kwargs):
        reference = (job.payload_json or {}).get(ANCHOR_KEY)
        if not isinstance(db, Session) or reference is None:
            return calculation(db, job, *args, **kwargs)
        expected = binding_reference(db, job_id=job.id)
        if reference != expected:
            raise ValueError("CONFIGURATION_ANCHOR_PARENT_MISMATCH")
        current = current_delivery()
        if current is not None:
            if current.anchor != expected:
                raise ValueError("CONFIGURATION_ANCHOR_PARENT_MISMATCH")
            return calculation(db, job, *args, **kwargs)
        delivery = load_configuration_delivery(db, reference, expected_anchor=expected)
        with configuration_delivery_scope(delivery):
            return calculation(db, job, *args, **kwargs)

    return execute


def pipeline_configuration_delivery(calculation):
    @wraps(calculation)
    def execute(db, pipeline_run_id, *args, **kwargs):
        if not isinstance(db, Session):
            return calculation(db, pipeline_run_id, *args, **kwargs)
        reference = binding_reference(db, pipeline_run_id=pipeline_run_id)
        current = current_delivery()
        if current is not None:
            if current.anchor != reference:
                raise ValueError("CONFIGURATION_ANCHOR_PARENT_MISMATCH")
            return calculation(db, pipeline_run_id, *args, **kwargs)
        delivery = load_configuration_delivery(db, reference)
        with configuration_delivery_scope(delivery):
            return calculation(db, pipeline_run_id, *args, **kwargs)

    return execute


def delivered_configuration(namespace, *, scope=None):
    delivery = current_delivery()
    if delivery is None:
        return None
    key = namespace if scope is None else namespace + ":" + scope
    config = delivery.configurations.get(key)
    if config is None:
        raise ValueError("MISSING_FROZEN_CONFIGURATION: " + namespace)
    return config


def anchor_fingerprint(references):
    """Business fingerprint excludes operational values and resolution provenance."""
    return Canonical.fingerprint(
        {
            "contract": CONTRACT,
            "configurations": {key: value["identity"] for key, value in sorted(references.items())},
        }
    )


def persist_configuration_anchor(db, configurations):
    references = {}
    if not isinstance(configurations, dict):
        configurations = {c.snapshot.family.namespace: c for c in configurations}
    snapshots = {c.snapshot.resolution_hash: c.snapshot for c in configurations.values()}
    if snapshots:
        values = [
            dict(
                resolution_hash=s.resolution_hash,
                namespace=s.family.namespace,
                semantic_hash=s.semantic_hash,
                payload_json=s.as_dict(),
            )
            for s in snapshots.values()
        ]
        db.execute(insert(EffectiveConfigurationRecord).values(values).on_conflict_do_nothing())
    for key, config in configurations.items():
        snapshot = config.snapshot
        references[key] = {
            "record": snapshot.resolution_hash,
            "identity": snapshot.identity.as_dict(),
        }
    anchor = {
        "contract": CONTRACT,
        "configurations": references,
        "fingerprint": anchor_fingerprint(references),
    }
    # Integrity also covers provenance/reference addresses excluded from semantics.
    anchor["integrity"] = Canonical.fingerprint(anchor)
    db.execute(
        insert(ExecutionConfigurationAnchor)
        .values(
            anchor_id=anchor["integrity"],
            fingerprint=anchor["fingerprint"],
            payload_json=anchor,
        )
        .on_conflict_do_nothing()
    )
    return {"anchor_id": anchor["integrity"], "fingerprint": anchor["fingerprint"]}


def validate_anchor(anchor):
    if not isinstance(anchor, dict) or anchor.get("contract") != CONTRACT:
        raise ValueError("MISSING_CONFIGURATION_ANCHOR")
    if anchor.get("fingerprint") != anchor_fingerprint(anchor["configurations"]) or anchor.get(
        "integrity"
    ) != Canonical.fingerprint({key: value for key, value in anchor.items() if key != "integrity"}):
        raise ValueError("CONFIGURATION_ANCHOR_INTEGRITY_MISMATCH")


def load_configuration_delivery(db, anchor, *, expected_anchor=None):
    if expected_anchor is not None and anchor != expected_anchor:
        raise ValueError("CONFIGURATION_ANCHOR_PARENT_MISMATCH")
    reference = anchor
    if not isinstance(reference, dict) or set(reference) != {"anchor_id", "fingerprint"}:
        raise ValueError("MISSING_CONFIGURATION_ANCHOR")
    retained = db.get(ExecutionConfigurationAnchor, reference["anchor_id"])
    if retained is None or retained.fingerprint != reference["fingerprint"]:
        raise ValueError("MISSING_OR_MISMATCHED_CONFIGURATION_ANCHOR")
    anchor = retained.payload_json
    validate_anchor(anchor)
    if anchor["integrity"] != retained.anchor_id:
        raise ValueError("CONFIGURATION_ANCHOR_INTEGRITY_MISMATCH")
    references = anchor["configurations"]
    rows = db.scalars(
        select(EffectiveConfigurationRecord).where(
            EffectiveConfigurationRecord.resolution_hash.in_(
                {ref["record"] for ref in references.values()}
            )
        )
    ).all()
    by_hash = {row.resolution_hash: row for row in rows}
    configs = {}
    for namespace, ref in references.items():
        row = by_hash.get(ref["record"])
        if row is None:
            raise ValueError("MISSING_FROZEN_CONFIGURATION_RECORD")
        config = configuration_from_payload(row.payload_json)
        if config.snapshot.family.namespace.startswith("decision."):
            config.require_family(config.snapshot.family.namespace)
        validate_executable_configuration(config)
        if (
            config.snapshot.family.namespace != ref["identity"]["namespace"]
            or (
                namespace != row.namespace
                and not (
                    row.namespace == "core.ranking"
                    and namespace == "core.ranking:" + config.values["profile"]["name"]
                )
            )
            or config.snapshot.identity.as_dict() != ref["identity"]
            or config.snapshot.resolution_hash != row.resolution_hash
            or config.snapshot.semantic_hash != row.semantic_hash
            or row.namespace != config.snapshot.family.namespace
        ):
            raise ValueError("CONFIGURATION_ANCHOR_RECORD_MISMATCH")
        configs[namespace] = config
    return ConfigurationDelivery(Canonical.canonicalize(reference), MappingProxyType(configs))


def binding_reference(db, *, job_id=None, pipeline_run_id=None, winner_cohort_generation_id=None):
    if winner_cohort_generation_id is not None:
        key = "winner-generation:" + str(winner_cohort_generation_id)
    else:
        key = "job:" + str(job_id) if job_id is not None else "pipeline:" + str(pipeline_run_id)
    binding = db.get(ExecutionConfigurationBinding, key)
    if binding is None:
        raise ValueError("MISSING_CONFIGURATION_ANCHOR_BINDING")
    anchor = db.get(ExecutionConfigurationAnchor, binding.anchor_id)
    if (
        anchor is None
        or binding.job_id != job_id
        or binding.pipeline_run_id != pipeline_run_id
        or binding.winner_cohort_generation_id != winner_cohort_generation_id
    ):
        raise ValueError("CONFIGURATION_ANCHOR_BINDING_MISMATCH")
    return {"anchor_id": anchor.anchor_id, "fingerprint": anchor.fingerprint}


def bind_winner_generation_configuration(db, generation, config):
    from app.services.decision_effective_configuration import resolve_winner_configuration

    if generation.config_hash != config.config_hash:
        raise ValueError("WINNER_GENERATION_CONFIGURATION_MISMATCH")
    key = "winner-generation:" + str(generation.id)
    if db.get(ExecutionConfigurationBinding, key) is None and not getattr(
        generation, "_configuration_generation_created", False
    ):
        raise ValueError("MISSING_WINNER_GENERATION_FROZEN_CONFIGURATION")
    configurations = [
        resolve_winner_configuration(config, family=family) for family in ("cohort", "generation")
    ]
    reference = persist_configuration_anchor(db, configurations)
    db.execute(
        insert(ExecutionConfigurationBinding)
        .values(
            binding_key=key,
            anchor_id=reference["anchor_id"],
            winner_cohort_generation_id=generation.id,
        )
        .on_conflict_do_nothing()
    )
    if binding_reference(db, winner_cohort_generation_id=generation.id) != reference:
        raise ValueError("WINNER_GENERATION_CONFIGURATION_ANCHOR_MISMATCH")
    return reference


def configuration_for_winner_generation(db, generation, config):
    if generation.config_hash != config.config_hash:
        raise ValueError("WINNER_GENERATION_CONFIGURATION_MISMATCH")
    binding = db.get(ExecutionConfigurationBinding, "winner-generation:" + str(generation.id))
    if binding is None:
        if current_delivery() is not None:
            raise ValueError("MISSING_WINNER_GENERATION_FROZEN_CONFIGURATION")
        # A standalone current-rules operation can make NEW proof, but cannot
        # certify or backfill the historical generation's original authority.
        from app.services.decision_effective_configuration import resolve_winner_configuration

        decoded = resolve_winner_configuration(config, family="cohort").winner_config()
        object.__setattr__(
            decoded, "_configuration_execution_semantics", "CURRENT_RULES_LEGACY_GENERATION"
        )
        return decoded
    reference = binding_reference(db, winner_cohort_generation_id=generation.id)
    delivery = load_configuration_delivery(db, reference)
    retained = delivery.configurations["decision.winner.cohort"]
    from app.services.decision_effective_configuration import resolve_winner_configuration

    if (
        retained.snapshot.semantic_hash
        != resolve_winner_configuration(config, family="cohort").snapshot.semantic_hash
    ):
        raise ValueError("WINNER_GENERATION_CONFIGURATION_ANCHOR_MISMATCH")
    return retained.winner_config()


def bind_job_configuration(db, job):
    if not durable_business_job(job.job_type):
        return
    reference = job.payload_json[ANCHOR_KEY]
    scopes = [("job:" + str(job.id), {"job_id": job.id})]
    pipeline_id = job.payload_json.get("pipeline_run_id")
    if pipeline_id is not None:
        scopes.append(("pipeline:" + str(pipeline_id), {"pipeline_run_id": pipeline_id}))
    for key, scope in scopes:
        db.execute(
            insert(ExecutionConfigurationBinding)
            .values(
                binding_key=key,
                anchor_id=reference["anchor_id"],
                **scope,
            )
            .on_conflict_do_nothing()
        )
        expected = binding_reference(db, **scope)
        if expected != reference:
            raise ValueError("CONFIGURATION_ANCHOR_BINDING_MISMATCH")


@contextmanager
def configuration_delivery_scope(delivery):
    token = _DELIVERY.set(delivery)
    settings_token = _RUNTIME_SETTINGS.set(None)
    try:
        yield delivery
    finally:
        _DELIVERY.reset(token)
        _RUNTIME_SETTINGS.reset(settings_token)


def settings_for_delivery(current_settings):
    if current_delivery() is None:
        return current_settings
    retained = _RUNTIME_SETTINGS.get()
    if retained is None:
        retained = current_settings.model_copy(
            update=delivered_configuration("execution.settings").values
        )
        _RUNTIME_SETTINGS.set(retained)
    return retained


def resolve_pipeline_configurations(db, job_type="FULL_PIPELINE", payload=None):
    """New execution only. One bulk resolution, reusable safe snapshot references."""
    from dataclasses import asdict

    from app.services.ceri.config import load_ceri_config
    from app.services.combined_decision import _load_scoring_config
    from app.services.contextual_effective_configuration import (
        resolve_ceri_configuration,
        resolve_ibmi_configuration,
        resolve_regime_configuration,
        resolve_sector_configuration,
    )
    from app.services.core_effective_configuration import (
        resolve_combined_configuration,
        resolve_fundamental_configuration,
        resolve_ranking_configuration,
        resolve_technical_configuration,
    )
    from app.services.decision_effective_configuration import (
        freeze_decision_configuration,
        resolve_alert_configuration,
        resolve_ceri_decision_configuration,
        resolve_lifecycle_configuration,
        resolve_setup_configuration,
        resolve_winner_configuration,
    )
    from app.services.effective_configuration import ConfigurationSource, ConfigurationSourceKind
    from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
    from app.services.ranking_profile_config import load_ranking_profiles
    from app.services.setup_lifecycle.config import load_setup_lifecycle_config
    from app.services.setup_lifecycle.repository import SetupLifecycleRepository
    from app.services.technical_indicators import load_pine_defaults
    from app.services.technical_scoring_config import load_technical_scoring_v4_config
    from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
    from app.services.winner_probability.config import load_winner_probability_config
    from app.settings import get_settings

    settings = get_settings()
    pine, v4, v5 = (
        load_pine_defaults(),
        load_technical_scoring_v4_config(),
        load_technical_scoring_v5_config(),
    )
    scoring = _load_scoring_config()
    full = job_type == "FULL_PIPELINE"
    setup_needed = full or job_type.startswith("SETUP_")
    winner_needed = full or job_type.startswith("WINNER_")
    contextual_needed = full or winner_needed or job_type.startswith(("CERI_", "IB_"))
    setup = load_setup_lifecycle_config() if setup_needed else None
    winner = load_winner_probability_config() if winner_needed else None
    ceri = load_ceri_config() if contextual_needed else None
    ibmi = load_ib_market_intelligence_config() if contextual_needed else None
    from app.services.ceri.feature_flags import ceri_flags

    ceri_consumer = None
    if contextual_needed:
        ceri_consumer = {
            "run_capture": ceri_flags(settings).run_capture,
            "revision_feature_config_hash": ceri.config_hash,
            "ibmi_enabled": settings.ib_market_intelligence_enabled,
            "volatility_enabled": settings.ib_volatility_intelligence_enabled,
            "short_pressure_enabled": settings.ib_short_pressure_enabled,
            "volatility": {
                "ceri_risk_max_contribution": float(
                    ibmi.section("volatility").get("ceri_risk_max_contribution", 1.5)
                )
            },
        }
    effective_rules = {}
    if setup_needed:
        from app.models.tables import SignalAlertRule

        effective_rules = {r.rule_id: r for r in SetupLifecycleRepository().alert_rules(db)}
        if setup.alerts.built_in_rules_enabled:
            for rule_id, rule in setup.alerts.rules.items():
                previous = effective_rules.get(rule_id)
                effective_rules[rule_id] = SignalAlertRule(
                    id=previous.id if previous is not None else None,
                    rule_id=rule_id,
                    enabled=rule.enabled,
                    severity=rule.severity.value,
                    scope=rule.source,
                    setup_family=rule.filters.get("setup_family"),
                    cooldown_sessions=rule.cooldown_sessions,
                    minimum_confidence=rule.minimum_confidence,
                    config_version=setup.engine.config_version,
                    condition_json=dict(rule.filters),
                    market_restrictions_json=rule.filters.get("market_restrictions"),
                )
                effective_rules[
                    rule_id
                ]._configuration_rule_source = setup._native_configuration_source
    configs = [
        resolve_fundamental_configuration(),
        resolve_combined_configuration(scoring),
        resolve_technical_configuration(pine=pine, v4=v4, v5=v5, settings=settings),
        resolve_regime_configuration(pine=pine, v4=v4),
        resolve_sector_configuration(pine=pine, v4=v4),
        freeze_decision_configuration("execution.scoring", {"native": scoring}, ()),
        freeze_decision_configuration(
            "execution.settings",
            {key: getattr(settings, key) for key in EXECUTION_SETTINGS},
            tuple(EXECUTION_SETTINGS),
            source=ConfigurationSource(
                ConfigurationSourceKind.SETTINGS_MODEL, "durable-behavioral-switches"
            ),
        ),
    ]
    if setup_needed:
        configs.extend(
            [
                resolve_setup_configuration(setup),
                resolve_lifecycle_configuration(setup),
                resolve_alert_configuration(
                    setup, rules=tuple(effective_rules[key] for key in sorted(effective_rules))
                ),
            ]
        )
    if winner_needed:
        configs.extend(
            resolve_winner_configuration(winner, family=family)
            for family in ("prediction", "outcome", "cohort", "generation")
        )
    if contextual_needed:
        from app.models.ceri_tables import CeriAlertRule

        ceri_rules = db.scalars(select(CeriAlertRule).order_by(CeriAlertRule.rule_id)).all()
        from app.services.ceri.feature_flags import parse_explicit_bool

        alerts_requested = ceri.alerts.enabled
        if job_type == "CERI_ALERT_REBUILD":
            alerts_requested = parse_explicit_bool(
                (payload or {}).get("alerts_enabled"), default=ceri_flags(settings).alerts
            )
        configs.extend(
            [
                resolve_ceri_configuration(ceri, consumer=ceri_consumer),
                resolve_ceri_decision_configuration(
                    ceri, enabled=ceri_flags(settings).alerts and alerts_requested, rules=ceri_rules
                ),
                resolve_ceri_decision_configuration(ceri, family="changes"),
                freeze_decision_configuration("execution.ceri", {"native": asdict(ceri)}, ()),
            ]
        )
        configs.extend(
            resolve_ibmi_configuration(ibmi, module=name)
            for name in (
                "liquidity",
                "short_pressure",
                "volatility",
                "options_activity",
                "histogram",
            )
        )
    result = {c.snapshot.family.namespace: c for c in configs}
    for profile in load_ranking_profiles():
        result["core.ranking:" + profile.name] = resolve_ranking_configuration(profile, scoring)
    for frozen in resolve_readiness_configurations():
        result[frozen.snapshot.family.namespace] = frozen
    return result


# Behavioral execution/module switches, not transport, concurrency or credentials.
EXECUTION_SETTINGS = (
    "technical_v5_enabled",
    "technical_v5_shadow_compare_enabled",
    "technical_v5_persist_shadow_results",
    "setup_lifecycle_pipeline_step_enabled",
    "setup_capture_handoff_enabled",
    "winner_probability_enabled",
    "winner_probability_capture_in_pipeline",
    "winner_cohort_refresh_v2_enabled",
    "ceri_enabled",
    "ceri_run_capture_enabled",
    "ceri_alerts_enabled",
    "ceri_provider_ingest_enabled",
    "ceri_backfill_enabled",
    "ib_market_intelligence_enabled",
    "ib_volatility_intelligence_enabled",
    "ib_short_pressure_enabled",
)


def durable_business_job(job_type):
    return (
        job_type == "FULL_PIPELINE"
        or job_type.startswith(("SETUP_", "WINNER_", "CERI_", "IB_INTELLIGENCE_"))
        or job_type == "IB_HISTOGRAM_FETCH"
    )


def anchor_enqueue_payload(db, job_type, payload, *, parent_job_id=None):
    """Child/resume inherit; a genuinely new root resolves once before enqueue."""
    if not durable_business_job(job_type):
        return payload
    from app.models.tables import BackgroundJob

    delivery = current_delivery()
    expected = delivery.anchor if delivery is not None else None
    if parent_job_id is not None:
        parent = db.get(BackgroundJob, parent_job_id)
        if parent is None:
            raise ValueError("MISSING_CONFIGURATION_ANCHOR_PARENT")
        expected = binding_reference(db, job_id=parent.id)
    pipeline_id = payload.get("pipeline_run_id")
    if pipeline_id is not None:
        existing_binding = db.get(ExecutionConfigurationBinding, "pipeline:" + str(pipeline_id))
        existing = binding_reference(db, pipeline_run_id=pipeline_id) if existing_binding else None
        if existing is not None:
            if expected is not None and expected != existing:
                raise ValueError("CONFIGURATION_ANCHOR_PARENT_MISMATCH")
            expected = existing
        elif payload.get("resume_from_step"):
            raise ValueError("MISSING_CONFIGURATION_ANCHOR_FOR_RESUME")
    supplied = payload.get(ANCHOR_KEY)
    if expected is not None:
        if supplied is not None and supplied != expected:
            raise ValueError("CONFIGURATION_ANCHOR_PARENT_MISMATCH")
        anchor = expected
    elif supplied is not None:
        load_configuration_delivery(db, supplied)
        anchor = supplied
    else:
        anchor = persist_configuration_anchor(
            db, resolve_pipeline_configurations(db, job_type, payload)
        )
    return {**payload, ANCHOR_KEY: anchor}


def delivered_native(kind):
    from app.services.contextual_effective_configuration import (
        ContextualEffectiveConfiguration,
        _restore,
    )
    from app.services.core_effective_configuration import CoreEffectiveConfiguration

    if kind in {"pine", "v4", "v5"}:
        return delivered_configuration("core.technical").values[kind]
    if kind == "fundamental":
        from app.services.fundamental_ranker_v2 import parse_fundamentals_v2_config

        values = delivered_configuration("core.fundamental").values
        return parse_fundamentals_v2_config(
            {key: value for key, value in values.items() if key != "column_aliases"}
        )
    if kind == "scoring":
        return delivered_configuration("execution.scoring").values["native"]
    if kind == "ranking":
        return [
            CoreEffectiveConfiguration(c.snapshot).ranking_profile()
            for key, c in current_delivery().configurations.items()
            if key.startswith("core.ranking:")
        ]
    if kind == "regime":
        frozen = delivered_configuration("contextual.regime")
        return ContextualEffectiveConfiguration(frozen.snapshot).regime_config()
    if kind == "sector":
        from app.services.configuration_source_values import SourcedConfigurationValues

        frozen = delivered_configuration("contextual.sector")
        result = SourcedConfigurationValues(frozen.values["config"], ())
        result.effective_configuration = ContextualEffectiveConfiguration(frozen.snapshot)
        return result
    if kind == "setup":
        return delivered_configuration("decision.setup").setup_config()
    if kind == "winner":
        return delivered_configuration("decision.winner.prediction").winner_config()
    if kind == "ceri":
        from app.services.ceri.config import CeriConfig

        return _restore(CeriConfig, delivered_configuration("execution.ceri").values["native"])
    if kind == "ibmi":
        frozen = delivered_configuration("contextual.ibmi.liquidity")
        config = ContextualEffectiveConfiguration(frozen.snapshot).ibmi_config()
        raw = dict(config.raw)
        for module in ("short_pressure", "volatility", "options_activity", "histogram"):
            raw.update(delivered_configuration("contextual.ibmi." + module).values["config"])
        from dataclasses import replace

        return replace(config, raw=raw)
    raise ValueError("UNSUPPORTED_FROZEN_NATIVE_CONFIGURATION: " + kind)


def resolve_readiness_configurations():
    """The existing named Phase-3 policies, deduplicated by their own family."""
    result = {}
    from app.services import contextual_consumer_eligibility, technical_consumer_eligibility
    from app.services.decision_effective_configuration import resolve_readiness_policy_configuration
    from app.services.winner_probability import consumer_eligibility

    policy_types = (
        contextual_consumer_eligibility.ContextualConsumerPolicy,
        technical_consumer_eligibility.TechnicalConsumerPolicy,
    )
    for module in (
        contextual_consumer_eligibility,
        technical_consumer_eligibility,
        consumer_eligibility,
    ):
        for policy in vars(module).values():
            if isinstance(policy, policy_types):
                frozen = resolve_readiness_policy_configuration(policy)
                result[frozen.snapshot.family.namespace] = frozen
    return tuple(result.values())


def anchored_decision_calculator(calculation):
    """Preconstructed native services cannot supersede a delivered authority."""

    @wraps(calculation)
    def execute(self, *args, **kwargs):
        if current_delivery() is not None:
            owned = self.effective_configuration.snapshot
            retained = delivered_configuration(owned.family.namespace).snapshot
            if owned.resolution_hash != retained.resolution_hash:
                raise ValueError("DECISION_SERVICE_CONFIGURATION_ANCHOR_MISMATCH")
        return calculation(self, *args, **kwargs)

    return execute
