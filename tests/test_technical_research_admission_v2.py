from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType

import pytest

from app.services.configuration_delivery import ConfigurationDelivery, configuration_delivery_scope
from app.services.decision_effective_configuration import resolve_readiness_policy_configuration
from app.services.producer_readiness import (
    NativeReadinessMetrics,
    ProducerReadinessEnvelope,
    ReadinessReason,
    ReadinessStatus,
)
from app.services.technical_consumer_eligibility import TechnicalConsumerPolicy


def healthy_risk_readiness(flags=None):
    flags = flags or ["market_risk_off", "distribution_risk"]
    native = {
        "has_price_data": True,
        "has_volume_data": True,
        "has_sufficient_history": True,
        "has_benchmark_data": True,
        "has_market_data": True,
        "has_htf_data": True,
        "has_relative_strength_data": True,
        "missing_reasons": [],
        "confidence": "high",
        "data_quality_score": 10,
    }
    missing = {
        **native,
        "insufficient_history": False,
        "missing_columns": [],
        "bar_quality": {
            "session_complete": True,
            "has_calendar_gap": False,
            "duplicate_date_count": 0,
            "missing_trading_session_count": 0,
            "expected_session": "2026-10-30",
            "latest_observed_session": "2026-10-30",
        },
    }
    return ProducerReadinessEnvelope(
        "TECHNICAL",
        ReadinessStatus.DEGRADED,
        "a" * 64,
        warning_reasons=(ReadinessReason.PRODUCER_WARNING,),
        native_confidence=NativeReadinessMetrics.freeze(
            {"technical_confidence": "high", "data_quality_score": "10"}
        ),
        native_freshness=NativeReadinessMetrics.freeze({"input_as_of_session": "2026-10-30"}),
        native_signals=NativeReadinessMetrics.freeze(
            {
                "technical_data_readiness": native,
                "missing_data_json": missing,
                "insufficient_data": False,
                "warning_flags_json": flags,
            }
        ),
        evaluated_at="2026-11-02T17:18:43.440620Z",
        business_anchor="2026-10-30",
        evidence_id=123,
    )


@pytest.mark.parametrize("consumer", ["COMBINED", "RANKING"])
def test_only_two_versioned_research_edges_admit_risk_only_degradation(consumer):
    source = healthy_risk_readiness()
    original = deepcopy(source.to_dto())
    policy = TechnicalConsumerPolicy(consumer, f"technical-to-{consumer.lower()}-v2")
    decision = policy.evaluate(source)
    assert decision.status.value == "ELIGIBLE"
    assert [reason.value for reason in decision.reasons] == ["CERTIFIED_STRATEGY_RESTRICTION_ONLY"]
    proof = decision.justification.canonical_payload()
    assert all(proof["checks"].values())
    assert proof["trading_authority"] is False and proof["research_only"] is True
    assert proof["catalog_version"] == "warning-taxonomy-v4"
    assert source.to_dto() == original and source.status is ReadinessStatus.DEGRADED
    assert (
        TechnicalConsumerPolicy("SETUP", "technical-to-setup-v1").evaluate(source).status.value
        == "POLICY_UNDECIDED"
    )


@pytest.mark.parametrize(
    "flags",
    [
        ["market_risk_off", "missing_market_data"],
        ["distribution_risk", "unknown_new_warning"],
        ["market_risk_off", "low_technical_confidence"],
        ["market_risk_off", "liquidity_warning"],
        ["market_risk_off", "stale_market_data"],
        ["market_risk_off", "integrity_error"],
    ],
)
def test_unknown_mixed_quality_and_non_strategy_warnings_do_not_gain_permission(flags):
    decision = TechnicalConsumerPolicy("RANKING", "technical-to-ranking-v2").evaluate(
        healthy_risk_readiness(flags)
    )
    assert decision.status.value != "ELIGIBLE"


@pytest.mark.parametrize(
    "mutation",
    ["missing_history", "low_confidence", "quality", "session", "uncertified", "extra_reason"],
)
def test_independent_quality_history_temporal_and_certificate_requirements(mutation):
    source = healthy_risk_readiness()
    signals = source.native_signals.canonical_payload()
    if mutation == "missing_history":
        signals["insufficient_data"] = True
    elif mutation == "low_confidence":
        source = replace(
            source,
            native_confidence=NativeReadinessMetrics.freeze(
                {"technical_confidence": "low", "data_quality_score": 10}
            ),
        )
    elif mutation == "quality":
        signals["technical_data_readiness"]["data_quality_score"] = 0
    elif mutation == "session":
        signals["missing_data_json"]["bar_quality"]["latest_observed_session"] = "2026-10-29"
    elif mutation == "uncertified":
        source = replace(source, evidence_id=None)
    else:
        source = replace(
            source,
            warning_reasons=(
                ReadinessReason.PRODUCER_WARNING,
                ReadinessReason.LOW_NATIVE_CONFIDENCE,
            ),
        )
    source = replace(source, native_signals=NativeReadinessMetrics.freeze(signals))
    assert (
        TechnicalConsumerPolicy("COMBINED", "technical-to-combined-v2")
        .evaluate(source)
        .status.value
        != "ELIGIBLE"
    )


