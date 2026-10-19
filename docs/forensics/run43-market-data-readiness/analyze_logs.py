"""Extract bounded log witnesses and hash the exact source prefixes read."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
snapshots = []


def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def records(path):
    length = path.stat().st_size
    digest = hashlib.sha256()
    read = 0
    with path.open("rb") as stream:
        number = 0
        while read < length:
            line = stream.readline(length - read)
            if not line:
                break
            read += len(line)
            number += 1
            digest.update(line)
            if b"2026-10-18" not in line:
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            yield {"source_file": path.relative_to(ROOT).as_posix(), "source_line": number, **row}
    snapshots.append(
        {
            "file": path.relative_to(ROOT).as_posix(),
            "captured_prefix_bytes": read,
            "sha256_of_captured_prefix": digest.hexdigest(),
            "lines": number,
        }
    )


def main():
    events = []
    for row in records(ROOT / "logs/lifecycle-worker.log"):
        stamp = row.get("timestamp", "")
        if not "2026-10-18T19:57:20" <= stamp <= "2026-10-18T20:22:45":
            continue
        if (
            row.get("background_job_id") in (1450, 1451, 1452)
            or row.get("job_id") in (1450, 1451, 1452)
            or row.get("pipeline_run_id") == 43
        ):
            events.append(row)
    save("application-events.json", events)
    decisions = []
    for row in records(ROOT / "logs/lifecycle-supervisor.err.log"):
        if row.get("job_id") == 1452:
            decisions.append(row)
    save("supervisor-decisions.json", decisions)
    sql = []
    for path in sorted((ROOT / "logs/db-monitor/worker").glob("sql-2026-10-18-p76372*.jsonl")):
        for row in records(path):
            if "2026-10-18T20:09:04" <= row.get("timestamp", "") <= "2026-10-18T20:09:12":
                sql.append(row)
    save("sql-dds-fetch-window.json", sql)
    dds_sql = [
        row
        for row in sql
        if "2026-10-18T20:09:04.940859" <= row.get("timestamp", "") <= "2026-10-18T20:09:11.400000"
    ]
    summary = {
        "application_events": len(events),
        "application_event_counts": dict(Counter(r.get("event") for r in events)),
        "watchdog_decision_counts": dict(Counter(r.get("decision") for r in decisions)),
        "sql_context_records": len(sql),
        "sql_dds_window_records": len(dds_sql),
        "sql_failed": sum(r.get("success") is False for r in dds_sql),
        "sql_price_bar_inserts": sum(
            r.get("normalized_sql", "").startswith("INSERT INTO price_bars ") for r in dds_sql
        ),
        "sql_fetch_item_updates": sum(
            r.get("normalized_sql", "").startswith("UPDATE ib_fetch_items SET") for r in dds_sql
        ),
        "sql_window_utc": ["2026-10-18T20:09:04.940859", "2026-10-18T20:09:11.400000"],
        "sql_context_note": (
            "The wider retained context includes the preceding DBX cache insert at "
            "20:09:04.762894; it precedes DDS start and is excluded from these DDS-window counts. "
            "DDS final failure SQL is at 20:09:11.320231; the next ticker starts at "
            "20:09:11.412206."
        ),
        "limitations": (
            "SQL telemetry retains parameter shapes/digests, not raw values. Source line "
            "numbers and UTC windows correlate the item writes to retained database rows; "
            "raw provider OHLCV was not retained."
        ),
    }
    save("log-summary.json", summary)
    save("source-log-sha256.json", snapshots)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
