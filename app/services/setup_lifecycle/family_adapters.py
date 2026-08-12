from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import FamilyEvidence, NormalizedSnapshot, SignalValue
from app.services.setup_lifecycle.enums import SetupFamily


class FamilyAdapter(Protocol):
    setup_family: SetupFamily

    def evaluate(
        self,
        snapshot: NormalizedSnapshot,
        *,
        history: tuple[NormalizedSnapshot, ...] = (),
        previous_state: object | None = None,
        state_age_sessions: int = 0,
    ) -> FamilyEvidence:
        ...


def evaluate_family_candidates(
    snapshot: NormalizedSnapshot,
    *,
    config: SetupLifecycleConfig | None = None,
    history: tuple[NormalizedSnapshot, ...] = (),
    previous_state: object | None = None,
    state_age_sessions: int = 0,
) -> tuple[FamilyEvidence, ...]:
    from app.services.setup_lifecycle.breakout_adapter import BreakoutAdapter
    from app.services.setup_lifecycle.continuation_adapter import ContinuationAdapter
    from app.services.setup_lifecycle.generic_adapter import GenericAdapter
    from app.services.setup_lifecycle.pullback_adapter import PullbackAdapter
    from app.services.setup_lifecycle.vcp_adapter import VcpAdapter

    config = config or load_setup_lifecycle_config()
    adapters: tuple[FamilyAdapter, ...] = (
        BreakoutAdapter(config),
        PullbackAdapter(config),
        VcpAdapter(config),
        ContinuationAdapter(config),
    )
    evidences = [
        adapter.evaluate(
            snapshot,
            history=history,
            previous_state=previous_state,
            state_age_sessions=state_age_sessions,
        )
        for adapter in adapters
        if config.families.policies[adapter.setup_family].enabled
    ]
    candidates = [
        evidence
        for evidence in evidences
        if evidence.trackable
        and evidence.evidence_score
        >= config.families.policies[evidence.setup_family].tracking_score_min
    ]
    if candidates:
        return tuple(
            sorted(candidates, key=lambda item: _family_sort_key(item, config), reverse=True)
        )

    generic = GenericAdapter(config).evaluate(
        snapshot,
        history=history,
        previous_state=previous_state,
        state_age_sessions=state_age_sessions,
    )
    return (generic,) if generic.trackable else ()


def select_primary_family(
    evidences: tuple[FamilyEvidence, ...],
    *,
    config: SetupLifecycleConfig | None = None,
) -> FamilyEvidence | None:
    if not evidences:
        return None
    config = config or load_setup_lifecycle_config()
    return max(evidences, key=lambda item: _family_sort_key(item, config))


def generic_family_evidence(
    snapshot: NormalizedSnapshot,
    *,
    config: SetupLifecycleConfig | None = None,
) -> FamilyEvidence:
    from app.services.setup_lifecycle.generic_adapter import GenericAdapter

    return GenericAdapter(config).evaluate(snapshot)


def signal_value(snapshot: NormalizedSnapshot, key: str, default: Any = None) -> Any:
    signal = snapshot.signals.get(key)
    if isinstance(signal, SignalValue):
        return signal.normalized_value if signal.normalized_value is not None else signal.raw_value
    return default


