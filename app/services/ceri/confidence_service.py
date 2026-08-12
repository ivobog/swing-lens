from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from app.models.ceri_tables import CeriRevisionFeature
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import CeriConfidenceLabel, CeriDataset


@dataclass(frozen=True)
class ConfidenceLedgerEntry:
    name: str
    value: float | None
    weight: float
    contribution: float
    unavailable_reason: str | None = None
    basis: str | None = None


@dataclass(frozen=True)
class ConfidenceResult:
    score: float
    label: CeriConfidenceLabel
    coverage_pct: float
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    ledger: tuple[ConfidenceLedgerEntry, ...] = ()
    gates: tuple[str, ...] = ()
    caps: tuple[str, ...] = ()


class CeriConfidenceService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()

    def calculate(
        self,
        *,
        as_of_session: date,
        revision_features: list[CeriRevisionFeature],
        source_quality: float | None = None,
        dataset_freshness_days: Mapping[str, int | None] | None = None,
        conflict_penalty: float = 0.0,
    ) -> ConfidenceResult:
        available = [feature for feature in revision_features if feature.pct_change is not None]
        expected_slots = (
            len(self.config.metrics.required)
            * len(self.config.metrics.period_types)
            * len(self.config.revision.windows_days)
        )
        coverage_pct = 100.0 * len(available) / expected_slots if expected_slots else 0.0
        coverage_pct = min(100.0, coverage_pct)
        if source_quality is None:
            quality_values = [
                float(feature.revision_confidence_score)
                for feature in available
                if feature.revision_confidence_score is not None
            ]
            source_quality = sum(quality_values) / len(quality_values) if quality_values else None
        freshness = _freshness_score(dataset_freshness_days, self.config)
        analyst = _analyst_score(self.config.revision.minimum_analyst_count, revision_features)
        timestamp = _timestamp_score(revision_features)
        estimate_coverage = min(10.0, coverage_pct / 10.0)
        conflict_free = max(0.0, 10.0 - conflict_penalty)
        weights = self.config.confidence.weights
        values = (
            ("source_quality", source_quality, "accepted_revision_feature_quality"),
            ("freshness", freshness, "dataset_known_or_retrieved_age"),
            ("estimate_coverage", estimate_coverage, "required_metric_period_window_slots"),
            ("analyst_sample", analyst, "accepted_estimate_revision_counts"),
            ("timestamp_quality", timestamp, "accepted_evidence_timestamp_provenance"),
            ("conflict_free_score", conflict_free, "accepted_evidence_conflicts"),
        )
        ledger = tuple(
            ConfidenceLedgerEntry(
                name=name,
                value=value,
                weight=float(weights[name]),
                contribution=(0.0 if value is None else value * float(weights[name])),
                unavailable_reason=(f"{name.upper()}_UNAVAILABLE" if value is None else None),
                basis=basis,
            )
            for name, value, basis in values
        )
        score = sum(entry.contribution for entry in ledger)
        warnings: list[str] = []
        reasons: list[str] = []
        gates: list[str] = []
        caps: list[str] = []
        if coverage_pct < self.config.revision.minimum_component_coverage_pct:
            warnings.append("estimate_coverage_low")
        if analyst is None:
            warnings.append("analyst_sample_unavailable")
        elif analyst < 6.0:
            warnings.append("analyst_sample_sparse")
        if freshness is None:
            warnings.append("dataset_freshness_unavailable")
        elif freshness < 6.0:
            warnings.append("estimate_data_stale")
        label = self._label(score)
        if not available:
            label = CeriConfidenceLabel.INSUFFICIENT
            gates.append("ZERO_USABLE_CORE_REVISION_COVERAGE")
            reasons.append("no_usable_core_revision_evidence")
        if warnings and label is CeriConfidenceLabel.HIGH:
            label = CeriConfidenceLabel.NORMAL
            reasons.append("high_confidence_capped_by_warnings")
            caps.append("WARNINGS_CAP_HIGH_TO_NORMAL")
        return ConfidenceResult(
            score=max(0.0, min(10.0, score)),
            label=label,
            coverage_pct=coverage_pct,
            ledger=ledger,
            gates=tuple(gates),
            caps=tuple(caps),
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


def _freshness_score(
    dataset_freshness_days: Mapping[str, int | None] | None,
    config: CeriConfig,
) -> float | None:
    if not dataset_freshness_days:
        return None
    ages = [age for age in dataset_freshness_days.values() if age is not None]
    if not ages:
        return None
    worst_age = max(ages)
    estimate_limit = config.datasets[CeriDataset.ESTIMATES].max_stale_days
    if worst_age <= 1:
        return 10.0
    if worst_age <= estimate_limit:
        return 7.0
    return max(0.0, 7.0 - float(worst_age - estimate_limit))


def _analyst_score(minimum: int, features: list[CeriRevisionFeature]) -> float | None:
    counts = [
        (feature.upward_count or 0) + (feature.downward_count or 0)
        for feature in features
        if feature.upward_count is not None and feature.downward_count is not None
    ]
    if not counts:
        return None
    sample = max(counts)
    if sample >= minimum * 2:
        return 10.0
    if sample >= minimum:
        return 7.0
    return 3.0


def _timestamp_score(features: list[CeriRevisionFeature]) -> float | None:
    usable = [feature for feature in features if feature.pct_change is not None]
    if not usable:
        return None
    warnings = set().union(*(set(feature.warnings_json or []) for feature in usable))
    if "current_snapshot_unavailable" in warnings or "baseline_unavailable" in warnings:
        return 4.0
    if any("missing_timestamp" in warning for warning in warnings):
        return 6.0
    return 9.0
