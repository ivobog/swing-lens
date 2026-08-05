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
        return duration_ms

    def add_fallback(self, component: str) -> None:
        if component not in self.fallbacks:
            self.fallbacks.append(component)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": PERFORMANCE_SCHEMA_VERSION,
            "phase": PERFORMANCE_PHASE,
            "pipeline_wall_ms": _round_ms((self.clock() - self.started_at) * 1000),
            "step_durations_ms": dict(sorted(self.step_durations_ms.items())),
            "setup_latest_bar_query_ms": None,
            "setup_context_build_ms": None,
            "setup_capture_ms": None,
            "setup_evaluation_ms": None,
            "technical_input_load_ms": None,
            "technical_worker_span_ms": None,
            "technical_cache_hits": 0,
            "technical_cache_misses": 0,
            "technical_tickers_completed_during_fetch": 0,
            "technical_finalize_ms": None,
            "ib_pacing_wait_ms": None,
            "ib_network_ms": None,
            "bar_cache_write_ms": None,
            "prewarm_age_seconds": None,
            "prewarm_covered_tickers": 0,
            "fallbacks": sorted(self.fallbacks),
        }


def _round_ms(value: float) -> float:
    return round(max(0.0, value), 3)
