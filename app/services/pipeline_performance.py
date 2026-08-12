from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

PERFORMANCE_SCHEMA_VERSION = "pipeline-performance-v1"
PERFORMANCE_PHASE = 1


@dataclass
class PipelinePerformanceTracker:
    """Collect low-overhead, monotonic pipeline timing diagnostics.

    Phase 1 deliberately measures the existing sequential execution path. The
    component-specific fields are kept in the result contract now so later
    phases can fill them without changing the diagnostics shape.
    """

    clock: Any = perf_counter
    started_at: float = field(init=False)
    step_started_at: dict[str, float] = field(default_factory=dict, init=False)
    step_durations_ms: dict[str, float] = field(default_factory=dict, init=False)
    component_metrics: dict[str, float | int | None] = field(default_factory=dict, init=False)
    fallbacks: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    def start_step(self, step_name: str) -> None:
        self.step_started_at[step_name] = self.clock()

    def finish_step(self, step_name: str, status: str) -> float | None:
        started_at = self.step_started_at.pop(step_name, None)
        if started_at is None:
            return None
        duration_ms = _round_ms((self.clock() - started_at) * 1000)
        self.step_durations_ms[step_name] = duration_ms
        component_name = {
            "CAPTURING_SETUP_SIGNALS": "setup_capture_ms",
            "EVALUATING_SETUP_LIFECYCLES": "setup_evaluation_ms",
        }.get(step_name)
        if component_name is not None:
            self.component_metrics[component_name] = duration_ms
        return duration_ms

    def set_metric(self, name: str, value: float | int | None) -> None:
        self.component_metrics[name] = value

    def add_fallback(self, component: str) -> None:
        if component not in self.fallbacks:
            self.fallbacks.append(component)

    def snapshot(self) -> dict[str, Any]:
        execution_ms = _round_ms((self.clock() - self.started_at) * 1000)
        queue_delay_ms = self.component_metrics.get("pipeline_queue_delay_ms")
        total_wall_ms = (
            _round_ms(float(queue_delay_ms) + execution_ms)
            if queue_delay_ms is not None
            else execution_ms
        )
        return {
            "schema_version": PERFORMANCE_SCHEMA_VERSION,
            "phase": PERFORMANCE_PHASE,
            "pipeline_queue_delay_ms": queue_delay_ms,
            "pipeline_execution_ms": execution_ms,
            "pipeline_total_wall_ms": total_wall_ms,
            # Compatibility for existing reports.
            "pipeline_wall_ms": execution_ms,
            "step_durations_ms": dict(sorted(self.step_durations_ms.items())),
            "setup_latest_bar_query_ms": self.component_metrics.get("setup_latest_bar_query_ms"),
            "setup_context_build_ms": self.component_metrics.get("setup_context_build_ms"),
            "setup_capture_ms": self.component_metrics.get("setup_capture_ms"),
            "setup_evaluation_ms": self.component_metrics.get("setup_evaluation_ms"),
            "technical_input_load_ms": self.component_metrics.get("technical_input_load_ms"),
            "technical_worker_span_ms": self.component_metrics.get("technical_worker_span_ms"),
            "technical_cache_hits": int(self.component_metrics.get("technical_cache_hits") or 0),
            "technical_cache_misses": int(
                self.component_metrics.get("technical_cache_misses") or 0
            ),
            "technical_cache_shadow_candidates": int(
                self.component_metrics.get("technical_cache_shadow_candidates") or 0
            ),
            "technical_cache_shadow_misses": int(
                self.component_metrics.get("technical_cache_shadow_misses") or 0
            ),
            "technical_cache_shadow_validations": int(
                self.component_metrics.get("technical_cache_shadow_validations") or 0
            ),
            "technical_cache_shadow_mismatches": int(
                self.component_metrics.get("technical_cache_shadow_mismatches") or 0
            ),
            "technical_worker_processes": self.component_metrics.get("technical_worker_processes"),
            "technical_max_in_flight": self.component_metrics.get("technical_max_in_flight"),
            "technical_tickers_completed_during_fetch": int(
                self.component_metrics.get("technical_tickers_completed_during_fetch") or 0
            ),
            "technical_finalize_ms": self.component_metrics.get("technical_finalize_ms"),
            "ib_pacing_wait_ms": self.component_metrics.get("ib_pacing_wait_ms"),
            "ib_network_ms": self.component_metrics.get("ib_network_ms"),
            "bar_cache_write_ms": self.component_metrics.get("bar_cache_write_ms"),
            "prewarm_age_seconds": self.component_metrics.get("prewarm_age_seconds"),
            "prewarm_covered_tickers": int(
                self.component_metrics.get("prewarm_covered_tickers") or 0
            ),
            "prewarm_reused_tickers": int(
                self.component_metrics.get("prewarm_reused_tickers") or 0
            ),
            "prewarm_job_id": self.component_metrics.get("prewarm_job_id"),
            "fallbacks": sorted(self.fallbacks),
        }


def _round_ms(value: float) -> float:
    return round(max(0.0, value), 3)
