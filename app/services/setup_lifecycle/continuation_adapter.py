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
    policy_parameter,
    relative_strength_agreement,
    signal_bool,
    signal_number,
    trend_agreement,
)


class ContinuationAdapter:
    setup_family = SetupFamily.CONTINUATION

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
        tight_range = signal_number(snapshot, "range_percentile_252", default=100.0)
        close_cross = signal_bool(snapshot, "close_trigger_cross")
        extended_atr = derived_atr_from_trigger(snapshot)
        failed = (
            signal_bool(snapshot, "failed_breakout")
            or signal_bool(snapshot, "box_failure")
            or signal_bool(snapshot, "heavy_mid_ma_break")
            or classification_contains(snapshot, "distribution risk", "failed continuation")
        )
        expired = state_age_sessions >= policy.max_age_sessions

        tight_max = float(
            policy_parameter(self.config, self.setup_family, "tight_range_percentile_max", 40)
        )
        extended_limit = float(
            policy_parameter(self.config, self.setup_family, "extended_atr_from_trigger", 2.5)
        )
        tight_sessions_min = int(
            policy_parameter(self.config, self.setup_family, "tight_range_sessions_min", 2)
        )
        tight_sessions = consecutive_family_condition_sessions(
            snapshot,
            history,
            lambda item: signal_number(
                item,
                "range_percentile_252",
                default=100.0,
            )
            <= tight_max,
        )
        trackable = setup_score >= policy.tracking_score_min and classification_contains(
            snapshot,
            "continuation",
            "flag",
            "pause",
        )
        ready = (
            setup_score >= policy.ready_score_min
            and tight_range <= tight_max
            and tight_sessions >= tight_sessions_min
        )
        triggered = ready and close_cross
        follow_through = consecutive_true_sessions(snapshot, history, "close_trigger_cross")
        confirmed = triggered and follow_through >= 2
        extended = (
            triggered
            and extended_atr is not None
            and extended_atr >= extended_limit
        )

        reasons: list[str] = []
        if failed:
            phase = "FAILED"
            reasons.append("CONTINUATION_FAILED")
        elif expired:
            phase = "EXPIRED"
            reasons.append("MAX_AGE_EXCEEDED")
        elif extended:
            phase = "CONTINUATION_TRIGGER"
            reasons.append("EXTENDED_FROM_TRIGGER")
        elif confirmed:
            phase = "CONTINUATION_TRIGGER"
            reasons.append("FOLLOW_THROUGH_CONFIRMED")
        elif triggered:
            phase = "CONTINUATION_TRIGGER"
            reasons.append("CONTINUATION_TRIGGER")
        elif ready:
            phase = "CONTINUATION_READY"
            reasons.append("TIGHT_RANGE_READY")
        elif tight_range <= tight_max:
            phase = "TIGHT_RANGE"
            reasons.append("TIGHT_RANGE")
        elif classification_contains(snapshot, "flag"):
            phase = "FLAG_FORMING"
            reasons.append("FLAG_FORMING")
        else:
            phase = "PAUSE"
            if trackable:
                reasons.append("CONTINUATION_TRACKABLE")

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
            reason_codes=tuple(reasons or ("WEAK_CONTINUATION_EVIDENCE",)),
            evidence={
                "setup_score": setup_score,
                "range_percentile_252": tight_range,
                "tight_range_sessions": tight_sessions,
                "close_trigger_cross": close_cross,
                "follow_through_sessions": follow_through,
                "extended_atr_from_trigger": extended_atr,
                "state_age_sessions": state_age_sessions,
            },
            agreement_components={
                "trend": trend_agreement(snapshot),
                "contraction": 1.0 if tight_sessions >= tight_sessions_min else 0.0,
                "relative_strength": relative_strength_agreement(snapshot),
                "classification": classification_agreement(
                    snapshot,
                    "continuation",
                    "flag",
                    "pause",
                ),
            },
        )
