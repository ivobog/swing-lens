from copy import deepcopy
from dataclasses import asdict, replace

import pytest
from contextual_readiness_helpers import ibmi_feature, seal_contextual
from setup_lifecycle.test_snapshot_builder import (
    _market_snapshot,
    _sector_row,
    _sector_snapshot,
    _ticker_context,
)
from test_ranking_profile_engine import _config, _fundamental, _profile, _row, _technical

from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import SetupLifecycleEvent, SetupSignalSnapshot
from app.services.ceri.event_risk_service import CeriEventRiskService
from app.services.contextual_consumer_eligibility import (
    CONTEXTUAL_ELIGIBILITY_KEY,
    IBMI_LIQUIDITY_TO_RANKING,
    IBMI_SHORT_PRESSURE_TO_CERI,
    IBMI_VOLATILITY_TO_CERI,
    REGIME_TO_SETUP,
    SECTOR_TO_SETUP,
    contextual_decision_input,
    setup_with_contextual_permission,
)
from app.services.core_calculation_evidence import EvidenceUnavailableError
from app.services.producer_readiness import (
    ConsumerEligibilityStatus,
    NativeReadinessMetrics,
    ProducerReadinessEnvelope,
    ReadinessReason,
    ReadinessStatus,
    readiness_from_evidence,
)
from app.services.ranking_profile_config import TradeabilityOverlayConfig
from app.services.ranking_profile_engine import rank_profile, rank_single_row
from app.services.setup_lifecycle.alert_service import _event_market_regime
from app.services.setup_lifecycle.episode_service import normalized_snapshot_from_row
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder

POLICIES = (
    IBMI_LIQUIDITY_TO_RANKING,
    IBMI_VOLATILITY_TO_CERI,
    IBMI_SHORT_PRESSURE_TO_CERI,
    REGIME_TO_SETUP,
    SECTOR_TO_SETUP,
)


@pytest.mark.parametrize("policy", POLICIES, ids=lambda item: item.policy_version)
@pytest.mark.parametrize("status", list(ReadinessStatus))
def test_policy_matrix_is_explicit_typed_and_retry_deterministic(policy, status):
    readiness = ProducerReadinessEnvelope(policy.producer, status, "identity", evidence_id=71)
    decision = policy.evaluate(readiness)
    expected = (
        ConsumerEligibilityStatus.ELIGIBLE
        if status is ReadinessStatus.READY
        else ConsumerEligibilityStatus.INELIGIBLE
        if status
        in {
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
            ReadinessStatus.ERROR,
            ReadinessStatus.STALE,
        }
        else ConsumerEligibilityStatus.POLICY_UNDECIDED
    )
    assert decision.status is expected
    assert decision == policy.evaluate(readiness)
    assert decision.policy_version == policy.policy_version
    assert decision.producer_evidence_id == 71
    assert decision.producer_readiness_fingerprint == readiness.fingerprint()
    with pytest.raises(ValueError, match="no configurable overrides"):
        policy.evaluate(readiness, config=NativeReadinessMetrics.freeze({"allow_degraded": True}))


@pytest.mark.parametrize("policy", POLICIES, ids=lambda item: item.policy_version)
def test_blocking_reason_never_grants_permission(policy):
    envelope = ProducerReadinessEnvelope(
        policy.producer,
        ReadinessStatus.UNKNOWN,
        "identity",
        blocking_reasons=(ReadinessReason.INSUFFICIENT_NATIVE_CONFIDENCE,),
    )
    assert policy.evaluate(envelope).status is ConsumerEligibilityStatus.INELIGIBLE


@pytest.mark.parametrize(
    "values",
    [
        {"coverage_status": "UNAVAILABLE"},
        {"coverage_status": "FAILED"},
        {"freshness_status": "STALE"},
        {"freshness_status": "FRESH"},
        {"confidence": "LOW"},
        {"confidence": "INSUFFICIENT"},
        {"legacy": True},
    ],
)
def test_invalid_liquidity_matches_missing_path_without_double_penalty_or_peer_effect(values):
    values = dict(values)
    legacy = values.pop("legacy", False)
    feature = ibmi_feature(**values)
    if legacy:
        feature.evidence_id = None
    profile = replace(
        _profile("momentum_swing"),
        tradeability_overlay=TradeabilityOverlayConfig(
            enabled=True,
            poor_penalty=0.5,
            very_poor_penalty=1,
            maximum_penalty=0.75,
        ),
    )
    row, fundamental = _row("MSFT"), _fundamental("MSFT", 8)
    technical = _technical("MSFT", trend=8, momentum=8, setup=8, risk=2, rs=8)
    baseline = rank_single_row(
        profile=profile,
        row=row,
        fundamental=fundamental,
        technical=technical,
        config=_config(),
    )
    omitted = rank_single_row(
        profile=profile,
        row=row,
        fundamental=fundamental,
        technical=technical,
        config=_config(),
        liquidity_feature=feature,
    )
    assert replace(omitted, debug=baseline.debug) == baseline
    assert "ibkr_tradeability" not in omitted.penalties
    assert omitted.has_technical and omitted.is_complete
    permission = omitted.debug[CONTEXTUAL_ELIGIBILITY_KEY]["ibmi_liquidity"]
    assert not permission["included"]
    assert permission["decision"]["status"] != "ELIGIBLE"
    feature.components_json = {"dollar_volume": 0}
    repeated = rank_single_row(
        profile=profile,
        row=row,
        fundamental=fundamental,
        technical=technical,
        config=_config(),
        liquidity_feature=feature,
    )
    assert omitted == repeated
    alone = rank_profile(
        profile=profile,
        rows=[row],
        fundamentals={"MSFT": fundamental},
        technicals={"MSFT": technical},
        config=_config(),
    )
    with_invalid = rank_profile(
        profile=profile,
        rows=[row],
        fundamentals={"MSFT": fundamental},
        technicals={"MSFT": technical},
        config=_config(),
        liquidity_features={"MSFT": feature},
    )
    assert replace(with_invalid[0], debug=alone[0].debug) == alone[0]


