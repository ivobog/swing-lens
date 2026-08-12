from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.background_performance_baseline import _distribution, _elapsed_ms


def test_baseline_distribution_reports_exact_queue_delay_bounds() -> None:
    assert _distribution([40.0, 10.0, 20.0, 30.0]) == {
        "count": 4,
        "min": 10.0,
        "median": 25.0,
        "max": 40.0,
    }


def test_baseline_elapsed_time_handles_aware_timestamps() -> None:
    started_at = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)

    assert _elapsed_ms(started_at, started_at + timedelta(seconds=1.25)) == 1_250.0
    assert _elapsed_ms(None, started_at) is None
