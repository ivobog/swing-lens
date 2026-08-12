from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class MetricSample:
    name: str
    value: float
    labels: dict[str, str]


class OperationalMetricRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)

    def increment(self, name: str, value: float = 1.0, **labels: Any) -> None:
        key = (name, _label_tuple(labels))
        with self._lock:
            self._counters[key] += float(value)

    def samples(self) -> list[MetricSample]:
        with self._lock:
            rows = [
                MetricSample(name=name, value=value, labels=dict(labels))
                for (name, labels), value in self._counters.items()
            ]
        return sorted(rows, key=lambda sample: (sample.name, sorted(sample.labels.items())))

    def total(self, name: str, **labels: Any) -> float:
        """Return a counter total, optionally matching a subset of labels."""
        expected = {str(key): str(value) for key, value in labels.items()}
        with self._lock:
            return sum(
                value
                for (metric_name, metric_labels), value in self._counters.items()
                if metric_name == name
                and all(dict(metric_labels).get(key) == value for key, value in expected.items())
            )

    def as_prometheus(self) -> str:
        lines: list[str] = []
        for sample in self.samples():
            labels = ",".join(
                f'{key}="{value}"' for key, value in sorted(sample.labels.items())
            )
            suffix = f"{{{labels}}}" if labels else ""
            lines.append(f"{sample.name}{suffix} {sample.value:g}")
        return "\n".join(lines) + ("\n" if lines else "")

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()


operational_metrics = OperationalMetricRegistry()


def _label_tuple(labels: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))