def test_same_liquidity_different_readiness_and_valid_zero_are_distinct():
    feature = ibmi_feature(components_json={"dollar_volume": 0, "iv_hv_ratio": 2.5})
    allowed, dto = contextual_decision_input(feature, IBMI_LIQUIDITY_TO_RANKING)
    assert allowed.components_json["dollar_volume"] == 0 and dto["included"]
    old = deepcopy(dto)
    feature.coverage_status = "UNAVAILABLE"
    seal_contextual(feature, evidence_id=999001)
    omitted, blocked = contextual_decision_input(feature, IBMI_LIQUIDITY_TO_RANKING)
    assert omitted is None and not blocked["included"]
    assert old["decision"]["status"] == "ELIGIBLE"
    assert blocked["decision"]["status"] == "INELIGIBLE"
    assert feature.components_json["dollar_volume"] == 0


def test_frozen_ready_values_cannot_be_replaced_by_current_numeric_or_classification():
    feature = ibmi_feature()
    feature.components_json = {"iv_hv_ratio": 0, "dollar_volume": 999999999}
    feature.classification = "EXCELLENT"
    feature.coverage_status = "UNAVAILABLE"
    eligible, permission = contextual_decision_input(feature, IBMI_LIQUIDITY_TO_RANKING)
    assert eligible.classification == "VERY_POOR"
    assert eligible.components_json["dollar_volume"] == 2_000_000
    assert permission["producer_readiness"]["status"] == "READY"


def test_unresolved_wrong_producer_module_and_scope_fail_without_fallback():
    feature = IBIntelligenceFeature(evidence_id=99)
    with pytest.raises(EvidenceUnavailableError, match="detached"):
        contextual_decision_input(feature, IBMI_LIQUIDITY_TO_RANKING)
    feature = ibmi_feature()
    with pytest.raises(EvidenceUnavailableError, match="module/scope"):
        contextual_decision_input(feature, IBMI_VOLATILITY_TO_CERI)
    feature.calculation_evidence.ticker = "OTHER"
    with pytest.raises(EvidenceUnavailableError, match="module/scope"):
        contextual_decision_input(feature, IBMI_LIQUIDITY_TO_RANKING)


def ready_setup_context():
    context = _ticker_context()
    market = _market_snapshot()
    market.confidence = "normal"
    market.gate_ok = True
    sector, row = context.sector_rotation_snapshot or _sector_snapshot(), _sector_row()
    seal_contextual(market)
    seal_contextual(sector, rows=(row,))
    return replace(
        context,
        market_regime_snapshot=market,
        sector_rotation_snapshot=sector,
        sector_rotation_row=row,
    )


@pytest.mark.parametrize("regime_bad,sector_bad", [(True, False), (False, True), (True, True)])
def test_contexts_omit_independently_and_use_existing_optional_quality(regime_bad, sector_bad):
    context = ready_setup_context()
    if regime_bad:
        context.market_regime_snapshot.warning_flags_json = ["severely_stale_market_data"]
        # Native Regime mapping reads emitted warning names, not a new age threshold.
        context.market_regime_snapshot.warnings_json = ["severely_stale_market_data"]
        seal_contextual(context.market_regime_snapshot, evidence_id=999002)
    if sector_bad:
        context.sector_rotation_row.confidence = "insufficient"
        seal_contextual(
            context.sector_rotation_snapshot,
            rows=(context.sector_rotation_row,),
            evidence_id=999003,
        )
    built = SetupLifecycleSnapshotBuilder().build(context).dto
    missing = (
        SetupLifecycleSnapshotBuilder()
        .build(
            replace(
                context,
                market_regime_snapshot=None if regime_bad else context.market_regime_snapshot,
                sector_rotation_snapshot=None if sector_bad else context.sector_rotation_snapshot,
                sector_rotation_row=None if sector_bad else context.sector_rotation_row,
            )
        )
        .dto
    )
    assert built.promoted_fields == missing.promoted_fields
    assert built.signals == missing.signals
    assert built.data_quality_label == missing.data_quality_label
    permissions = built.source_lineage[CONTEXTUAL_ELIGIBILITY_KEY]
    assert permissions["regime"]["included"] is (not regime_bad)
    assert permissions["sector"]["included"] is (not sector_bad)
    assert built.signals["market_gate"]["value"] is (None if regime_bad else True)
    assert built.signals["sector_rank"]["value"] == (None if sector_bad else 1)


