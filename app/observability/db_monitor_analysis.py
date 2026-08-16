from __future__ import annotations

import argparse
import heapq
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class NumericAggregate:
    count: int = 0
    total: float = 0.0
    maximum: float = 0.0

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.maximum = max(self.maximum, value)

    def as_dict(self, *, prefix: str = "") -> dict[str, Any]:
        return {
            f"{prefix}count": self.count,
            f"{prefix}total": round(self.total, 3),
            f"{prefix}mean": round(self.total / self.count, 3) if self.count else 0.0,
            f"{prefix}max": round(self.maximum, 3),
        }


@dataclass
class FingerprintAggregate:
    operation: str = "UNKNOWN"
    normalized_sql: str = ""
    calls: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    callers: Counter[str] = field(default_factory=Counter)
    routes: Counter[str] = field(default_factory=Counter)
    jobs: Counter[str] = field(default_factory=Counter)

    def add(self, record: Mapping[str, Any]) -> None:
        duration = float(record.get("duration_ms") or 0)
        self.calls += 1
        self.total_ms += duration
        self.max_ms = max(self.max_ms, duration)
        if record.get("success", True):
            self.success_count += 1
        else:
            self.failure_count += 1
        caller = caller_label(record.get("python_caller"))
        if caller:
            self.callers[caller] += 1
        route = route_label(record)
        if route:
            self.routes[route] += 1
        if record.get("job_type"):
            self.jobs[str(record["job_type"])] += 1

    def as_dict(self, fingerprint: str) -> dict[str, Any]:
        return {
            "query_fingerprint": fingerprint,
            "operation": self.operation,
            "calls": self.calls,
            "total_ms": round(self.total_ms, 3),
            "mean_ms": round(self.total_ms / self.calls, 3) if self.calls else 0.0,
            "max_ms": round(self.max_ms, 3),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "normalized_sql": self.normalized_sql,
            "primary_origins": self.callers.most_common(10),
            "routes": self.routes.most_common(10),
            "jobs": self.jobs.most_common(10),
        }


@dataclass
class RouteAggregate:
    requests: int = 0
    request_ms: NumericAggregate = field(default_factory=NumericAggregate)
    sql_ms: NumericAggregate = field(default_factory=NumericAggregate)
    query_count: NumericAggregate = field(default_factory=NumericAggregate)

    def as_dict(self, route: str) -> dict[str, Any]:
        sql_pct = (self.sql_ms.total / self.request_ms.total * 100) if self.request_ms.total else 0
        return {
            "route": route,
            "requests": self.requests,
            "mean_request_ms": round(self.request_ms.total / self.requests, 3),
            "max_request_ms": round(self.request_ms.maximum, 3),
            "mean_sql_ms": round(self.sql_ms.total / self.requests, 3),
            "sql_percentage": round(sql_pct, 3),
            "mean_query_count": round(self.query_count.total / self.requests, 3),
            "max_query_count": int(self.query_count.maximum),
        }


@dataclass
class JobAggregate:
    executions: int = 0
    duration_ms: NumericAggregate = field(default_factory=NumericAggregate)
    sql_ms: NumericAggregate = field(default_factory=NumericAggregate)
    query_count: int = 0
    max_sql_ms: float = 0.0

    def as_dict(self, job_type: str) -> dict[str, Any]:
        return {
            "job_type": job_type,
            "executions": self.executions,
            "mean_duration_ms": round(self.duration_ms.total / self.executions, 3),
            "max_duration_ms": round(self.duration_ms.maximum, 3),
            "sql_total_ms": round(self.sql_ms.total, 3),
            "mean_sql_ms": round(self.sql_ms.total / self.executions, 3),
            "sql_count": self.query_count,
            "maximum_sql_ms": round(self.max_sql_ms, 3),
        }


def iter_records(log_dir: Path, *, since: datetime | None = None) -> Iterator[dict[str, Any]]:
    for path in sorted(Path(log_dir).glob("sql-*.jsonl")):
        try:
            stream = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if since is not None:
                    timestamp = parse_timestamp(record.get("timestamp"))
                    if timestamp is None or timestamp < since:
                        continue
                yield record


