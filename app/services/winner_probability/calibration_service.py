from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import WinnerCalibrationBin
from app.services.core_mutation_authority import core_writer_transaction


@dataclass(frozen=True)
class CalibrationExample:
    probability: Decimal
    observed: bool
    weight: Decimal = Decimal("1")
    segment: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReliabilityBin:
    bin_floor: Decimal
    bin_ceiling: Decimal
    sample_n: int
    effective_n: Decimal
    mean_prediction: Decimal | None
    observed_rate: Decimal | None
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    error: Decimal | None


@dataclass(frozen=True)
class CalibrationReport:
    bins: tuple[ReliabilityBin, ...]
    metrics: dict[str, Decimal | int | None]


class CalibrationService:
    def calculate(
        self,
        examples: tuple[CalibrationExample, ...],
        *,
        bin_count: int = 10,
    ) -> CalibrationReport:
        if bin_count <= 0:
            raise ValueError("bin_count must be positive")
        bins = tuple(_calculate_bin(index, bin_count, examples) for index in range(bin_count))
        metrics = _metrics(examples, bins)
        return CalibrationReport(bins=bins, metrics=metrics)

    @core_writer_transaction
    def persist_bins(
        self,
        db: Session,
        *,
        report: CalibrationReport,
        outcome_definition_id: int,
        estimate_kind: str,
        model_version_id: int | None = None,
        segment: dict[str, Any] | None = None,
        mutation_context=None,
        effective_configuration=None,
        estimate_ids: tuple[int, ...] = (),
    ) -> tuple[WinnerCalibrationBin, ...]:
        if isinstance(db, Session):
            from app.services.winner_probability.mutation_authority import (
                validate_diagnostic_authority,
            )

            population = validate_diagnostic_authority(
                db,
                mutation_context,
                writer="CalibrationService.persist_bins",
                subject_id=model_version_id,
                outcome_id=outcome_definition_id,
                configuration=effective_configuration,
                contract={
                    "artifact": "CALIBRATION",
                    "report": asdict(report),
                    "estimate_kind": estimate_kind,
                    "segment": segment or {},
                    "estimate_ids": estimate_ids,
                },
            )
            from app.services.winner_probability.mutation_authority import calibration_examples

            examples = calibration_examples(
                db,
                population,
                outcome_id=outcome_definition_id,
                model_id=model_version_id,
                estimate_kind=estimate_kind,
                estimate_ids=estimate_ids,
                as_of=mutation_context.temporal.cutoff_at,
            )
            if CalibrationService().calculate(examples, bin_count=len(report.bins)) != report:
                raise ValueError("MUTATION_WINNER_CALIBRATION_REPORT_MISMATCH")
        rows: list[WinnerCalibrationBin] = []
        for bin_row in report.bins:
            row = WinnerCalibrationBin(
                model_version_id=model_version_id,
                outcome_definition_id=outcome_definition_id,
                estimate_kind=estimate_kind,
                bin_floor=bin_row.bin_floor,
                bin_ceiling=bin_row.bin_ceiling,
                sample_n=bin_row.sample_n,
                effective_n=bin_row.effective_n,
                mean_prediction=bin_row.mean_prediction,
                observed_rate=bin_row.observed_rate,
                lower_bound=bin_row.lower_bound,
                upper_bound=bin_row.upper_bound,
                error=bin_row.error,
                calculated_at=_utcnow(),
                segment_json={
                    **(segment or {}),
                    "mutation_authority": mutation_context.canonical_payload(),
                    "native_report": _native_report_payload(report),
                }
                if isinstance(db, Session)
                else segment or {},
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


def _native_report_payload(report):
    from app.services.canonical_evidence import CanonicalEvidenceSerializer

    return CanonicalEvidenceSerializer.canonicalize(asdict(report))


def _calculate_bin(
    index: int,
    bin_count: int,
    examples: tuple[CalibrationExample, ...],
) -> ReliabilityBin:
    floor = Decimal(index) / Decimal(bin_count)
    ceiling = Decimal(index + 1) / Decimal(bin_count)
    members = [
        example
        for example in examples
        if example.probability >= floor
        and (
            example.probability < ceiling
            or (index == bin_count - 1 and example.probability <= ceiling)
        )
    ]
    sample_n = len(members)
    effective_n = sum((example.weight for example in members), Decimal("0"))
    if not members or effective_n <= 0:
        return ReliabilityBin(
            bin_floor=_quantize(floor),
            bin_ceiling=_quantize(ceiling),
            sample_n=0,
            effective_n=Decimal("0.000000"),
            mean_prediction=None,
            observed_rate=None,
            lower_bound=None,
            upper_bound=None,
            error=None,
        )
    weighted_prediction = sum(
        (example.probability * example.weight for example in members),
        Decimal("0"),
    )
    weighted_observed = sum(
        (example.weight for example in members if example.observed),
        Decimal("0"),
    )
    mean_prediction = weighted_prediction / effective_n
    observed_rate = weighted_observed / effective_n
    lower_bound, upper_bound = _normal_interval(observed_rate, effective_n)
    return ReliabilityBin(
        bin_floor=_quantize(floor),
        bin_ceiling=_quantize(ceiling),
        sample_n=sample_n,
        effective_n=_quantize(effective_n),
        mean_prediction=_quantize(mean_prediction),
        observed_rate=_quantize(observed_rate),
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        error=_quantize(abs(mean_prediction - observed_rate)),
    )


def _metrics(
    examples: tuple[CalibrationExample, ...],
    bins: tuple[ReliabilityBin, ...],
) -> dict[str, Decimal | int | None]:
    total_weight = sum((example.weight for example in examples), Decimal("0"))
    if not examples or total_weight <= 0:
        return {
            "sample_n": 0,
            "effective_n": Decimal("0.000000"),
            "brier_score": None,
            "log_loss": None,
            "ece": None,
            "calibration_slope": None,
            "calibration_intercept": None,
            "coverage": Decimal("0.000000"),
        }
    brier = (
        sum(
            ((example.probability - _observed_decimal(example)) ** 2) * example.weight
            for example in examples
        )
        / total_weight
    )
    log_loss = (
        sum(
            (_log_loss(example.probability, example.observed) * example.weight)
            for example in examples
        )
        / total_weight
    )
    ece = sum(
        (bin_row.effective_n / total_weight) * (bin_row.error or Decimal("0"))
        for bin_row in bins
        if bin_row.effective_n > 0
    )
    slope, intercept = _linear_calibration(examples)
    coverage = Decimal(len(examples)) / Decimal(max(len(examples), 1))
    return {
        "sample_n": len(examples),
        "effective_n": _quantize(total_weight),
        "brier_score": _quantize(brier),
        "log_loss": _quantize(log_loss),
        "ece": _quantize(ece),
        "calibration_slope": slope,
        "calibration_intercept": intercept,
        "coverage": _quantize(coverage),
    }


def _linear_calibration(
    examples: tuple[CalibrationExample, ...],
) -> tuple[Decimal | None, Decimal | None]:
    if len(examples) < 2:
        return None, None
    xs = [float(example.probability) for example in examples]
    ys = [1.0 if example.observed else 0.0 for example in examples]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    variance = sum((value - x_mean) ** 2 for value in xs)
    if variance == 0:
        return None, None
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True)) / variance
    intercept = y_mean - slope * x_mean
    return _quantize(Decimal(str(slope))), _quantize(Decimal(str(intercept)))


def _normal_interval(rate: Decimal, effective_n: Decimal) -> tuple[Decimal, Decimal]:
    if effective_n <= 0:
        return Decimal("0.000000"), Decimal("1.000000")
    variance = rate * (Decimal("1") - rate) / effective_n
    radius = Decimal("1.96") * Decimal(str(math.sqrt(max(float(variance), 0.0))))
    return _quantize(max(Decimal("0"), rate - radius)), _quantize(min(Decimal("1"), rate + radius))


def _log_loss(probability: Decimal, observed: bool) -> Decimal:
    clipped = min(max(float(probability), 1e-6), 1 - 1e-6)
    loss = -math.log(clipped if observed else 1 - clipped)
    return Decimal(str(loss))


def _observed_decimal(example: CalibrationExample) -> Decimal:
    return Decimal("1") if example.observed else Decimal("0")


def _quantize(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.000001"))


def _utcnow() -> datetime:
    return datetime.now(UTC)
