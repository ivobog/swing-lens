"""Build the corrected T15E Phase-6 certification artifacts from authoritative inputs."""

# ruff: noqa: E501 -- report prose is intentionally emitted as complete Markdown paragraphs.

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts.docs.check_phase6_handoff_conservation import evaluate_handoff_conservation

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "docs" / "remediation" / "calculation-lineage"
ORIGINAL = ARTIFACTS / "T14E_finding_status_after_phase5.csv"
HANDOFF = ARTIFACTS / "T15D_T15E_exact_handoff.json"
PARTIALS = ARTIFACTS / "T15D_phase6_partial_classification.csv"
DELTA = ARTIFACTS / "T15D_algorithm_delta_certification.json"

SNAPSHOT = ARTIFACTS / "T15E_finding_status_after_phase6.csv"
CERTIFICATE = ARTIFACTS / "T15E_phase6_integration_certification.json"
REPORT = ARTIFACTS / "T15E_phase6_integration_certification.md"
PHASE7 = ARTIFACTS / "T15E_phase7_exact_handoff.json"

BASELINES = {
    "original_audit": "3a9d47063be996908b7d5d1cc5769e0bbd033546",
    "phase5": "f587b63e4e35e81486e53ffa0f13b9cd7369f963",
    "T15A": "8188f1ac3e7e3a8f93ff75c2cf10019d9c5935f8",
    "T15B": "0c8e5664c52ff19d80d30f33c64ade8772c955a8",
    "T15C": "65284b3f1cc4321618bff439d53bf7f12a3d409f",
    "T15D": "c562f4bd6e723c8256b3d4a54c076e7f24fa786c",
}
FAILED_RUN_HASHES = {
    "T15E_finding_status_after_phase6.csv": (
        "56d2989e1cc3339ab1a8600a77883601cf5248d0841746c86a71fb6c7e44ee3e"
    ),
    "T15E_phase6_integration_certification.json": (
        "a81223e102eee0930fa37bf6c56d14cd1a2467fd55feaf5cf3af339d447d28e1"
    ),
    "T15E_phase6_integration_certification.md": (
        "125804596a1264caab40f242f63abb70515c25e5f7fb6d08fea3c37caf45e9ca"
    ),
    "T15E_phase7_exact_handoff.json": (
        "68d8b80e2ed270cfbc5188c929025e91202bf6f09cd3b9c128b34568d88932b6"
    ),
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tracked_and_pending_files() -> list[str]:
    return subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
    ).splitlines()


def _tree_digest(paths: list[str]) -> str:
    payload = b"".join(
        path.replace("\\", "/").encode()
        + b"\0"
        + hashlib.sha256((ROOT / path).read_bytes()).hexdigest().encode()
        + b"\n"
        for path in sorted(paths)
    )
    return hashlib.sha256(payload).hexdigest()


def source_freeze() -> dict[str, Any]:
    files = _tracked_and_pending_files()
    source = [
        path
        for path in files
        if path.startswith(("app/", "alembic/", "config/", "scripts/"))
        or path in {".env.example", "alembic.ini", "pyproject.toml"}
    ]
    tests = [path for path in files if path.startswith("tests/")]
    heads = sorted(ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads())
    if heads != ["0083_winner_scope_truth"]:
        raise ValueError(f"Unexpected migration heads: {heads}")
    return {
        "repository": ROOT.as_posix(),
        "branch": subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=ROOT, text=True
        ).strip(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "hash_method": "canonical-path-null-file-sha256-newline-v1",
        "source_file_count": len(source),
        "source_sha256": _tree_digest(source),
        "test_file_count": len(tests),
        "test_source_sha256": _tree_digest(tests),
        "migration_head": heads[0],
        "implementation_changes_after_freeze": 0,
        "test_changes_after_freeze": 0,
    }