def analyze_logs(
    log_dir: Path,
    *,
    since: datetime | None = None,
    limit: int = 20,
    min_average_calls: int = 5,
    n_plus_one_threshold: int = 10,
    fingerprint_detail: str | None = None,
) -> dict[str, Any]:
    fingerprints: dict[str, FingerprintAggregate] = {}
    routes: dict[str, RouteAggregate] = defaultdict(RouteAggregate)
    jobs: dict[str, JobAggregate] = defaultdict(JobAggregate)
    request_queries: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    n_plus_one: list[dict[str, Any]] = []
    write_heavy: dict[str, Counter[str]] = defaultdict(Counter)
    database_heavy_origins: dict[str, Counter[str]] = defaultdict(Counter)
    slowest_heap: list[tuple[float, int, dict[str, Any]]] = []
    detail_executions: list[dict[str, Any]] = []
    record_counts: Counter[str] = Counter()
    sequence = 0

    for record in iter_records(log_dir, since=since):
        record_type = str(record.get("record_type") or "unknown")
        record_counts[record_type] += 1
        if record_type == "sql":
            fingerprint = str(record.get("query_fingerprint") or "")
            aggregate = fingerprints.setdefault(
                fingerprint,
                FingerprintAggregate(
                    operation=str(record.get("operation") or "UNKNOWN"),
                    normalized_sql=str(record.get("normalized_sql") or ""),
                ),
            )
            aggregate.add(record)
            duration = float(record.get("duration_ms") or 0)
            origin = caller_label(record.get("python_caller")) or area_label(record)
            database_heavy_origins[origin]["statements"] += 1
            database_heavy_origins[origin]["duration_ms"] += duration
            database_heavy_origins[origin][str(record.get("operation") or "UNKNOWN")] += 1
            compact = compact_execution(record)
            sequence += 1
            if len(slowest_heap) < limit:
                heapq.heappush(slowest_heap, (duration, sequence, compact))
            elif duration > slowest_heap[0][0]:
                heapq.heapreplace(slowest_heap, (duration, sequence, compact))
            if fingerprint_detail and fingerprint == fingerprint_detail:
                detail_executions.append(compact)
                detail_executions.sort(key=lambda row: row["duration_ms"], reverse=True)
                del detail_executions[limit:]
            request_id = record.get("request_id")
            if request_id:
                per_request = request_queries[str(request_id)]
                entry = per_request.setdefault(
                    fingerprint,
                    {
                        "calls": 0,
                        "total_ms": 0.0,
                        "caller": record.get("python_caller"),
                        "route": route_label(record),
                    },
                )
                entry["calls"] += 1
                entry["total_ms"] += duration
            operation = str(record.get("operation") or "UNKNOWN")
            if operation in {"INSERT", "UPDATE", "DELETE", "MERGE"}:
                area = area_label(record)
                write_heavy[area]["statements"] += 1
                write_heavy[area][operation] += 1
                write_heavy[area]["duration_ms"] += duration
        elif record_type == "request_summary":
            route = route_label(record) or "UNKNOWN"
            aggregate = routes[route]
            aggregate.requests += 1
            aggregate.request_ms.add(float(record.get("total_duration_ms") or 0))
            aggregate.sql_ms.add(float(record.get("total_sql_ms") or 0))
            aggregate.query_count.add(float(record.get("sql_query_count") or 0))
            request_id = str(record.get("request_id") or "")
            per_request = request_queries.pop(request_id, {})
            for fingerprint, entry in per_request.items():
                if entry["calls"] >= n_plus_one_threshold:
                    n_plus_one.append(
                        {
                            "route": route,
                            "request_id": request_id,
                            "query_fingerprint": fingerprint,
                            "calls_in_request": entry["calls"],
                            "cumulative_ms": round(entry["total_ms"], 3),
                            "python_caller": entry["caller"],
                        }
                    )
        elif record_type == "job_summary":
            job_type = str(record.get("job_type") or "UNKNOWN")
            aggregate = jobs[job_type]
            aggregate.executions += 1
            aggregate.duration_ms.add(float(record.get("total_duration_ms") or 0))
            aggregate.sql_ms.add(float(record.get("total_sql_ms") or 0))
            aggregate.query_count += int(record.get("sql_query_count") or 0)
            aggregate.max_sql_ms = max(
                aggregate.max_sql_ms,
                float(record.get("maximum_sql_ms") or 0),
            )

    fingerprint_rows = [value.as_dict(key) for key, value in fingerprints.items()]
    detail = fingerprints.get(fingerprint_detail or "")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "log_directory": str(Path(log_dir).resolve()),
        "since": since.isoformat() if since else None,
        "record_counts": dict(record_counts),
        "slowest_individual_queries": [
            item[2] for item in sorted(slowest_heap, key=lambda item: item[0], reverse=True)
        ],
        "most_expensive_fingerprints": sorted(
            fingerprint_rows, key=lambda row: row["total_ms"], reverse=True
        )[:limit],
        "most_frequently_executed": sorted(
            fingerprint_rows, key=lambda row: row["calls"], reverse=True
        )[:limit],
        "slowest_average_queries": sorted(
            (row for row in fingerprint_rows if row["calls"] >= min_average_calls),
            key=lambda row: row["mean_ms"],
            reverse=True,
        )[:limit],
        "worst_gui_routes": sorted(
            (value.as_dict(key) for key, value in routes.items()),
            key=lambda row: row["mean_request_ms"],
            reverse=True,
        )[:limit],
        "most_sql_heavy_routes": sorted(
            (value.as_dict(key) for key, value in routes.items()),
            key=lambda row: row["mean_sql_ms"],
            reverse=True,
        )[:limit],
        "worst_background_jobs": sorted(
            (value.as_dict(key) for key, value in jobs.items()),
            key=lambda row: row["sql_total_ms"],
            reverse=True,
        )[:limit],
        "n_plus_one_candidates": sorted(
            n_plus_one,
            key=lambda row: (row["calls_in_request"], row["cumulative_ms"]),
            reverse=True,
        )[:limit],
        "write_heavy_areas": sorted(
            ({"area": area, **dict(counts)} for area, counts in write_heavy.items()),
            key=lambda row: row.get("duration_ms", 0),
            reverse=True,
        )[:limit],
        "database_heavy_origins": sorted(
            (
                {"origin": origin, **dict(counts)}
                for origin, counts in database_heavy_origins.items()
            ),
            key=lambda row: row.get("duration_ms", 0),
            reverse=True,
        )[:limit],
        "query_detail": {
            **(detail.as_dict(fingerprint_detail or "") if detail else {}),
            "slowest_executions": detail_executions,
        }
        if fingerprint_detail
        else None,
    }


