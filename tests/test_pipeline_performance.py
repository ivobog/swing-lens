from __future__ import annotations

import json

from app.services.pipeline_baseline import (
    run_baseline_benchmark,
    write_sequential_parity_fixture,
)
from app.services.pipeline_performance import PipelinePerformanceTracker


def test_pipeline_performance_tracker_records_step_and_contract_metrics() -> None:
    ticks = iter([10.0, 10.25, 10.5, 10.6])
    tracker = PipelinePerformanceTracker(clock=lambda: next(ticks))

    tracker.start_step("SCORING_TECHNICALS")
    duration = tracker.finish_step("SCORING_TECHNICALS", "COMPLETED")
    tracker.set_metric("pipeline_queue_delay_ms", 1_000.0)
    tracker.set_metric("technical_cache_hits", 7)
    tracker.set_metric("technical_cache_misses", 2)
    tracker.set_metric("technical_cache_shadow_candidates", 6)
    tracker.set_metric("technical_cache_shadow_validations", 6)
    tracker.set_metric("technical_cache_shadow_mismatches", 0)
    tracker.set_metric("ib_pacing_wait_ms", 125.0)
    tracker.add_fallback("sequential_technicals")

    snapshot = tracker.snapshot()

    assert duration == 250.0
    assert snapshot["phase"] == 1
    assert snapshot["step_durations_ms"] == {"SCORING_TECHNICALS": 250.0}
    assert snapshot["pipeline_queue_delay_ms"] == 1_000.0
    assert snapshot["pipeline_execution_ms"] == 600.0
    assert snapshot["pipeline_total_wall_ms"] == 1_600.0
    assert snapshot["pipeline_wall_ms"] == snapshot["pipeline_execution_ms"]
    assert snapshot["technical_cache_hits"] == 7
    assert snapshot["technical_cache_misses"] == 2
    assert snapshot["technical_cache_shadow_candidates"] == 6
    assert snapshot["technical_cache_shadow_validations"] == 6
    assert snapshot["technical_cache_shadow_mismatches"] == 0
    assert snapshot["ib_pacing_wait_ms"] == 125.0
    assert snapshot["prewarm_age_seconds"] is None
    assert snapshot["fallbacks"] == ["sequential_technicals"]


def test_run_baseline_benchmark_reports_p50_and_p95() -> None:
    ticks = iter([0.0, 0.1, 0.1, 0.3])
    calls = []

    def operation() -> dict[str, str]:
        calls.append("run")
        return {"status": "COMPLETED"}

    report = run_baseline_benchmark(
        operation,
        warmup_iterations=1,
        measured_iterations=2,
        metadata={"fixture": "run-78"},
        result_serializer=lambda result: result,
        clock=lambda: next(ticks),
    )

    assert calls == ["run", "run", "run"]
    assert report["summary"] == {
        "min_ms": 100.0,
        "max_ms": 200.0,
        "p50_ms": 150.0,
        "p95_ms": 195.0,
    }
    assert report["metadata"]["fixture"] == "run-78"
    assert report["sequential_results"] == [
        {"status": "COMPLETED"},
        {"status": "COMPLETED"},
    ]


def test_write_sequential_parity_fixture_normalizes_only_surrogate_fields(tmp_path) -> None:
    path = tmp_path / "parity.json"

    write_sequential_parity_fixture(
        path,
        [{
            "id": 10,
            "ticker": "MSFT",
            "price_bar_id": 42,
            "created_at": "now",
        }],
        metadata={"run": 78},
    )

    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["cases"] == [{"price_bar_id": 42, "ticker": "MSFT"}]
    assert fixture["execution_mode"] == "sequential"
