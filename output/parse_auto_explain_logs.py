from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


START = datetime.fromisoformat("2026-08-26T11:25:41.966762+00:00")
END = datetime.fromisoformat("2026-09-04T09:14:30+00:00")
LOCAL = timezone(timedelta(hours=2))
HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) CEST (?:\[[^]]+\] )?LOG:\s+duration: ([\d.]+) ms\s+plan:")
ANY_HEADER = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} CEST ")
QUERY_ID = re.compile(r"Query Identifier:\s+(-?\d+)")
TABLE = re.compile(r"\b(?:from|join|update|into|delete\s+from)\s+([a-z_][a-z0-9_\.]*)", re.I)


def main() -> int:
    log_dir = Path(r"C:\Program Files\PostgreSQL\18\data\log")
    paths = sorted(p for p in log_dir.glob("postgresql-2026-*.log") if p.name >= "postgresql-2026-08-26" and p.name <= "postgresql-2026-09-04_999999.log")
    records: list[dict] = []
    current: list[str] = []

    def consume(lines: list[str]) -> None:
        if not lines:
            return
        match = HEADER.match(lines[0])
        if not match:
            return
        when = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL).astimezone(timezone.utc)
        if not START <= when <= END:
            return
        body = "".join(lines)
        query_id = QUERY_ID.search(body)
        query_lines: list[str] = []
        in_query = False
        for line in lines[1:]:
            stripped = line.lstrip("\t ").rstrip("\r\n")
            if stripped.startswith("Query Text:"):
                in_query = True
                query_lines.append(stripped.removeprefix("Query Text:").strip())
                continue
            if in_query:
                if re.match(r"(?:->\s+)?(?:Seq Scan|Index Scan|Index Only Scan|Bitmap|Nested Loop|Hash Join|Merge Join|Sort|Aggregate|Finalize Aggregate|Gather|Delete on|Insert on|Update on|LockRows)\b", stripped):
                    break
                if stripped.startswith("Query Parameters:"):
                    continue
                query_lines.append(stripped)
        query = " ".join(x for x in query_lines if x)
        tables = sorted({m.group(1).split(".")[-1] for m in TABLE.finditer(query)})
        plan_terms = Counter()
        for label, pattern in {
            "seq_scan": r"\bSeq Scan\b", "index_scan": r"\bIndex Scan\b", "index_only_scan": r"\bIndex Only Scan\b",
            "bitmap_heap_scan": r"\bBitmap Heap Scan\b", "nested_loop": r"\bNested Loop\b", "hash_join": r"\bHash Join\b",
            "merge_join": r"\bMerge Join\b", "sort": r"(?:^|\n)\s*(?:->\s*)?Sort\s+\(", "hash": r"(?:^|\n)\s*(?:->\s*)?Hash\s+\(",
            "gather": r"\bGather\b", "parallel": r"\bParallel\b", "external_sort": r"Sort Method: external", "temp": r"temp (?:read|written)=",
        }.items():
            count = len(re.findall(pattern, body, re.M))
            if count:
                plan_terms[label] = count
        rows_est = [int(x) for x in re.findall(r"\bcost=[^\n]*? rows=(\d+)", body)]
        records.append({
            "timestamp": when.isoformat(), "duration_ms": float(match.group(2)), "query_id": query_id.group(1) if query_id else None,
            "query": query[:12000], "tables": tables, "plan_terms": dict(plan_terms), "max_estimated_rows": max(rows_est) if rows_est else None,
            "plan_excerpt": body[:20000],
        })

    for path in paths:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if ANY_HEADER.match(line):
                    consume(current)
                    current = [line]
                else:
                    current.append(line)
        consume(current)
        current = []

    by_query: dict[str, dict] = defaultdict(lambda: {"calls": 0, "total_ms": 0.0, "max_ms": 0.0, "query": "", "tables": Counter(), "plan_terms": Counter()})
    for rec in records:
        key = str(rec["query_id"] or rec["query"][:500])
        item = by_query[key]
        item["calls"] += 1
        item["total_ms"] += rec["duration_ms"]
        item["max_ms"] = max(item["max_ms"], rec["duration_ms"])
        item["query"] = rec["query"]
        item["tables"].update(rec["tables"])
        item["plan_terms"].update(rec["plan_terms"])
    aggregate = []
    for key, item in by_query.items():
        aggregate.append({"query_id": key, "calls": item["calls"], "total_ms": round(item["total_ms"], 3), "mean_ms": round(item["total_ms"] / item["calls"], 3), "max_ms": round(item["max_ms"], 3), "query": item["query"], "tables": item["tables"].most_common(), "plan_terms": dict(item["plan_terms"])})
    output = {
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "files": len(paths), "plans": len(records),
        "plan_terms": dict(sum((Counter(x["plan_terms"]) for x in records), Counter())),
        "by_total": sorted(aggregate, key=lambda x: x["total_ms"], reverse=True),
        "slowest": sorted(records, key=lambda x: x["duration_ms"], reverse=True)[:100],
    }
    Path("output/swinglens_auto_explain_audit.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"plans": len(records), "queries": len(aggregate), "terms": output["plan_terms"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
