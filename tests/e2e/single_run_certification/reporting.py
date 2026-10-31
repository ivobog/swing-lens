from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PIPELINE_SCOPE = "Deterministic frozen-provider, native-worker, ten-ticker pipeline certification"
PINNED_SOURCE = "3a78cfbda12f9d05823dd51a749328ec5bd0a2ec"
CARDINALITY_TEST = (
    "tests/e2e/single_run_certification/test_real_writer_cardinality.py::"
    "test_frozen_real_writer_cardinality"
)
EXACT_TEST_IDS = tuple(CARDINALITY_TEST + f"[chromium-{mode}-10]" for mode in (False, True))
REQUIRED_ARTIFACTS = (
    "expected-manifest.json",
    "environment.json",
    "certificate-contract.json",
    "pipeline-evidence.json",
    "db-results/production-worker-fidelity.json",
    "db-results/independent-golden-subset.json",
    "db-results/independent-complete-oracle.json",
    "db-results/provider-10-metrics.json",
    "runtime-invariants.jsonl",
    "logs/worker-fidelity.jsonl",
    "checks-reached.json",
    "cleanup.json",
    "screenshots/gui/020-pipeline-progress.png",
)


def pipeline_gate_inventory(manifest: dict) -> dict[str, str]:
    from single_run_certification.golden_completion import relationship_inventory

    inventory = {
        name: surface
        for surface, names in {
            "identity": (
                "identity",
                "oracle_prerequisite",
                "configuration",
                "disposable_postgresql",
            ),
            "native_worker": ("handler", "authority", "job_membership", "dependency", "terminal"),
            "diagnostics": ("diagnostics",),
            "committed_oracle": ("fresh_committed", "comparison_inventory", "immutable_lineage"),
            "gui_admission": ("gui_admission",),
            "finalization": ("checks_reached", "invocation", "cleanup", "evidence_seal"),
        }.items()
        for name in names
    }
    inventory.update(
        {"stage:" + row["name"]: "durable_stages" for row in manifest["domains"]["stages"]}
    )
    inventory.update(
        {"section:" + name: "committed_oracle" for name in manifest["completion"]["domains"]}
    )
    inventory.update(
        {"relationship:" + name: "committed_lineage" for name in relationship_inventory(manifest)}
    )
    inventory.update(
        {
            "subset:" + name: "committed_oracle"
            for name in (
                "raw",
                "combined_members",
                "fundamental",
                "technical_indicators",
                "rank_members",
                "stages",
                "ceri_sources",
                "business_time",
                "operational_ingestion_clock",
            )
        }
    )
    inventory.update({"artifact:" + path: "evidence" for path in REQUIRED_ARTIFACTS})
    return inventory


def pipeline_contract(manifest: dict, identity: dict) -> dict:
    from single_run_certification.golden_oracle import canonical_hash, require_complete

    require_complete(manifest)
    return {
        "schema": "swinglens.exact-pipeline-contract.v1",
        "scope": PIPELINE_SCOPE,
        "identity": identity,
        "manifest_sha256": canonical_hash(manifest),
        "required_gates": pipeline_gate_inventory(manifest),
    }


