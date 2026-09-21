from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "docs" / "remediation" / "calculation-lineage"
ALLOWED_STATES = {"CLOSED", "PARTIAL", "OPEN", "OUT_OF_SCOPE"}


def evaluate_handoff_conservation() -> dict[str, Any]:
    t15a = _json("T15A_phase6_handoff.json")
    assignments = {
        task: tuple(section["finding_ids"])
        for task, section in t15a["decomposition"].items()
    }
    assigned = set().union(*(set(values) for values in assignments.values()))
    if sum(map(len, assignments.values())) != len(assigned):
        raise ValueError("T15A finding assignment overlaps between downstream tasks")

    historical_handoff = _json("T15C_T15D_exact_handoff.json")
    historical_t15d = {
        finding_id
        for family in historical_handoff["t15d_operation_families"]
        for finding_id in family["finding_ids"]
    }
    remaining = historical_handoff["remaining_findings"]
    for value in remaining.values():
        historical_t15d.update(value if isinstance(value, list) else [value])
    phase5_status = {
        row["finding_id"]: row["status"]
        for row in _csv("T14E_finding_status_after_phase5.csv")
    }
    historically_lost = sorted(
        finding_id
        for finding_id in assignments["T15D"]
        if phase5_status.get(finding_id) == "OPEN" and finding_id not in historical_t15d
    )

    handoff = _json("T15D_T15E_exact_handoff.json")
    final_rows = handoff["findings"]
    final_ids = [row["finding_id"] for row in final_rows]
    if len(final_ids) != len(set(final_ids)):
        raise ValueError("Corrected T15E handoff contains duplicate finding IDs")
    invalid = sorted(
        row["finding_id"] for row in final_rows if row["status"] not in ALLOWED_STATES
    )
    if invalid:
        raise ValueError(f"Invalid downstream states: {invalid}")
    for row in final_rows:
        if row["status"] == "PARTIAL" and not row["residual"]:
            raise ValueError(f"PARTIAL finding lacks residual: {row['finding_id']}")
        if row["status"] == "OPEN" and not row["next_phase"]:
            raise ValueError(f"OPEN finding lacks next task: {row['finding_id']}")

    lost = sorted(assigned - set(final_ids))
    assigned_rows = [row for row in final_rows if row["finding_id"] in assigned]
    counts = Counter(row["status"] for row in assigned_rows)
    transitions = {
        "T15A_to_assigned_tasks": {
            "assigned": len(assigned),
            "accounted": len(assigned),
            "lost": [],
        },
        "T15B_to_downstream": _transition(assignments["T15B"], final_ids),
        "T15C_to_downstream": _transition(assignments["T15C"], final_ids),
        "T15C_to_T15D_historical": {
            "assigned": len(assignments["T15D"]),
            "lost_findings_detected": historically_lost,
            "status": "HISTORICAL_OMISSION_PRESERVED",
        },
        "T15D_to_T15E_corrected": _transition(assignments["T15D"], final_ids),
    }
    return {
        "invariant": "INV-HANDOFF-001",
        "total_assigned_findings": len(assigned),
        "closed_findings": counts["CLOSED"],
        "partial_findings": counts["PARTIAL"],
        "open_handed_forward_findings": counts["OPEN"],
        "out_of_scope_findings": counts["OUT_OF_SCOPE"],
        "lost_findings_detected_historically": historically_lost,
        "lost_finding_ids": lost,
        "unaccounted_assigned_findings": lost,
        "transitions": transitions,
        "conservation_status": "PASS" if not lost else "FAIL",
    }


def _transition(source_ids: tuple[str, ...], final_ids: list[str]) -> dict[str, Any]:
    lost = sorted(set(source_ids) - set(final_ids))
    return {"assigned": len(source_ids), "accounted": len(source_ids) - len(lost), "lost": lost}


def _json(name: str) -> dict[str, Any]:
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def _csv(name: str) -> list[dict[str, str]]:
    with (ARTIFACTS / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    result = evaluate_handoff_conservation()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["conservation_status"] == "PASS" else 1)
