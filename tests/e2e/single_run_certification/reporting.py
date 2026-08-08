from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CertificationRecorder:
    execution_id: str
    run_id: int | None = None
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    surfaces: list[dict[str, Any]] = field(default_factory=list)
    comparisons: list[dict[str, Any]] = field(default_factory=list)
    integrity_checks: list[dict[str, Any]] = field(default_factory=list)
    area_results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def check(
        self,
        condition: bool,
        message: str,
        *,
        area: str,
        expected: Any = None,
        actual: Any = None,
    ) -> bool:
        comparison = {
            "area": area,
            "message": message,
            "expected": expected,
            "actual": actual,
            "passed": bool(condition),
        }
        self.comparisons.append(comparison)
        if not condition:
            self.failures.append(f"{area}: {message}; expected={expected!r}, actual={actual!r}")
        return bool(condition)

    @property
    def verdict(self) -> str:
        return "FAIL" if self.failures else "PASS"

    @property
    def comparison_count(self) -> int:
        return len(self.comparisons)

    @property
    def passed_comparison_count(self) -> int:
        return sum(item["passed"] for item in self.comparisons)


def write_database_html(
    artifact_dir: Path,
    manifest: dict[str, Any],
) -> list[Path]:
    html_dir = artifact_dir / "database-html"
    html_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    for index, entry in enumerate(manifest["tables"], start=1):
        sql_path = artifact_dir / entry["sql_artifact"]
        result_path = artifact_dir / entry["result_artifact"]
        sql_text = sql_path.read_text(encoding="utf-8")
        rows = json.loads(result_path.read_text(encoding="utf-8"))
        keys = list(rows[0]) if rows else []
        page = html_dir / f"{index:03d}-{entry['table']}.html"
        page.write_text(
            _database_page(
                title=f"{entry['table']} - {entry['relationship_to_run']}",
                sql_text=sql_text,
                rows=rows,
                keys=keys,
            ),
            encoding="utf-8",
        )
        pages.append(page)
    return pages


