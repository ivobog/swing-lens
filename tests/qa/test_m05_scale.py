from argparse import Namespace

import pytest

from scripts.qa.run_m05_scale import (
    DATABASE_PREFIX,
    _current_rss_bytes,
    _database_url,
    _evaluate_thresholds,
    _percentiles,
    _tickers,
    _validate_args,
)


def test_m05_scale_helpers_are_deterministic_and_disposable() -> None:
    assert _tickers(3) == ["QA0001", "QA0002", "QA0003"]
    assert _database_url(
        "postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        f"{DATABASE_PREFIX}abc",
    ).endswith(f"/{DATABASE_PREFIX}abc")
    assert _percentiles([100.0, 200.0, 300.0]) == {
        "min_ms": 100.0,
        "p50_ms": 200.0,
        "p95_ms": 290.0,
        "max_ms": 300.0,
    }


def test_m05_scale_rejects_remote_or_unbounded_targets() -> None:
    base = {
        "sizes": [50, 250, 1000],
        "bars": 756,
        "http_iterations": 3,
        "admin_url": "postgresql://postgres:postgres@db.example.com:5432/postgres",
    }
    with pytest.raises(ValueError, match="localhost"):
        _validate_args(Namespace(**base))

    with pytest.raises(ValueError, match="1 through 5000"):
        _validate_args(
            Namespace(
                **{
                    **base,
                    "sizes": [50, 6000],
                    "admin_url": "postgresql://postgres:postgres@127.0.0.1:5432/postgres",
                }
            )
        )


def test_m05_scale_evaluates_documented_thresholds_and_rss() -> None:
    profile = {
        "ticker_count": 250,
        "pipeline_step_durations_ms": {"SCORING_TECHNICALS": 60_001.0},
        "http": {
            "history": {"timing": {"p95_ms": 10.0}},
            "run_detail": {"timing": {"p95_ms": 1400.0}},
            "combined_export": {"timing": {"p95_ms": 2100.0}},
        },
    }

    evaluation = _evaluate_thresholds([profile])

    assert evaluation["status"] == "FAIL"
    assert evaluation["passed"] == 2
    assert evaluation["failed"] == 2
    assert _current_rss_bytes() > 0
