from __future__ import annotations

# ruff: noqa: E501
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs" / "remediation" / "calculation-lineage"


def _json(name: str):
    return json.loads((ARTIFACTS / name).read_text())


def test_t15d_certificate_exactly_reconciles_machine_handoff() -> None:
    handoff = _json("T15C_T15D_exact_handoff.json")
    certificate = _json("T15D_provenance_algorithm_certification.json")
    assigned = set(handoff["t15d_operation_family_ids"])
    certified = {row["operation_family_id"] for row in certificate["operation_families"]}

    assert len(assigned) == 7
    assert certified == assigned
    assert certificate["summary"] == {
        "assigned": 7,
        "reconciled": 7,
        "unreconciled": 0,
        "defect": 0,
        "unknown": 0,
    }
    assert certificate["findings"]["open"] == []
    assert certificate["verdict"] == "PASS"


def test_t15d_finding_matrix_has_no_open_post_status() -> None:
    with (ARTIFACTS / "T15D_finding_remediation_matrix.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    assignment = _json("T15A_phase6_handoff.json")["decomposition"]["T15D"]["finding_ids"]
    assert {row["finding_id"] for row in rows} == set(assignment)
    assert {row["post_t15d_status"] for row in rows} <= {"CLOSED", "PARTIAL"}
    assert not [row for row in rows if row["post_t15d_status"] == "OPEN"]
    assert all(row["residual"] for row in rows if row["post_t15d_status"] == "PARTIAL")


def test_t15d_report_and_delta_artifact_are_complete() -> None:
    report = (ARTIFACTS / "T15D_provenance_algorithm_remediation.md").read_text()
    sections = [line for line in report.splitlines() if line.startswith("## ")]
    delta = _json("T15D_algorithm_delta_certification.json")

    assert len(sections) == 34
    assert sections[-1] == "## 34. Final verdict"
    assert "T15D CONTINUATION PASS" in report
    assert {row["finding_id"] for row in delta["changes"]} == {
        "CERI-001",
        "CERI-002",
        "CERI-005",
        "CERI-006",
        "CERI-007",
        "CERI-009",
        "CERI-011",
        "CORE-002",
        "CORE-003",
        "CORE-004",
        "RANK-008",
    }
    assert delta["unexpected_numeric_changes"] == []
    assert delta["verdict"] == "PASS"


def test_t15e_handoff_covers_every_t15a_assigned_finding() -> None:
    handoff = _json("T15D_T15E_exact_handoff.json")
    assignment = _json("T15A_phase6_handoff.json")["decomposition"]
    assigned = set().union(*(set(section["finding_ids"]) for section in assignment.values()))
    rows = handoff["findings"]

    assert {row["finding_id"] for row in rows} == assigned
    assert all(
        {"finding_id", "status", "evidence", "residual", "current_supported_path_safe", "next_phase"}
        <= set(row)
        for row in rows
    )
    assert handoff["summary"] == {
        "assigned": 25,
        "accounted": 25,
        "closed": 19,
        "partial": 6,
        "open": 0,
        "out_of_scope": 0,
        "lost": 0,
    }
    assert handoff["unaccounted"] == []


def test_phase6_handoff_conservation_and_partial_classification() -> None:
    from scripts.docs.check_phase6_handoff_conservation import (
        evaluate_handoff_conservation,
    )

    result = evaluate_handoff_conservation()
    reconciliation = _json("T15D_phase6_handoff_reconciliation.json")
    with (ARTIFACTS / "T15D_phase6_partial_classification.csv").open(newline="") as handle:
        partials = list(csv.DictReader(handle))

    assert result["conservation_status"] == "PASS"
    assert result["lost_finding_ids"] == []
    assert result["unaccounted_assigned_findings"] == []
    assert result["lost_findings_detected_historically"] == [
        "CERI-005",
        "CERI-006",
        "CERI-009",
        "CERI-011",
    ]
    assert reconciliation["unaccounted_findings"] == []
    assert len(partials) == 15
    assert not [
        row
        for row in partials
        if row["current_status"] == "PARTIAL"
        and row["supported-current path affected?"] == "YES"
    ]
