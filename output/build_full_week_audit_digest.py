from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "swinglens_full_week_audit_raw.json"
OUT = ROOT / "swinglens_full_week_audit_digest.json"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return round(ordered[index], 3)


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "total": round(sum(values), 3),
        "mean": round(sum(values) / len(values), 3),
        "median": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": round(max(values), 3),
    }


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def interval_overlap(start: datetime, end: datetime, intervals: list[tuple[datetime, datetime]]) -> bool:
    return any(job_start <= end and job_end >= start for job_start, job_end in intervals)


def main() -> None:
    data = json.loads(RAW.read_text(encoding="utf-8"))
    digest: dict[str, object] = {}

    # Exact route records for the principal screens.
    route_records = data["route_records"]
    focus_routes = [
        "GET /ceri/changes",
        "GET /runs/{run_id}/ceri",
        "GET /ceri",
        "GET /ceri/operations",
        "GET /",
        "GET /runs/{run_id}",
        "GET /runs",
        "GET /runs/{run_id}/winner-probability",
        "GET /runs/{run_id}/winner-probability/{ticker}",
        "GET /runs/{run_id}/setup-lifecycle",
        "GET /setup-lifecycle/alerts",
        "GET /api/ib-gateway/status",
    ]
    digest["focus_route_records"] = {
        route: [record for record in route_records if record["route"] == route]
        for route in focus_routes
    }

    # Slowest GUI hours by mean and p95 wall time, excluding health/static endpoints.
    hour_routes: dict[str, list[float]] = defaultdict(list)
    for record in route_records:
        if record["route"] in {"GET /health", "GET /ready"} or record["route"].startswith("GET /static/"):
            continue
        hour = parse_time(record["timestamp"]).strftime("%Y-%m-%dT%H:00Z")
        hour_routes[hour].append(float(record["wall_ms"]))
    digest["slowest_gui_hours"] = sorted(
        (
            {
                "hour": hour,
                **summarize(values),
            }
            for hour, values in hour_routes.items()
        ),
        key=lambda item: (item.get("p95") or 0, item.get("total") or 0),
        reverse=True,
    )[:20]

    # Correlate GUI requests with actual overlapping heavy background jobs.
    heavy_types = {
        "FULL_PIPELINE",
        "CERI_PROVIDER_INGEST_BATCH",
        "CERI_NORMALIZE_BATCH",
        "CERI_FEATURE_BATCH",
        "CERI_CAPTURE_RUN",
        "CERI_CHANGE_DETECTION",
        "WINNER_OUTCOME_MATURATION",
        "WINNER_COHORT_REFRESH",
    }
    intervals_by_type: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    all_heavy: list[tuple[datetime, datetime]] = []
    for job in data["job_records"]:
        if job["job_type"] not in heavy_types:
            continue
        interval = (parse_time(job["start"]), parse_time(job["end"]))
        intervals_by_type[job["job_type"]].append(interval)
        all_heavy.append(interval)
    contention: dict[str, object] = {}
    for route in focus_routes:
        records = [record for record in route_records if record["route"] == route]
        buckets: dict[str, list[float]] = {"heavy": [], "idle": []}
        by_type: dict[str, list[float]] = defaultdict(list)
        for record in records:
            start, end = parse_time(record["start"]), parse_time(record["end"])
            wall = float(record["wall_ms"])
            buckets["heavy" if interval_overlap(start, end, all_heavy) else "idle"].append(wall)
            for job_type, intervals in intervals_by_type.items():
                if interval_overlap(start, end, intervals):
                    by_type[job_type].append(wall)
        contention[route] = {
            "heavy": summarize(buckets["heavy"]),
            "idle": summarize(buckets["idle"]),
            "by_job_type": {name: summarize(values) for name, values in sorted(by_type.items())},
        }
    digest["background_gui_contention"] = contention

    # Control-plane fingerprints are stable and identified by caller/function.
    control_terms = {
        "worker_registration": ("background_workers", "register_worker"),
        "worker_heartbeat": ("background_workers", "heartbeat_worker"),
        "job_claiming": ("background_jobs", "claim"),
        "recovery_checks": ("background_jobs", "recover"),
        "winner_scheduling": ("winner", "schedule"),
        "sec_release_checks": ("ceri_sec_processor_releases", "release"),
        "supervisor_registration": ("background_supervisors", "register"),
        "supervisor_heartbeat": ("background_supervisors", "heartbeat"),
        "supervisor_fencing": ("background_jobs", "fence"),
    }
    all_fingerprints: dict[str, dict] = {}
    for ranking in data["fingerprints"].values():
        if isinstance(ranking, list):
            for row in ranking:
                if isinstance(row, dict) and row.get("fingerprint"):
                    all_fingerprints[row["fingerprint"]] = row
    control: dict[str, list[dict]] = defaultdict(list)
    for row in all_fingerprints.values():
        haystack = (row.get("sql", "") + " " + json.dumps(row.get("callers", {}))).lower()
        for label, terms in control_terms.items():
            if all(term in haystack for term in terms):
                control[label].append(row)
    digest["control_plane_candidates"] = control

    # Price coverage candidates and CERI table-load candidates.
    digest["price_coverage_candidates"] = [
        row for row in all_fingerprints.values()
        if "price_bars" in row.get("sql", "").lower()
        and any("_bar_stats" in caller for caller in row.get("callers", {}))
    ]
    digest["ceri_snapshot_candidates"] = [
        row for row in all_fingerprints.values()
        if "ceri_score_snapshots" in row.get("sql", "").lower()
    ]

    # N+1 and exact-duplicate leaders by route.
    digest["n_plus_one_by_route"] = {
        route: [row for row in data["n_plus_one"] if row["route"] == route][:30]
        for route in focus_routes
    }
    digest["exact_duplicates_by_route"] = {
        route: [row for row in data["exact_duplicates"] if row["route"] == route][:30]
        for route in focus_routes
    }

    # Long transaction summaries by route and job, plus idle-in-transaction sampler summary.
    tx_by_origin: dict[str, list[float]] = defaultdict(list)
    for row in data["long_transactions"]["top"]:
        origin = row.get("route") or row.get("job_type") or row.get("role") or "UNKNOWN"
        if origin == "UNKNOWN" and row.get("job_type"):
            origin = row["job_type"]
        tx_by_origin[origin].append(float(row["duration_ms"]))
    digest["top_long_transaction_origins"] = {
        origin: summarize(values) for origin, values in sorted(tx_by_origin.items())
    }
    health_top = data["database_health"]["top"]
    idle = [row for row in health_top if row.get("state") == "idle in transaction"]
    digest["idle_in_transaction_top_summary"] = summarize([float(row["duration_ms"]) for row in idle])
    digest["idle_in_transaction_examples"] = idle[:10]

    # Pool waits by route/job/role.
    pool_by_origin: dict[str, list[float]] = defaultdict(list)
    pool_by_hour: dict[str, list[float]] = defaultdict(list)
    for row in data["pool_events"]["top"]:
        origin = row.get("route") if row.get("route") not in (None, "UNKNOWN") else row.get("job_type") or row.get("role") or "UNKNOWN"
        pool_by_origin[origin].append(float(row["wait_ms"]))
        pool_by_hour[parse_time(row["timestamp"]).strftime("%Y-%m-%dT%H:00Z")].append(float(row["wait_ms"]))
    digest["pool_by_origin_top_events"] = {origin: summarize(values) for origin, values in sorted(pool_by_origin.items())}
    digest["pool_by_hour_top_events"] = sorted(
        ({"hour": hour, **summarize(values)} for hour, values in pool_by_hour.items()),
        key=lambda item: item.get("total") or 0,
        reverse=True,
    )

    # Job outliers and phase summaries.
    digest["job_outliers"] = {
        job_type: sorted(
            [row for row in data["job_records"] if row["job_type"] == job_type],
            key=lambda row: row["wall_ms"],
            reverse=True,
        )[:10]
        for job_type in data["jobs"]
    }
    digest["job_phases"] = data["job_phases"]

    # Monitor health maxima across latest counters for every process incarnation.
    latest = list(data["monitor_health"]["latest_by_process"].values())
    numeric_fields = [
        "records_written",
        "records_dropped",
        "writer_errors",
        "telemetry_queue_depth",
        "writer_latency_mean_ms",
        "writer_latency_max_ms",
        "current_files",
        "current_bytes",
    ]
    digest["monitor_health_maxima"] = {
        field: max((float(row.get(field) or 0) for row in latest), default=0)
        for field in numeric_fields
    }
    digest["monitor_health_sums"] = {
        "records_dropped": sum(int(row.get("records_dropped") or 0) for row in latest),
        "writer_errors": sum(int(row.get("writer_errors") or 0) for row in latest),
    }

    # Most expensive table totals already include multi-table SQL duration for each mentioned table.
    digest["tables_top"] = data["tables"][:40]
    digest["large_results_top"] = data["large_results"][:40]
    digest["slowest_individual"] = data["slowest_individual"]
    digest["daily"] = data["daily"]
    digest["hourly_sql_top"] = sorted(
        ({"hour": hour, **stats} for hour, stats in data["hourly_sql"].items()),
        key=lambda item: item.get("total") or 0,
        reverse=True,
    )[:20]

    OUT.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