def finalize_pipeline_certificate(
    artifact_dir: Path, *, invocation: dict, expected_identity: dict
) -> dict:
    """Re-evaluate artifacts, never accept a reported PASS/CERTIFIED as proof."""
    from single_run_certification.evidence import evaluate_pipeline_evidence
    from single_run_certification.golden_oracle import expected_manifest

    contract = json.loads((artifact_dir / "certificate-contract.json").read_text())
    identity = contract["identity"]
    manifest = expected_manifest(identity["handoff"])
    bundle = {}
    hashes = {}
    for relative in REQUIRED_ARTIFACTS:
        path = artifact_dir / relative
        if not path.is_file() or path.is_symlink() or not path.stat().st_size:
            continue
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.suffix == ".json":
            bundle[relative] = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".jsonl":
            bundle[relative] = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    recorder = evaluate_pipeline_evidence(
        contract,
        manifest,
        bundle,
        invocation=invocation,
        expected_identity=expected_identity,
        hashes=hashes,
    )
    certificate = {
        "schema": "swinglens.exact-pipeline-certificate.v1",
        "scope": PIPELINE_SCOPE,
        "identity": identity,
        "manifest_sha256": contract["manifest_sha256"],
        "required_gates": contract["required_gates"],
        "gates": recorder.gate_results,
        "verdict": recorder.verdict,
        "failures": recorder.failures,
        "reasons": {
            name: result["reason"]
            for name, result in recorder.gate_results.items()
            if result["status"] != "PASS"
        },
        "missing_gates": sorted(recorder.required_gates.keys() - recorder.gate_results.keys()),
        "expected_counts": {
            name: len(rows) for name, rows in manifest["completion"]["domains"].items()
        },
        "actual_counts": {
            name: len(rows)
            for name, rows in bundle.get("db-results/independent-complete-oracle.json", {})
            .get("actual", {})
            .items()
        },
        "relationship_count": len(
            bundle.get("db-results/independent-complete-oracle.json", {}).get("relationships", [])
        ),
        "native_lineage": bundle.get("db-results/production-worker-fidelity.json", {}),
        "artifact_sha256": hashes,
        "gui_financial_oracle": "NOT_IN_SCOPE",
        "live_provider_promotion": "NOT_RUN",
    }
    certificate["evidence_sha256"] = hashlib.sha256(
        json.dumps(hashes, sort_keys=True).encode()
    ).hexdigest()
    (artifact_dir / "certificate.json").write_text(
        json.dumps(certificate, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return certificate


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
    required_gates: dict[str, str] = field(default_factory=dict)
    gate_results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def gate(self, name: str, status: str, *, evidence: Any = None, reason: str = "") -> None:
        if name not in self.required_gates or name in self.gate_results:
            self.failures.append("undeclared or duplicate gate: " + name)
            return
        if status not in {"PASS", "FAILED", "BLOCKED", "INCOMPLETE"}:
            raise ValueError("invalid certification gate status: " + status)
        self.gate_results[name] = {
            "status": status,
            "surface": self.required_gates[name],
            "evidence": evidence,
            "reason": reason,
        }

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
        if self.required_gates:
            states = {item["status"] for item in self.gate_results.values()}
            if self.failures or "FAILED" in states:
                return "FAILED"
            if "BLOCKED" in states:
                return "BLOCKED"
            if "INCOMPLETE" in states or self.required_gates.keys() != self.gate_results.keys():
                return "INCOMPLETE"
            return "CERTIFIED"
        # Explicit legacy compatibility. These area comparisons are not an exact
        # pipeline certificate, and an empty set cannot even produce legacy PASS.
        return "FAIL" if self.failures else "PASS" if self.comparisons else "INCOMPLETE"

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
- Provider mode: `{environment.get("provider_mode")}`
- Pipeline ID: `{environment.get("runtime_identity", {}).get("pipeline_id")}`
- Root job ID: `{environment.get("runtime_identity", {}).get("root_job_id")}`
- Root correlation ID: `{environment.get("runtime_identity", {}).get("root_correlation_id")}`
- Worker ID: `{environment.get("runtime_identity", {}).get("worker_id")}`
- Worker generation: `{environment.get("runtime_identity", {}).get("worker_instance_id")}`
- Worker generation counter: `{environment.get("runtime_identity", {}).get("worker_generation")}`
- Runtime instance: `{environment.get("runtime_identity", {}).get("runtime_instance_id")}`
- Pipeline started: `{environment.get("runtime_identity", {}).get("pipeline_started_at")}`
- Pipeline finished: `{environment.get("runtime_identity", {}).get("pipeline_finished_at")}`
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
The pipeline-owned prediction capture is exercised through the durable worker. The
browser maturation endpoint is then challenged and must be rejected by certification
isolation without changing the point-in-time prediction or pending outcomes.

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
        "| Area | Recorded comparisons | Separate DB/GUI/oracle proof |",
        "|---|---|---|",
    ]
    for area in areas:
        failures = [item for item in recorder.failures if item.startswith(f"{area}:")]
        compared = any(item["area"] == area for item in recorder.comparisons)
        result = "FAIL" if failures else "PASS" if compared else "N/A"
        lines.append(f"| {area} | {result} | See individual evidence; not inferred |")
    return "\n".join(lines)
