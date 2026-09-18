from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import WinnerDriftMetric
from app.services.core_mutation_authority import core_writer_transaction
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

    @core_writer_transaction
    def persist_metrics(
        self,
        db: Session,
        *,
        results: tuple[DriftMetricResult, ...],
        outcome_definition_id: int,
        as_of_date: date,
        model_version_id: int | None = None,
        mutation_context=None,
        effective_configuration=None,
        baseline_estimate_ids: tuple[int, ...] = (),
        recent_estimate_ids: tuple[int, ...] = (),
        estimate_kind: str = "DECISION_TIME",
        baseline_held_out_indices: tuple[int, ...] = (),
        recent_held_out_indices: tuple[int, ...] = (),
    ) -> tuple[WinnerDriftMetric, ...]:
        if isinstance(db, Session):
            from app.services.winner_probability.mutation_authority import (
                validate_diagnostic_authority,
            )

            population = validate_diagnostic_authority(
                db,
                mutation_context,
                writer="DriftService.persist_metrics",
                subject_id=model_version_id,
                outcome_id=outcome_definition_id,
                configuration=effective_configuration,
                contract={
                    "artifact": "DRIFT",
                    "results": [asdict(r) for r in results],
                    "as_of_date": as_of_date,
                    "baseline_estimate_ids": baseline_estimate_ids,
                    "recent_estimate_ids": recent_estimate_ids,
                    "estimate_kind": estimate_kind,
                    **(
                        {
                            "baseline_held_out_indices": baseline_held_out_indices,
                            "recent_held_out_indices": recent_held_out_indices,
                        }
                        if estimate_kind == "SHADOW_WALK_FORWARD"
                        else {}
                    ),
                },
            )
            if as_of_date != mutation_context.temporal.latest_completed_session:
                raise ValueError("MUTATION_WINNER_DRIFT_AS_OF_MISMATCH")
            from types import SimpleNamespace

            from app.models.tables import WinnerProbabilityEstimate
            from app.services.winner_probability.mutation_authority import calibration_examples

            ids = baseline_estimate_ids + recent_estimate_ids
            shadow = estimate_kind == "SHADOW_WALK_FORWARD"
            if not results or (
                not shadow and (len(ids) != len(set(ids)) or len(ids) != population.member_count)
            ):
                raise ValueError("MUTATION_WINNER_DRIFT_EXACT_POPULATIONS_REQUIRED")
            if shadow and (
                ids
                or not baseline_held_out_indices
                or not recent_held_out_indices
                or set(baseline_held_out_indices) & set(recent_held_out_indices)
            ):
                raise ValueError("MUTATION_WINNER_SHADOW_DRIFT_EXACT_SPLIT_REQUIRED")

            def examples(selected):
                if shadow:
                    from app.services.winner_probability.mutation_authority import (
                        shadow_calibration_examples,
                    )

                    return shadow_calibration_examples(
                        db,
                        population,
                        outcome_id=outcome_definition_id,
                        model_id=model_version_id,
                        as_of=mutation_context.temporal.cutoff_at,
                        indices=selected,
                    )
                estimates = [db.get(WinnerProbabilityEstimate, id) for id in selected]
                if any(p is None for p in estimates):
                    raise ValueError("MUTATION_WINNER_DRIFT_ESTIMATE_MISSING")
                predictions = {p.prediction_id for p in estimates}
                partial = SimpleNamespace(
                    payload_json={
                        "members": [
                            p
                            for p in population.payload_json["members"]
                            if p["prediction_id"] in predictions
                        ]
                    }
                )
                return calibration_examples(
                    db,
                    partial,
                    outcome_id=outcome_definition_id,
                    model_id=model_version_id,
                    estimate_kind=estimate_kind,
                    estimate_ids=selected,
                    as_of=mutation_context.temporal.cutoff_at,
                )

            native = DriftService().calculate(
                baseline=examples(baseline_held_out_indices if shadow else baseline_estimate_ids),
                recent=examples(recent_held_out_indices if shadow else recent_estimate_ids),
                comparison_window=results[0].comparison_window,
                segment=results[0].segment,
                config=effective_configuration.winner_config(),
            )
            if native != results:
                raise ValueError("MUTATION_WINNER_DRIFT_REPORT_MISMATCH")
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
                segment_json={
                    **result.segment,
                    "mutation_authority": mutation_context.canonical_payload(),
                    "native_report": _native_report_payload(results),
                }
                if isinstance(db, Session)
                else result.segment,
                sufficient_sample=result.sufficient_sample,
                calculated_at=_utcnow(),
            )
            if isinstance(db, Session):
                from app.services.winner_probability.mutation_authority import (
                    seal_diagnostic_artifact,
                )

                seal_diagnostic_artifact(row, "segment_json")
            db.add(row)
            rows.append(row)
        db.flush()
        return tuple(rows)


def _native_report_payload(results):
    from app.services.canonical_evidence import CanonicalEvidenceSerializer

    return CanonicalEvidenceSerializer.canonicalize([asdict(row) for row in results])


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
