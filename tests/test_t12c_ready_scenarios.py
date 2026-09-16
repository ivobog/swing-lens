"""Identical fully eligible business scenarios runnable against T12B and T12C."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from contextual_readiness_helpers import ibmi_feature, seal_contextual
from setup_lifecycle.test_snapshot_builder import (
    _market_snapshot,
    _sector_row,
    _sector_snapshot,
    _ticker_context,
)
from test_contextual_calculation_identity_adoption import _cutoff, _RowsDb
from test_ranking_profile_engine import _config, _fundamental, _row, _technical

from app.models.tables import SetupSignalSnapshot
from app.services.ceri import capture_service as capture
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.event_risk_service import CeriEventRiskService
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.ib_market_intelligence.calculations import options_event_premium_score
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.ranking_profile_config import TradeabilityOverlayConfig, load_ranking_profiles
from app.services.ranking_profile_engine import rank_profile
from app.services.setup_lifecycle.actionability_policy import SetupLifecycleActionabilityPolicy
from app.services.setup_lifecycle.enums import Actionability, LifecycleState
from app.services.setup_lifecycle.episode_service import normalized_snapshot_from_row
from app.services.setup_lifecycle.lifecycle_engine import (
    LifecycleEvaluationInput,
    SetupLifecycleEngine,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder


@pytest.mark.parametrize("profile", load_ranking_profiles(), ids=lambda item: item.name)
def test_ready_liquidity_all_profiles_preserve_full_population(profile):
    profile = replace(
        profile,
        tradeability_overlay=TradeabilityOverlayConfig(
            enabled=True,
            poor_penalty=0.5,
            very_poor_penalty=1,
            maximum_penalty=0.75,
        ),
    )
    rows = [_row(ticker) for ticker in ("A", "B", "C")]
    fundamentals = {row.ticker: _fundamental(row.ticker, 8) for row in rows}
    technicals = {
        row.ticker: _technical(row.ticker, trend=8, momentum=8, setup=8, risk=2, rs=8)
        for row in rows
    }
    liquidities = {row.ticker: ibmi_feature(ticker=row.ticker) for row in rows}
    decisions = rank_profile(
        profile=profile,
        rows=rows,
        fundamentals=fundamentals,
        technicals=technicals,
        config=_config(),
        liquidity_features=liquidities,
    )
    assert [decision.profile_rank for decision in decisions] == [1, 2, 3]
    assert all(decision.penalties["ibkr_tradeability"] == 0.75 for decision in decisions)


def ready_ceri_sources(monkeypatch):
    cutoff = _cutoff()
    config = load_ib_market_intelligence_config()
    monkeypatch.setattr(
        capture,
        "get_settings",
        lambda: SimpleNamespace(
            ib_market_intelligence_enabled=True,
            ib_volatility_intelligence_enabled=True,
            ib_short_pressure_enabled=True,
        ),
    )
    common = dict(
        config_hash=config.config_hash,
        calculation_cutoff_at=cutoff.cutoff_at,
        calendar_version=cutoff.calendar_version,
        as_of_session=cutoff.latest_completed_session,
    )
    return (
        cutoff,
        config,
        ibmi_feature("VOLATILITY", **common),
        ibmi_feature(
            "SHORT_PRESSURE",
            classification="HIGH",
            **common,
        ),
    )


def ceri_snapshot(cutoff, volatility, short_pressure):
    risk = CeriEventRiskService().calculate(
        as_of_session=cutoff.latest_completed_session,
        options_event_premium_score=(
            options_event_premium_score(volatility) if volatility else None
        ),
        short_pressure_classification=short_pressure.classification if short_pressure else None,
    )
    opportunity = CeriOpportunityScoreService().calculate(
        revision_features=[],
        as_of_session=cutoff.latest_completed_session,
    )
    confidence = CeriConfidenceService().calculate(
        revision_features=[],
        as_of_session=cutoff.latest_completed_session,
    )
    snapshot = CeriSnapshotService().build_snapshot(
        company_id=1,
        ticker="MSFT",
        as_of_session=cutoff.latest_completed_session,
        cutoff_at=cutoff.cutoff_at,
        opportunity=opportunity,
        event_risk=risk,
        confidence=confidence,
        source_ids=[],
    )
    return snapshot, opportunity, confidence, risk


def test_ready_ibmi_exact_ceri_scores_confidence_posture_and_drivers(monkeypatch):
    cutoff, config, volatility, short_pressure = ready_ceri_sources(monkeypatch)
    kwargs = dict(
        as_of_session=cutoff.latest_completed_session, market_cutoff=cutoff, ibmi_config=config
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
    assert vol and short
    snapshot, opportunity, _confidence, risk = ceri_snapshot(cutoff, vol, short)
    assert risk.score == 1.5
    assert any("ibkr_short_pressure_context:" in reason for reason in risk.reasons)
    assert opportunity.available_weight == 0 and not opportunity.rated
    assert snapshot.posture == "Unrated"


@pytest.mark.parametrize("prior", [None, LifecycleState.DEVELOPING, LifecycleState.READY])
def test_ready_regime_sector_actual_setup_lifecycle_chain(prior):
    market = _market_snapshot()
    market.confidence = "normal"
    market.gate_ok = True
    sector, row = _sector_snapshot(), _sector_row()
    seal_contextual(market)
    seal_contextual(sector, rows=(row,))
    context = _ticker_context(
        market_regime_snapshot=market, sector_rotation_snapshot=sector, sector_rotation_row=row
    )
    built = SetupLifecycleSnapshotBuilder().build(context).dto
    model = SetupSignalSnapshot()
    SetupLifecycleRepository()._apply_snapshot_fields(model, built)
    normalized = normalized_snapshot_from_row(model)
    decision = SetupLifecycleEngine().evaluate(
        LifecycleEvaluationInput(
            snapshot=normalized,
            previous_state=prior,
        )
    )
    actionability = SetupLifecycleActionabilityPolicy().evaluate(decision, normalized)
    assert actionability.actionability is not Actionability.BLOCKED