def signal_bool(snapshot: NormalizedSnapshot, key: str) -> bool:
    value = signal_value(snapshot, key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes"}
    return bool(value)


def signal_number(snapshot: NormalizedSnapshot, key: str, default: float = 0.0) -> float:
    value = _number(signal_value(snapshot, key))
    return default if value is None else value


def signal_optional_number(snapshot: NormalizedSnapshot, key: str) -> float | None:
    return _number(signal_value(snapshot, key))


def signal_text(snapshot: NormalizedSnapshot, key: str) -> str:
    value = signal_value(snapshot, key)
    return str(value or "").strip()


def feature_flags(snapshot: NormalizedSnapshot) -> set[str]:
    raw = signal_value(snapshot, "feature_flags", ())
    if isinstance(raw, str):
        return {item.strip().casefold() for item in raw.split(",") if item.strip()}
    try:
        return {str(item).strip().casefold() for item in raw if str(item).strip()}
    except TypeError:
        return set()


def classification_contains(snapshot: NormalizedSnapshot, *tokens: str) -> bool:
    haystack = " ".join(
        (
            signal_text(snapshot, "classification"),
            signal_text(snapshot, "stage"),
            " ".join(feature_flags(snapshot)),
        )
    ).casefold()
    return any(token.casefold() in haystack for token in tokens)


def consecutive_true_sessions(
    snapshot: NormalizedSnapshot,
    history: tuple[NormalizedSnapshot, ...],
    key: str,
) -> int:
    count = 0
    for item in reversed((*history, snapshot)):
        if not signal_bool(item, key):
            break
        count += 1
    return count


def consecutive_family_condition_sessions(
    snapshot: NormalizedSnapshot,
    history: tuple[NormalizedSnapshot, ...],
    predicate,
) -> int:
    count = 0
    for item in reversed((*history, snapshot)):
        if not predicate(item):
            break
        count += 1
    return count


def numeric_history_is_improving(
    snapshot: NormalizedSnapshot,
    history: tuple[NormalizedSnapshot, ...],
    key: str,
    *,
    lower_is_better: bool,
    minimum_observations: int = 2,
) -> bool:
    values = [
        value
        for item in (*history, snapshot)
        if (value := signal_optional_number(item, key)) is not None
    ]
    if len(values) < minimum_observations:
        return False
    recent = values[-minimum_observations:]
    if lower_is_better:
        return all(
            current <= previous
            for previous, current in zip(recent, recent[1:], strict=False)
        ) and any(
            current < previous
            for previous, current in zip(recent, recent[1:], strict=False)
        )
    return all(
        current >= previous
        for previous, current in zip(recent, recent[1:], strict=False)
    ) and any(
        current > previous
        for previous, current in zip(recent, recent[1:], strict=False)
    )


def trend_agreement(snapshot: NormalizedSnapshot) -> float:
    score = signal_optional_number(snapshot, "trend_score")
    if score is None:
        score = signal_optional_number(snapshot, "technical_score")
    if score is None:
        return 0.5
    if score >= 5.5:
        return 1.0
    if score >= 4.0:
        return 0.5
    return 0.0


def relative_strength_agreement(snapshot: NormalizedSnapshot) -> float:
    values = [
        value
        for key in ("relative_strength", "leadership_score")
        if (value := signal_optional_number(snapshot, key)) is not None
    ]
    if not values:
        return 0.5
    score = max(values)
    if score >= 5.5:
        return 1.0
    if score >= 4.0:
        return 0.5
    return 0.0


def classification_agreement(snapshot: NormalizedSnapshot, *family_tokens: str) -> float:
    classification = signal_text(snapshot, "classification")
    stage = signal_text(snapshot, "stage")
    flags = feature_flags(snapshot)
    if not classification and not stage and not flags:
        return 0.5
    return 1.0 if classification_contains(snapshot, *family_tokens) else 0.0


def derived_atr_from_trigger(snapshot: NormalizedSnapshot) -> float | None:
    close = signal_optional_number(snapshot, "close_price")
    trigger = signal_optional_number(snapshot, "trigger_price")
    atr = signal_optional_number(snapshot, "atr_value")
    if close is None or trigger is None or atr is None or atr <= 0:
        return None
    return (close - trigger) / atr


def policy_parameter(
    config: SetupLifecycleConfig,
    family: SetupFamily,
    key: str,
    default: Any,
) -> Any:
    return config.families.policies[family].parameters.get(key, default)


def _family_sort_key(evidence: FamilyEvidence, config: SetupLifecycleConfig) -> tuple[Any, ...]:
    precedence = {
        family: len(config.families.precedence) - index
        for index, family in enumerate(config.families.precedence)
    }
    return (
        evidence.agreement_components.get("classification", 0.0),
        evidence.confidence_score,
        evidence.evidence_score,
        precedence[evidence.setup_family],
    )


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _confidence_from_score(score: float) -> int:
    if score <= 10:
        return max(0, min(100, round(score * 10)))
    return max(0, min(100, round(score)))
