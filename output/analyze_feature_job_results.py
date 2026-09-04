from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

os.environ["DB_MONITOR_ENABLED"] = "false"

from sqlalchemy import text
from app.db import SessionLocal


START = datetime.fromisoformat("2026-08-26T11:25:41.966762+00:00")
END = datetime.fromisoformat("2026-09-04T09:14:30+00:00")


def summary(values):
    values = sorted(float(x) for x in values)
    if not values:
        return {"count": 0}
    def p(f):
        return values[max(0, min(len(values) - 1, int((len(values) * f + 0.999999)) - 1))]
    return {"count": len(values), "total": round(sum(values), 3), "mean": round(sum(values)/len(values), 3), "median": round(p(.5), 3), "p95": round(p(.95), 3), "max": round(values[-1], 3)}


def main() -> int:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        rows = list(db.execute(text("""
            SELECT id, related_run_id, status, started_at, completed_at, result_json
            FROM background_jobs
            WHERE job_type = 'CERI_FEATURE_BATCH'
              AND completed_at >= :start AND completed_at <= :end
            ORDER BY completed_at
        """), {"start": START, "end": END}).mappings())
        db.rollback()
    totals = Counter()
    timings = defaultdict(list)
    rows_loaded = Counter()
    versions = Counter()
    top_queries = Counter()
    jobs = []
    for row in rows:
        result = row["result_json"] or {}
        telemetry = result.get("telemetry") or {}
        monitor = telemetry.get("sql_monitor") or {}
        versions[str(telemetry.get("feature_rebuild_impl_version") or "UNKNOWN")] += 1
        for key in ("processed_tickers", "features", "failed"):
            totals[key] += int(result.get(key) or 0)
        for key in ("ticker_count", "companies_rebuilt", "companies_skipped_unchanged", "features_inserted", "features_updated", "features_deduplicated", "sql_select_count", "sql_upsert_write_count"):
            totals[key] += int(telemetry.get(key) or 0)
        for key, value in (telemetry.get("rows_loaded") or {}).items():
            rows_loaded[key] += int(value or 0)
        for key in ("batch_total_ms", "load_context_ms", "persistence_ms", "revision_compute_ms", "surprise_compute_ms", "guidance_compute_ms", "catalyst_compute_ms", "price_response_compute_ms", "confidence_compute_ms"):
            timings[key].append(float(telemetry.get(key) or 0))
        for key in ("total_duration_ms", "total_sql_ms", "sql_query_count", "sql_select_count", "sql_insert_count", "sql_update_count", "sql_delete_count", "sql_other_count", "orm_flush_count", "pool_wait_total_ms", "transaction_total_ms"):
            timings[f"monitor_{key}"].append(float(monitor.get(key) or 0))
        for item in monitor.get("top_expensive_queries") or []:
            top_queries[str(item.get("query_fingerprint"))] += float(item.get("total_ms") or 0)
        jobs.append({
            "job_id": row["id"], "run_id": row["related_run_id"], "status": row["status"],
            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
            "ticker_count": telemetry.get("ticker_count"), "batch_total_ms": telemetry.get("batch_total_ms"),
            "sql_ms": monitor.get("total_sql_ms"), "sql_calls": monitor.get("sql_query_count"),
            "load_context_ms": telemetry.get("load_context_ms"), "persistence_ms": telemetry.get("persistence_ms"),
            "companies_rebuilt": telemetry.get("companies_rebuilt"), "companies_skipped": telemetry.get("companies_skipped_unchanged"),
        })
    output = {
        "window": {"start": START.isoformat(), "end": END.isoformat()}, "jobs": len(rows),
        "statuses": dict(Counter(str(row["status"]) for row in rows)), "versions": dict(versions),
        "totals": dict(totals), "rows_loaded": dict(rows_loaded),
        "timings": {key: summary(values) for key, values in timings.items()},
        "top_query_fingerprints_ms": top_queries.most_common(30),
        "slowest_jobs": sorted(jobs, key=lambda x: float(x["batch_total_ms"] or 0), reverse=True)[:30],
    }
    Path("output/swinglens_feature_job_audit.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"jobs": output["jobs"], "versions": output["versions"], "totals": output["totals"], "timings": output["timings"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
