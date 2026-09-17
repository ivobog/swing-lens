from copy import deepcopy

import pytest
from contextual_readiness_helpers import seal_contextual
from test_contextual_calculation_identity_adoption import _RowsDb
from test_t12c_ready_scenarios import ceri_snapshot, ready_ceri_sources

from app.services.ceri import capture_service as capture


@pytest.mark.parametrize("vol_bad,short_bad", [(True, False), (False, True), (True, True)])
def test_ceri_constituents_omit_independently_before_existing_evidence_logic(
    monkeypatch,
    vol_bad,
    short_bad,
):
    cutoff, config, volatility, short_pressure = ready_ceri_sources(monkeypatch)
    if vol_bad:
        volatility.coverage_status = "UNAVAILABLE"
        seal_contextual(volatility)
    if short_bad:
        short_pressure.confidence = "INSUFFICIENT"
        seal_contextual(short_pressure)
    decisions = {}
    kwargs = dict(
        as_of_session=cutoff.latest_completed_session,
        market_cutoff=cutoff,
        ibmi_config=config,
        decisions=decisions,
    )
    vol = capture._point_in_time_volatility_feature(
        _RowsDb([volatility]),
        "MSFT",
        cutoff.cutoff_at,
        **kwargs,
    )
    short = capture._point_in_time_short_pressure_feature(
        _RowsDb([short_pressure]),
        "MSFT",
        cutoff.cutoff_at,
        **kwargs,
    )
    assert (vol is None) is vol_bad
    assert (short is None) is short_bad
    assert decisions["ibmi_volatility"]["included"] is (not vol_bad)
    assert decisions["ibmi_short_pressure"]["included"] is (not short_bad)
    snapshot, opportunity, confidence, risk = ceri_snapshot(cutoff, vol, short)
    missing, missing_opp, missing_conf, missing_risk = ceri_snapshot(
        cutoff,
        None if vol_bad else vol,
        None if short_bad else short,
    )
    assert opportunity == missing_opp and confidence == missing_conf and risk == missing_risk
    assert snapshot.opportunity_score == missing.opportunity_score
    assert snapshot.event_risk_score == (0 if vol_bad else 1.5)
    assert snapshot.posture == "Unrated"
    assert opportunity.score is None and not opportunity.rated
    assert opportunity.available_weight == 0
    assert opportunity.coverage_pct < opportunity.minimum_required_coverage_pct
    assert not any(component.available for component in opportunity.components)
    has_short_context = any("ibkr_short_pressure_context:" in reason for reason in risk.reasons)
    assert has_short_context is (not short_bad)
    assert any(penalty["name"] == "ibkr_options_event_premium" for penalty in risk.penalties) is (
        not vol_bad
    )


@pytest.mark.parametrize(
    "module,edge,selector",
    [
        ("VOLATILITY", "ibmi_volatility", capture._point_in_time_volatility_feature),
        ("SHORT_PRESSURE", "ibmi_short_pressure", capture._point_in_time_short_pressure_feature),
    ],
)
@pytest.mark.parametrize("status", ["STALE", "ERROR", "UNKNOWN", "LEGACY_UNKNOWN", "DEGRADED"])
def test_ceri_exact_invalid_constituent_cannot_fall_back_to_older_or_current_ready(
    monkeypatch,
    module,
    edge,
    selector,
    status,
):
    cutoff, config, volatility, short_pressure = ready_ceri_sources(monkeypatch)
    source = volatility if module == "VOLATILITY" else short_pressure
    older = deepcopy(source)
    older.id += 1
    source.id += 2
    if status == "STALE":
        source.freshness_status = "STALE"
    elif status == "ERROR":
        source.coverage_status = "FAILED"
    elif status == "UNKNOWN":
        source.freshness_status = "FRESH"  # unrecognized by the native T12A contract
    elif status == "DEGRADED":
        source.confidence = "LOW"
    seal_contextual(source, evidence_id=999004)
    if status == "LEGACY_UNKNOWN":
        source.evidence_id = None
    decisions = {}
    selected = selector(
        _RowsDb([source, older]),
        "MSFT",
        cutoff.cutoff_at,
        as_of_session=cutoff.latest_completed_session,
        market_cutoff=cutoff,
        ibmi_config=config,
        decisions=decisions,
    )
    assert selected is None
    assert decisions[edge]["source_feature_id"] == source.id
    assert decisions[edge]["producer_readiness"]["status"] == status
    assert not decisions[edge]["included"]


def test_eligible_volatility_numeric_zero_remains_distinct_from_omission(monkeypatch):
    cutoff, config, volatility, _short = ready_ceri_sources(monkeypatch)
    volatility.components_json = {"iv_hv_ratio": 1.0}
    seal_contextual(volatility)
    decisions = {}
    vol = capture._point_in_time_volatility_feature(
        _RowsDb([volatility]),
        "MSFT",
        cutoff.cutoff_at,
        as_of_session=cutoff.latest_completed_session,
        market_cutoff=cutoff,
        ibmi_config=config,
        decisions=decisions,
    )
    assert vol is not None
    assert decisions["ibmi_volatility"]["included"]
    snapshot, _opportunity, _confidence, risk = ceri_snapshot(cutoff, vol, None)
    assert snapshot.event_risk_score == 0 and risk.penalties == ()
    volatility.coverage_status = "UNAVAILABLE"
    seal_contextual(volatility, evidence_id=999005)
    omitted = capture._point_in_time_volatility_feature(
        _RowsDb([volatility]),
        "MSFT",
        cutoff.cutoff_at,
        as_of_session=cutoff.latest_completed_session,
        market_cutoff=cutoff,
        ibmi_config=config,
        decisions=decisions,
    )
    assert omitted is None
    assert not decisions["ibmi_volatility"]["included"]