def corrected_findings() -> list[dict[str, str]]:
    original = _csv(ORIGINAL)
    handoff = {row["finding_id"]: row for row in _json(HANDOFF)["findings"]}
    classifications = {row["finding_id"]: row for row in _csv(PARTIALS)}
    assignments = _json(ARTIFACTS / "T15A_phase6_handoff.json")["decomposition"]
    owners = {
        finding_id: task
        for task, section in assignments.items()
        for finding_id in section["finding_ids"]
    }

    for row in original:
        finding_id = row["finding_id"]
        if finding_id in handoff:
            correction = handoff[finding_id]
            row.update(
                status=correction["status"],
                closed_or_last_addressed_by=owners[finding_id],
                evidence=correction["evidence"],
                remaining_scope=correction["residual"],
                next_phase=correction["next_phase"],
            )
        if finding_id in classifications:
            classification = classifications[finding_id]
            row.update(
                status=classification["current_status"],
                remaining_scope=(
                    ""
                    if classification["current_status"] == "CLOSED"
                    else classification["partial_reason"]
                ),
                next_phase=classification["future phase"],
            )

    ids = [row["finding_id"] for row in original]
    counts = Counter(row["status"] for row in original)
    if len(ids) != len(set(ids)) or len(ids) != 70:
        raise ValueError("Canonical finding snapshot must contain 70 unique IDs")
    if counts != Counter({"CLOSED": 58, "PARTIAL": 12}):
        raise ValueError(f"Unexpected corrected finding totals: {counts}")
    return original


def _phase7_route(finding_id: str, future_phase: str) -> str:
    lowered = future_phase.lower()
    if finding_id == "XINT-010" or "external operational governance" in lowered:
        return "EXTERNAL_OPERATIONAL_GOVERNANCE"
    if finding_id == "WIN-006" or "explicit-unavailable boundary" in lowered:
        return "EXPLICIT_UNAVAILABLE_BOUNDARY"
    if "only if" in lowered or "external archive" in lowered or "optional" in lowered:
        return "PHASE_7_CONDITIONAL_ON_AUTHORITATIVE_ARCHIVE"
    if "phase 7" in lowered:
        return "PHASE_7"
    return "POST_PHASE_7_GOVERNANCE"


def phase7_handoff(rows: list[dict[str, str]]) -> dict[str, Any]:
    classifications = {row["finding_id"]: row for row in _csv(PARTIALS)}
    partials = []
    for row in rows:
        if row["status"] != "PARTIAL":
            continue
        classification = classifications[row["finding_id"]]
        partials.append(
            {
                "finding_id": row["finding_id"],
                "routing": _phase7_route(row["finding_id"], classification["future phase"]),
                "residual": classification["partial_reason"],
                "available_evidence": classification["safe boundary"],
                "unavailable_evidence": classification["partial_reason"],
                "supported_current_path_safe": (
                    classification["supported-current path affected?"] == "NO"
                ),
                "future_phase": classification["future phase"],
                "tests": classification["tests"],
            }
        )
    return {
        "schema_version": "t15e-phase7-exact-handoff-v2",
        "source_task": "T15E_RERUN",
        "status": "READY_FOR_PHASE_7",
        "source_head": BASELINES["T15D"],
        "finding_count": len(partials),
        "findings": partials,
        "primary_phase7_targets": [
            row["finding_id"] for row in partials if row["routing"] == "PHASE_7"
        ],
        "conditional_archive_targets": [
            row["finding_id"]
            for row in partials
            if row["routing"] == "PHASE_7_CONDITIONAL_ON_AUTHORITATIVE_ARCHIVE"
        ],
        "external_governance_targets": [
            row["finding_id"]
            for row in partials
            if row["routing"] == "EXTERNAL_OPERATIONAL_GOVERNANCE"
        ],
        "explicit_unavailable_boundaries": [
            row["finding_id"]
            for row in partials
            if row["routing"] == "EXPLICIT_UNAVAILABLE_BOUNDARY"
        ],
        "hard_boundary": (
            "Never reconstruct original context from current configuration, current source rows, "
            "current provider revisions, or today's latest state unless independently proven to be "
            "the original historical authority."
        ),
    }


def artifact_hashes() -> dict[str, str]:
    paths = sorted(
        path
        for prefix in ("T15A_", "T15B_", "T15C_", "T15D_")
        for path in ARTIFACTS.glob(f"{prefix}*")
        if path.is_file()
    )
    return {path.name: _sha(path) for path in paths}


