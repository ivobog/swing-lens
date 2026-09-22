from __future__ import annotations

import ast
import csv
import hashlib
import json
from pathlib import Path

from app.services.historical_authority_retention import (
    FINDING_BOUNDARIES,
    RETENTION_REQUIREMENTS,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs/remediation/calculation-lineage"

EXPECTED_INPUT_HASHES = {
    "T16A_original_context_reconstruction_foundation.md": (
        "79cfa0057f5c627615f1df6cd058304c07abe8013074b0e715da4c5deeb7de12"
    ),
    "T16A_reconstruction_feasibility_inventory.json": (
        "31c1ea270cb3efb602140d7ff6fff7b276f07108e4a7836b131e7f2acbc9a44a"
    ),
    "T16A_historical_authority_matrix.csv": (
        "c33bbd862f9a268ee19585a68e1ca91d1ec53659306331fedfbd7d016a013ff4"
    ),
    "T16A_phase7_handoff.json": (
        "9f3d291b3e6a1f38bc85fd86f344a9f6e866ba360ad85765e01dfa6dd8f4edd7"
    ),
    "T16B_setup_lifecycle_alert_reconstruction.md": (
        "8fe13a52462e7566dcbde655b95c3cc877e442881549fb353fd42b578a33fd0d"
    ),
    "T16B_setup_lifecycle_alert_reconstruction_certification.json": (
        "43d827ea285150e120c87c03d7137afe37e115effd7115da5a03a3fb3cec34bf"
    ),
    "T16B_legacy_reconstruction_boundaries.csv": (
        "6069a6bc9ad350445794d82b966520472519677590e2b0512a18486db1e6d171"
    ),
    "T16B_phase7_handoff.json": (
        "5ee59c8a14c385f24165bbe7b7de1c0fd21180feaddb528a45b03f96623fc013"
    ),
    "T16C_cross_domain_authority_composition.md": (
        "a451d5a73ac14fc0a5e26b161ec2d7934123d71fbfbe669af845adcca27f90cb"
    ),
    "T16C_cross_domain_compatibility_matrix.csv": (
        "3b843b1f4a4ef3e1bd5df9c8be3e6edb6f25b528d277b415289161ed188c76e5"
    ),
    "T16C_cross_domain_authority_certification.json": (
        "2ffb18cec18d3f9c3799c3f7e406c93ae8234865d07633ba00ec871b3a24e66e"
    ),
    "T16C_phase7_handoff.json": (
        "0b15e29539abdb1fb458a1072c36f6e97c9777ece858ffa4222ddab254c6aa90"
    ),
}

ASSIGNED = {"CORE-005", "CERI-010", "RANK-007", "WIN-006", "XINT-010"}
T16E_SCOPE = {
    "CORE-005",
    "CORE-009",
    "CERI-008",
    "CERI-010",
    "RANK-007",
    "SETUP-004",
    "SETUP-006",
    "SETUP-007",
    "SETUP-010",
    "WIN-006",
    "XINT-007",
    "XINT-010",
}


def _json(name: str):
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def _csv(name: str) -> list[dict[str, str]]:
    with (ARTIFACTS / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_t16a_t16b_and_t16c_inputs_remain_byte_identical() -> None:
    actual = {
        name: hashlib.sha256((ARTIFACTS / name).read_bytes()).hexdigest()
        for name in EXPECTED_INPUT_HASHES
    }
    assert actual == EXPECTED_INPUT_HASHES


def test_retention_matrix_matches_the_machine_policy_without_unknowns() -> None:
    rows = _csv("T16D_retention_authority_matrix.csv")
    assert {row["authority_type"] for row in rows} == set(RETENTION_REQUIREMENTS)
    for row in rows:
        material, purge_policy = RETENTION_REQUIREMENTS[row["authority_type"]]
        assert row["material_to_reconstruction"] == material
        assert row["purge_policy"] == purge_policy
        assert "UNKNOWN" not in row.values()
        assert row["current_path_status"] == "CURRENT_PATH_CERTIFIED"


def test_finding_boundary_matrix_reconciles_exact_assigned_scope() -> None:
    rows = _csv("T16D_legacy_governance_boundaries.csv")
    by_id = {row["finding_id"]: row for row in rows}
    assert set(by_id) == ASSIGNED == set(FINDING_BOUNDARIES)
    for finding_id, boundary in FINDING_BOUNDARIES.items():
        assert by_id[finding_id]["final_status"] == boundary["status"]
        assert by_id[finding_id]["current_path_safe"] == "YES"
        assert by_id[finding_id]["reason"].strip()
        assert by_id[finding_id]["missing_authority"].strip()


def test_certificate_is_complete_fail_closed_and_zero_defect() -> None:
    certificate = _json("T16D_legacy_retention_governance_certification.json")
    required = {
        "assigned_findings",
        "finding_statuses",
        "archive_invariant",
        "retention_invariant",
        "purge_invariant",
        "prospective_reconstructability",
        "legacy_unavailable_boundaries",
        "external_governance_boundaries",
        "current_supported_defects",
        "tests",
        "T16E_handoff",
        "lost_findings",
        "status",
    }
    assert required <= set(certificate)
    assert certificate["verdict"] == "PASS"
    assert set(certificate["assigned_findings"]) == ASSIGNED
    assert set(certificate["finding_statuses"]) == ASSIGNED
    assert all(
        value["status"] in {"CLOSED", "PARTIAL"} and value["current_supported_path_safe"]
        for value in certificate["finding_statuses"].values()
    )
    assert certificate["current_supported_defects"] == 0
    assert certificate["lost_findings"] == 0
    assert certificate["migration_required"] is False
    assert certificate["production_runtime_mutations"] == "NONE"
    assert certificate["status"] == "CERTIFIED_READY_FOR_T16E"


def test_t16e_handoff_conserves_every_phase7_finding_exactly_once() -> None:
    handoff = _json("T16D_T16E_exact_handoff.json")
    findings = handoff["findings"]
    ids = [item["finding_id"] for item in findings]
    required_fields = {
        "finding_id",
        "status",
        "implementation_task",
        "historical_boundary",
        "current_supported_path_safe",
        "exact_reconstruction_support",
        "retrospective_support",
        "permanent_unavailability",
        "external_governance",
        "evidence",
        "T16E_tests",
    }
    assert len(ids) == len(set(ids)) == 12
    assert set(ids) == T16E_SCOPE
    assert all(required_fields <= set(item) for item in findings)
    assert all(item["status"] in {"CLOSED", "PARTIAL"} for item in findings)
    assert all(item["current_supported_path_safe"] for item in findings)
    assert handoff["assigned"] == handoff["accounted"] == 12
    assert handoff["lost"] == 0
    assert handoff["unaccounted"] == []
    assert handoff["current_supported_defects"] == 0


def test_report_has_all_required_numbered_sections() -> None:
    report = (ARTIFACTS / "T16D_legacy_retention_governance_boundaries.md").read_text(
        encoding="utf-8"
    )
    titles = [
        "Executive verdict",
        "Baselines",
        "T16C handoff",
        "Finding reconciliation",
        "Historical vs prospective correctness",
        "CORE-005",
        "CERI-010",
        "RANK-007",
        "WIN-006",
        "XINT-010",
        "Archive contract",
        "Retention contract",
        "Purge semantics",
        "Configuration retention",
        "Source-evidence retention",
        "PriceBar revision retention",
        "Provider governance",
        "Currency authority",
        "Permanent unavailability",
        "Current-rules retrospective boundary",
        "Prospective reconstructability",
        "Phase-7 invariants",
        "Performance",
        "PostgreSQL",
        "T16B/T16C regression",
        "Phase-6 regression",
        "Finding statuses",
        "T16E handoff",
        "Residual governance risks",
        "Final verdict",
    ]
    for number, title in enumerate(titles, 1):
        assert f"## {number}. {title}" in report


def test_architecture_contract_contains_all_t16d_invariants() -> None:
    contract = (
        ROOT / "docs/architecture/SWINGLENS_ORIGINAL_CONTEXT_RECONSTRUCTION_CONTRACT.md"
    ).read_text(encoding="utf-8")
    for invariant in ("INV-ARCHIVE-001", "INV-RETENTION-001", "INV-PURGE-001"):
        assert f"`{invariant}`" in contract


def test_classifier_has_no_current_state_or_environment_dependency() -> None:
    path = ROOT / "app/services/historical_authority_retention.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not {"os", "environ", "settings", "requests", "httpx"} & imports
    source = path.read_text(encoding="utf-8")
    assert "current_state:" not in source
    assert "current configuration" not in source.lower()


def test_runtime_integrations_keep_explicit_purge_and_revision_boundaries() -> None:
    purge = (ROOT / "app/services/ceri/purge_service.py").read_text(encoding="utf-8")
    winner = (ROOT / "app/services/winner_probability/outcome_authority.py").read_text(
        encoding="utf-8"
    )
    assert "assess_authority_purge(" in purge
    assert "explicit_reconstruction_downgrade=True" in purge
    assert "REVISION_IDENTITY_UNAVAILABLE" in winner
