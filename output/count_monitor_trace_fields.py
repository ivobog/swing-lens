import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

counts = {"lines": 0, "slow_query": 0, "full_stack": 0, "sql_failure": 0}
health = defaultdict(lambda: {
    "first_positive_drop": None,
    "last_positive_drop": None,
    "max_records_dropped": 0,
    "max_writer_errors": 0,
    "max_queue_depth": 0,
    "max_oldest_queue_age_ms": 0.0,
    "max_writer_latency_ms": 0.0,
})
health_times = []
for role in ("worker", "web", "supervisor", "cli", "diagnostic"):
    for path in (Path("logs/db-monitor") / role).glob("*.jsonl"):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                counts["lines"] += 1
                counts["slow_query"] += '"slow_query":true' in line
                counts["full_stack"] += '"application_stack":[' in line
                counts["sql_failure"] += '"failed":true' in line
                if '"record_type":"monitor_health"' in line:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    item = health[role]
                    if row.get("timestamp"):
                        health_times.append((datetime.fromisoformat(row["timestamp"]), role))
                    dropped = int(row.get("records_dropped") or 0)
                    if dropped:
                        item["first_positive_drop"] = item["first_positive_drop"] or row.get("timestamp")
                        item["last_positive_drop"] = row.get("timestamp")
                    item["max_records_dropped"] = max(item["max_records_dropped"], dropped)
                    item["max_writer_errors"] = max(item["max_writer_errors"], int(row.get("writer_errors") or 0))
                    item["max_queue_depth"] = max(item["max_queue_depth"], int(row.get("telemetry_queue_depth") or 0))
                    item["max_oldest_queue_age_ms"] = max(item["max_oldest_queue_age_ms"], float(row.get("oldest_queue_age_ms") or 0))
                    item["max_writer_latency_ms"] = max(item["max_writer_latency_ms"], float(row.get("writer_latency_max_ms") or 0))
all_times = sorted({stamp for stamp, _role in health_times})
overall_gaps = []
for previous, current in zip(all_times, all_times[1:]):
    gap = (current - previous).total_seconds()
    if gap > 180:
        overall_gaps.append({"start": previous.isoformat(), "end": current.isoformat(), "seconds": gap})
print(json.dumps({"counts": counts, "health": health, "overall_health_gaps_over_180s": overall_gaps}, indent=2, default=dict))
