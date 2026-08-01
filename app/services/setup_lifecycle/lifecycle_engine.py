from __future__ import annotations

from dataclasses import dataclass

from app.services.setup_lifecycle.confidence_service import SetupLifecycleConfidenceService
from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import FamilyEvidence, LifecycleDecision, NormalizedSnapshot
from app.services.setup_lifecycle.enums import Actionability, LifecycleState
from app.services.setup_lifecycle.family_adapters import (
    evaluate_family_candidates,
    select_primary_family,
)

STATE_FROM_EVIDENCE_PRECEDENCE = (
    LifecycleState.FAILED,
    LifecycleState.EXTENDED,
    LifecycleState.CONFIRMED,
    LifecycleState.TRIGGERED,
    LifecycleState.READY,
    LifecycleState.TIGHTENING,
    LifecycleState.DEVELOPING,
    LifecycleState.DISCOVERED,
    LifecycleState.EXPIRED,
)

NON_TERMINAL_STATES = {
    LifecycleState.DISCOVERED,
    LifecycleState.DEVELOPING,
    LifecycleState.TIGHTENING,
    LifecycleState.READY,
    LifecycleState.TRIGGERED,
    LifecycleState.CONFIRMED,
    LifecycleState.EXTENDED,
}


@dataclass(frozen=True)
class LifecycleEvaluationInput:
    snapshot: NormalizedSnapshot
    previous_state: LifecycleState | None = None
    previous_phase: str | None = None
    state_age_sessions: int = 0
    persistence_sessions: int = 0
    missing_observation_sessions: int = 0


