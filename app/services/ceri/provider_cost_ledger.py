from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any


class ProviderCostLedger:
    def summarize(self, rows: Iterable[Any]) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = defaultdict(
            lambda: {
                "request_rows": 0,
                "call_cost_units": 0,
                "runtime_ms": 0,
                "response_bytes": 0,
                "stored_bytes": 0,
            }
        )
        for row in rows:
            values = result[str(row.provider)]
            values["request_rows"] += 1
            values["call_cost_units"] += int(row.call_cost or 0)
            values["runtime_ms"] += int(row.latency_ms or 0)
            values["response_bytes"] += int(row.response_bytes or 0)
            values["stored_bytes"] += int(row.stored_bytes or 0)
        return dict(sorted(result.items()))
