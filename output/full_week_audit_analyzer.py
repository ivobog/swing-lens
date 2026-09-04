from __future__ import annotations

import argparse
import heapq
import json
import math
import re
from array import array
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc
TABLE_RE = re.compile(
    r"\b(?:from|join|update|into|delete\s+from)\s+([a-z_][a-z0-9_\.]*)",
    re.IGNORECASE,
)
FILE_RE = re.compile(r"sql-(\d{4}-\d{2}-\d{2})-p(\d+)(?:\.(\d+))?\.jsonl$")


def ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def pct(values: array | list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(float(ordered[pos]), 3)


def summarize(values: array | list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "total": 0.0, "mean": 0.0, "median": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    total = sum(values)
    return {
        "count": len(values),
        "total": round(total, 3),
        "mean": round(total / len(values), 3),
        "median": pct(values, 0.5),
        "p90": pct(values, 0.9),
        "p95": pct(values, 0.95),
        "p99": pct(values, 0.99),
        "max": round(max(values), 3),
    }


def file_key(path: Path) -> tuple[str, int, int]:
    match = FILE_RE.search(path.name)
    if not match:
        return (path.name, 0, 0)
    return (match.group(1), int(match.group(2)), int(match.group(3) or 0))


def counter_top(value: Counter[str], n: int = 10) -> list[list[Any]]:
    return [[key, count] for key, count in value.most_common(n)]


def caller(rec: dict[str, Any]) -> str:
    item = rec.get("python_caller") or {}
    if not isinstance(item, dict):
        return "UNKNOWN"
    return f"{item.get('source_file', '?')}:{item.get('line_number', '?')} {item.get('function', '?')}"


def route(rec: dict[str, Any]) -> str:
    path = rec.get("route_path") or rec.get("route_name") or rec.get("http_path")
    return f"{rec.get('http_method', '')} {path}".strip() if path else "UNKNOWN"


class Fingerprint:
    __slots__ = (
        "operation", "sql", "durations", "total_ms", "max_ms", "rows_total",
        "rows_max", "rows_samples", "callers", "routes", "jobs", "runs", "roles",
        "failures", "slow", "full_traces", "param_digests",
    )

    def __init__(self, rec: dict[str, Any]) -> None:
        self.operation = str(rec.get("operation") or "OTHER")
        self.sql = str(rec.get("normalized_sql") or "")
        self.durations = array("f")
        self.total_ms = 0.0
        self.max_ms = 0.0
        self.rows_total = 0
        self.rows_max = 0
        self.rows_samples = 0
        self.callers: Counter[str] = Counter()
        self.routes: Counter[str] = Counter()
        self.jobs: Counter[str] = Counter()
        self.runs: Counter[str] = Counter()
        self.roles: Counter[str] = Counter()
        self.failures = 0
        self.slow = 0
        self.full_traces = 0
        self.param_digests: Counter[str] = Counter()

    def add(self, rec: dict[str, Any], duration: float) -> None:
        self.durations.append(duration)
        self.total_ms += duration
        self.max_ms = max(self.max_ms, duration)
        rowcount = rec.get("rowcount")
        if isinstance(rowcount, int) and rowcount >= 0:
            self.rows_total += rowcount
            self.rows_max = max(self.rows_max, rowcount)
            self.rows_samples += 1
        self.callers[caller(rec)] += 1
        r = route(rec)
        if r != "UNKNOWN":
            self.routes[r] += 1
        if rec.get("job_type"):
            self.jobs[str(rec["job_type"])] += 1
        if rec.get("run_id") is not None:
            self.runs[str(rec["run_id"])] += 1
        self.roles[str(rec.get("process_role") or "other")] += 1
        self.failures += int(not rec.get("success", True))
        self.slow += int(bool(rec.get("slow_query")))
        self.full_traces += int(bool(rec.get("application_stack")))
        if rec.get("parameter_digest"):
            self.param_digests[str(rec["parameter_digest"])] += 1

    def result(self, fp: str) -> dict[str, Any]:
        stats = summarize(self.durations)
        return {
            "fingerprint": fp,
            "operation": self.operation,
            "sql": self.sql,
            "calls": stats["count"],
            "total_ms": round(self.total_ms, 3),
            "mean_ms": stats["mean"],
            "median_ms": stats["median"],
            "p95_ms": stats["p95"],
            "p99_ms": stats["p99"],
            "max_ms": round(self.max_ms, 3),
            "rows_total": self.rows_total,
            "rows_max": self.rows_max,
            "rowcount_samples": self.rows_samples,
            "primary_callers": counter_top(self.callers),
            "routes": counter_top(self.routes),
            "jobs": counter_top(self.jobs),
            "runs": counter_top(self.runs),
            "roles": counter_top(self.roles),
            "failures": self.failures,
            "slow_records": self.slow,
            "full_traces": self.full_traces,
            "unique_parameter_digests": len(self.param_digests),
            "top_parameter_digests": counter_top(self.param_digests, 5),
        }


class SummaryGroup:
    def __init__(self) -> None:
        self.wall: list[float] = []
        self.sql: list[float] = []
        self.queries: list[float] = []
        self.pool: list[float] = []
        self.transactions: list[float] = []
        self.operations: Counter[str] = Counter()
        self.statuses: Counter[str] = Counter()

    def add(self, rec: dict[str, Any]) -> None:
        self.wall.append(float(rec.get("total_duration_ms") or 0))
        self.sql.append(float(rec.get("total_sql_ms") or 0))
        self.queries.append(float(rec.get("sql_query_count") or 0))
        self.pool.append(float(rec.get("pool_wait_total_ms") or 0))
        self.transactions.append(float(rec.get("transaction_total_ms") or 0))
        for op in ("select", "insert", "update", "delete", "other"):
            self.operations[op.upper()] += int(rec.get(f"sql_{op}_count") or 0)
        self.statuses[str(rec.get("outcome") or rec.get("status") or rec.get("status_code") or "UNKNOWN")] += 1

    def result(self, name_key: str, name: str) -> dict[str, Any]:
        wall = summarize(self.wall)
        sql = summarize(self.sql)
        queries = summarize(self.queries)
        pool = summarize(self.pool)
        txn = summarize(self.transactions)
        return {
            name_key: name,
            "executions": len(self.wall),
            "wall": wall,
            "sql": sql,
            "queries": queries,
            "sql_share_pct": round(100 * sql["total"] / wall["total"], 3) if wall["total"] else 0.0,
            "pool_wait": pool,
            "transaction": txn,
            "operations": dict(self.operations),
            "statuses": dict(self.statuses),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", type=Path, default=Path("logs/db-monitor"))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = ts(args.start)
    end = ts(args.end)
    assert start and end

    files = sorted(args.log_dir.rglob("sql-*.jsonl"), key=lambda p: (p.parent.name, *file_key(p)))
    all_bytes = sum(p.stat().st_size for p in files)
    record_counts: Counter[str] = Counter()
    role_record_counts: dict[str, Counter[str]] = defaultdict(Counter)
    role_sql_ms: Counter[str] = Counter()
    role_sql_calls: Counter[str] = Counter()
    op_counts: Counter[str] = Counter()
    sql_durations = array("f")
    fingerprints: dict[str, Fingerprint] = {}
    daily: dict[str, dict[str, Any]] = defaultdict(lambda: {"durations": array("f"), "ops": Counter()})
    hourly_sql: dict[str, dict[str, float]] = defaultdict(lambda: {"calls": 0, "sql_ms": 0.0})
    routes: dict[str, SummaryGroup] = defaultdict(SummaryGroup)
    jobs: dict[str, SummaryGroup] = defaultdict(SummaryGroup)
    route_records: list[dict[str, Any]] = []
    job_records: list[dict[str, Any]] = []
    job_phases: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    request_queries: dict[str, dict[str, Any]] = defaultdict(lambda: {"fps": defaultdict(lambda: [0, 0.0, "UNKNOWN"]), "exact": defaultdict(lambda: [0, 0.0, "UNKNOWN"])})
    nplus: list[dict[str, Any]] = []
    exact_dupes: list[dict[str, Any]] = []
    slowest: list[tuple[float, int, dict[str, Any]]] = []
    large_results: list[tuple[int, int, dict[str, Any]]] = []
    tables: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, "sql_ms": 0.0, "reads": 0, "writes": 0, "routes": Counter(), "jobs": Counter()})
    deployments: dict[str, dict[str, Any]] = defaultdict(lambda: {"records": 0, "sql_calls": 0, "sql_ms": 0.0, "first": None, "last": None, "versions": Counter(), "dirty": Counter()})
    health: list[dict[str, Any]] = []
    health_by_process: dict[str, list[datetime]] = defaultdict(list)
    pool_events: list[dict[str, Any]] = []
    long_transactions: list[dict[str, Any]] = []
    db_health: list[dict[str, Any]] = []
    errors: Counter[str] = Counter()
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    first_by_role: dict[str, datetime] = {}
    last_by_role: dict[str, datetime] = {}
    seq = 0
    parsed_lines = 0

    for file_index, path in enumerate(files, 1):
        with path.open("rb") as stream:
            for raw in stream:
                try:
                    rec = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    errors["parse_errors"] += 1
                    continue
                parsed_lines += 1
                when = ts(rec.get("timestamp"))
                if when is None:
                    errors["missing_or_invalid_timestamp"] += 1
                    continue
                if when < start or when > end:
                    continue
                rtype = str(rec.get("record_type") or "unknown")
                role = str(rec.get("process_role") or (rec.get("process") or {}).get("role") or path.parent.name or "other")
                record_counts[rtype] += 1
                role_record_counts[role][rtype] += 1
                first_ts = when if first_ts is None or when < first_ts else first_ts
                last_ts = when if last_ts is None or when > last_ts else last_ts
                first_by_role[role] = when if role not in first_by_role or when < first_by_role[role] else first_by_role[role]
                last_by_role[role] = when if role not in last_by_role or when > last_by_role[role] else last_by_role[role]

                commit = str(rec.get("git_commit") or "UNKNOWN")
                dep = deployments[commit]
                dep["records"] += 1
                dep["first"] = when if dep["first"] is None or when < dep["first"] else dep["first"]
                dep["last"] = when if dep["last"] is None or when > dep["last"] else dep["last"]
                dep["versions"][str(rec.get("application_version") or "UNKNOWN")] += 1
                dep["dirty"][str(bool(rec.get("git_dirty")))] += 1

                if rtype == "sql":
                    duration = float(rec.get("duration_ms") or 0)
                    operation = str(rec.get("operation") or "OTHER").upper()
                    fp = str(rec.get("query_fingerprint") or "MISSING")
                    sql_durations.append(duration)
                    op_counts[operation] += 1
                    role_sql_calls[role] += 1
                    role_sql_ms[role] += duration
                    day = when.date().isoformat()
                    daily[day]["durations"].append(duration)
                    daily[day]["ops"][operation] += 1
                    hour = when.strftime("%Y-%m-%dT%H:00Z")
                    hourly_sql[hour]["calls"] += 1
                    hourly_sql[hour]["sql_ms"] += duration
                    item = fingerprints.get(fp)
                    if item is None:
                        item = fingerprints[fp] = Fingerprint(rec)
                    item.add(rec, duration)
                    dep["sql_calls"] += 1
                    dep["sql_ms"] += duration
                    compact = {
                        "timestamp": when.isoformat(), "duration_ms": duration, "fingerprint": fp,
                        "operation": operation, "sql": str(rec.get("normalized_sql") or ""),
                        "caller": caller(rec), "route": route(rec), "job_type": rec.get("job_type"),
                        "job_id": rec.get("job_id"), "run_id": rec.get("run_id"), "role": role,
                        "rowcount": rec.get("rowcount"), "stack": rec.get("application_stack"),
                    }
                    seq += 1
                    if len(slowest) < 30:
                        heapq.heappush(slowest, (duration, seq, compact))
                    elif duration > slowest[0][0]:
                        heapq.heapreplace(slowest, (duration, seq, compact))
                    rowcount = rec.get("rowcount")
                    if isinstance(rowcount, int) and rowcount >= 100:
                        if len(large_results) < 100:
                            heapq.heappush(large_results, (rowcount, seq, compact))
                        elif rowcount > large_results[0][0]:
                            heapq.heapreplace(large_results, (rowcount, seq, compact))
                    sql_text = str(rec.get("normalized_sql") or "")
                    found_tables = {m.group(1).split(".")[-1].strip('"') for m in TABLE_RE.finditer(sql_text)}
                    for table in found_tables:
                        value = tables[table]
                        value["calls"] += 1
                        value["sql_ms"] += duration
                        if operation == "SELECT":
                            value["reads"] += 1
                        elif operation in {"INSERT", "UPDATE", "DELETE", "MERGE"}:
                            value["writes"] += 1
                        rr = route(rec)
                        if rr != "UNKNOWN":
                            value["routes"][rr] += 1
                        if rec.get("job_type"):
                            value["jobs"][str(rec["job_type"])] += 1
                    request_id = rec.get("request_id")
                    if request_id:
                        rq = request_queries[str(request_id)]
                        fentry = rq["fps"][fp]
                        fentry[0] += 1
                        fentry[1] += duration
                        fentry[2] = caller(rec)
                        digest = str(rec.get("parameter_digest") or "NO_DIGEST")
                        eentry = rq["exact"][(fp, digest)]
                        eentry[0] += 1
                        eentry[1] += duration
                        eentry[2] = caller(rec)

                elif rtype == "request_summary":
                    name = route(rec)
                    routes[name].add(rec)
                    compact = {
                        "timestamp": when.isoformat(), "start": rec.get("request_started_at"), "end": rec.get("request_finished_at"),
                        "route": name, "request_id": rec.get("request_id"), "wall_ms": float(rec.get("total_duration_ms") or 0),
                        "sql_ms": float(rec.get("total_sql_ms") or 0), "queries": int(rec.get("sql_query_count") or 0),
                        "pool_wait_ms": float(rec.get("pool_wait_total_ms") or 0), "transaction_ms": float(rec.get("transaction_total_ms") or 0),
                        "status": rec.get("status_code"), "top_fp": rec.get("top_query_fingerprint"),
                        "top_sql": rec.get("top_query_sql"), "top_query_ms": rec.get("top_query_total_ms"),
                        "most_repeated_fp": rec.get("most_repeated_query_fingerprint"),
                        "most_repeated_calls": rec.get("most_repeated_query_calls"), "git_commit": commit,
                    }
                    route_records.append(compact)
                    request_id = str(rec.get("request_id") or "")
                    rq = request_queries.pop(request_id, None)
                    if rq:
                        for fp, entry in rq["fps"].items():
                            if entry[0] >= 2:
                                nplus.append({"route": name, "request_id": request_id, "fingerprint": fp, "calls": entry[0], "total_ms": round(entry[1], 3), "caller": entry[2]})
                        for (fp, digest), entry in rq["exact"].items():
                            if digest != "NO_DIGEST" and entry[0] >= 2:
                                exact_dupes.append({"route": name, "request_id": request_id, "fingerprint": fp, "parameter_digest": digest, "calls": entry[0], "redundant_calls": entry[0] - 1, "total_ms": round(entry[1], 3), "caller": entry[2]})

                elif rtype == "job_summary":
                    name = str(rec.get("job_type") or "UNKNOWN")
                    jobs[name].add(rec)
                    duration = float(rec.get("total_duration_ms") or 0)
                    job_records.append({
                        "timestamp": when.isoformat(), "start": rec.get("job_started_at"), "end": rec.get("job_finished_at"),
                        "job_type": name, "job_id": rec.get("job_id"), "run_id": rec.get("run_id"), "wall_ms": duration,
                        "sql_ms": float(rec.get("total_sql_ms") or 0), "queries": int(rec.get("sql_query_count") or 0),
                        "pool_wait_ms": float(rec.get("pool_wait_total_ms") or 0), "transaction_ms": float(rec.get("transaction_total_ms") or 0),
                        "outcome": rec.get("outcome"), "top_fp": rec.get("top_query_fingerprint"), "top_sql": rec.get("top_query_sql"),
                        "most_repeated_calls": rec.get("most_repeated_query_calls"), "git_commit": commit,
                    })

                elif rtype == "job_phase":
                    job_phases[str(rec.get("job_type") or "UNKNOWN")][str(rec.get("phase") or "UNKNOWN")].append(float(rec.get("duration_ms") or 0))
                elif rtype == "monitor_health":
                    health.append({key: rec.get(key) for key in (
                        "timestamp", "process_role", "process_id", "records_written", "records_dropped", "writer_errors",
                        "telemetry_queue_depth", "telemetry_queue_capacity", "writer_latency_mean_ms", "writer_latency_max_ms",
                        "oldest_queue_age_ms", "current_files", "current_bytes", "oldest_retained_timestamp", "newest_retained_timestamp",
                        "retention_days", "max_files", "max_total_bytes",
                    )})
                    health_by_process[f"{role}:{rec.get('process_id')}:{rec.get('process_started_at')}"] .append(when)
                elif rtype == "pool_checkout":
                    pool_events.append({
                        "timestamp": when.isoformat(), "wait_ms": float(rec.get("pool_wait_ms") or 0), "timeout": bool(rec.get("pool_timeout")),
                        "overflow": bool(rec.get("pool_overflow")), "route": route(rec), "job_type": rec.get("job_type"), "role": role,
                    })
                elif rtype == "long_transaction":
                    long_transactions.append({
                        "timestamp": when.isoformat(), "duration_ms": float(rec.get("duration_ms") or rec.get("transaction_duration_ms") or 0),
                        "route": route(rec), "job_type": rec.get("job_type"), "job_id": rec.get("job_id"), "role": role,
                        "sql_count": rec.get("sql_query_count"), "transaction_start": rec.get("transaction_start"), "transaction_end": rec.get("transaction_end"),
                    })
                elif rtype == "database_health":
                    db_health.append({
                        "timestamp": when.isoformat(), "pid": rec.get("pid"), "duration_ms": float(rec.get("duration_ms") or 0),
                        "transaction_duration_ms": float(rec.get("transaction_duration_ms") or 0), "state": rec.get("state"),
                        "wait_event_type": rec.get("wait_event_type"), "wait_event": rec.get("wait_event"),
                        "blocking_pids": rec.get("blocking_pids") or [], "sql": rec.get("normalized_sql"), "role": role,
                    })

        if file_index % 20 == 0:
            print(f"processed {file_index}/{len(files)} files", flush=True)

    fp_rows = [item.result(fp) for fp, item in fingerprints.items()]
    fp_total = sum(row["total_ms"] for row in fp_rows)
    by_total = sorted(fp_rows, key=lambda x: x["total_ms"], reverse=True)
    pareto = {}
    for count in (1, 5, 10, 20, 50):
        pareto[str(count)] = round(100 * sum(x["total_ms"] for x in by_total[:count]) / fp_total, 3) if fp_total else 0.0

    gaps: list[dict[str, Any]] = []
    for process, values in health_by_process.items():
        values.sort()
        for left, right in zip(values, values[1:]):
            gap = (right - left).total_seconds()
            if gap > 180:
                gaps.append({"process": process, "start": left.isoformat(), "end": right.isoformat(), "seconds": round(gap, 3)})

    route_out = {name: group.result("route", name) for name, group in routes.items()}
    job_out = {name: group.result("job_type", name) for name, group in jobs.items()}
    daily_out = {}
    for day, item in sorted(daily.items()):
        stats = summarize(item["durations"])
        daily_out[day] = {"sql": stats, "operations": dict(item["ops"])}
    table_out = []
    for name, item in tables.items():
        table_out.append({
            "table": name, "calls": item["calls"], "sql_ms": round(item["sql_ms"], 3), "reads": item["reads"], "writes": item["writes"],
            "routes": counter_top(item["routes"], 5), "jobs": counter_top(item["jobs"], 5),
        })

    health_latest: dict[str, dict[str, Any]] = {}
    for item in health:
        key = f"{item.get('process_role')}:{item.get('process_id')}"
        if key not in health_latest or str(item.get("timestamp")) > str(health_latest[key].get("timestamp")):
            health_latest[key] = item

    report = {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "seconds": (end - start).total_seconds(), "first_record": first_ts.isoformat() if first_ts else None, "last_record": last_ts.isoformat() if last_ts else None},
        "files": {"count": len(files), "bytes": all_bytes, "parsed_lines_all_dates": parsed_lines, "parse_errors_all_dates": errors["parse_errors"]},
        "record_counts": dict(record_counts),
        "role_record_counts": {role: dict(counts) for role, counts in role_record_counts.items()},
        "role_ranges": {role: {"first": first_by_role[role].isoformat(), "last": last_by_role[role].isoformat()} for role in first_by_role},
        "role_sql": {role: {"calls": role_sql_calls[role], "total_ms": round(role_sql_ms[role], 3)} for role in role_sql_calls},
        "sql_overall": {**summarize(sql_durations), "operations": dict(op_counts), "unique_fingerprints": len(fingerprints)},
        "daily": daily_out,
        "hourly_sql": dict(sorted(hourly_sql.items())),
        "fingerprints": {
            "by_total": by_total[:100],
            "by_calls": sorted(fp_rows, key=lambda x: x["calls"], reverse=True)[:100],
            "by_mean_min5": sorted((x for x in fp_rows if x["calls"] >= 5), key=lambda x: x["mean_ms"], reverse=True)[:100],
            "by_p95": sorted(fp_rows, key=lambda x: x["p95_ms"], reverse=True)[:100],
            "by_p99": sorted(fp_rows, key=lambda x: x["p99_ms"], reverse=True)[:100],
            "pareto_pct": pareto,
        },
        "slowest_individual": [row for _, _, row in sorted(slowest, reverse=True)],
        "routes": route_out,
        "route_records": route_records,
        "jobs": job_out,
        "job_records": job_records,
        "job_phases": {job: {phase: summarize(values) for phase, values in phases.items()} for job, phases in job_phases.items()},
        "n_plus_one": sorted(nplus, key=lambda x: (x["total_ms"], x["calls"]), reverse=True)[:1000],
        "exact_duplicates": sorted(exact_dupes, key=lambda x: (x["total_ms"], x["redundant_calls"]), reverse=True)[:1000],
        "tables": sorted(table_out, key=lambda x: x["sql_ms"], reverse=True),
        "large_results": [row for _, _, row in sorted(large_results, reverse=True)],
        "deployments": {commit: {**item, "first": item["first"].isoformat(), "last": item["last"].isoformat(), "versions": dict(item["versions"]), "dirty": dict(item["dirty"]), "sql_ms": round(item["sql_ms"], 3)} for commit, item in deployments.items()},
        "monitor_health": {"records": len(health), "latest_by_process": health_latest, "gaps_over_180s": sorted(gaps, key=lambda x: x["seconds"], reverse=True)},
        "pool_events": {"count": len(pool_events), "wait": summarize([x["wait_ms"] for x in pool_events]), "timeouts": sum(x["timeout"] for x in pool_events), "overflows": sum(x["overflow"] for x in pool_events), "top": sorted(pool_events, key=lambda x: x["wait_ms"], reverse=True)[:100]},
        "long_transactions": {"count": len(long_transactions), "duration": summarize([x["duration_ms"] for x in long_transactions]), "top": sorted(long_transactions, key=lambda x: x["duration_ms"], reverse=True)[:100]},
        "database_health": {"count": len(db_health), "blocking_records": sum(bool(x["blocking_pids"]) for x in db_health), "wait_events": dict(Counter(f"{x['wait_event_type']}:{x['wait_event']}" for x in db_health if x["wait_event_type"])), "top": sorted(db_health, key=lambda x: x["duration_ms"], reverse=True)[:200]},
        "errors": dict(errors),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "records": dict(record_counts), "sql": report["sql_overall"], "routes": len(routes), "jobs": len(jobs), "fingerprints": len(fingerprints)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
