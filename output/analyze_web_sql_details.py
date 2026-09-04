from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


START = datetime.fromisoformat("2026-08-26T11:25:41.966762+00:00")
END = datetime.fromisoformat("2026-09-04T09:14:30+00:00")
LOG_DIR = Path("logs/db-monitor/web")
OUT = Path("output/swinglens_web_sql_details.json")


def add(bucket: dict, row: dict) -> None:
    bucket["calls"] += 1
    bucket["sql_ms"] += float(row.get("duration_ms") or 0)
    if row.get("rowcount") is not None:
        bucket["rows"] += max(0, int(row["rowcount"]))


def main() -> None:
    route_fp = defaultdict(lambda: defaultdict(lambda: {"calls": 0, "sql_ms": 0.0, "rows": 0, "sql": "", "callers": Counter()}))
    score_run = {"unfiltered": {"calls": 0, "sql_ms": 0.0, "rows": 0}, "scoped": {"calls": 0, "sql_ms": 0.0, "rows": 0}}
    changes_tables = defaultdict(lambda: {"calls": 0, "sql_ms": 0.0, "rows": 0})
    coverage = defaultdict(lambda: {"calls": 0, "sql_ms": 0.0, "rows": 0})
    slow = 0
    full_stack = 0
    sql_records = 0

    for path in LOG_DIR.glob("*.jsonl"):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"record_type":"sql"' not in line:
                    continue
                try:
                    row = json.loads(line)
                    timestamp = datetime.fromisoformat(row["timestamp"])
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
                if not (START <= timestamp <= END):
                    continue
                sql_records += 1
                slow += bool(row.get("slow_query"))
                full_stack += bool(row.get("application_stack"))
                route = f"{row.get('http_method')} {row.get('route_path')}" if row.get("route_path") else "UNKNOWN"
                fp = row.get("query_fingerprint") or "UNKNOWN"
                bucket = route_fp[route][fp]
                add(bucket, row)
                bucket["sql"] = row.get("normalized_sql") or ""
                caller = row.get("python_caller") or {}
                caller_name = f"{caller.get('source_file')}:{caller.get('line_number')} {caller.get('function')}"
                bucket["callers"][caller_name] += 1

                sql_lower = (row.get("normalized_sql") or "").lower()
                if route == "GET /runs/{run_id}/ceri" and row.get("operation") == "SELECT" and "from ceri_score_snapshots" in sql_lower:
                    add(score_run["scoped" if " where " in sql_lower else "unfiltered"], row)

                if route == "GET /ceri/changes":
                    for table in (
                        "ceri_change_events", "ceri_score_snapshots", "ceri_guidance_events",
                        "ceri_revision_features", "ceri_catalyst_events", "ceri_catalyst_event_revisions",
                    ):
                        if f"from {table}" in sql_lower:
                            add(changes_tables[table], row)

                if caller.get("function") == "_bar_stats" and "price_bars" in sql_lower:
                    add(coverage[route], row)

    important_routes = [
        "GET /runs/{run_id}/ceri", "GET /ceri", "GET /ceri/changes", "GET /ceri/operations",
        "GET /", "GET /runs/{run_id}", "GET /runs/{run_id}/winner-probability",
    ]
    route_fingerprints = {}
    for route in important_routes:
        rows = []
        for fingerprint, item in route_fp.get(route, {}).items():
            rows.append({
                "fingerprint": fingerprint,
                "calls": item["calls"],
                "sql_ms": round(item["sql_ms"], 3),
                "rows": item["rows"],
                "sql": item["sql"],
                "callers": item["callers"].most_common(5),
            })
        route_fingerprints[route] = sorted(rows, key=lambda item: item["sql_ms"], reverse=True)[:40]

    result = {
        "sql_records": sql_records,
        "slow_query_records": slow,
        "full_stack_records": full_stack,
        "run_ceri_score_reads": score_run,
        "ceri_changes_table_reads": changes_tables,
        "price_coverage_by_route": coverage,
        "route_fingerprints": route_fingerprints,
    }
    OUT.write_text(json.dumps(result, indent=2, default=dict), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
