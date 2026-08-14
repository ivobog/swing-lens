from __future__ import annotations

from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import FamilyEvidence, NormalizedSnapshot
from app.services.setup_lifecycle.enums import SetupFamily
from app.services.setup_lifecycle.family_adapters import (
    classification_agreement,
    classification_contains,
    consecutive_family_condition_sessions,
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


class BreakoutAdapter:
    setup_family = SetupFamily.BREAKOUT

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
        distance = signal_optional_number(snapshot, "distance_to_pivot_pct")
        close_cross = signal_bool(snapshot, "close_trigger_cross")
        follow_through = consecutive_true_sessions(snapshot, history, "close_trigger_cross")
        dry_up = signal_bool(snapshot, "volume_dry_up") or classification_contains(
            snapshot, "dry", "vdu"
        )
        volume_percentile = signal_optional_number(snapshot, "volume_percentile_252")
        dry_up = (
            dry_up
            or (volume_percentile is not None and volume_percentile <= 35)
            or (
                numeric_history_is_improving(
                    snapshot,
                    history,
                    "volume_percentile_252",
                    lower_is_better=True,
                )
            )
        )

        def contraction_observation(item: NormalizedSnapshot) -> bool:
            return (
                signal_bool(item, "range_contraction")
                or signal_number(item, "tightness_score") >= 6.0
                or classification_contains(item, "contraction", "tight")
            )

        contraction_sessions = consecutive_family_condition_sessions(
            snapshot,
            history,
            contraction_observation,
        )
        contraction_min_sessions = int(
            policy_parameter(self.config, self.setup_family, "contraction_sessions_min", 2)
        )
        contraction = contraction_sessions >= contraction_min_sessions or (
            numeric_history_is_improving(
                snapshot,
                history,
                "range_percentile_252",
                lower_is_better=True,
            )
        )
        breakout_like = (
            classification_contains(snapshot, "breakout", "base", "pivot", "cup")
            or signal_bool(snapshot, "fresh_breakout")
            or signal_bool(snapshot, "failed_breakout")
            or signal_bool(snapshot, "box_failure")
        )
        failed = (
            signal_bool(snapshot, "failed_breakout")
            or signal_bool(snapshot, "box_failure")
            or classification_contains(snapshot, "failed breakout", "breakout failure")
        )
        extended_atr = derived_atr_from_trigger(snapshot)
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
        ready = (
            setup_score >= policy.ready_score_min
            and distance is not None
            and distance <= ready_distance
        )
        triggered = ready and close_cross
        confirmed = triggered and follow_through >= confirmed_sessions
        extended = triggered and extended_atr is not None and extended_atr >= extended_limit

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
                "trigger_distance_missing_reason": (
                    None if distance is not None else "PIVOT_UNAVAILABLE"
                ),
                "close_trigger_cross": close_cross,
                "follow_through_sessions": follow_through,
                "extended_atr_from_trigger": extended_atr,
                "contraction_sessions": contraction_sessions,
                "volume_percentile_252": volume_percentile,
                "state_age_sessions": state_age_sessions,
                "breakout_like": breakout_like,
            },
            agreement_components={
                "trend": trend_agreement(snapshot),
                "contraction": 1.0 if contraction or dry_up else 0.0,
                "relative_strength": relative_strength_agreement(snapshot),
                "classification": classification_agreement(
                    snapshot,
                    "breakout",
                    "base",
                    "pivot",
                    "cup",
                ),
            },
        )