class SetupLifecycleEngine:
    def __init__(
        self,
        *,
        config: SetupLifecycleConfig | None = None,
        confidence_service: SetupLifecycleConfidenceService | None = None,
    ) -> None:
        self.config = config or load_setup_lifecycle_config()
        self.confidence_service = confidence_service or SetupLifecycleConfidenceService(
            self.config
        )

    def evaluate(self, request: LifecycleEvaluationInput) -> LifecycleDecision:
        if request.previous_state in {LifecycleState.FAILED, LifecycleState.EXPIRED}:
            return self._terminal_decision(request)

        candidates = evaluate_family_candidates(
            request.snapshot,
            config=self.config,
            previous_state=request.previous_state,
            state_age_sessions=request.state_age_sessions,
        )
        evidence = select_primary_family(candidates, config=self.config)
        if evidence is None:
            return self._no_evidence_decision(request)

        proposed = self._state_from_evidence(evidence, request)
        proposed = self._apply_hysteresis(proposed, evidence, request)
        confidence = self.confidence_service.score(
            request.snapshot,
            evidence,
            persistence_sessions=request.persistence_sessions,
        )
        actionability = self._actionability(proposed, confidence.score)
        reasons = tuple(
            dict.fromkeys(
                (
                    *evidence.reason_codes,
                    *confidence.reason_codes,
                    _transition_reason(request.previous_state, proposed),
                )
            )
        )
        return LifecycleDecision(
            setup_family=evidence.setup_family,
            phase_code=evidence.phase_code,
            previous_state=request.previous_state,
            proposed_state=proposed,
            actionability_candidate=actionability,
            confidence_score=confidence.score,
            confidence_label=confidence.label,
            reason_codes=reasons or ("NO_STATE_CHANGE",),
            evidence={
                **evidence.evidence,
                "confidence": confidence.components,
                "candidate_reason_codes": list(evidence.reason_codes),
            },
            immediate_transition=evidence.hard_failure or _stronger_than(
                proposed,
                request.previous_state,
            ),
            terminal_reason="HARD_FAILURE" if proposed is LifecycleState.FAILED else None,
        )

    def _state_from_evidence(
        self,
        evidence: FamilyEvidence,
        request: LifecycleEvaluationInput,
    ) -> LifecycleState:
        if evidence.hard_failure:
            return LifecycleState.FAILED
        if request.missing_observation_sessions > self.config.episodes.observation_gap_sessions:
            return LifecycleState.EXPIRED
        if request.state_age_sessions >= self.config.families.policies[
            evidence.setup_family
        ].max_age_sessions:
            return LifecycleState.EXPIRED
        if evidence.extended:
            return LifecycleState.EXTENDED
        if evidence.confirmed:
            return LifecycleState.CONFIRMED
        if evidence.triggered:
            return LifecycleState.TRIGGERED
        if evidence.ready:
            return LifecycleState.READY
        if evidence.phase_code in {
            "RANGE_CONTRACTION",
            "VOLUME_DRY_UP",
            "SELLING_PRESSURE_DECLINING",
            "SUPPORT_TEST",
            "CONTRACTION_2",
            "CONTRACTION_3",
            "TIGHT_RANGE",
        }:
            return LifecycleState.TIGHTENING
        if evidence.trackable:
            return LifecycleState.DEVELOPING
        return LifecycleState.DISCOVERED

    def _apply_hysteresis(
        self,
        proposed: LifecycleState,
        evidence: FamilyEvidence,
        request: LifecycleEvaluationInput,
    ) -> LifecycleState:
        previous = request.previous_state
        if previous is None:
            return proposed
        if proposed in {LifecycleState.FAILED, LifecycleState.EXPIRED}:
            return proposed
        if _stronger_than(previous, proposed) and previous in NON_TERMINAL_STATES:
            margin = evidence.confidence_score - self.config.confidence.normal_min
            if previous in {LifecycleState.READY, LifecycleState.TRIGGERED} and margin >= -5:
                return previous
        if proposed is LifecycleState.CONFIRMED and request.persistence_sessions < 2:
            return LifecycleState.TRIGGERED
        return proposed

    def _terminal_decision(self, request: LifecycleEvaluationInput) -> LifecycleDecision:
        state = request.previous_state or LifecycleState.EXPIRED
        return LifecycleDecision(
            setup_family=_fallback_family(),
            phase_code=request.previous_phase or state.value,
            previous_state=request.previous_state,
            proposed_state=state,
            actionability_candidate=Actionability.BLOCKED,
            confidence_score=100,
            confidence_label=self.confidence_service.label_for_score(100),
            reason_codes=("TERMINAL_STATE_LOCKED",),
            evidence={"previous_state": state.value},
            immediate_transition=False,
            terminal_reason=state.value,
        )

    def _no_evidence_decision(self, request: LifecycleEvaluationInput) -> LifecycleDecision:
        state = request.previous_state or LifecycleState.DISCOVERED
        return LifecycleDecision(
            setup_family=_fallback_family(),
            phase_code=request.previous_phase or "CANDIDATE",
            previous_state=request.previous_state,
            proposed_state=state,
            actionability_candidate=Actionability.LOW_CONFIDENCE,
            confidence_score=0,
            confidence_label=self.confidence_service.label_for_score(0),
            reason_codes=("INSUFFICIENT_FAMILY_EVIDENCE",),
            evidence={"available_signals": sorted(request.snapshot.signals)},
            immediate_transition=False,
        )

    def _actionability(self, state: LifecycleState, confidence_score: int) -> Actionability:
        if state is LifecycleState.FAILED:
            return Actionability.BLOCKED
        if confidence_score < self.config.confidence.low_min:
            return Actionability.LOW_CONFIDENCE
        if state in {LifecycleState.READY, LifecycleState.TRIGGERED, LifecycleState.CONFIRMED}:
            return Actionability.ACTIONABLE
        return Actionability.WATCH_ONLY


def evaluate_lifecycle(
    snapshot: NormalizedSnapshot,
    *,
    previous_state: LifecycleState | None = None,
    previous_phase: str | None = None,
    state_age_sessions: int = 0,
    persistence_sessions: int = 0,
    missing_observation_sessions: int = 0,
    config: SetupLifecycleConfig | None = None,
) -> LifecycleDecision:
    return SetupLifecycleEngine(config=config).evaluate(
        LifecycleEvaluationInput(
            snapshot=snapshot,
            previous_state=previous_state,
            previous_phase=previous_phase,
            state_age_sessions=state_age_sessions,
            persistence_sessions=persistence_sessions,
            missing_observation_sessions=missing_observation_sessions,
        )
    )


def _stronger_than(candidate: LifecycleState, current: LifecycleState | None) -> bool:
    if current is None:
        return True
    priority = {
        state: len(STATE_FROM_EVIDENCE_PRECEDENCE) - index
        for index, state in enumerate(STATE_FROM_EVIDENCE_PRECEDENCE)
    }
    return priority[candidate] > priority[current]


def _transition_reason(
    previous: LifecycleState | None,
    proposed: LifecycleState,
) -> str:
    if previous is None:
        return f"OPENED_{proposed.value}"
    if previous is proposed:
        return "NO_STATE_CHANGE"
    return f"{previous.value}_TO_{proposed.value}"


def _fallback_family():
    from app.services.setup_lifecycle.enums import SetupFamily

    return SetupFamily.GENERIC
