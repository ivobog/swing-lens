from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.models.ceri_tables import CeriEarningsActual, CeriEstimateSnapshot
from app.services.ceri.config import CeriConfig, load_ceri_config


@dataclass(frozen=True)
class SurpriseFeature:
    earnings_actual_id: int | None
    consensus_snapshot_id: int | None
    metric: str
    fiscal_period_end: str
    surprise_absolute: Decimal | None
    surprise_pct: Decimal | None
    direction: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SurpriseSummary:
    features: tuple[SurpriseFeature, ...]
    average_surprise_pct: Decimal | None
    positive_count: int
    negative_count: int
    consistency: str
    price_response_quality: float | None


class CeriSurpriseFeatureService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()

    def attach_consensus_snapshot(
        self,
        earnings: CeriEarningsActual,
        estimates: list[CeriEstimateSnapshot],
    ) -> SurpriseFeature:
        if earnings.provider_consensus_value is not None:
            earnings.consensus_snapshot_id = None
            earnings.consensus_selection_reason = "provider_consensus_at_report"
            if earnings.actual_value is None:
                earnings.surprise_absolute = None
                earnings.surprise_pct = None
                return _feature(earnings, None, ["surprise_actual_unavailable"])
            earnings.surprise_absolute = (
                earnings.actual_value - earnings.provider_consensus_value
            )
            threshold = Decimal(str(self.config.revision.near_zero_threshold))
            if abs(earnings.provider_consensus_value) <= threshold:
                earnings.surprise_pct = None
                warnings = ["surprise_pct_unavailable_near_zero_consensus"]
            else:
                earnings.surprise_pct = (
                    earnings.surprise_absolute
                    / abs(earnings.provider_consensus_value)
                    * Decimal("100")
                )
                warnings = []
            return _feature(earnings, None, warnings)
        consensus = self._consensus_before_report(earnings, estimates)
        warnings: list[str] = []
        if consensus is None:
            earnings.consensus_snapshot_id = None
            earnings.consensus_selection_reason = "pre_report_consensus_unavailable"
            earnings.surprise_absolute = None
            earnings.surprise_pct = None
            warnings.append("pre_report_consensus_unavailable")
            return _feature(earnings, None, warnings)

        earnings.consensus_snapshot_id = consensus.id
        earnings.consensus_selection_reason = "latest_consensus_before_report_at"
        if earnings.actual_value is None or consensus.consensus is None:
            earnings.surprise_absolute = None
            earnings.surprise_pct = None
            warnings.append("surprise_value_unavailable")
            return _feature(earnings, consensus, warnings)

        earnings.surprise_absolute = earnings.actual_value - consensus.consensus
        threshold = Decimal(str(self.config.revision.near_zero_threshold))
        if abs(consensus.consensus) <= threshold:
            earnings.surprise_pct = None
            warnings.append("surprise_pct_unavailable_near_zero_consensus")
        else:
            earnings.surprise_pct = (
                earnings.surprise_absolute / abs(consensus.consensus) * Decimal("100")
            )
        return _feature(earnings, consensus, warnings)

    def summarize(
        self,
        earnings: list[CeriEarningsActual],
        estimates: list[CeriEstimateSnapshot],
        *,
        price_response_quality: float | None = None,
    ) -> SurpriseSummary:
        ordered = sorted(
            earnings,
            key=lambda row: row.report_at or datetime.min,
            reverse=True,
        )[:4]
        features = tuple(self.attach_consensus_snapshot(row, estimates) for row in ordered)
        pct_values = [
            feature.surprise_pct for feature in features if feature.surprise_pct is not None
        ]
        average = sum(pct_values, Decimal("0")) / Decimal(len(pct_values)) if pct_values else None
        positive = sum(1 for feature in features if feature.direction == "positive")
        negative = sum(1 for feature in features if feature.direction == "negative")
        return SurpriseSummary(
            features=features,
            average_surprise_pct=average,
            positive_count=positive,
            negative_count=negative,
            consistency=_consistency(positive, negative, len(features)),
            price_response_quality=price_response_quality,
        )

    def _consensus_before_report(
        self,
        earnings: CeriEarningsActual,
        estimates: list[CeriEstimateSnapshot],
    ) -> CeriEstimateSnapshot | None:
        if earnings.report_at is None:
            return None
        candidates = [
            snapshot
            for snapshot in estimates
            if snapshot.company_id == earnings.company_id
            and snapshot.metric == earnings.metric
            and snapshot.period_type == earnings.period_type
            and snapshot.fiscal_period_end == earnings.fiscal_period_end
            and snapshot.consensus is not None
            and snapshot.effective_at is not None
            and snapshot.effective_at < earnings.report_at
            and _known_at(snapshot) < earnings.report_at
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: (row.effective_at, row.id or 0))


def _feature(
    earnings: CeriEarningsActual,
    consensus: CeriEstimateSnapshot | None,
    warnings: list[str],
) -> SurpriseFeature:
    direction = "neutral"
    if earnings.surprise_absolute is not None and earnings.surprise_absolute > 0:
        direction = "positive"
    elif earnings.surprise_absolute is not None and earnings.surprise_absolute < 0:
        direction = "negative"
    return SurpriseFeature(
        earnings_actual_id=earnings.id,
        consensus_snapshot_id=consensus.id if consensus is not None else None,
        metric=earnings.metric,
        fiscal_period_end=earnings.fiscal_period_end.isoformat(),
        surprise_absolute=earnings.surprise_absolute,
        surprise_pct=earnings.surprise_pct,
        direction=direction,
        warnings=tuple(warnings),
    )


def _consistency(positive: int, negative: int, total: int) -> str:
    if total == 0:
        return "unavailable"
    if positive == total:
        return "consistently_positive"
    if negative == total:
        return "consistently_negative"
    if positive > negative:
        return "mixed_positive"
    if negative > positive:
        return "mixed_negative"
    return "mixed"


def _known_at(snapshot: CeriEstimateSnapshot) -> datetime:
    return (
        snapshot.known_at
        or snapshot.provider_observed_at
        or snapshot.source_timestamp
        or snapshot.retrieved_at
        or snapshot.effective_at
        or datetime.max
    )
