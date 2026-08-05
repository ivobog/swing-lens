from __future__ import annotations

import json
import math
import os
import platform
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

BASELINE_SCHEMA_VERSION = "pipeline-baseline-v1"
PARITY_SCHEMA_VERSION = "pipeline-sequential-parity-v1"


def run_baseline_benchmark(
    operation: Callable[[], Any],
    *,
    warmup_iterations: int = 1,
    measured_iterations: int = 5,
    metadata: Mapping[str, Any] | None = None,
    result_serializer: Callable[[Any], Any] | None = None,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Run a repeatable sequential benchmark and return a JSON-safe report."""

    if warmup_iterations < 0:
        raise ValueError("warmup_iterations must be non-negative")
    if measured_iterations < 1:
        raise ValueError("measured_iterations must be positive")

    for _ in range(warmup_iterations):
        operation()

    samples: list[dict[str, Any]] = []
    serialized_results: list[Any] = []
    for iteration in range(1, measured_iterations + 1):
        started_at = clock()
        result = operation()
        elapsed_ms = round(max(0.0, (clock() - started_at) * 1000), 3)
        samples.append({"iteration": iteration, "duration_ms": elapsed_ms})
        if result_serializer is not None:
            serialized_results.append(_json_safe(result_serializer(result)))

    durations = [sample["duration_ms"] for sample in samples]
    report: dict[str, Any] = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "phase": 1,
        "warmup_iterations": warmup_iterations,
        "measured_iterations": measured_iterations,
        "samples": samples,
        "summary": {
            "min_ms": min(durations),
            "max_ms": max(durations),
            "p50_ms": _percentile(durations, 0.50),
            "p95_ms": _percentile(durations, 0.95),
        },
        "metadata": {
            **_runtime_metadata(),
            **dict(metadata or {}),
        },
    }
    if serialized_results:
        report["sequential_results"] = serialized_results
    return report


def write_baseline_report(path: str | Path, report: Mapping[str, Any]) -> None:
    _write_json(path, report)


def normalize_parity_payload(
    value: Any,
    *,
    ignored_keys: Sequence[str] = (
        "id",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    ),
) -> Any:
    """Normalize JSON-like output while retaining meaningful lineage fields."""

    ignored = set(ignored_keys)
    if isinstance(value, Mapping):
        return {
            str(key): normalize_parity_payload(nested, ignored_keys=ignored_keys)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) not in ignored
        }
    if isinstance(value, (list, tuple)):
        return [normalize_parity_payload(item, ignored_keys=ignored_keys) for item in value]
    return value


def write_sequential_parity_fixture(
    path: str | Path,
    outputs: Sequence[Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    fixture = {
        "schema_version": PARITY_SCHEMA_VERSION,
        "execution_mode": "sequential",
        "metadata": dict(metadata or {}),
        "cases": [normalize_parity_payload(_json_safe(output)) for output in outputs],
    }
    _write_json(path, fixture)


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)


def _runtime_metadata() -> dict[str, Any]:
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "logical_cpu_count": os.cpu_count() or 1,
    }


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