def test_frozen_context_omission_cannot_be_cleared_by_residual_setup_signals_or_policy_change():
    context = ready_setup_context()
    context.market_regime_snapshot.warning_flags_json = ["stale_market_data"]
    context.market_regime_snapshot.warnings_json = ["stale_market_data"]
    seal_contextual(context.market_regime_snapshot)
    built = SetupLifecycleSnapshotBuilder().build(context).dto
    row = SetupSignalSnapshot()
    SetupLifecycleRepository()._apply_snapshot_fields(row, built)
    normalized = normalized_snapshot_from_row(row)
    signals = dict(normalized.signals)
    signals["market_gate"] = replace(signals["market_gate"], raw_value=True, normalized_value=True)
    signals["market_regime"] = replace(signals["market_regime"], raw_value="RISK_ON")
    restored = setup_with_contextual_permission(replace(normalized, signals=signals))
    assert restored.signals["market_gate"].raw_value is None
    assert restored.signals["market_regime"].raw_value is None
    assert (
        restored.source_lineage[CONTEXTUAL_ELIGIBILITY_KEY]
        == (normalized.source_lineage[CONTEXTUAL_ELIGIBILITY_KEY])
    )
    assert restored.source_lineage["market_regime_as_of"] is None


def test_sparse_regime_producer_gap_is_recorded_without_new_consumer_threshold():
    context = ready_setup_context()
    market = context.market_regime_snapshot
    market.confidence = "low"
    market.index_health_json = {"SPY": {"bar_count": 1}}
    seal_contextual(market)
    assert readiness_from_evidence(market.calculation_evidence).status is ReadinessStatus.DEGRADED
    omitted, permission = contextual_decision_input(market, REGIME_TO_SETUP)
    assert omitted is None
    assert permission["decision"]["status"] == "POLICY_UNDECIDED"


def test_ceri_missing_volatility_is_absent_and_not_an_available_zero_or_double_penalty():
    service = CeriEventRiskService()
    missing = service.calculate(as_of_session=ibmi_feature().as_of_session)
    excluded = service.calculate(
        as_of_session=ibmi_feature().as_of_session, options_event_premium_score=None
    )
    assert excluded == missing
    assert not any(entry["name"] == "ibkr_options_event_premium" for entry in excluded.penalties)
    assert "ibkr_options_event_premium" not in excluded.reasons
    assert asdict(excluded) == asdict(missing)


def test_sparse_normal_regime_remains_native_ready_producer_gap():
    context = ready_setup_context()
    market = context.market_regime_snapshot
    market.confidence = "NORMAL"
    market.index_health_json = {"SPY": {"bar_count": 1}}
    seal_contextual(market)
    assert readiness_from_evidence(market.calculation_evidence).status is ReadinessStatus.READY
    included, permission = contextual_decision_input(market, REGIME_TO_SETUP)
    assert included is not None
    assert permission["decision"]["status"] == "ELIGIBLE"


def test_setup_frozen_decision_rejects_wrong_context_producer_binding():
    built = SetupLifecycleSnapshotBuilder().build(ready_setup_context()).dto
    row = SetupSignalSnapshot()
    SetupLifecycleRepository()._apply_snapshot_fields(row, built)
    normalized = normalized_snapshot_from_row(row)
    lineage = deepcopy(normalized.source_lineage)
    permission = lineage[CONTEXTUAL_ELIGIBILITY_KEY]["regime"]
    permission["producer_readiness"]["producer"] = "SECTOR"
    readiness = ProducerReadinessEnvelope.from_payload(permission["producer_readiness"])
    permission["decision"]["producer_readiness_fingerprint"] = readiness.fingerprint()
    with pytest.raises(ValueError, match="binding mismatch"):
        setup_with_contextual_permission(replace(normalized, source_lineage=lineage))


def test_alert_context_fallback_cannot_restore_omitted_regime():
    context = ready_setup_context()
    context.market_regime_snapshot.warnings_json = ["stale_market_data"]
    seal_contextual(context.market_regime_snapshot)
    row = SetupSignalSnapshot()
    SetupLifecycleRepository()._apply_snapshot_fields(
        row,
        SetupLifecycleSnapshotBuilder().build(context).dto,
    )
    row.signals_json = {**row.signals_json, "market_regime": {"value": "RISK_ON"}}
    assert _event_market_regime(SetupLifecycleEvent(evidence_json={}), row) is None
