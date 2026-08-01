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


class BreakoutAdapter:
    setup_family = SetupFamily.BREAKOUT

    def __init__(self, config: SetupLifecycleConfig | None = None) -> None:
        self.config = config or load_setup_lifecycle_config()

    def evaluate(
        self,
        snapshot: NormalizedSnapshot,
        *,
        previous_state: object | None = None,
        state_age_sessions: int = 0,
    ) -> FamilyEvidence:
        policy = self.config.families.policies[self.setup_family]
        setup_score = signal_number(snapshot, "setup_score")
        distance = signal_number(snapshot, "distance_to_pivot_pct", default=999.0)
        close_cross = signal_bool(snapshot, "close_trigger_cross")
        follow_through = signal_number(snapshot, "follow_through_sessions")
        dry_up = signal_bool(snapshot, "volume_dry_up") or classification_contains(
            snapshot, "dry", "vdu"
        )
        contraction = signal_bool(snapshot, "range_contraction") or classification_contains(
            snapshot, "contraction", "tight"
        )
        breakout_like = (
            classification_contains(snapshot, "breakout", "base", "pivot", "cup")
            or dry_up
            or contraction
            or distance != 999.0
        )
        explicit_failed = signal_bool(snapshot, "failed_breakout")
        failed = explicit_failed or (
            breakout_like and previous_state is LifecycleState.TRIGGERED and not close_cross
        )
        extended_atr = signal_number(snapshot, "extended_atr_from_trigger")
        expired = state_age_sessions >= policy.max_age_sessions

        ready_distance = float(
            policy_parameter(
                self.config,
                self.setup_family,
                "ready_pivot_distance_enter_pct",
                2.0,
            )
        )
        confirmed_sessions = int(
            policy_parameter(self.config, self.setup_family, "confirmed_hold_sessions", 2)
        )
        extended_limit = float(
            policy_parameter(self.config, self.setup_family, "extended_atr_from_trigger", 2.5)
        )

        reasons: list[str] = []
        phase = "BASE_FORMING"
        ready = setup_score >= policy.ready_score_min and distance <= ready_distance
        triggered = ready and close_cross
        confirmed = triggered and follow_through >= confirmed_sessions
        extended = triggered and extended_atr >= extended_limit

        if failed:
            phase = "BREAKOUT_FAILED"
            reasons.append("FAILED_BREAKOUT")
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
            phase = "BREAKOUT"
            reasons.append("CLOSE_TRIGGER_CROSSED")
        elif ready:
            phase = "PIVOT_READY"
            reasons.append("PIVOT_DISTANCE_READY")
        elif dry_up:
            phase = "VOLUME_DRY_UP"
            reasons.append("VOLUME_DRY_UP")
        elif contraction:
            phase = "RANGE_CONTRACTION"
            reasons.append("RANGE_CONTRACTION")
        elif breakout_like and setup_score >= policy.tracking_score_min:
            reasons.append("BREAKOUT_TRACKABLE")

        trackable = breakout_like and bool(reasons) and setup_score >= policy.tracking_score_min
        return FamilyEvidence(
            setup_family=self.setup_family,
            phase_code=phase,
            evidence_score=setup_score,
            confidence_score=max(0, min(100, round(setup_score * 10))),
            trackable=trackable,
            ready=ready,
            triggered=triggered,
            confirmed=confirmed,
            extended=extended,
            hard_failure=failed,
            reason_codes=tuple(reasons or ("WEAK_BREAKOUT_EVIDENCE",)),
            evidence={
                "setup_score": setup_score,
                "distance_to_pivot_pct": distance,
                "close_trigger_cross": close_cross,
                "follow_through_sessions": follow_through,
                "extended_atr_from_trigger": extended_atr,
                "state_age_sessions": state_age_sessions,
                "breakout_like": breakout_like,
            },
        )
