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
        previous_state: object | None = None,
        state_age_sessions: int = 0,
    ) -> FamilyEvidence:
        ...


def evaluate_family_candidates(
    snapshot: NormalizedSnapshot,
    *,
    config: SetupLifecycleConfig | None = None,
    previous_state: object | None = None,
    state_age_sessions: int = 0,
) -> tuple[FamilyEvidence, ...]:
    from app.services.setup_lifecycle.breakout_adapter import BreakoutAdapter
    from app.services.setup_lifecycle.continuation_adapter import ContinuationAdapter
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

    generic = generic_family_evidence(snapshot, config=config)
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
    config = config or load_setup_lifecycle_config()
    policy = config.families.policies[SetupFamily.GENERIC]
    score = _number(signal_value(snapshot, "technical_score")) or _number(
        signal_value(snapshot, "setup_score")
    ) or 0.0
    trackable = policy.enabled and score >= policy.tracking_score_min
    ready = score >= policy.ready_score_min
    phase = "READY" if ready else "IMPROVING" if trackable else "CANDIDATE"
    return FamilyEvidence(
        setup_family=SetupFamily.GENERIC,
        phase_code=phase,
        evidence_score=score,
        confidence_score=_confidence_from_score(score),
        trackable=trackable,
        ready=ready,
        triggered=bool(signal_value(snapshot, "close_trigger_cross")) if ready else False,
        confirmed=False,
        extended=False,
        hard_failure=False,
        reason_codes=("GENERIC_FALLBACK",),
        evidence={"technical_score": score},
    )


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
