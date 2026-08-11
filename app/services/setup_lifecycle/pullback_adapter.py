from __future__ import annotations

from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import FamilyEvidence, NormalizedSnapshot
from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily
from app.services.setup_lifecycle.family_adapters import (
    classification_contains,
    policy_parameter,
    signal_bool,
    signal_number,
)


class PullbackAdapter:
    setup_family = SetupFamily.PULLBACK

    def __init__(self, config: SetupLifecycleConfig | None = None) -> None:
        self.config = config or load_setup_lifecycle_config()

    def evaluate(
        self,
        snapshot: NormalizedSnapshot,
        *,
        history: tuple[NormalizedSnapshot, ...] = (),
        previous_state: object | None = None,
        state_age_sessions: int = 0,
    ) -> FamilyEvidence:
        policy = self.config.families.policies[self.setup_family]
        setup_score = signal_number(snapshot, "setup_score")
        trend_score = signal_number(snapshot, "technical_score")
        support_distance_atr = signal_number(snapshot, "support_distance_atr", default=999.0)
        declining_volume = signal_bool(snapshot, "declining_volume") or classification_contains(
            snapshot, "declining volume", "selling pressure declining"
        )
        reversal_ready = signal_bool(snapshot, "reversal_ready") or signal_number(
            snapshot, "distance_to_pivot_pct",
            default=999.0,
        ) <= 1.0
        reversal_trigger = signal_bool(snapshot, "close_trigger_cross")
        follow_through = signal_number(snapshot, "follow_through_sessions")
        support_break = signal_bool(snapshot, "support_break") or signal_bool(
            snapshot, "failed_pullback"
        )
        extended_atr = signal_number(snapshot, "extended_atr_from_trigger")

        support_limit = float(
            policy_parameter(self.config, self.setup_family, "support_distance_atr", 1.0)
        )
        confirmed_sessions = int(
            policy_parameter(self.config, self.setup_family, "confirmed_hold_sessions", 2)
        )
        extended_limit = float(
            policy_parameter(self.config, SetupFamily.BREAKOUT, "extended_atr_from_trigger", 2.5)
        )

        reasons: list[str] = []
        pullback_like = (
            classification_contains(snapshot, "uptrend", "pullback", "support")
            or support_distance_atr != 999.0
            or declining_volume
            or reversal_ready
            or support_break
        )
        prior_uptrend = pullback_like and (
            trend_score >= policy.tracking_score_min
            or classification_contains(snapshot, "uptrend", "pullback")
        )
        support_approach = support_distance_atr <= support_limit
        ready = setup_score >= policy.ready_score_min and support_approach and reversal_ready
        triggered = ready and reversal_trigger
        confirmed = triggered and follow_through >= confirmed_sessions
        extended = triggered and extended_atr >= extended_limit
        expired = state_age_sessions >= policy.max_age_sessions

        if support_break:
            phase = "SUPPORT_BREAK"
            reasons.append("SUPPORT_BREAK")
        elif expired:
            phase = "EXPIRED"
            reasons.append("MAX_AGE_EXCEEDED")
        elif extended:
            phase = "FOLLOW_THROUGH"
            reasons.append("EXTENDED_FROM_TRIGGER")
        elif confirmed:
            phase = "FOLLOW_THROUGH"
            reasons.append("FOLLOW_THROUGH_CONFIRMED")
        elif triggered:
            phase = "REVERSAL_TRIGGER"
            reasons.append("REVERSAL_TRIGGER")
        elif ready:
            phase = "REVERSAL_READY"
            reasons.append("REVERSAL_READY")
        elif support_approach:
            phase = "SUPPORT_TEST"
            reasons.append("SUPPORT_TEST")
        elif declining_volume:
            phase = "SELLING_PRESSURE_DECLINING"
            reasons.append("SELLING_PRESSURE_DECLINING")
        elif prior_uptrend and setup_score >= policy.tracking_score_min:
            phase = "PULLBACK_STARTED"
            reasons.append("PRIOR_UPTREND_PULLBACK")
        else:
            phase = "PULLBACK_STARTED"

        failed = support_break or (
            previous_state is LifecycleState.TRIGGERED and not reversal_trigger
        )
        trackable = pullback_like and prior_uptrend and setup_score >= policy.tracking_score_min
        return FamilyEvidence(
            setup_family=self.setup_family,
            phase_code=phase,
            evidence_score=max(setup_score, trend_score),
            confidence_score=max(0, min(100, round(max(setup_score, trend_score) * 10))),
            trackable=trackable,
            ready=ready,
            triggered=triggered,
            confirmed=confirmed,
            extended=extended,
            hard_failure=failed,
            reason_codes=tuple(reasons or ("WEAK_PULLBACK_EVIDENCE",)),
            evidence={
                "setup_score": setup_score,
                "trend_score": trend_score,
                "support_distance_atr": support_distance_atr,
                "declining_volume": declining_volume,
                "reversal_trigger": reversal_trigger,
                "follow_through_sessions": follow_through,
                "state_age_sessions": state_age_sessions,
                "pullback_like": pullback_like,
            },
        )
