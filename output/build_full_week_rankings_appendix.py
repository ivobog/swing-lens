from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
data = json.loads((ROOT / "swinglens_full_week_audit_raw.json").read_text(encoding="utf-8"))


def label(row: dict) -> str:
    caller = (row.get("primary_callers") or [["UNKNOWN"]])[0][0]
    sql = row.get("sql", "")
    table = ""
    for candidate in (
        "ceri_score_snapshots", "ceri_guidance_events", "ceri_revision_features", "price_bars",
        "background_jobs", "background_workers", "background_supervisors", "winner_prediction_snapshots",
        "winner_forward_outcomes", "winner_target_stop_outcomes", "ceri_source_records",
    ):
        if candidate in sql.lower():
            table = candidate
            break
    return f"{row.get('operation')} {table or caller.split('/')[-1]}"


lines = [
    "# SwingLens full-week fingerprint ranking appendix",
    "",
    "Window: `2026-08-26T11:25:41.966762Z` through `2026-09-04T09:14:30Z`. "
    "Worker figures are observed lower bounds because the worker writer dropped records.",
    "",
]

for title, key in (
    ("Highest cumulative SQL time", "by_total"),
    ("Highest call count", "by_calls"),
    ("Highest mean latency (minimum 5 samples)", "by_mean_min5"),
    ("Highest p95 latency", "by_p95"),
    ("Highest p99 latency", "by_p99"),
):
    lines += [
        f"## {title}",
        "",
        "| Rank | Fingerprint | Shape | Calls | Total s | Mean ms | p95 ms | p99 ms | Max ms | Rows | Primary caller |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, row in enumerate(data["fingerprints"][key][:30], 1):
        caller = (row.get("primary_callers") or [["UNKNOWN"]])[0][0]
        lines.append(
            f"| {index} | `{row['fingerprint'][:12]}` | {label(row)} | {row['calls']:,} | "
            f"{row['total_ms']/1000:,.3f} | {row['mean_ms']:,.3f} | {row['p95_ms']:,.3f} | "
            f"{row['p99_ms']:,.3f} | {row['max_ms']:,.3f} | {row.get('rows_total', 0):,} | `{caller}` |"
        )
    lines.append("")

lines += [
    "## Slowest individual executions",
    "",
    "| Rank | Duration ms | Fingerprint | Route/job | Rows | Caller | Shape |",
    "| ---: | ---: | --- | --- | ---: | --- | --- |",
]
for index, row in enumerate(data["slowest_individual"][:30], 1):
    origin = row.get("route") if row.get("route") != "UNKNOWN" else row.get("job_type") or "UNKNOWN"
    shape = row.get("sql", "").split(" FROM ")[0][:80].replace("|", "\\|")
    lines.append(
        f"| {index} | {row['duration_ms']:,.3f} | `{row['fingerprint'][:12]}` | `{origin}` | "
        f"{row.get('rowcount') or 0:,} | `{row.get('caller')}` | {shape} |"
    )

(ROOT / "swinglens_full_week_rankings_appendix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(ROOT / "swinglens_full_week_rankings_appendix.md")
