from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.services.setup_lifecycle.enums import (
    Actionability,
    ConfidenceLabel,
    DataQualityLabel,
    EventSeverity,
    LifecycleState,
    SetupFamily,
    SignalCategory,
    SignalValueType,
)


@dataclass(frozen=True)
class SignalValue:
    key: str
    value_type: SignalValueType
    raw_value: Any
    normalized_value: Any = None
    unit: str | None = None
    quality: DataQualityLabel | None = None
    source_path: str | None = None


@dataclass(frozen=True)
class NormalizedSnapshot:
    ticker: str
    timeframe: str
    data_as_of_date: date
    calculated_at: datetime
    signals: dict[str, SignalValue]
    data_quality_label: DataQualityLabel
    required_feature_coverage: float | None = None
    freshness_status: str | None = None
    warning_flags: tuple[str, ...] = ()
    source_ids: dict[str, int | None] = field(default_factory=dict)
    source_lineage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChangeDecision:
    signal_key: str
    material: bool
    category: SignalCategory
    severity: EventSeverity
    direction: str
    old_value: Any
    new_value: Any
    delta: float | None = None
    normalized_delta: float | None = None
    threshold_name: str | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FamilyEvidence:
    setup_family: SetupFamily
    phase_code: str
    evidence_score: float
    confidence_score: int
    trackable: bool
    ready: bool = False
    triggered: bool = False
    confirmed: bool = False
    extended: bool = False
    hard_failure: bool = False
    reason_codes: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LifecycleDecision:
    setup_family: SetupFamily
    phase_code: str
    previous_state: LifecycleState | None
    proposed_state: LifecycleState
    actionability_candidate: Actionability
    confidence_score: int
    confidence_label: ConfidenceLabel
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]
    immediate_transition: bool = False
    terminal_reason: str | None = None


@dataclass(frozen=True)
class ActionabilityDecision:
    actionability: Actionability
    reason_codes: tuple[str, ...]
    blockers: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EpisodeApplyResult:
    episode_id: int | None
    opened: bool = False
    updated: bool = False
    closed: bool = False
    lifecycle_event_id: int | None = None
    warning_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlertEvaluationResult:
    created: int = 0
    suppressed: int = 0
    duplicate: int = 0
    warning_codes: tuple[str, ...] = ()
