from __future__ import annotations

from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import FamilyEvidence, NormalizedSnapshot
from app.services.setup_lifecycle.enums import SetupFamily
from app.services.setup_lifecycle.family_adapters import (
    _confidence_from_score,
    _number,
    numeric_history_is_improving,
    relative_strength_agreement,
    signal_bool,
    signal_text,
    signal_value,
    trend_agreement,
)


class GenericAdapter:
    setup_family = SetupFamily.GENERIC

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
        score = _number(signal_value(snapshot, "technical_score")) or _number(
            signal_value(snapshot, "setup_score")
        ) or 0.0
        trackable = policy.enabled and score >= policy.tracking_score_min
        ready = trackable and score >= policy.ready_score_min
        triggered = ready and signal_bool(snapshot, "close_trigger_cross")
        expired = state_age_sessions >= policy.max_age_sessions
        improving = numeric_history_is_improving(
            snapshot,
            history,
            "technical_score",
            lower_is_better=False,
        )

        if expired:
            phase = "EXPIRED"
            reasons = ("MAX_AGE_EXCEEDED",)
        elif triggered:
            phase = "TRIGGERED"
            reasons = ("GENERIC_TRIGGERED",)
        elif ready:
            phase = "READY"
            reasons = ("GENERIC_READY",)
        elif trackable and improving:
            phase = "IMPROVING"
            reasons = ("GENERIC_SCORE_IMPROVING",)
        elif trackable:
            phase = "CANDIDATE"
            reasons = ("GENERIC_FALLBACK",)
        else:
            phase = "CANDIDATE"
            reasons = ("WEAK_GENERIC_EVIDENCE",)

        return FamilyEvidence(
            setup_family=self.setup_family,
            phase_code=phase,
            evidence_score=score,
            confidence_score=_confidence_from_score(score),
            trackable=trackable,
            ready=ready,
            triggered=triggered,
            confirmed=False,
            extended=False,
            hard_failure=False,
            reason_codes=reasons,
            evidence={
                "technical_score": score,
                "score_improving": improving,
                "state_age_sessions": state_age_sessions,
            },
            agreement_components={
                "trend": trend_agreement(snapshot),
                "contraction": 0.5,
                "relative_strength": relative_strength_agreement(snapshot),
                "classification": 1.0 if signal_text(snapshot, "classification") else 0.5,
            },
        )
