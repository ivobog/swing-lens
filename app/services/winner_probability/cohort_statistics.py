from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import mean, median

from app.models.tables import EvidenceGrade
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.services.winner_probability.evidence_service import GenerationEvidenceMember

SIX_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class CohortStatisticsResult:
    sample_n: int
    effective_n: Decimal
    wins: Decimal
    raw_rate: Decimal | None
    posterior_probability: Decimal
    lower_bound: Decimal
    upper_bound: Decimal
    interval_width: Decimal
    mean_return_pct: Decimal | None
    median_return_pct: Decimal | None
    median_mfe_pct: Decimal | None
    median_mae_pct: Decimal | None
    target_first_rate: Decimal | None
    evidence_grade: str
    insufficient_reasons: tuple[str, ...]


class CohortStatisticsService:
    def calculate(
        self,
        evidence: tuple[GenerationEvidenceMember, ...],
        config: WinnerProbabilityConfig,
    ) -> CohortStatisticsResult:
        sample_n = len(evidence)
        effective_n = _decimal(sum(row.inclusion_weight for row in evidence))
        wins = _decimal(sum(row.inclusion_weight for row in evidence if row.won))
        raw_rate = _ratio(wins, effective_n) if effective_n > 0 else None
        prior_strength = Decimal(str(config.cohort.prior_strength))
        prior_probability = Decimal(str(config.cohort.prior_probability))
        posterior = _ratio(wins + prior_strength * prior_probability, effective_n + prior_strength)
        lower, upper = _credible_interval(posterior, effective_n + prior_strength)
        interval_width = (upper - lower).quantize(SIX_PLACES)
        grade = evidence_grade(effective_n, interval_width, config)
        reasons: list[str] = []
        if grade == EvidenceGrade.INSUFFICIENT:
            reasons.append("cohort_evidence_below_threshold")
        return CohortStatisticsResult(
            sample_n=sample_n,
            effective_n=effective_n,
            wins=wins,
            raw_rate=raw_rate,
            posterior_probability=posterior,
            lower_bound=lower,
            upper_bound=upper,
            interval_width=interval_width,
            mean_return_pct=_aggregate(evidence, "close_return_pct", mean),
            median_return_pct=_aggregate(evidence, "close_return_pct", median),
            median_mfe_pct=_aggregate(evidence, "mfe_pct", median),
            median_mae_pct=_aggregate(evidence, "mae_pct", median),
            target_first_rate=_target_first_rate(evidence),
            evidence_grade=grade,
            insufficient_reasons=tuple(reasons),
        )


def evidence_grade(
    effective_n: Decimal,
    interval_width: Decimal,
    config: WinnerProbabilityConfig,
) -> str:
    for key, label in (
        ("high", EvidenceGrade.HIGH),
        ("medium", EvidenceGrade.MEDIUM),
        ("low", EvidenceGrade.LOW),
    ):
        rule = config.evidence_grades[key]
        if effective_n >= Decimal(rule.min_effective_n) and interval_width <= Decimal(
            str(rule.max_interval_width)
        ):
            return label
    return EvidenceGrade.INSUFFICIENT


def _credible_interval(probability: Decimal, denominator: Decimal) -> tuple[Decimal, Decimal]:
    if denominator <= 0:
        return Decimal("0.000000"), Decimal("1.000000")
    p = float(probability)
    n = float(denominator)
    margin = Decimal(str(1.96 * ((p * (1 - p) / n) ** 0.5)))
    return (
        max(Decimal("0"), probability - margin).quantize(SIX_PLACES),
        min(Decimal("1"), probability + margin).quantize(SIX_PLACES),
    )


def _aggregate(
    evidence: tuple[GenerationEvidenceMember, ...], field_name: str, function
) -> Decimal | None:
    values = [
        Decimal(str(getattr(row.forward_outcome, field_name)))
        for row in evidence
        if getattr(row.forward_outcome, field_name) is not None
    ]
    if not values:
        return None
    return Decimal(str(function(values))).quantize(SIX_PLACES)


def _target_first_rate(evidence: tuple[GenerationEvidenceMember, ...]) -> Decimal | None:
    if not evidence:
        return None
    count = sum(1 for row in evidence if row.target_stop_outcome.first_event == "TARGET_FIRST")
    return (Decimal(count) / Decimal(len(evidence))).quantize(SIX_PLACES)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    return (numerator / denominator).quantize(SIX_PLACES)


def _decimal(value) -> Decimal:
    return Decimal(str(value)).quantize(SIX_PLACES)
