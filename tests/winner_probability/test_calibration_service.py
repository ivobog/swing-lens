from __future__ import annotations

from decimal import Decimal

from app.models.tables import WinnerCalibrationBin
from app.services.winner_probability.calibration_service import (
    CalibrationExample,
    CalibrationService,
)


def test_calibration_bins_compare_prediction_with_observed_rate() -> None:
    examples = (
        CalibrationExample(Decimal("0.62"), True),
        CalibrationExample(Decimal("0.68"), True),
        CalibrationExample(Decimal("0.65"), False),
        CalibrationExample(Decimal("0.15"), False),
    )

    report = CalibrationService().calculate(examples, bin_count=2)

    low, high = report.bins
    assert low.sample_n == 1
    assert low.mean_prediction == Decimal("0.150000")
    assert low.observed_rate == Decimal("0.000000")
    assert high.sample_n == 3
    assert high.mean_prediction == Decimal("0.650000")
    assert high.observed_rate == Decimal("0.666667")
    assert high.error == Decimal("0.016667")
    assert report.metrics["sample_n"] == 4
    assert report.metrics["brier_score"] == Decimal("0.172950")
    assert report.metrics["ece"] == Decimal("0.050000")


def test_calibration_persists_reliability_bins() -> None:
    db = CalibrationFakeDb()
    report = CalibrationService().calculate(
        (
            CalibrationExample(Decimal("0.61"), True),
            CalibrationExample(Decimal("0.69"), False),
        ),
        bin_count=2,
    )

    rows = CalibrationService().persist_bins(
        db,
        report=report,
        outcome_definition_id=3,
        estimate_kind="DECISION_TIME",
        model_version_id=8,
        segment={"setup_family": "Breakout"},
    )

    assert len(rows) == 2
    assert db.flushes == 1
    assert isinstance(db.rows[0], WinnerCalibrationBin)
    assert db.rows[1].model_version_id == 8
    assert db.rows[1].segment_json == {"setup_family": "Breakout"}


class CalibrationFakeDb:
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
