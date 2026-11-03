"""Frozen, exact-catalog authority for the two Technical research edges only."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.producer_readiness import ReadinessReason, ReadinessStatus
from app.services.warning_taxonomy import normalize_warning_code, strategy_restriction_catalog

ALGORITHM = "certified-technical-strategy-research-v2"
SUPPORTED = {"COMBINED": "technical-to-combined-v2", "RANKING": "technical-to-ranking-v2"}


def research_rules():
    return {
        "catalog": strategy_restriction_catalog(),
        "allowed_category": "STRATEGY_RESTRICTION",
        "native_confidence": ["HIGH", "NORMAL"],
        "only_warning_reason": "PRODUCER_WARNING",
        "require_certified_identity": True,
        "require_native_history_quality": True,
        "preserve_producer_status": True,
        "trading_authority": False,
    }


@lru_cache(maxsize=64)
def decode_policy(serialized):
    import json

    from app.services.decision_effective_configuration import configuration_from_payload

    configuration = configuration_from_payload(json.loads(serialized))
    values = configuration.values
    policy = values.get("policy") or {}
    consumer = policy.get("consumer")
    if (
        SUPPORTED.get(consumer) != policy.get("policy_version")
        or configuration.snapshot.family.namespace
        != "decision.readiness.technical." + str(consumer).lower()
        or values.get("algorithm") != ALGORITHM
        or values.get("research_admission") != research_rules()
    ):
        raise ValueError("TECHNICAL_RESEARCH_POLICY_CONFIGURATION_UNSUPPORTED")
    return configuration


def research_qualification(readiness, configuration):
    """No score threshold or unknown/degraded catch-all confers permission."""
    values = configuration.values
    rules = values["research_admission"]
    signals = readiness.native_signals.canonical_payload()
    confidence = readiness.native_confidence.canonical_payload()
    native = signals.get("technical_data_readiness") or {}
    missing = signals.get("missing_data_json") or {}
    bar_quality = missing.get("bar_quality") or {}
    checks = {
        "certified_identity": bool(
            readiness.evidence_id and readiness.calculation_identity_fingerprint
        ),
        "degraded_warning_only": readiness.status is ReadinessStatus.DEGRADED
        and readiness.warning_reasons == (ReadinessReason.PRODUCER_WARNING,)
        and not readiness.blocking_reasons,
        "native_confidence": str(confidence.get("technical_confidence", "")).upper()
        in rules["native_confidence"]
        and str(native.get("confidence", "")).upper() in rules["native_confidence"],
        "explicit_sufficiency": signals.get("insufficient_data") is False
        and native.get("has_sufficient_history") is True
        and missing.get("has_sufficient_history") is True,
        "native_required_inputs": all(
            native.get(key) is True for key in ("has_price_data", "has_volume_data")
        )
        and not native.get("missing_reasons")
        and not missing.get("missing_reasons")
        and not any(
            value
            for key, value in missing.items()
            if key.startswith(("missing_", "insufficient_"))
            and key not in {"missing_reasons", "missing_columns"}
        )
        and not missing.get("missing_columns"),
        "session_complete": bar_quality.get("session_complete") is True
        and bar_quality.get("has_calendar_gap") is False
        and bar_quality.get("duplicate_date_count") == 0
        and bar_quality.get("missing_trading_session_count") == 0,
    }
    try:
        from app.services.technical_confidence import _data_quality_score

        expected_quality = _data_quality_score(
            has_price_data=True,
            has_volume_data=True,
            has_sufficient_history=True,
            missing_required_context=False,
        )
        checks["native_quality"] = (
            Decimal(str(confidence.get("data_quality_score")))
            == Decimal(str(expected_quality))
            == Decimal(str(native.get("data_quality_score")))
        )
        session = readiness.native_freshness.canonical_payload().get("input_as_of_session")
        cutoff = datetime.fromisoformat(readiness.evaluated_at.replace("Z", "+00:00"))
        checks["temporal_anchor"] = (
            cutoff.tzinfo is not None
            and date.fromisoformat(session) <= cutoff.date()
            and session
            == readiness.business_anchor
            == bar_quality.get("expected_session")
            == bar_quality.get("latest_observed_session")
        )
    except (ValueError, TypeError, AttributeError, InvalidOperation):
        checks["native_quality"] = False
        checks["temporal_anchor"] = False
    flags = signals.get("warning_flags_json")
    definitions = {row["code"]: row for row in rules["catalog"]["definitions"]}
    classified = []
    checks["catalogued_strategy_only"] = isinstance(flags, list) and bool(flags)
    if isinstance(flags, list):
        for code in flags:
            definition = definitions.get(normalize_warning_code(code))
            if (
                definition is None
                or definition["category"] != "STRATEGY_RESTRICTION"
                or definition["blocks_integrity_readiness"]
            ):
                checks["catalogued_strategy_only"] = False
            classified.append({"original_code": code, "definition": definition})
    return all(checks.values()), {
        "checks": checks,
        "warnings": classified,
        "catalog_version": rules["catalog"]["version"],
        "producer_status": readiness.status.value,
        "policy_semantic_hash": configuration.snapshot.semantic_hash,
        "policy_resolution_hash": configuration.snapshot.resolution_hash,
        "policy_configuration": configuration.snapshot.as_dict(),
        "trading_authority": False,
    }


def policy_for_core_configuration(config, fallback):
    from app.services.technical_consumer_eligibility import TechnicalConsumerPolicy

    payload = config.get("technical_admission") if config else None
    if payload is None:
        return fallback, None
    frozen = decode_policy(Canonical.dumps(payload))
    values = frozen.values["policy"]
    if values["consumer"] != fallback.consumer:
        raise ValueError("TECHNICAL_RESEARCH_CONSUMER_MISMATCH")
    return TechnicalConsumerPolicy(**values), frozen


def fresh_policy_payload(consumer):
    from app.services.decision_effective_configuration import resolve_readiness_policy_configuration
    from app.services.technical_consumer_eligibility import TechnicalConsumerPolicy

    return resolve_readiness_policy_configuration(
        TechnicalConsumerPolicy(consumer, SUPPORTED[consumer])
    ).snapshot.as_dict()


def research_only(permission):
    return "CERTIFIED_STRATEGY_RESTRICTION_ONLY" in permission["decision"]["reasons"]
