from __future__ import annotations

import pytest

from app.services.background_job_service import JobStatus
from app.services.winner_probability.job_handlers import (
    _freeze_latest_rescore_targets,
    classify_maturation_status,
)


def test_completed_scan_with_retryable_pending_is_not_partial() -> None:
    status = classify_maturation_status(
        {
            "due_h5_next_open": 3439,
            "processed_h5": 3439,
            "matured_h5": 2328,
            "deferred_pending_h5": 1111,
            "failed_h5": 0,
            "unvisited_h5_after_cycle": 0,
        }
    )

    assert status == JobStatus.COMPLETED


def test_unvisited_or_failed_maturation_is_partial() -> None:
    assert (
        classify_maturation_status({"failed_h5": 1, "unvisited_h5_after_cycle": 0})
        == JobStatus.PARTIAL
    )


def test_latest_rescore_requires_a_bounded_explicit_scope() -> None:
    assert _freeze_latest_rescore_targets(
        object(),
        {"scope": {"type": "EXPLICIT_PREDICTIONS", "prediction_ids": [7, 2, 7]}},
    ) == [2, 7]
    with pytest.raises(ValueError, match="ALL_HISTORICAL_ELIGIBLE is not supported"):
        _freeze_latest_rescore_targets(object(), {"scope": {"type": "ALL_HISTORICAL_ELIGIBLE"}})
    assert (
        classify_maturation_status({"failed_h5": 0, "unvisited_h5_after_cycle": 1})
        == JobStatus.PARTIAL
    )