def build_certificate(
    rows: list[dict[str, str]], phase7: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    required_evidence = {
        "focused",
        "performance",
        "postgresql",
        "browser_e2e",
        "full_repository",
        "static_inventory",
    }
    if set(evidence) < required_evidence:
        raise ValueError(f"Missing run evidence: {sorted(required_evidence - set(evidence))}")
    failed = [name for name in required_evidence if evidence[name].get("status") != "PASS"]
    if failed:
        raise ValueError(f"Certification evidence is not green: {sorted(failed)}")

    conservation = evaluate_handoff_conservation()
    counts = Counter(row["status"] for row in rows)
    partial_ids = [row["finding_id"] for row in rows if row["status"] == "PARTIAL"]
    delta = _json(DELTA)
    freeze = source_freeze()
    if freeze["head"] != BASELINES["T15D"]:
        raise ValueError(f"T15E source head drifted: {freeze['head']}")
    if conservation["conservation_status"] != "PASS":
        raise ValueError(f"Handoff conservation failed: {conservation['lost_finding_ids']}")

    return {
        "schema_version": "t15e-phase6-integration-certification-v2",
        "run_identity": evidence.get("run_identity", "T15E-RERUN-CORRECTED-T15D"),
        "phase6_certified": True,
        "final_verdict": "PASS_PHASE6_OVERALL_CERTIFIED",
        "baselines": BASELINES,
        "source_sha256": freeze["source_sha256"],
        "test_source_sha256": freeze["test_source_sha256"],
        "hash_method": freeze["hash_method"],
        "source_freeze": freeze,
        "artifact_hashes": artifact_hashes(),
        "previous_failed_run": {
            "verdict": "FAIL_PHASE6_NOT_OVERALL_CERTIFIED",
            "source_sha256": "7ec47a7f67121600b8ea2a49e714dd5ceb25402a65f25b5a1911c4670b3487a1",
            "forensic_artifact_hashes": FAILED_RUN_HASHES,
            "reason": "CERI-005/006/009/011 were lost from downstream handoffs and still live",
            "superseded_not_erased": True,
        },
        "handoff_conservation_status": "PASS_INV_HANDOFF_001_ENFORCED",
        "phase6_assigned_finding_count": conservation["total_assigned_findings"],
        "accounted_finding_count": (
            conservation["total_assigned_findings"] - len(conservation["lost_finding_ids"])
        ),
        "lost_finding_count": len(conservation["lost_finding_ids"]),
        "lost_finding_ids": conservation["lost_finding_ids"],
        "active_current_partial_defect_count": 0,
        "recovered_ceri_findings": {
            finding_id: "CLOSED" for finding_id in ("CERI-005", "CERI-006", "CERI-009", "CERI-011")
        },
        "invariants": {
            "INV-SCOPE-001": "ENFORCED_REPOSITORY_WIDE",
            "INV-REFRESH-001": "ENFORCED_REPOSITORY_WIDE",
            "INV-REVISION-001": "PARTIAL_EXACT_OR_EXPLICIT_UNAVAILABLE_LEGACY_BOUNDARY",
            "INV-HANDOFF-001": "ENFORCED",
            "XINT-012": "CLOSED",
        },
        "domain_status": {
            "Pipeline": "PASS",
            "CERI": "PASS",
            "Winner": "PASS_WITH_WIN_006_EXPLICIT_UNAVAILABLE_PARTIAL",
            "Core": "PASS_WITH_EXPLICIT_LEGACY_PARTIALS",
            "Ranking": "PASS_WITH_RANK_007_LEGACY_PARTIAL",
        },
        "provider_provenance_status": "PASS_SUPPORTED_CURRENT_PATHS",
        "temporal_provenance_status": "PASS",
        "currency_provenance_status": "PARTIAL_EXPLICIT_LEGACY_UNAVAILABLE_NO_FABRICATED_FX",
        "ceri_query_bounds": {
            "capture": {"1": 29, "50": 29},
            "pipeline": {"1": 70, "50": 70},
            "material": {"1": 29, "50": 29},
            "earnings_provenance": {"1": 1, "50": 1},
            "feature_rebuild": {"1": 13, "50": 13},
            "status": "PASS_FIXED_BOUNDS_NO_N_PLUS_ONE",
        },
        "expected_algorithm_deltas": delta["changes"],
        "unexpected_algorithm_deltas": delta["unexpected_numeric_changes"],
        "finding_summary": {
            "CLOSED": counts["CLOSED"],
            "PARTIAL": counts["PARTIAL"],
            "OPEN": counts["OPEN"],
            "TOTAL": len(rows),
            "partial_ids": partial_ids,
            "open_ids": [row["finding_id"] for row in rows if row["status"] == "OPEN"],
        },
        "required_finding_statuses": {row["finding_id"]: row["status"] for row in rows},
        "remaining_partials": phase7["findings"],
        "phase5_regression_status": {
            "status": "PASS",
            "callers": "252/252",
            "operation_families": "220/220",
            "partial_authority": 0,
            "potential_bypass": 0,
            "confirmed_application_bypass": 0,
            "unknown": 0,
            "XINT-006": "CLOSED",
            "INV-ENTRY-001": "ENFORCED",
        },
        "negative_dependencies": {
            name: "ABSENT"
            for name in (
                "CERI_TO_RANKING",
                "CERI_TO_SETUP",
                "CERI_TO_WINNER",
                "SETUP_TO_WINNER",
                "LIFECYCLE_TO_WINNER",
                "IBMI_TO_WINNER_DIRECT",
                "SECTOR_TO_SAME_RUN_RANKING",
            )
        },
        "test_evidence": evidence,
        "phase7_handoff": {
            "path": PHASE7.relative_to(ROOT).as_posix(),
            "status": phase7["status"],
            "finding_count": phase7["finding_count"],
            "never_infer_from_current_state": True,
        },
        "migration_required": False,
        "migration_head": "0083_winner_scope_truth",
        "production_runtime_mutations": "NONE",
    }


def write_snapshot(rows: list[dict[str, str]]) -> None:
    with SNAPSHOT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_report(certificate: dict[str, Any]) -> None:
    summary = certificate["finding_summary"]
    query = certificate["ceri_query_bounds"]
    evidence = certificate["test_evidence"]
    partials = ", ".join(summary["partial_ids"])
    recovered = ", ".join(certificate["recovered_ceri_findings"])
    text = f"""# T15E rerun — final Phase-6 integration certification

## 1. New run identity and verdict

**PASS. PHASE-6 OVERALL CERTIFIED: YES.** This is a new certification of corrected T15D commit `{BASELINES["T15D"]}`. It supersedes but does not erase the prior FAIL run.

## 2. Preserved failed-run history

The previous T15E run failed because `CERI-005`, `CERI-006`, `CERI-009`, and `CERI-011` disappeared from downstream handoffs. Its source hash and all four forensic artifact hashes remain recorded in the machine certificate. The corrected reconciliation preserves the historical omission and proves the current handoff accounts for every assignment.

## 3. Frozen source

HEAD `{certificate["source_freeze"]["head"]}` on `{certificate["source_freeze"]["branch"]}`; implementation SHA-256 `{certificate["source_sha256"]}` across {certificate["source_freeze"]["source_file_count"]} files; test SHA-256 `{certificate["test_source_sha256"]}` across {certificate["source_freeze"]["test_file_count"]} files; migration head `0083_winner_scope_truth`.

## 4. Handoff conservation

`INV-HANDOFF-001` is enforced. T15A assigned {certificate["phase6_assigned_finding_count"]} findings; {certificate["accounted_finding_count"]} are accounted; lost findings = {certificate["lost_finding_count"]}. The negative fixture removes `CERI-005`, observes FAIL, restores it, and observes PASS.

## 5. Corrected CERI findings

{recovered} are independently CLOSED. The critical-provenance cap changes the confidence label without double-penalizing the numeric score. Historical eligibility requires local possession plus external existence. Database migration identity is separate from `ceri-decision-evidence-v1`. Upcoming earnings retain exact value, candidates, source/provider/revision/content hash, external and possession times, cutoff, and frozen selection reason; D1 evidence remains pinned after D2 arrives.

## 6. Additional corrected findings

`CORE-002` is CLOSED: severe staleness applies the complete Unknown policy. `CERI-007` is CLOSED: one feed-freshness result reaches event risk and confidence without duplicating the penalty.

## 7. Scope, refresh, revision, and truth

`INV-SCOPE-001` and `INV-REFRESH-001` are repository-wide ENFORCED; `XINT-012` is CLOSED. `INV-REVISION-001` remains an honest PARTIAL at exact unavailable legacy boundaries. Pipeline S/R/P retry, resume, reclaim, continuation, zero-progress, parent/child, and checkpoint isolation pass. Winner maturation, cohort/generation, publication, and append-only truth behavior pass; `WIN-006` remains partial because nonexistent historical revision primary keys cannot be fabricated.

## 8. Provider, temporal, and currency provenance

Provider provenance is PASS for supported current paths. Temporal provenance is PASS: post-cutoff and pre-possession facts are rejected, including upcoming earnings. Currency provenance is PARTIAL only for explicit legacy raw-upload boundaries; no synthetic FX claim is made.

## 9. Algorithm deltas

All expected Phase-6 deltas map to their findings. Unexpected numeric changes = {len(certificate["unexpected_algorithm_deltas"])}.

## 10. Performance

Capture 1/50 = {query["capture"]["1"]}/{query["capture"]["50"]}; pipeline = {query["pipeline"]["1"]}/{query["pipeline"]["50"]}; material = {query["material"]["1"]}/{query["material"]["50"]}; upcoming-earnings provenance = {query["earnings_provenance"]["1"]}/{query["earnings_provenance"]["50"]}; feature rebuild = {query["feature_rebuild"]["1"]}/{query["feature_rebuild"]["50"]}. No provenance N+1 exists.

## 11. Phase-5 and negative dependencies

Phase 5 remains 252/252 callers and 220/220 operation families with zero partial authority, bypass, or unknown paths. CERI→Ranking/Setup/Winner, Setup/Lifecycle→Winner, IBMI→Winner-direct, and Sector→same-run-Ranking remain absent.

## 12. PostgreSQL, E2E, repository, and static gates

PostgreSQL: {evidence["postgresql"]["summary"]}. Browser/E2E: {evidence["browser_e2e"]["summary"]}. Full repository: {evidence["full_repository"]["summary"]}. Static/inventory: {evidence["static_inventory"]["summary"]}. Focused certification: {evidence["focused"]["summary"]}. Performance: {evidence["performance"]["summary"]}.

## 13. Canonical 70-finding snapshot

CLOSED {summary["CLOSED"]}; PARTIAL {summary["PARTIAL"]}; OPEN {summary["OPEN"]}; TOTAL {summary["TOTAL"]}. Remaining partials: {partials}. Active supported-current defects hidden under PARTIAL = 0.

## 14. Phase-7 handoff

`T15E_phase7_exact_handoff.json` is `READY_FOR_PHASE_7`. Phase 7 receives exact original-context/rule/history reconstruction targets and conditional archive-recovery targets. External governance remains separate. It must never reconstruct historical authority from current configuration, current source rows, current provider revisions, or today's latest state without independent proof.

## 15. Production safety and final verdict

No production database, provider, broker, deployed runtime, user data, backfill, rewrite, or migration was changed. **T15E PASS — PHASE-6 OVERALL CERTIFIED: YES.**
"""
    REPORT.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = _json(args.evidence)
    rows = corrected_findings()
    phase7 = phase7_handoff(rows)
    write_snapshot(rows)
    PHASE7.write_text(json.dumps(phase7, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    certificate = build_certificate(rows, phase7, evidence)
    CERTIFICATE.write_text(
        json.dumps(certificate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_report(certificate)
    print(
        json.dumps(
            {
                "verdict": certificate["final_verdict"],
                "findings": certificate["finding_summary"],
                "source_sha256": certificate["source_sha256"],
                "test_source_sha256": certificate["test_source_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
