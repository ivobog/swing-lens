from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import WinnerDriftMetric
from app.services.winner_probability.calibration_service import (
    CalibrationExample,
    CalibrationService,
)
from app.services.winner_probability.config import (
    WinnerProbabilityConfig,
    load_winner_probability_config,
)


@dataclass(frozen=True)
class DriftMetricResult:
    metric_name: str
    metric_value: Decimal | None
    threshold_value: Decimal
    breached: bool
    sample_n: int
    sufficient_sample: bool
    comparison_window: str
    segment: dict[str, Any]


class DriftService:
    def calculate(
        self,
        *,
        baseline: tuple[CalibrationExample, ...],
        recent: tuple[CalibrationExample, ...],
        comparison_window: str,
        config: WinnerProbabilityConfig | None = None,
        segment: dict[str, Any] | None = None,
    ) -> tuple[DriftMetricResult, ...]:
        config = config or load_winner_probability_config()
        thresholds = config.drift.thresholds
        min_sample = int(thresholds["min_sample"])
        sufficient = len(recent) >= min_sample and len(baseline) >= min_sample
        baseline_report = CalibrationService().calculate(baseline)
        recent_report = CalibrationService().calculate(recent)
        values = {
            "brier_score_delta": _metric_delta(
                recent_report.metrics.get("brier_score"),
                baseline_report.metrics.get("brier_score"),
            ),
            "ece_delta": _metric_delta(
                recent_report.metrics.get("ece"),
                baseline_report.metrics.get("ece"),
            ),
            "win_rate_delta": _metric_delta(_win_rate(recent), _win_rate(baseline)),
            "psi": _psi(
                _probability_distribution(baseline),
                _probability_distribution(recent),
            ),
        }
        return tuple(
            _result(
                metric_name=name,
                value=value,
                threshold=Decimal(str(thresholds[name])),
                sample_n=len(recent),
                sufficient_sample=sufficient,
                comparison_window=comparison_window,
                segment=segment or {},
            )
            for name, value in values.items()
        )

    def persist_metrics(
        self,
        db: Session,
        *,
        results: tuple[DriftMetricResult, ...],
        outcome_definition_id: int,
        as_of_date: date,
        model_version_id: int | None = None,
    ) -> tuple[WinnerDriftMetric, ...]:
        rows: list[WinnerDriftMetric] = []
        for result in results:
            row = WinnerDriftMetric(
                model_version_id=model_version_id,
                outcome_definition_id=outcome_definition_id,
                as_of_date=as_of_date,
                metric_name=result.metric_name,
                metric_value=result.metric_value,
                threshold_value=result.threshold_value,
                breached=result.breached,
                sample_n=result.sample_n,
                comparison_window=result.comparison_window,
                segment_json=result.segment,
                sufficient_sample=result.sufficient_sample,
                calculated_at=_utcnow(),
            )
            db.add(row)
            rows.append(row)
        db.flush()
        return tuple(rows)


def _result(
    *,
    metric_name: str,
    value: Decimal | None,
    threshold: Decimal,
    sample_n: int,
    sufficient_sample: bool,
    comparison_window: str,
    segment: dict[str, Any],
) -> DriftMetricResult:
    return DriftMetricResult(
        metric_name=metric_name,
        metric_value=value,
        threshold_value=threshold,
        breached=bool(sufficient_sample and value is not None and abs(value) > threshold),
        sample_n=sample_n,
        sufficient_sample=sufficient_sample,
        comparison_window=comparison_window,
        segment=segment,
    )


def _metric_delta(
    recent: Decimal | int | None,
    baseline: Decimal | int | None,
) -> Decimal | None:
    if recent is None or baseline is None:
        return None
    return _quantize(Decimal(str(recent)) - Decimal(str(baseline)))


def _win_rate(examples: tuple[CalibrationExample, ...]) -> Decimal | None:
    if not examples:
        return None
    total_weight = sum((example.weight for example in examples), Decimal("0"))
    if total_weight <= 0:
        return None
    wins = sum((example.weight for example in examples if example.observed), Decimal("0"))
    return _quantize(wins / total_weight)


def _probability_distribution(
    examples: tuple[CalibrationExample, ...],
    *,
    bucket_count: int = 10,
) -> tuple[Decimal, ...]:
    counts = [Decimal("0.000001") for _ in range(bucket_count)]
    for example in examples:
        index = min(int(example.probability * bucket_count), bucket_count - 1)
        counts[index] += example.weight
    total = sum(counts, Decimal("0"))
    return tuple(count / total for count in counts)


def _psi(
    baseline_distribution: tuple[Decimal, ...],
    recent_distribution: tuple[Decimal, ...],
) -> Decimal:
    value = sum(
        (recent - baseline) * Decimal(str(float(recent) / float(baseline))).ln()
        for baseline, recent in zip(
            baseline_distribution,
            recent_distribution,
            strict=True,
        )
    )
    return _quantize(value)


def _quantize(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.000001"))


def _utcnow() -> datetime:
    return datetime.now(UTC)
