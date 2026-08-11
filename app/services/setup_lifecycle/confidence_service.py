from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import FamilyEvidence, NormalizedSnapshot
from app.services.setup_lifecycle.enums import ConfidenceLabel, DataQualityLabel


@dataclass(frozen=True)
class ConfidenceBreakdown:
    score: int
    label: ConfidenceLabel
    components: dict[str, float]
    reason_codes: tuple[str, ...]


class SetupLifecycleConfidenceService:
    def __init__(self, config: SetupLifecycleConfig | None = None) -> None:
        self.config = config or load_setup_lifecycle_config()

    def score(
        self,
        snapshot: NormalizedSnapshot,
        evidence: FamilyEvidence,
        *,
        persistence_sessions: int = 0,
        context_complete: bool | None = None,
    ) -> ConfidenceBreakdown:
        coverage = _coverage(snapshot)
        agreement = _agreement(evidence)
        persistence = min(1.0, max(0.0, persistence_sessions / 3))
        freshness = _freshness(snapshot)
        context = _context(snapshot) if context_complete is None else float(context_complete)

        components = {
            "required_feature_coverage": coverage,
            "signal_agreement": agreement,
            "persistence": persistence,
            "freshness_and_lineage": freshness,
            "context_completeness": context,
        }
        weighted = sum(
            components[key] * self.config.confidence.weights.get(key, 0.0)
            for key in components
        )
        score = max(0, min(100, round(weighted * 100)))
        if evidence.confidence_score:
            score = round((score + evidence.confidence_score) / 2)

        reason_codes: list[str] = []
        if coverage < 0.5:
            reason_codes.append("LOW_REQUIRED_FEATURE_COVERAGE")
        if freshness < 1.0:
            reason_codes.append("STALE_OR_LOW_QUALITY_SOURCE")
        if context < 1.0:
            reason_codes.append("MISSING_CONTEXT")
        if not evidence.trackable:
            reason_codes.append("NOT_TRACKABLE")

        return ConfidenceBreakdown(
            score=score,
            label=self.label_for_score(score),
            components=components,
            reason_codes=tuple(reason_codes),
        )

    def label_for_score(self, score: int) -> ConfidenceLabel:
        if score >= self.config.confidence.high_min:
            return ConfidenceLabel.HIGH
        if score >= self.config.confidence.normal_min:
            return ConfidenceLabel.NORMAL
        if score >= self.config.confidence.low_min:
            return ConfidenceLabel.LOW
        return ConfidenceLabel.INSUFFICIENT


def _coverage(snapshot: NormalizedSnapshot) -> float:
    if snapshot.required_feature_coverage is not None:
        return max(0.0, min(1.0, float(snapshot.required_feature_coverage)))
    if not snapshot.signals:
        return 0.0
    present = sum(signal.raw_value is not None for signal in snapshot.signals.values())
    return present / len(snapshot.signals)


def _agreement(evidence: FamilyEvidence) -> float:
    positive = sum(
        bool(value)
        for value in (
            evidence.trackable,
            evidence.ready,
            evidence.triggered,
            evidence.confirmed,
            evidence.extended,
        )
    )
    if evidence.hard_failure:
        return 0.0
    return max(0.0, min(1.0, (evidence.evidence_score / 10 + positive / 5) / 2))


def _freshness(snapshot: NormalizedSnapshot) -> float:
    if snapshot.data_quality_label in {DataQualityLabel.HIGH, DataQualityLabel.NORMAL}:
        return 1.0
    if snapshot.data_quality_label is DataQualityLabel.LOW:
        return 0.5
    return 0.0


def _context(snapshot: NormalizedSnapshot) -> float:
    lineage: dict[str, Any] = snapshot.source_lineage or {}
    has_market = lineage.get("market_regime_as_of") is not None
    has_sector = lineage.get("sector_rotation_as_of") is not None
    return (float(has_market) + float(has_sector)) / 2
