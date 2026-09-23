from __future__ import annotations

# ruff: noqa: E501
import csv
import json
from pathlib import Path

from scripts.qa.committed_source_identity import classify_legacy_checkout_hash

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs" / "remediation" / "calculation-lineage"
T15A_COMMIT = "8188f1ac3e7e3a8f93ff75c2cf10019d9c5935f8"
T15B_COMMIT = "0c8e5664c52ff19d80d30f33c64ade8772c955a8"


def test_t15c_certificate_is_complete_and_matches_t15a_assignment() -> None:
    inventory = json.loads((ARTIFACTS / "T15A_scope_operation_inventory.json").read_text())
    assigned = {
        row["operation_family_id"]
        for row in inventory["records"]
        if row["phase6_target_task"] == "T15C"
    }
    certificate = json.loads((ARTIFACTS / "T15C_winner_scope_truth_certification.json").read_text())
    certified = {row["operation_family_id"] for row in certificate["operation_families"]}
    with (ARTIFACTS / "T15C_operation_reconciliation.csv").open(newline="") as handle:
        reconciled = {row["operation_family_id"] for row in csv.DictReader(handle)}

    assert len(assigned) == 15
    assert certified == reconciled == assigned
    assert certificate["verdict"] == "PASS"
    assert certificate["summary"]["unreconciled_operation_families"] == 0
    assert certificate["summary"]["scope_refresh_defects"] == 0
    assert certificate["findings"]["WIN-008"] == "CLOSED"
    assert certificate["findings"]["WIN-006"].startswith("PARTIAL_")


def test_t15c_report_has_exact_required_section_topology() -> None:
    report = (ARTIFACTS / "T15C_winner_scope_truth_adoption.md").read_text()
    assert sum(line.startswith("## ") and line[3:4].isdigit() for line in report.splitlines()) == 32
    assert "## 32. Final verdict" in report
    assert "T15C VERDICT: PASS" in report


def test_t15a_and_t15b_handoff_artifacts_match_committed_history() -> None:
    expected = {
        "T15A_scope_refresh_identity_foundation.md": "a53c563661e4883f77bd190fd97689e73705894eb37f9ead85338fdc6a8525e0",
        "T15A_scope_operation_inventory.json": "caddc1a79b84bd1fd81ab131fdfE062188fe94d9ae88c45757c261f38cd2c5e4".lower(),
        "T15A_phase6_handoff.json": "54e4e702fc9eddc1b38a4b59ada60e4691fbe9a7dcb687382cdf5894b1bf5702",
        "T15B_pipeline_ceri_scope_refresh_adoption.md": "dedd42460a41f5647f2a080e36e6415d0ba8d85fe6a5c7af8ff7290cff524a08",
        "T15B_pipeline_ceri_scope_refresh_certification.json": "a41b3632ccbb90cfb5126b20edb6a5b7ccc9f64956c40f9ed7b05a3003cf86f3",
    }
    for name, digest in expected.items():
        revision = T15A_COMMIT if name.startswith("T15A_") else T15B_COMMIT
        materialization = classify_legacy_checkout_hash(
            ROOT, revision, f"docs/remediation/calculation-lineage/{name}", digest
        )
        assert materialization == (
            "GIT_CHECKOUT_AUTOCRLF_TRUE"
            if name == "T15B_pipeline_ceri_scope_refresh_certification.json"
            else "GIT_COMMITTED_BLOB"
        )
