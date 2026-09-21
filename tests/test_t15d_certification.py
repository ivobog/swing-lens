from __future__ import annotations

# ruff: noqa: E501
import csv
import hashlib
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

    assert len(rows) == 14
    assert {row["post_t15d_status"] for row in rows} <= {"CLOSED", "PARTIAL"}
    assert not [row for row in rows if row["post_t15d_status"] == "OPEN"]
    assert all(row["residual"] for row in rows if row["post_t15d_status"] == "PARTIAL")


def test_t15d_report_and_delta_artifact_are_complete() -> None:
    report = (ARTIFACTS / "T15D_provenance_algorithm_remediation.md").read_text()
    sections = [line for line in report.splitlines() if line.startswith("## ")]
    delta = _json("T15D_algorithm_delta_certification.json")

    assert len(sections) == 34
    assert sections[-1] == "## 34. Final verdict"
    assert "T15D PASS" in report
    assert {row["finding_id"] for row in delta["changes"]} == {
        "CERI-001",
        "CERI-002",
        "CORE-003",
        "CORE-004",
        "RANK-008",
    }
    assert delta["unexpected_numeric_changes"] == []
    assert delta["verdict"] == "PASS"


def test_t15e_handoff_covers_all_phase6_families_and_preserves_prior_artifacts() -> None:
    handoff = _json("T15D_T15E_exact_handoff.json")
    families = handoff["phase6_operation_families"]
    all_ids = [*families["T15B"], *families["T15C"], *families["T15D"]]
    expected_hashes = {
        "T15A_scope_operation_inventory.json": "caddc1a79b84bd1fd81ab131fdfe062188fe94d9ae88c45757c261f38cd2c5e4",
        "T15B_pipeline_ceri_scope_refresh_certification.json": "a41b3632ccbb90cfb5126b20edb6a5b7ccc9f64956c40f9ed7b05a3003cf86f3",
        "T15C_winner_scope_truth_certification.json": "b756ba75fc7e333be0b754e866db59721f8bbe194b7dc921fd015505b67062ec",
        "T15D_provenance_algorithm_certification.json": "b8c8edea568c14a55f3b00117044c7c5e2beae257bf8bcd6e507743bc35dcedf",
    }

    assert len(all_ids) == len(set(all_ids)) == families["count"] == 41
    assert handoff["phase6_summary"]["t15d_unreconciled"] == 0
    assert handoff["finding_status"]["open"] == []
    for name, expected in expected_hashes.items():
        assert hashlib.sha256((ARTIFACTS / name).read_bytes()).hexdigest() == expected
