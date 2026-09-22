"""Build deterministic Phase-7 finding and residual-governance artifacts."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "docs" / "remediation" / "calculation-lineage"
PHASE6_SNAPSHOT = ARTIFACTS / "T15E_finding_status_after_phase6.csv"
PHASE7_HANDOFF = ARTIFACTS / "T16D_T16E_exact_handoff.json"
PHASE7_SNAPSHOT = ARTIFACTS / "T16E_finding_status_after_phase7.csv"
RESIDUAL_HANDOFF = ARTIFACTS / "T16E_post_phase7_residual_handoff.json"

SNAPSHOT_FIELDS = (
    "finding_id",
    "severity",
    "domain",
    "status",
    "last_addressed_by",
    "current_supported_path_safe",
    "historical_exactness",
    "residual",
    "governance_boundary",
    "evidence",
    "next_action",
)

WHY_UNRECOVERABLE = {
    "CORE-005": (
        "The legacy value never retained per-value provider, revision, time, fiscal, "
        "currency, or FX authority."
    ),
    "CORE-009": (
        "The legacy projection never retained the original calculation context needed "
        "to prove historical readiness."
    ),
    "CERI-008": (
        "The legacy alert never persisted its exact rule and effective-configuration identities."
    ),
    "RANK-007": (
        "The legacy decision never retained the provider schedule revision and "
        "possession-time evidence."
    ),
    "SETUP-004": (
        "The legacy Setup artifact never retained its complete decision context and "
        "predecessor authority."
    ),
    "SETUP-007": (
        "The legacy alert never retained its exact rule, cooldown, configuration, and "
        "predecessor authority."
    ),
    "WIN-006": (
        "The legacy birth bar predates exact PriceBarRevision identity capture and has "
        "no independently unique immutable revision body."
    ),
}


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def phase7_reconciliation() -> list[dict[str, Any]]:
    before = {row["finding_id"]: row for row in _csv(PHASE6_SNAPSHOT)}
    handoff = _json(PHASE7_HANDOFF)
    findings = handoff["findings"]
    if handoff["assigned"] != 12 or handoff["accounted"] != 12 or handoff["lost"] != 0:
        raise ValueError("T16D handoff does not conserve the twelve Phase-7 findings")
    if not len(findings) == len({item["finding_id"] for item in findings}) == 12:
        raise ValueError("T16D handoff finding identities are not exactly unique")
    return [
        {
            "finding_id": item["finding_id"],
            "pre_phase7_status": before[item["finding_id"]]["status"],
            "implementation_task": item["implementation_task"],
            "final_status": item["status"],
            "current_path_status": (
                "CERTIFIED_SAFE" if item["current_supported_path_safe"] else "ACTIVE_DEFECT"
            ),
            "historical_exactness": item["exact_reconstruction_support"],
            "residual": item["historical_boundary"],
            "machine_boundary": "; ".join(item["evidence"]),
        }
        for item in findings
    ]


def finding_snapshot() -> list[dict[str, str]]:
    phase6 = _csv(PHASE6_SNAPSHOT)
    handoff = {item["finding_id"]: item for item in _json(PHASE7_HANDOFF)["findings"]}
    if not len(phase6) == len({row["finding_id"] for row in phase6}) == 70:
        raise ValueError("Phase-6 snapshot must contain exactly seventy unique findings")
    if {row["finding_id"] for row in phase6 if row["status"] == "PARTIAL"} != set(handoff):
        raise ValueError("Phase-7 handoff must account for every Phase-6 partial")

    rows: list[dict[str, str]] = []
    for prior in phase6:
        item = handoff.get(prior["finding_id"])
        if item is None:
            rows.append(
                {
                    "finding_id": prior["finding_id"],
                    "severity": prior["severity"],
                    "domain": prior["domain"],
                    "status": prior["status"],
                    "last_addressed_by": prior["closed_or_last_addressed_by"],
                    "current_supported_path_safe": "YES",
                    "historical_exactness": "PHASE6_CERTIFIED",
                    "residual": prior["remaining_scope"],
                    "governance_boundary": "NONE",
                    "evidence": prior["evidence"],
                    "next_action": "NONE",
                }
            )
            continue

        if item["status"] == "PARTIAL":
            historical_exactness = "LEGACY_AUTHORITY_UNAVAILABLE"
            next_action = "GOVERNANCE_ONLY; NO_CODE_REMEDIATION"
        elif item["permanent_unavailability"]:
            historical_exactness = "LEGACY_BOUNDARY_EXPLICIT_CURRENT_DEFECT_CLOSED"
            next_action = "NONE"
        else:
            historical_exactness = "SUPPORTED_PATH_EXACT_OR_MODE_SEPARATED"
            next_action = "NONE"
        rows.append(
            {
                "finding_id": prior["finding_id"],
                "severity": prior["severity"],
                "domain": prior["domain"],
                "status": item["status"],
                "last_addressed_by": item["implementation_task"],
                "current_supported_path_safe": (
                    "YES" if item["current_supported_path_safe"] else "NO"
                ),
                "historical_exactness": historical_exactness,
                "residual": item["historical_boundary"],
                "governance_boundary": item["external_governance"],
                "evidence": "; ".join(item["evidence"]),
                "next_action": next_action,
            }
        )
    return rows


def residual_handoff() -> dict[str, Any]:
    partials = [item for item in _json(PHASE7_HANDOFF)["findings"] if item["status"] == "PARTIAL"]
    residuals = [
        {
            "finding_id": item["finding_id"],
            "status": item["status"],
            "missing_authority": item["historical_boundary"],
            "why_unrecoverable": WHY_UNRECOVERABLE[item["finding_id"]],
            "current_path_safe": item["current_supported_path_safe"],
            "external_dependency": item["external_governance"],
            "governance_action": (
                "Preserve the explicit unavailable boundary; retain consumed authority "
                "where legally and technically permitted."
            ),
            "code_action_remaining": "none",
        }
        for item in partials
    ]
    return {
        "schema_version": "t16e-post-phase7-residual-handoff-v1",
        "source_task": "T16E",
        "phase7_complete": True,
        "active_supported_current_defects": 0,
        "residual_count": len(residuals),
        "residuals": residuals,
        "status": "GOVERNANCE_ONLY_NO_CODE_REMEDIATION",
    }


def write_artifacts() -> None:
    rows = finding_snapshot()
    counts = Counter(row["status"] for row in rows)
    if counts != Counter({"CLOSED": 63, "PARTIAL": 7}):
        raise ValueError(f"unexpected Phase-7 finding summary: {counts}")
    with PHASE7_SNAPSHOT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    RESIDUAL_HANDOFF.write_text(
        json.dumps(residual_handoff(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    write_artifacts()