def compact_execution(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": record.get("timestamp"),
        "duration_ms": float(record.get("duration_ms") or 0),
        "operation": record.get("operation"),
        "query_fingerprint": record.get("query_fingerprint"),
        "normalized_sql": record.get("normalized_sql"),
        "python_caller": record.get("python_caller"),
        "application_stack": record.get("application_stack"),
        "request_id": record.get("request_id"),
        "route": route_label(record),
        "job_id": record.get("job_id"),
        "job_type": record.get("job_type"),
        "run_id": record.get("run_id"),
        "success": record.get("success"),
        "error_type": record.get("error_type"),
        "error_summary": record.get("error_summary"),
    }


def route_label(record: Mapping[str, Any]) -> str | None:
    route = record.get("route_path") or record.get("route_name") or record.get("http_path")
    if not route:
        return None
    method = record.get("http_method")
    return f"{method} {route}" if method else str(route)


def caller_label(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    source = value.get("source_file")
    line = value.get("line_number")
    function = value.get("function")
    if not source:
        return None
    return f"{source}:{line} {function}"


def area_label(record: Mapping[str, Any]) -> str:
    if record.get("job_type"):
        return f"job:{record['job_type']}"
    route = route_label(record)
    if route:
        return f"route:{route}"
    caller = caller_label(record.get("python_caller"))
    return f"caller:{caller or 'UNKNOWN'}"


def parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def render_text(report: Mapping[str, Any]) -> str:
    lines = [
        "SwingLens SQL Flight Recorder report",
        f"Generated: {report['generated_at']}",
        f"Log directory: {report['log_directory']}",
        f"Records: {json.dumps(report['record_counts'], sort_keys=True)}",
    ]
    sections: Iterable[tuple[str, str, Sequence[Mapping[str, Any]]]] = (
        ("Slowest individual queries", "duration_ms", report["slowest_individual_queries"]),
        ("Most expensive fingerprints", "total_ms", report["most_expensive_fingerprints"]),
        ("Most frequently executed", "calls", report["most_frequently_executed"]),
        ("Slowest average queries", "mean_ms", report["slowest_average_queries"]),
        ("Worst GUI routes", "mean_request_ms", report["worst_gui_routes"]),
        ("Worst background jobs", "sql_total_ms", report["worst_background_jobs"]),
        ("N+1 candidates", "calls_in_request", report["n_plus_one_candidates"]),
        ("Write-heavy areas", "statements", report["write_heavy_areas"]),
        ("Database-heavy origins", "duration_ms", report["database_heavy_origins"]),
    )
    for title, metric, rows in sections:
        lines.extend(["", title])
        if not rows:
            lines.append("  none")
            continue
        for row in rows:
            label = (
                row.get("route")
                or row.get("job_type")
                or row.get("area")
                or row.get("origin")
                or row.get("query_fingerprint")
                or row.get("normalized_sql")
            )
            lines.append(f"  {metric}={row.get(metric)}  {label}")
    if report.get("query_detail") is not None:
        lines.extend(["", "Query detail", json.dumps(report["query_detail"], indent=2)])
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze SwingLens SQL flight-recorder JSONL.")
    parser.add_argument("--log-dir", type=Path, default=Path("logs/db-monitor"))
    parser.add_argument("--hours", type=float, default=24 * 7)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-average-calls", type=int, default=5)
    parser.add_argument("--n-plus-one-threshold", type=int, default=10)
    parser.add_argument("--fingerprint")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    since = datetime.now(UTC) - timedelta(hours=max(0, args.hours)) if args.hours else None
    report = analyze_logs(
        args.log_dir,
        since=since,
        limit=max(1, args.limit),
        min_average_calls=max(1, args.min_average_calls),
        n_plus_one_threshold=max(2, args.n_plus_one_threshold),
        fingerprint_detail=args.fingerprint,
    )
    output = (
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.format == "json"
        else render_text(report)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