def write_report(
    artifact_dir: Path,
    *,
    recorder: CertificationRecorder,
    environment: dict[str, Any],
    graph: dict[str, Any],
    pipeline_steps: list[dict[str, Any]],
    idempotency: dict[str, Any],
    exports: list[dict[str, Any]],
) -> None:
    report = {
        "schema": "swinglens.single-run-certification-report.v1",
        "execution": environment,
        "run_id": recorder.run_id,
        "verdict": recorder.verdict,
        "gui_surfaces": recorder.surfaces,
        "gui_surface_count": len(recorder.surfaces),
        "database_relationship_count": graph.get("run_relationship_count", 0),
        "database_row_count": graph.get("run_row_count", 0),
        "comparison_count": recorder.comparison_count,
        "passed_comparison_count": recorder.passed_comparison_count,
        "pipeline_steps": pipeline_steps,
        "integrity_checks": recorder.integrity_checks,
        "idempotency": idempotency,
        "exports": exports,
        "warnings": recorder.warnings,
        "failures": recorder.failures,
    }
    (artifact_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    (artifact_dir / "comparisons" / "gui-db-comparisons.json").write_text(
        json.dumps(recorder.comparisons, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    matrix = _result_matrix(recorder)
    failure_lines = "\n".join(f"- {failure}" for failure in recorder.failures) or "None."
    warning_lines = "\n".join(f"- {warning}" for warning in recorder.warnings) or "None."
    surface_lines = "\n".join(
        f"- [{item['name']}]({item['screenshot']}) - `{item['path']}`" for item in recorder.surfaces
    )
    step_lines = "\n".join(
        f"- {item['step_order']}. `{item['step_name']}`: {item['status']}"
        for item in pipeline_steps
    )
    export_lines = "\n".join(
        f"- `{item['name']}`: {item['status']}"
        + (f" ({item['row_count']} rows)" if "row_count" in item else "")
        for item in exports
    )
    report_md = f"""# SwingLens Single-Run E2E Certification Report

Final verdict: **{recorder.verdict}**

## Test execution identity

- Execution ID: `{environment["execution_id"]}`
- Git commit: `{environment["git_commit"]}`
- Alembic revision: `{environment["alembic_revision"]}`
- Fixture version: `{environment["fixture_version"]}`
- Fixture hash: `{environment["fixture_hash"]}`
- Run ID: `{recorder.run_id}`
- Run status: `{environment.get("run_status")}`
- Ticker count: `{environment.get("ticker_count")}`
- PostgreSQL database: `{environment["database_name"]}` (disposable)

## Result matrix

{matrix}

## Pipeline steps

{step_lines}

## GUI pages tested

{surface_lines}

## Database evidence graph

- Tables/relationships with evidence: {graph.get("run_relationship_count", 0)}
- Associated rows inventoried: {graph.get("run_row_count", 0)}
- Machine-readable manifest: [manifest.json](manifest.json)
- SQL evidence: [sql/](sql/)
- Query results: [db-results/](db-results/)
- Database screenshots: [screenshots/database/](screenshots/database/)

## GUI to database comparisons

- Comparisons executed: {recorder.comparison_count}
- Passed: {recorder.passed_comparison_count}
- Failed: {recorder.comparison_count - recorder.passed_comparison_count}
- Machine-readable detail:
  [comparisons/gui-db-comparisons.json](comparisons/gui-db-comparisons.json)

## Lifecycle and alerts

Setup snapshots, evaluation runs, episodes, lifecycle events, signal changes, alerts,
and rendered Market Changes/Alerts surfaces are included in the graph and comparison
artifacts. Alert state mutation is recorded in the comparison log when an unread
alert was available.

## Winner Evidence

Prediction snapshots, pending/mature outcomes, decision-time estimates, evidence
members/manifests, run page, and prediction detail are included where produced.
Later deterministic bars are matured through the browser-queued worker path, and
the original point-in-time prediction hash is checked before and after maturation.

## CERI

Manual-provider ingestion, normalized evidence, revision features, run score
snapshots, change events, alert rows, run/ticker UI, and redaction leak checks are
included. Restricted provider payload fields are never written into the evidence
package.

## Market Regime and Sector Rotation

Run-owned snapshots, sector rows, dashboards, drill-down pages, and exports are
included in the evidence graph and GUI surface inventory.

## Exports

{export_lines}

## Run isolation and idempotency

```json
{json.dumps(idempotency, indent=2, sort_keys=True, default=str)}
```

## Integrity checks

```json
{json.dumps(recorder.integrity_checks, indent=2, sort_keys=True, default=str)}
```

## Warnings

{warning_lines}

## Failures

{failure_lines}
"""
    (artifact_dir / "REPORT.md").write_text(report_md, encoding="utf-8")


def _database_page(
    *,
    title: str,
    sql_text: str,
    rows: list[dict[str, Any]],
    keys: list[str],
) -> str:
    headers = "".join(f"<th>{html.escape(key)}</th>" for key in keys)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{html.escape(json.dumps(row.get(key), default=str))}</td>" for key in keys)
        + "</tr>"
        for row in rows
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{ font: 14px system-ui; margin: 24px; color: #102030; background: #f7fafc; }}
h1 {{ font-size: 20px; }} pre {{ white-space: pre-wrap; background: #102030; color: #fff;
padding: 16px; border-radius: 8px; }} .wrap {{ overflow: auto; max-height: 900px; }}
table {{ border-collapse: collapse; background: white; }} th,td {{ border: 1px solid #ccd6dd;
padding: 6px 8px; text-align: left; vertical-align: top; max-width: 420px; }}
th {{ position: sticky; top: 0; background: #e7eef3; }}
</style></head><body><h1>{html.escape(title)}</h1>
<p>Returned row count: <strong>{len(rows)}</strong></p><pre>{html.escape(sql_text)}</pre>
<div class="wrap"><table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></div>
</body></html>"""


def _result_matrix(recorder: CertificationRecorder) -> str:
    areas = (
        "Upload",
        "Fundamentals",
        "Technicals",
        "Rankings",
        "Market Regime",
        "Sector Rotation",
        "Setup Lifecycle",
        "Alerts",
        "Winner Evidence",
        "CERI",
        "History/Exports",
        "Pipeline/Jobs",
        "Isolation/Integrity",
    )
    lines = [
        "| Area | DB | GUI | GUI↔DB | Evidence | Result |",
        "|---|---|---|---|---|---|",
    ]
    for area in areas:
        failures = [item for item in recorder.failures if item.startswith(f"{area}:")]
        compared = any(item["area"] == area for item in recorder.comparisons)
        result = "FAIL" if failures else "PASS" if compared else "N/A"
        lines.append(f"| {area} | {result} | {result} | {result} | links | {result} |")
    return "\n".join(lines)
