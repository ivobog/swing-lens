from __future__ import annotations

from dataclasses import replace

import pytest
from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.actionability_policy import SetupLifecycleActionabilityPolicy
from app.services.setup_lifecycle.enums import Actionability, DataQualityLabel, LifecycleState
from app.services.setup_lifecycle.lifecycle_engine import evaluate_lifecycle


def test_ready_with_imminent_earnings_remains_ready_but_actionability_is_blocked() -> None:
    normalized = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        earnings_risk="IMMINENT",
    )
    lifecycle = evaluate_lifecycle(normalized)

    actionability = SetupLifecycleActionabilityPolicy().evaluate(lifecycle, normalized)

    assert lifecycle.proposed_state is LifecycleState.READY
    assert actionability.actionability is Actionability.BLOCKED
    assert actionability.blockers == ("IMMINENT_EARNINGS",)


def test_stale_source_ready_state_becomes_low_confidence_not_blocked() -> None:
    normalized = replace(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            data_quality=DataQualityLabel.LOW,
        ),
        warning_flags=("STALE_PRICE_BAR",),
    )
    lifecycle = evaluate_lifecycle(normalized)

    actionability = SetupLifecycleActionabilityPolicy().evaluate(lifecycle, normalized)

    assert lifecycle.proposed_state is LifecycleState.READY
    assert actionability.actionability is Actionability.LOW_CONFIDENCE
    assert "LOW_CONFIDENCE_SOURCE" in actionability.reason_codes


def test_failed_and_extended_are_not_actionable() -> None:
    failed_snapshot = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        failed_breakout=True,
    )
    extended_snapshot = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        close_trigger_cross=True,
        close_price=110.0,
        trigger_price=100.0,
        atr_value=3.0,
    )

    failed = SetupLifecycleActionabilityPolicy().evaluate(
        evaluate_lifecycle(failed_snapshot),
        failed_snapshot,
    )
    extended = SetupLifecycleActionabilityPolicy().evaluate(
        evaluate_lifecycle(extended_snapshot),
        extended_snapshot,
    )

    assert failed.actionability is Actionability.BLOCKED
    assert extended.actionability is Actionability.WATCH_ONLY


def test_explicit_market_gate_is_authoritative_over_regime_label() -> None:
    blocked_snapshot = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        market_regime="NEUTRAL",
        market_gate=False,
    )
    allowed_snapshot = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        market_regime="RISK_OFF",
        market_gate=True,
    )

    blocked = SetupLifecycleActionabilityPolicy().evaluate(
        evaluate_lifecycle(blocked_snapshot), blocked_snapshot
    )
    allowed = SetupLifecycleActionabilityPolicy().evaluate(
        evaluate_lifecycle(allowed_snapshot), allowed_snapshot
    )

    assert blocked.actionability is Actionability.BLOCKED
    assert blocked.blockers == ("MARKET_POLICY_BLOCK",)
    assert allowed.actionability is Actionability.ACTIONABLE


@pytest.mark.parametrize("market_regime", ["NEUTRAL", "YELLOW", "MIXED", "CAUTION"])
def test_reduced_market_posture_is_watch_only_not_low_confidence(
    market_regime: str,
) -> None:
    normalized = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        market_regime=market_regime,
    )
    lifecycle = replace(evaluate_lifecycle(normalized), confidence_score=90)

    decision = SetupLifecycleActionabilityPolicy().evaluate(lifecycle, normalized)

    assert decision.actionability is Actionability.WATCH_ONLY
    assert decision.reason_codes == ("MARKET_POLICY_REDUCED",)
    assert decision.metadata["market_posture"] == "REDUCED"
    assert decision.metadata["precedence"] == "REDUCED_MARKET_POSTURE"


