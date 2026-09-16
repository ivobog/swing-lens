from __future__ import annotations

from app.services.configuration_delivery import anchored_decision_calculator
from app.services.contextual_consumer_eligibility import setup_with_contextual_permission
from app.services.setup_lifecycle.config import SetupLifecycleConfig
from app.services.setup_lifecycle.dtos import (
    ActionabilityDecision,
    LifecycleDecision,
    NormalizedSnapshot,
)
from app.services.setup_lifecycle.enums import Actionability, DataQualityLabel, LifecycleState
from app.services.setup_lifecycle.family_adapters import signal_bool, signal_text, signal_value
from app.services.technical_consumer_eligibility import setup_technical_blocked


class SetupLifecycleActionabilityPolicy:
    def __init__(self, config: SetupLifecycleConfig | None = None) -> None:
        from app.services.decision_effective_configuration import resolve_lifecycle_configuration

        self.effective_configuration = resolve_lifecycle_configuration(config)
        self.config = self.effective_configuration.setup_config()

    @anchored_decision_calculator
    def evaluate(
        self,
        lifecycle: LifecycleDecision,
        snapshot: NormalizedSnapshot,
    ) -> ActionabilityDecision:
        snapshot = setup_with_contextual_permission(snapshot)
        state = lifecycle.proposed_state
        reasons: list[str] = []
        blockers: list[str] = []

        if state is LifecycleState.FAILED:
            return ActionabilityDecision(
                actionability=Actionability.BLOCKED,
                reason_codes=("FAILED_STATE_BLOCKED",),
                blockers=("FAILED",),
            )
        if state is LifecycleState.EXPIRED:
            return ActionabilityDecision(
                actionability=Actionability.WATCH_ONLY,
                reason_codes=(f"{state.value}_WATCH_ONLY",),
                metadata={"precedence": "TERMINAL_STATE"},
            )

        if _has_hard_required_absence(snapshot):
            blockers.append("HARD_REQUIRED_DATA_ABSENT")
        if setup_technical_blocked(snapshot):
            blockers.append("TECHNICAL_CONSUMER_INELIGIBLE")
        if snapshot.data_quality_label is DataQualityLabel.INSUFFICIENT:
            blockers.append("INSUFFICIENT_DATA_QUALITY")
        if signal_bool(snapshot, "liquidity"):
            blockers.append("LIQUIDITY_RISK")

        earnings = signal_text(snapshot, "earnings_risk").casefold()
        if earnings in {"imminent", "high", "blocked", "within_window"}:
            blockers.append("IMMINENT_EARNINGS")

        market = signal_text(snapshot, "market_regime").casefold()
        market_gate = signal_value(snapshot, "market_gate")
        if market_gate is False or (
            market_gate is None and market in {"blocked", "risk_off", "red", "bearish"}
        ):
            blockers.append("MARKET_POLICY_BLOCK")

        if blockers:
            return ActionabilityDecision(
                actionability=Actionability.BLOCKED,
                reason_codes=tuple(dict.fromkeys((*reasons, "GATE_BLOCKED"))),
                blockers=tuple(dict.fromkeys(blockers)),
                metadata={"precedence": "HARD_BLOCKER"},
            )

        if snapshot.data_quality_label is DataQualityLabel.LOW or _has_stale_warning(snapshot):
            reasons.append("LOW_CONFIDENCE_SOURCE")
        if lifecycle.confidence_score < self.config.actionability["minimum_actionable_confidence"]:
            reasons.append("CONFIDENCE_BELOW_ACTIONABLE_MIN")

        if reasons:
            return ActionabilityDecision(
                actionability=Actionability.LOW_CONFIDENCE,
                reason_codes=tuple(dict.fromkeys(reasons)),
                metadata={"precedence": "EVIDENCE_CONFIDENCE"},
            )

        if market in {"caution", "yellow", "neutral", "mixed"}:
            return ActionabilityDecision(
                actionability=Actionability.WATCH_ONLY,
                reason_codes=("MARKET_POLICY_REDUCED",),
                metadata={
                    "market_posture": "REDUCED",
                    "precedence": "REDUCED_MARKET_POSTURE",
                },
            )

        if state not in {
            LifecycleState.READY,
            LifecycleState.TRIGGERED,
            LifecycleState.CONFIRMED,
        }:
            return ActionabilityDecision(
                actionability=Actionability.WATCH_ONLY,
                reason_codes=(f"{state.value}_WATCH_ONLY",),
                metadata={"precedence": "LIFECYCLE_POSTURE"},
            )

        if state in {
            LifecycleState.READY,
            LifecycleState.TRIGGERED,
            LifecycleState.CONFIRMED,
        }:
            return ActionabilityDecision(
                actionability=Actionability.ACTIONABLE,
                reason_codes=("ACTIONABILITY_GATES_PASS",),
                metadata={"precedence": "ACTIONABLE_GATES_PASS"},
            )

        return ActionabilityDecision(
            actionability=Actionability.WATCH_ONLY,
            reason_codes=(f"{state.value}_WATCH_ONLY",),
        )


def _has_stale_warning(snapshot: NormalizedSnapshot) -> bool:
    warnings = {warning.casefold() for warning in snapshot.warning_flags}
    return any("stale" in warning or "near_stale" in warning for warning in warnings)


def _has_hard_required_absence(snapshot: NormalizedSnapshot) -> bool:
    warnings = {warning.casefold() for warning in snapshot.warning_flags}
    return any(
        warning
        in {
            "missing_required_technical_score",
            "missing_required_setup_score",
            "missing_required_classification",
            "missing_required_close_price",
            "hard_required_absent",
        }
        for warning in warnings
    )
