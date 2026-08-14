from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriEstimateSnapshot, CeriRevisionFeature
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import CeriConfidenceLabel, HistoricalViewMode
from app.services.ceri.point_in_time_query import (
    BaselineSelection,
    CeriPointInTimeQuery,
    canonical_estimate_key,
)


@dataclass(frozen=True)
class RevisionAggregate:
    strength: Decimal | None
    coverage_pct: float
    warnings: tuple[str, ...]


class CeriRevisionFeatureService:
    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        query: CeriPointInTimeQuery | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        self.query = query or CeriPointInTimeQuery(config=self.config)

    def calculate_feature(
        self,
        db: Session,
        *,
        company_id: int,
        metric: str,
        cutoff_at: datetime,
        window_days: int,
        period_slot: str | None = None,
        mode: HistoricalViewMode = HistoricalViewMode.AS_KNOWN,
    ) -> CeriRevisionFeature:
        current = self.query.current_snapshot(
            db,
            company_id=company_id,
            metric=metric,
            cutoff_at=cutoff_at,
            period_slot=period_slot,
            mode=mode,
        )
        selection = self.query.select_baseline(
            db,
            current=current,
            company_id=company_id,
            metric=metric,
            cutoff_at=cutoff_at,
            window_days=window_days,
            period_slot=period_slot,
            mode=mode,
        )
        return self._feature_from_selection(
            selection,
            company_id=company_id,
            metric=metric,
            cutoff_at=cutoff_at,
            window_days=window_days,
            period_slot=period_slot,
        )

    def calculate_windows(
        self,
        db: Session,
        *,
        company_id: int,
        metric: str,
        cutoff_at: datetime,
        period_slot: str | None = None,
        mode: HistoricalViewMode = HistoricalViewMode.AS_KNOWN,
    ) -> list[CeriRevisionFeature]:
        return [
            self.calculate_feature(
                db,
                company_id=company_id,
                metric=metric,
                cutoff_at=cutoff_at,
                window_days=window,
                period_slot=period_slot,
                mode=mode,
            )
            for window in self.config.revision.windows_days
        ]

    def persist_feature(self, db: Session, feature: CeriRevisionFeature) -> CeriRevisionFeature:
        db.add(feature)
        db.flush()
        return feature

    def aggregate_revision_strength(
        self,
        features: list[CeriRevisionFeature],
    ) -> RevisionAggregate:
        available = [feature for feature in features if feature.pct_change is not None]
        if not available:
            return RevisionAggregate(
                strength=None,
                coverage_pct=0.0,
                warnings=("revision_strength_unavailable",),
            )
        by_slot: dict[str, list[Decimal]] = {}
        for feature in available:
            if feature.period_slot is not None:
                by_slot.setdefault(feature.period_slot, []).append(feature.pct_change)
        available_weight = sum(
            (Decimal(str(weight))
            for slot, weight in self.config.revision.period_weights.items()
            if slot.value in by_slot),
            Decimal("0"),
        )
        coverage = float(Decimal("100") * available_weight)
        if coverage + 1e-9 < self.config.revision.minimum_component_coverage_pct:
            return RevisionAggregate(
                strength=None,
                coverage_pct=coverage,
                warnings=("revision_component_coverage_low",),
            )
        weighted = Decimal("0")
        for slot, weight in self.config.revision.period_weights.items():
            values = by_slot.get(slot.value)
            if not values:
                continue
            slot_value = sum(values, Decimal("0")) / Decimal(len(values))
            weighted += slot_value * Decimal(str(weight))
        strength = weighted / available_weight
        return RevisionAggregate(strength=strength, coverage_pct=coverage, warnings=())

    def reproduce_evidence_hash(self, feature: CeriRevisionFeature) -> str:
        return revision_evidence_hash(
            {
                "company_id": feature.company_id,
                "metric": feature.metric,
                "period_key": feature.period_key,
                "as_of_session": feature.as_of_session.isoformat(),
                "window_days": feature.window_days,
                "baseline_snapshot_id": feature.baseline_snapshot_id,
                "current_snapshot_id": feature.current_snapshot_id,
                "source_observation_ids": feature.source_observation_ids_json or [],
                "actual_elapsed_days": feature.actual_elapsed_days,
                "absolute_change": _decimal_text(feature.absolute_change),
                "pct_change": _decimal_text(feature.pct_change),
                "acceleration": _decimal_text(feature.acceleration),
                "config_hash": feature.config_hash,
                "calculation_version": feature.calculation_version,
                "unavailable_reason": feature.unavailable_reason,
                "comparison_mode": feature.comparison_mode,
                "current_source_record_id": feature.current_source_record_id,
                "baseline_source_record_id": feature.baseline_source_record_id,
                "provider_retrospective_source_record_id": (
                    feature.provider_retrospective_source_record_id
                ),
                "known_at": feature.known_at,
                "reference_at": feature.reference_at,
            }
        )

    def _feature_from_selection(
        self,
        selection: BaselineSelection,
        *,
        company_id: int,
        metric: str,
        cutoff_at: datetime,
        window_days: int,
        period_slot: str | None,
    ) -> CeriRevisionFeature:
        current = selection.current
        baseline = selection.baseline
        warnings: list[str] = []
        absolute_change: Decimal | None = None
        pct_change: Decimal | None = None
        dispersion: Decimal | None = None
        net_breadth: Decimal | None = None
        unavailable_reason = selection.unavailable_reason
        comparison_mode = selection.comparison_mode

        if current is not None:
            # Revision counts are dimensionless current-response evidence. They
            # do not depend on a monetary baseline or currency conversion.
            net_breadth = _net_breadth(current.upward_count, current.downward_count)
            if current.upward_count is None or current.downward_count is None:
                warnings.append("breadth_counts_unavailable")
            if current.analyst_count is None:
                warnings.append("analyst_sample_unavailable")
            elif current.analyst_count < self.config.revision.minimum_analyst_count:
                warnings.append("analyst_sample_sparse")

        if current is not None and baseline is not None:
            if current.consensus is None or baseline.consensus is None:
                unavailable_reason = "consensus_unavailable"
            else:
                if comparison_mode != "SAME_PROVIDER_RELATIVE":
                    absolute_change = current.consensus - baseline.consensus
                pct_change, pct_warnings = self._pct_change(current.consensus, baseline.consensus)
                warnings.extend(pct_warnings)
                if (
                    comparison_mode == "SAME_PROVIDER_RELATIVE"
                    and current.canonical_currency is None
                ):
                    warnings.append("canonical_currency_unavailable_relative_only")
                dispersion, dispersion_warning = self._dispersion(current)
                if dispersion_warning:
                    warnings.append(dispersion_warning)
        elif unavailable_reason:
            warnings.append(unavailable_reason)

        confidence_score = self._confidence_score(
            current,
            unavailable_reason=unavailable_reason,
            warnings=warnings,
        )
        confidence_label = self._confidence_label(confidence_score)
        period_key = (
            canonical_estimate_key(current)
            if current is not None
            else f"{company_id}:{metric}:{period_slot or 'unresolved'}:unavailable"
        )
        source_ids = [
            source_id
            for source_id in (
                baseline.source_record_id if baseline is not None else None,
                current.source_record_id if current is not None else None,
            )
            if source_id is not None
        ]
        feature = CeriRevisionFeature(
            company_id=company_id,
            metric=metric,
            period_key=period_key,
            period_slot=period_slot,
            as_of_session=cutoff_at.date(),
            window_days=window_days,
            baseline_snapshot_id=baseline.id if baseline is not None else None,
            current_snapshot_id=current.id if current is not None else None,
            actual_elapsed_days=selection.actual_elapsed_days,
            absolute_change=absolute_change,
            pct_change=pct_change,
            pct_change_unit="PERCENTAGE_POINTS",
            upward_count=current.upward_count if current is not None else None,
            downward_count=current.downward_count if current is not None else None,
            net_breadth=net_breadth,
            dispersion=dispersion,
            acceleration=None,
            acceleration_unit="PERCENTAGE_POINTS_PER_DAY",
            baseline_origin=(
                "ACCUMULATED_IMMUTABLE_OBSERVATION"
                if baseline is not None and comparison_mode == "HISTORICAL_OBSERVATION"
                else baseline.baseline_origin if baseline is not None else None
            ),
            comparison_mode=comparison_mode,
            current_source_record_id=(current.source_record_id if current is not None else None),
            baseline_source_record_id=(baseline.source_record_id if baseline is not None else None),
            provider_retrospective_source_record_id=(
                baseline.source_record_id
                if baseline is not None and comparison_mode == "SAME_PROVIDER_RELATIVE"
                else None
            ),
            known_at=current.known_at if current is not None else None,
            reference_at=(
                baseline.reference_at
                or baseline.known_at
                or baseline.effective_at
                if baseline is not None
                else None
            ),
            revision_confidence_score=confidence_score,
            revision_confidence_label=confidence_label.value,
            warnings_json=warnings or None,
            source_observation_ids_json=source_ids,
            provider_selection_reason=(
                "same_provider_retrospective_window"
                if comparison_mode == "SAME_PROVIDER_RELATIVE"
                else "point_in_time_latest_effective_at"
            ),
            unavailable_reason=unavailable_reason,
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
            calculation_version=self.config.engine.calculation_version,
        )
        feature.evidence_hash = self.reproduce_evidence_hash(feature)
        return feature

    def with_acceleration(
        self,
        recent: CeriRevisionFeature,
        longer: CeriRevisionFeature,
    ) -> CeriRevisionFeature:
        if (
            recent.pct_change is None
            or longer.pct_change is None
            or recent.actual_elapsed_days in (None, 0)
            or longer.actual_elapsed_days in (None, 0)
        ):
            recent.acceleration = None
            warnings = set(recent.warnings_json or [])
            warnings.add("acceleration_unavailable")
            recent.warnings_json = sorted(warnings)
            return recent
        recent_rate = recent.pct_change / Decimal(recent.actual_elapsed_days)
        longer_rate = longer.pct_change / Decimal(longer.actual_elapsed_days)
        recent.acceleration = recent_rate - longer_rate
        recent.acceleration_unit = "PERCENTAGE_POINTS_PER_DAY"
        recent.evidence_hash = self.reproduce_evidence_hash(recent)
        return recent

    def _pct_change(
        self,
        current: Decimal,
        baseline: Decimal,
    ) -> tuple[Decimal | None, list[str]]:
        threshold = Decimal(str(self.config.revision.near_zero_threshold))
        if abs(baseline) <= threshold:
            return None, ["pct_change_unavailable_near_zero_baseline"]
        if (current > 0 > baseline) or (current < 0 < baseline):
            return None, ["pct_change_unavailable_sign_change"]
        return (current - baseline) / abs(baseline) * Decimal("100"), []

    def _dispersion(self, current: CeriEstimateSnapshot) -> tuple[Decimal | None, str | None]:
        threshold = Decimal(str(self.config.revision.near_zero_threshold))
        if current.consensus is None or abs(current.consensus) <= threshold:
            return None, "dispersion_unavailable_near_zero_consensus"
        if current.high is None or current.low is None:
            return None, "dispersion_unavailable_missing_range"
        return (current.high - current.low) / abs(current.consensus), None

    def _confidence_score(
        self,
        current: CeriEstimateSnapshot | None,
        *,
        unavailable_reason: str | None,
        warnings: list[str],
    ) -> float:
        if current is None or unavailable_reason is not None:
            return 0.0
        score = 8.0
        if current.analyst_count is None:
            score -= 2.0
        elif current.analyst_count < self.config.revision.minimum_analyst_count:
            score -= 2.5
        if current.effective_at is None:
            score -= 1.0
        score -= min(2.0, 0.5 * len(warnings))
        return max(0.0, min(10.0, score))

    def _confidence_label(self, score: float) -> CeriConfidenceLabel:
        if score >= self.config.confidence.high_min:
            return CeriConfidenceLabel.HIGH
        if score >= self.config.confidence.normal_min:
            return CeriConfidenceLabel.NORMAL
        if score >= self.config.confidence.low_min:
            return CeriConfidenceLabel.LOW
        return CeriConfidenceLabel.INSUFFICIENT


def revision_evidence_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _net_breadth(upward_count: int | None, downward_count: int | None) -> Decimal | None:
    if upward_count is None or downward_count is None:
        return None
    total = upward_count + downward_count
    if total == 0:
        return Decimal("0")
    return Decimal(upward_count - downward_count) / Decimal(total)


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.normalize())
