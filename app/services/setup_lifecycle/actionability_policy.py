from __future__ import annotations

from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import (
    ActionabilityDecision,
    LifecycleDecision,
    NormalizedSnapshot,
)
from app.services.setup_lifecycle.enums import Actionability, DataQualityLabel, LifecycleState
from app.services.setup_lifecycle.family_adapters import signal_bool, signal_text


class SetupLifecycleActionabilityPolicy:
    def __init__(self, config: SetupLifecycleConfig | None = None) -> None:
        self.config = config or load_setup_lifecycle_config()

    def evaluate(
        self,
        lifecycle: LifecycleDecision,
        snapshot: NormalizedSnapshot,
    ) -> ActionabilityDecision:
        state = lifecycle.proposed_state
        reasons: list[str] = []
        blockers: list[str] = []

        if state is LifecycleState.FAILED:
            return ActionabilityDecision(
                actionability=Actionability.BLOCKED,
                reason_codes=("FAILED_STATE_BLOCKED",),
                blockers=("FAILED",),
            )
        if state in {LifecycleState.EXPIRED, LifecycleState.EXTENDED}:
            return ActionabilityDecision(
                actionability=Actionability.WATCH_ONLY,
                reason_codes=(f"{state.value}_WATCH_ONLY",),
            )

        if _has_hard_required_absence(snapshot):
            blockers.append("HARD_REQUIRED_DATA_ABSENT")
        if snapshot.data_quality_label is DataQualityLabel.INSUFFICIENT:
            blockers.append("INSUFFICIENT_DATA_QUALITY")
        if signal_bool(snapshot, "liquidity"):
            blockers.append("LIQUIDITY_RISK")

        earnings = signal_text(snapshot, "earnings_risk").casefold()
        if earnings in {"imminent", "high", "blocked", "within_window"}:
            blockers.append("IMMINENT_EARNINGS")

        market = signal_text(snapshot, "market_regime").casefold()
        if market in {"blocked", "risk_off", "red", "bearish"}:
            blockers.append("MARKET_POLICY_BLOCK")

        if blockers:
            return ActionabilityDecision(
                actionability=Actionability.BLOCKED,
                reason_codes=tuple(dict.fromkeys((*reasons, "GATE_BLOCKED"))),
                blockers=tuple(dict.fromkeys(blockers)),
            )

        if snapshot.data_quality_label is DataQualityLabel.LOW or _has_stale_warning(snapshot):
            reasons.append("LOW_CONFIDENCE_SOURCE")
        if lifecycle.confidence_score < self.config.actionability["minimum_actionable_confidence"]:
            reasons.append("CONFIDENCE_BELOW_ACTIONABLE_MIN")
        if market in {"caution", "yellow", "neutral", "mixed"}:
            reasons.append("MARKET_POLICY_REDUCED")

        if reasons:
            return ActionabilityDecision(
                actionability=Actionability.LOW_CONFIDENCE,
                reason_codes=tuple(dict.fromkeys(reasons)),
            )

        if state in {
            LifecycleState.READY,
            LifecycleState.TRIGGERED,
            LifecycleState.CONFIRMED,
        }:
            return ActionabilityDecision(
                actionability=Actionability.ACTIONABLE,
                reason_codes=("ACTIONABILITY_GATES_PASS",),
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
