from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.tables import WinnerDriftMetric
from app.services.winner_probability.calibration_service import CalibrationExample
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.drift_service import DriftService


def test_drift_metrics_detect_synthetic_performance_and_distribution_change() -> None:
    config = load_winner_probability_config()
    baseline = tuple(
        CalibrationExample(Decimal("0.70"), index < 28)
        for index in range(40)
    )
    recent = tuple(CalibrationExample(Decimal("0.90"), index < 8) for index in range(40))

    results = DriftService().calculate(
        baseline=baseline,
        recent=recent,
        comparison_window="short",
        config=config,
    )
    by_name = {result.metric_name: result for result in results}

    assert by_name["win_rate_delta"].metric_value == Decimal("-0.500000")
    assert by_name["win_rate_delta"].breached
    assert by_name["brier_score_delta"].breached
    assert by_name["psi"].breached
    assert all(result.sufficient_sample for result in results)


def test_drift_metrics_remain_insufficient_when_sample_is_too_low() -> None:
    config = load_winner_probability_config()
    baseline = tuple(CalibrationExample(Decimal("0.60"), True) for _ in range(5))
    recent = tuple(CalibrationExample(Decimal("0.10"), False) for _ in range(5))

    results = DriftService().calculate(
        baseline=baseline,
        recent=recent,
        comparison_window="short",
        config=config,
    )

    assert all(not result.sufficient_sample for result in results)
    assert all(not result.breached for result in results)


def test_drift_service_persists_metrics_with_thresholds_and_segments() -> None:
    db = DriftFakeDb()
    results = DriftService().calculate(
        baseline=tuple(CalibrationExample(Decimal("0.60"), True) for _ in range(40)),
        recent=tuple(CalibrationExample(Decimal("0.60"), True) for _ in range(40)),
        comparison_window="medium",
        segment={"market_risk_state": "Green"},
    )

    rows = DriftService().persist_metrics(
        db,
        results=results,
        outcome_definition_id=4,
        model_version_id=9,
        as_of_date=date(2026, 7, 31),
    )

    assert len(rows) == 4
    assert isinstance(db.rows[0], WinnerDriftMetric)
    assert db.rows[0].outcome_definition_id == 4
    assert db.rows[0].model_version_id == 9
    assert db.rows[0].comparison_window == "medium"
    assert db.rows[0].segment_json == {"market_risk_state": "Green"}


class DriftFakeDb:
    def __init__(self) -> None:
        self.rows = []
        self.flushes = 0
        self._next_id = 1

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        self.rows.append(row)

    def flush(self) -> None:
        self.flushes += 1
