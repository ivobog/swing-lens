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


class VcpAdapter:
    setup_family = SetupFamily.VCP

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
        def contraction_observation(item: NormalizedSnapshot) -> bool:
            return (
                signal_bool(item, "range_contraction")
                or signal_number(item, "vcp_score") >= policy.tracking_score_min
                or classification_contains(item, "vcp", "contraction")
            )

        contraction_count = consecutive_family_condition_sessions(
            snapshot,
            history,
            contraction_observation,
        )
        dry_up_percentile = signal_number(snapshot, "volume_percentile_252", default=100.0)
        close_cross = signal_bool(snapshot, "close_trigger_cross")
        failed = (
            signal_bool(snapshot, "failed_breakout")
            or signal_bool(snapshot, "box_failure")
            or classification_contains(snapshot, "failed breakout", "vcp failure")
        )
        expired = state_age_sessions >= policy.max_age_sessions

        contraction_min = int(
            policy_parameter(self.config, self.setup_family, "contraction_count_min", 2)
        )
        dry_up_max = float(
            policy_parameter(self.config, self.setup_family, "dry_up_percentile_max", 35)
        )
        trackable = (
            setup_score >= policy.tracking_score_min
            and (classification_contains(snapshot, "vcp") or contraction_count >= 1)
        )
        ready = setup_score >= policy.ready_score_min and contraction_count >= contraction_min
        ready = ready and dry_up_percentile <= dry_up_max
        triggered = ready and close_cross
        follow_through = consecutive_true_sessions(snapshot, history, "close_trigger_cross")
        confirmed = triggered and follow_through >= 2
        extended_atr = derived_atr_from_trigger(snapshot)
        extended_limit = float(
            policy_parameter(
                self.config,
                SetupFamily.BREAKOUT,
                "extended_atr_from_trigger",
                2.5,
            )
        )
        extended = (
            triggered
            and extended_atr is not None
            and extended_atr >= extended_limit
        )

        reasons: list[str] = []
        if failed:
            phase = "FAILED"
            reasons.append("VCP_FAILED")
        elif expired:
            phase = "EXPIRED"
            reasons.append("MAX_AGE_EXCEEDED")
        elif extended:
            phase = "BREAKOUT"
            reasons.append("EXTENDED_FROM_TRIGGER")
        elif confirmed:
            phase = "BREAKOUT"
            reasons.append("FOLLOW_THROUGH_CONFIRMED")
        elif triggered:
            phase = "BREAKOUT"
            reasons.append("VCP_BREAKOUT")
        elif ready:
            phase = "PIVOT_READY"
            reasons.append("VCP_PIVOT_READY")
        elif dry_up_percentile <= dry_up_max:
            phase = "VOLUME_DRY_UP"
            reasons.append("VOLUME_DRY_UP")
        elif contraction_count >= 3:
            phase = "CONTRACTION_3"
            reasons.append("THIRD_CONTRACTION")
        elif contraction_count >= 2:
            phase = "CONTRACTION_2"
            reasons.append("SECOND_CONTRACTION")
        else:
            phase = "CONTRACTION_1"
            if trackable:
                reasons.append("VCP_TRACKABLE")

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
            reason_codes=tuple(reasons or ("WEAK_VCP_EVIDENCE",)),
            evidence={
                "setup_score": setup_score,
                "contraction_count": contraction_count,
                "volume_percentile_252": dry_up_percentile,
                "close_trigger_cross": close_cross,
                "follow_through_sessions": follow_through,
                "extended_atr_from_trigger": extended_atr,
                "state_age_sessions": state_age_sessions,
            },
            agreement_components={
                "trend": trend_agreement(snapshot),
                "contraction": 1.0 if contraction_count >= contraction_min else 0.0,
                "relative_strength": relative_strength_agreement(snapshot),
                "classification": classification_agreement(
                    snapshot,
                    "vcp",
                    "contraction",
                ),
            },
        )