def test_actionability_truth_table_keeps_evidence_and_market_gates_independent() -> None:
    policy = SetupLifecycleActionabilityPolicy()
    green = snapshot(
        setup_score=7.8,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        market_regime="GREEN",
    )
    ready_90 = replace(evaluate_lifecycle(green), confidence_score=90)
    ready_60 = replace(ready_90, confidence_score=60)
    stale = replace(
        green,
        data_quality_label=DataQualityLabel.LOW,
        warning_flags=("STALE_PRICE_BAR",),
    )
    bearish_stale = replace(
        snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            market_regime="BEARISH",
        ),
        data_quality_label=DataQualityLabel.LOW,
        warning_flags=("STALE_PRICE_BAR",),
    )
    developing = snapshot(
        setup_score=6.0,
        classification="Breakout Base",
        market_regime="GREEN",
    )
    developing_lifecycle = evaluate_lifecycle(developing)

    assert policy.evaluate(ready_90, green).actionability is Actionability.ACTIONABLE
    assert policy.evaluate(ready_60, green).actionability is Actionability.LOW_CONFIDENCE
    assert policy.evaluate(ready_90, stale).actionability is Actionability.LOW_CONFIDENCE
    assert policy.evaluate(ready_90, bearish_stale).actionability is Actionability.BLOCKED
    assert (
        policy.evaluate(
            replace(developing_lifecycle, confidence_score=40),
            developing,
        ).actionability
        is Actionability.LOW_CONFIDENCE
    )
    assert (
        policy.evaluate(
            replace(developing_lifecycle, confidence_score=90),
            developing,
        ).actionability
        is Actionability.WATCH_ONLY
    )


def test_compound_actionability_precedence_truth_table() -> None:
    policy = SetupLifecycleActionabilityPolicy()

    def decide(
        state: LifecycleState,
        confidence: int,
        *,
        market: str = "GREEN",
        stale: bool = False,
        earnings: str | None = None,
        liquidity: bool = False,
    ) -> Actionability:
        normalized = snapshot(
            setup_score=7.8,
            classification="Breakout Base",
            distance_to_pivot_pct=1.0,
            market_regime=market,
            earnings_risk=earnings,
            liquidity=liquidity,
        )
        if stale:
            normalized = replace(
                normalized,
                data_quality_label=DataQualityLabel.LOW,
                warning_flags=("STALE_PRICE_BAR",),
            )
        lifecycle = replace(
            evaluate_lifecycle(normalized),
            proposed_state=state,
            confidence_score=confidence,
        )
        return policy.evaluate(lifecycle, normalized).actionability

    assert decide(LifecycleState.READY, 90) is Actionability.ACTIONABLE
    assert decide(LifecycleState.READY, 60) is Actionability.LOW_CONFIDENCE
    assert decide(LifecycleState.READY, 90, market="NEUTRAL") is Actionability.WATCH_ONLY
    assert decide(LifecycleState.READY, 60, market="NEUTRAL") is Actionability.LOW_CONFIDENCE
    assert (
        decide(LifecycleState.READY, 90, market="NEUTRAL", stale=True)
        is Actionability.LOW_CONFIDENCE
    )
    assert decide(LifecycleState.READY, 90, market="BEARISH", stale=True) is Actionability.BLOCKED
    assert decide(LifecycleState.READY, 60, earnings="IMMINENT") is Actionability.BLOCKED
    assert decide(LifecycleState.READY, 90, liquidity=True) is Actionability.BLOCKED
    assert decide(LifecycleState.DEVELOPING, 40) is Actionability.LOW_CONFIDENCE
    assert decide(LifecycleState.DEVELOPING, 90, market="NEUTRAL") is Actionability.WATCH_ONLY
    assert decide(LifecycleState.EXTENDED, 90, stale=True) is Actionability.LOW_CONFIDENCE
    assert decide(LifecycleState.EXPIRED, 90, stale=True) is Actionability.WATCH_ONLY
    assert decide(LifecycleState.FAILED, 90, market="BEARISH") is Actionability.BLOCKED
