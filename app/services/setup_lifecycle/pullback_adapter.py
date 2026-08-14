from __future__ import annotations

from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import FamilyEvidence, NormalizedSnapshot
from app.services.setup_lifecycle.enums import SetupFamily
from app.services.setup_lifecycle.family_adapters import (
    classification_agreement,
    classification_contains,
    consecutive_true_sessions,
    derived_atr_from_trigger,
    numeric_history_is_improving,
    policy_parameter,
    relative_strength_agreement,
    signal_bool,
    signal_number,
    signal_optional_number,
    trend_agreement,
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
        declining_volume = signal_bool(snapshot, "red_volume_declining") or (
            numeric_history_is_improving(
                snapshot,
                history,
                "volume_percentile_252",
                lower_is_better=True,
            )
        )
        shrinking_ranges = numeric_history_is_improving(
            snapshot,
            history,
            "range_percentile_252",
            lower_is_better=True,
        )
        support_held = signal_bool(snapshot, "held_near_support") or classification_contains(
            snapshot,
            "support test",
            "support hold",
        )
        reversal_trigger = signal_bool(snapshot, "close_trigger_cross")
        follow_through = consecutive_true_sessions(snapshot, history, "close_trigger_cross")
        support_break = (
            signal_bool(snapshot, "heavy_mid_ma_break")
            or signal_bool(snapshot, "failed_breakout")
            or classification_contains(snapshot, "distribution risk", "failed pullback")
        )
        extended_atr = derived_atr_from_trigger(snapshot)

        confirmed_sessions = int(
            policy_parameter(self.config, self.setup_family, "confirmed_hold_sessions", 2)
        )
        extended_limit = float(
            policy_parameter(self.config, SetupFamily.BREAKOUT, "extended_atr_from_trigger", 2.5)
        )

        reasons: list[str] = []
        pullback_like = (
            classification_contains(snapshot, "uptrend", "pullback", "support")
            or declining_volume
            or support_held
            or support_break
        )
        prior_uptrend = any(
            signal_number(item, "trend_score", default=signal_number(item, "technical_score"))
            >= policy.tracking_score_min
            or classification_contains(item, "uptrend", "stage 2", "pullback")
            for item in history
        ) or (
            trend_agreement(snapshot) == 1.0
            and classification_contains(snapshot, "uptrend", "pullback", "stage 2")
        )
        support_approach = support_held or classification_contains(
            snapshot,
            "support approach",
            "support test",
        )
        ready = setup_score >= policy.ready_score_min and support_approach and support_held
        triggered = ready and reversal_trigger
        confirmed = (
            triggered
            and follow_through >= confirmed_sessions
            and (
                relative_strength_agreement(snapshot) >= 0.5
                or signal_number(snapshot, "volume_ratio") >= 1.0
            )
        )
        extended = triggered and extended_atr is not None and extended_atr >= extended_limit
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
        elif declining_volume and shrinking_ranges:
            phase = "SELLING_PRESSURE_DECLINING"
            reasons.append("SELLING_PRESSURE_DECLINING")
        elif prior_uptrend and setup_score >= policy.tracking_score_min:
            phase = "PULLBACK_STARTED"
            reasons.append("PRIOR_UPTREND_PULLBACK")
        else:
            phase = "PULLBACK_STARTED"

        failed = support_break
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
                "support_held": support_held,
                "declining_volume": declining_volume,
                "shrinking_ranges": shrinking_ranges,
                "prior_uptrend": prior_uptrend,
                "pullback_depth_pct": signal_optional_number(
                    snapshot,
                    "pullback_depth_pct",
                ),
                "reversal_trigger": reversal_trigger,
                "trigger_price": signal_optional_number(snapshot, "trigger_price"),
                "trigger_distance_pct": signal_optional_number(
                    snapshot,
                    "distance_to_pivot_pct",
                ),
                "trigger_distance_missing_reason": (
                    None
                    if signal_optional_number(snapshot, "distance_to_pivot_pct") is not None
                    else "TRIGGER_UNAVAILABLE"
                ),
                "follow_through_sessions": follow_through,
                "extended_atr_from_trigger": extended_atr,
                "state_age_sessions": state_age_sessions,
                "pullback_like": pullback_like,
            },
            agreement_components={
                "trend": trend_agreement(snapshot),
                "contraction": (
                    1.0
                    if declining_volume and shrinking_ranges
                    else 0.5
                    if declining_volume or shrinking_ranges
                    else 0.0
                ),
                "relative_strength": relative_strength_agreement(snapshot),
                "classification": classification_agreement(
                    snapshot,
                    "pullback",
                    "support",
                    "uptrend",
                ),
            },
        )
