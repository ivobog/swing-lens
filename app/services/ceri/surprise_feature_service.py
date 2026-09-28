from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.models.ceri_tables import CeriEarningsActual, CeriEstimateSnapshot
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.pit_eligibility import estimate_snapshot_historical_eligibility_at


@dataclass(frozen=True)
class SurpriseFeature:
    earnings_actual_id: int | None
    earnings_source_record_id: int | None
    consensus_snapshot_id: int | None
    consensus_source_record_id: int | None
    consensus_selection_reason: str
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
        """Calculate context-owned evidence without mutating normalized source rows."""

        if (
            earnings.provider_consensus_value is not None
            and earnings.provider_consensus_semantics == "REPORT_TIME_CONSENSUS"
        ):
            selection_reason = (
                "provider_report_time_consensus_and_surprise"
                if earnings.provider_surprise_pct is not None
                else "provider_consensus_at_report"
            )
            if earnings.actual_value is None:
                return _feature(
                    earnings,
                    None,
                    selection_reason=selection_reason,
                    surprise_absolute=None,
                    surprise_pct=None,
                    warnings=["surprise_actual_unavailable"],
                )
            surprise_absolute = earnings.actual_value - earnings.provider_consensus_value
            if earnings.provider_surprise_pct is not None:
                return _feature(
                    earnings,
                    None,
                    selection_reason=selection_reason,
                    surprise_absolute=surprise_absolute,
                    surprise_pct=earnings.provider_surprise_pct,
                    warnings=[],
                )
            threshold = Decimal(str(self.config.revision.near_zero_threshold))
            if abs(earnings.provider_consensus_value) <= threshold:
                surprise_pct = None
                warnings = ["surprise_pct_unavailable_near_zero_consensus"]
            else:
                surprise_pct = (
                    surprise_absolute / abs(earnings.provider_consensus_value) * Decimal("100")
                )
                warnings = []
            return _feature(
                earnings,
                None,
                selection_reason=selection_reason,
                surprise_absolute=surprise_absolute,
                surprise_pct=surprise_pct,
                warnings=warnings,
            )
        consensus = self._consensus_before_report(earnings, estimates)
        warnings: list[str] = []
        if consensus is None:
            warnings.append("pre_report_consensus_unavailable")
            return _feature(
                earnings,
                None,
                selection_reason="pre_report_consensus_unavailable",
                surprise_absolute=None,
                surprise_pct=None,
                warnings=warnings,
            )

        if earnings.actual_value is None or consensus.consensus is None:
            warnings.append("surprise_value_unavailable")
            return _feature(
                earnings,
                consensus,
                selection_reason="latest_consensus_before_report_at",
                surprise_absolute=None,
                surprise_pct=None,
                warnings=warnings,
            )

        surprise_absolute = earnings.actual_value - consensus.consensus
        threshold = Decimal(str(self.config.revision.near_zero_threshold))
        if abs(consensus.consensus) <= threshold:
            surprise_pct = None
            warnings.append("surprise_pct_unavailable_near_zero_consensus")
        else:
            surprise_pct = surprise_absolute / abs(consensus.consensus) * Decimal("100")
        return _feature(
            earnings,
            consensus,
            selection_reason="latest_consensus_before_report_at",
            surprise_absolute=surprise_absolute,
            surprise_pct=surprise_pct,
            warnings=warnings,
        )

    def summarize(
        self,
        earnings: list[CeriEarningsActual],
        estimates: list[CeriEstimateSnapshot],
        *,
        price_response_quality: float | None = None,
    ) -> SurpriseSummary:
        reported = [
            row
            for row in earnings
            if row.actual_value is not None and row.event_kind in (None, "REPORTED")
        ]
        ordered = sorted(
            reported,
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
        candidates = []
        for snapshot in estimates:
            known_at = estimate_snapshot_historical_eligibility_at(snapshot)
            if (
                snapshot.company_id == earnings.company_id
                and snapshot.metric == earnings.metric
                and snapshot.period_type == earnings.period_type
                and snapshot.fiscal_period_end == earnings.fiscal_period_end
                and snapshot.consensus is not None
                and snapshot.effective_at is not None
                and snapshot.effective_at < earnings.report_at
                and known_at is not None
                and known_at < earnings.report_at
            ):
                candidates.append(snapshot)
        if not candidates:
            return None
        return max(candidates, key=lambda row: (row.effective_at, row.id or 0))


def _feature(
    earnings: CeriEarningsActual,
    consensus: CeriEstimateSnapshot | None,
    *,
    selection_reason: str,
    surprise_absolute: Decimal | None,
    surprise_pct: Decimal | None,
    warnings: list[str],
) -> SurpriseFeature:
    direction = "neutral"
    if surprise_absolute is not None and surprise_absolute > 0:
        direction = "positive"
    elif surprise_absolute is not None and surprise_absolute < 0:
        direction = "negative"
    return SurpriseFeature(
        earnings_actual_id=earnings.id,
        earnings_source_record_id=earnings.source_record_id,
        consensus_snapshot_id=consensus.id if consensus is not None else None,
        consensus_source_record_id=(consensus.source_record_id if consensus is not None else None),
        consensus_selection_reason=selection_reason,
        metric=earnings.metric,
        fiscal_period_end=earnings.fiscal_period_end.isoformat(),
        surprise_absolute=surprise_absolute,
        surprise_pct=surprise_pct,
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
