from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.tables import (
    WinnerForwardOutcome,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.cohort_statistics import CohortStatisticsService
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.evidence_service import EvidenceOutcome


def test_small_sample_raw_rate_shrinks_toward_prior_and_interval_widens() -> None:
    config = load_winner_probability_config()
    evidence = tuple(_evidence(index, won=True) for index in range(2))

    result = CohortStatisticsService().calculate(evidence, config)

    assert result.sample_n == 2
    assert result.wins == Decimal("2.000000")
    assert result.raw_rate == Decimal("1.000000")
    assert result.posterior_probability == Decimal("0.545455")
    assert result.interval_width > Decimal("0.350000")
    assert result.evidence_grade == "Insufficient"


def test_evidence_grade_is_reproducible_from_config_thresholds() -> None:
    config = load_winner_probability_config()
    evidence = tuple(_evidence(index, won=index % 2 == 0) for index in range(120))

    first = CohortStatisticsService().calculate(evidence, config)
    second = CohortStatisticsService().calculate(evidence, config)

    assert first.evidence_grade == second.evidence_grade == "High"
    assert first.posterior_probability == second.posterior_probability


def _evidence(index: int, *, won: bool) -> EvidenceOutcome:
    prediction = WinnerPredictionSnapshot(
        id=index + 1,
        run_id=1,
        ticker=f"T{index}",
        prediction_as_of_date=date(2026, 1, 1),
        source_data_cutoff_at=datetime(2026, 1, 1, tzinfo=UTC),
        entry_schedule_status="RESOLVED",
        entry_data_status="AVAILABLE",
        eligibility_status="ELIGIBLE",
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash=f"hash-{index}",
        config_hash="config",
        calculation_version="calc",
        feature_json={},
    )
    forward = WinnerForwardOutcome(
        id=index + 100,
        prediction_id=prediction.id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        close_return_pct=Decimal("1.0") if won else Decimal("-1.0"),
        mfe_pct=Decimal("2.0"),
        mae_pct=Decimal("-1.0"),
    )
    target = WinnerTargetStopOutcome(
        id=index + 200,
        prediction_id=prediction.id,
        outcome_definition_id=1,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
        primary_winner=won,
        first_event="TARGET_FIRST" if won else "STOP_FIRST",
    )
    return EvidenceOutcome(
        prediction=prediction,
        forward_outcome=forward,
        target_stop_outcome=target,
    )