@pytest.mark.parametrize(
    "status",
    [
        ReadinessStatus.STALE,
        ReadinessStatus.ERROR,
        ReadinessStatus.INSUFFICIENT_EVIDENCE,
        ReadinessStatus.UNKNOWN,
        ReadinessStatus.LEGACY_UNKNOWN,
    ],
)
def test_v2_preserves_non_ready_exclusions(status):
    source = replace(healthy_risk_readiness(), status=status)
    assert (
        TechnicalConsumerPolicy("COMBINED", "technical-to-combined-v2")
        .evaluate(source)
        .status.value
        != "ELIGIBLE"
    )


def test_retained_v1_authority_replays_original_decision_and_refuses_v2_substitution():
    source = healthy_risk_readiness()
    policy = TechnicalConsumerPolicy("COMBINED", "technical-to-combined-v1")
    original = policy.evaluate(source).to_dto()
    assert "justification" not in original
    frozen = resolve_readiness_policy_configuration(policy)
    with configuration_delivery_scope(
        ConfigurationDelivery({}, MappingProxyType({frozen.snapshot.family.namespace: frozen}))
    ):
        assert policy.evaluate(source).to_dto() == original
        with pytest.raises(ValueError, match="READINESS_POLICY_CONFIGURATION_ANCHOR_MISMATCH"):
            TechnicalConsumerPolicy("COMBINED", "technical-to-combined-v2").evaluate(source)


@pytest.mark.parametrize(
    "fundamental_score,technical_score,classification,expected,complete,ceiling",
    [
        ("1", "1", "Prime clean pullback", "Avoid", True, "preserve-stricter-v1"),
        (None, "9", "Prime clean pullback", "Incomplete data", False, "preserve-stricter-v1"),
        ("9", "9", "Prime clean pullback", "Watchlist", True, "preserve-stricter-v1"),
        ("9", "9", "Climax reversal risk", "Avoid", True, "preserve-stricter-v1"),
        (None, "9", "Prime clean pullback", "Watchlist", False, None),
    ],
)
def test_research_ceiling_never_promotes_low_priority_or_incomplete_inputs(
    monkeypatch, fundamental_score, technical_score, classification, expected, complete, ceiling
):
    from decimal import Decimal

    from test_combined_decision import _config, _fundamental, _row

    from app.models.tables import TechnicalScore
    from app.services import combined_decision
    from app.services.technical_research_admission import fresh_policy_payload

    technical = TechnicalScore(
        ticker="ACME",
        classification=classification,
        dual_score=Decimal(technical_score),
        risk_score=Decimal("2"),
        debug_json={"derived": {"liquidity_warning": False}},
        warning_flags_json=["market_risk_off"],
        insufficient_data=False,
    )

    # Admission itself is covered by the certified PostgreSQL path. Here the
    # admitted boundary is fixed so actual scoring and missing-input gates vary.
    def admitted(row, policy, **kwargs):
        assert policy.policy_version == "technical-to-combined-v2"
        return row, {"decision": {"reasons": ["CERTIFIED_STRATEGY_RESTRICTION_ONLY"]}}

    monkeypatch.setattr(combined_decision, "technical_decision_input", admitted)
    config = _config()
    config["technical_admission"] = fresh_policy_payload("COMBINED")
    if ceiling is not None:
        config["technical_research_decision_ceiling"] = ceiling
    fundamental = (
        _fundamental("ACME", "Clean compounder", fundamental_score) if fundamental_score else None
    )
    result = combined_decision.combine_row_decision(
        _row("ACME"), fundamental, technical, config=config
    )
    assert result.combined_decision == expected
    assert result.is_complete is complete
    assert result.position_size_hint == "No new entry"


def test_new_ceiling_configuration_does_not_retrofit_frozen_consumers():
    from app.services.core_effective_configuration import (
        freeze_core_configuration,
        resolve_combined_configuration,
    )

    new = resolve_combined_configuration()
    assert new.values["technical_research_decision_ceiling"] == "preserve-stricter-v1"
    values = new.values
    values.pop("technical_research_decision_ceiling")
    old = freeze_core_configuration(
        "core.combined",
        values,
        sources={
            entry.key: entry.source
            for entry in new.snapshot.entries
            if entry.key != "technical_research_decision_ceiling"
        },
    )
    assert old.snapshot.semantic_hash != new.snapshot.semantic_hash
    with configuration_delivery_scope(
        ConfigurationDelivery({}, MappingProxyType({"core.combined": old}))
    ):
        retained = resolve_combined_configuration()
        assert retained.snapshot == old.snapshot
        assert "technical_research_decision_ceiling" not in retained.values
