from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models.ceri_tables import CeriRevisionFeature
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import CeriConfidenceLabel


@dataclass(frozen=True)
class ConfidenceResult:
    score: float
    label: CeriConfidenceLabel
    coverage_pct: float
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


class CeriConfidenceService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()

    def calculate(
        self,
        *,
        as_of_session: date,
        revision_features: list[CeriRevisionFeature],
        source_quality: float = 8.0,
        conflict_penalty: float = 0.0,
    ) -> ConfidenceResult:
        available = [feature for feature in revision_features if feature.pct_change is not None]
        coverage_pct = 100.0 * len(available) / len(revision_features) if revision_features else 0.0
        freshness = _freshness_score(as_of_session, revision_features)
        analyst = _analyst_score(self.config.revision.minimum_analyst_count, revision_features)
        timestamp = _timestamp_score(revision_features)
        estimate_coverage = min(10.0, coverage_pct / 10.0)
        conflict_free = max(0.0, 10.0 - conflict_penalty)
        weights = self.config.confidence.weights
        score = (
            source_quality * weights["source_quality"]
            + freshness * weights["freshness"]
            + estimate_coverage * weights["estimate_coverage"]
            + analyst * weights["analyst_sample"]
            + timestamp * weights["timestamp_quality"]
            + conflict_free * weights["conflict_free_score"]
        )
        warnings: list[str] = []
        reasons: list[str] = []
        if coverage_pct < self.config.revision.minimum_component_coverage_pct:
            warnings.append("estimate_coverage_low")
        if analyst < 6.0:
            warnings.append("analyst_sample_sparse")
        if freshness < 6.0:
            warnings.append("estimate_data_stale")
        label = self._label(score)
        if warnings and label is CeriConfidenceLabel.HIGH:
            label = CeriConfidenceLabel.NORMAL
            reasons.append("high_confidence_capped_by_warnings")
        return ConfidenceResult(
            score=max(0.0, min(10.0, score)),
            label=label,
            coverage_pct=coverage_pct,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def _label(self, score: float) -> CeriConfidenceLabel:
        if score >= self.config.confidence.high_min:
            return CeriConfidenceLabel.HIGH
        if score >= self.config.confidence.normal_min:
            return CeriConfidenceLabel.NORMAL
        if score >= self.config.confidence.low_min:
            return CeriConfidenceLabel.LOW
        return CeriConfidenceLabel.INSUFFICIENT


def _freshness_score(as_of_session: date, features: list[CeriRevisionFeature]) -> float:
    elapsed = [feature.actual_elapsed_days for feature in features if feature.actual_elapsed_days]
    if not elapsed:
        return 4.0
    newest_age = min(abs((as_of_session - feature.as_of_session).days) for feature in features)
    if newest_age <= 1:
        return 10.0
    if newest_age <= 7:
        return 7.0
    return 4.0


def _analyst_score(minimum: int, features: list[CeriRevisionFeature]) -> float:
    counts = [
        (feature.upward_count or 0) + (feature.downward_count or 0)
        for feature in features
        if feature.upward_count is not None and feature.downward_count is not None
    ]
    if not counts:
        return 4.0
    sample = max(counts)
    if sample >= minimum * 2:
        return 10.0
    if sample >= minimum:
        return 7.0
    return 3.0


def _timestamp_score(features: list[CeriRevisionFeature]) -> float:
    warnings = set().union(*(set(feature.warnings_json or []) for feature in features))
    if "current_snapshot_unavailable" in warnings or "baseline_unavailable" in warnings:
        return 4.0
    if any("missing_timestamp" in warning for warning in warnings):
        return 6.0
    return 9.0
