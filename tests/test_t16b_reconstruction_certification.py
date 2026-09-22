from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs" / "remediation" / "calculation-lineage"
ASSIGNED = {
    "CORE-009",
    "CERI-008",
    "SETUP-004",
    "SETUP-006",
    "SETUP-007",
    "SETUP-010",
}


def _json(name: str):
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def _sha256(name: str) -> str:
    return hashlib.sha256((ARTIFACTS / name).read_bytes()).hexdigest()


def test_t16a_inputs_remain_byte_identical():
    assert {
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
    } == {
        name: _sha256(name)
        for name in (
            "T16A_original_context_reconstruction_foundation.md",
            "T16A_reconstruction_feasibility_inventory.json",
            "T16A_historical_authority_matrix.csv",
            "T16A_phase7_handoff.json",
        )
    }


def test_certificate_is_complete_and_fail_closed():
    certificate = _json("T16B_setup_lifecycle_alert_reconstruction_certification.json")
    assert certificate["verdict"] == "PASS"
    assert set(certificate["handoff"]["assigned"]) == ASSIGNED
    assert set(certificate["handoff"]["reconciled"]) == ASSIGNED
    assert certificate["handoff"]["lost"] == 0
    assert certificate["manifest_requirements"]["exact_material_authority_required"] is True
    assert certificate["manifest_requirements"]["current_state_substitution_allowed"] is False
    assert certificate["manifest_requirements"]["silent_mode_downgrade_allowed"] is False
    assert certificate["manifest_requirements"]["resolution_side_effects"] == "NONE"
    assert all(
        certificate["native_exact_support"][domain]["supported"]
        for domain in ("setup", "lifecycle", "alert")
    )
    assert certificate["persistence"]["added"] is False
    assert certificate["production_runtime_mutations"] == "NONE"


def test_finding_reconciliation_and_handoff_conserve_exact_scope():
    with (ARTIFACTS / "T16B_finding_reconciliation.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        reconciliation = list(csv.DictReader(handle))
    handoff = _json("T16B_phase7_handoff.json")
    assert {row["finding_id"] for row in reconciliation} == ASSIGNED
    assert {row["finding_id"] for row in handoff["findings"]} == ASSIGNED
    assert handoff["conservation"] == {
        "assigned": 6,
        "reconciled": 6,
        "resolved": 2,
        "partial_with_exact_residual": 4,
        "transferred_to_t16c": 0,
        "transferred_to_t16d": 0,
        "lost": 0,
        "lost_finding_ids": [],
        "status": "PASS_INV_HANDOFF_001",
    }
    assert all(row["post_t16b_status"] in {"CLOSED", "PARTIAL"} for row in reconciliation)


def test_legacy_boundary_is_explicit_and_never_claims_legacy_exactness():
    with (ARTIFACTS / "T16B_legacy_reconstruction_boundaries.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert set(rows[0]) == {
        "domain",
        "artifact_type",
        "required_dimension",
        "availability",
        "reason_unavailable",
        "exact_reconstruction_possible",
        "current_rules_possible",
        "future_new_rows_safe",
        "finding_ids",
    }
    legacy = [row for row in rows if row["artifact_type"].startswith("legacy")]
    assert legacy
    assert all(row["exact_reconstruction_possible"] == "false" for row in legacy)
    assert all(row["reason_unavailable"].strip() for row in legacy)


def test_report_contains_all_required_sections():
    report = (ARTIFACTS / "T16B_setup_lifecycle_alert_reconstruction.md").read_text(
        encoding="utf-8"
    )
    for section in range(1, 33):
        assert f"## {section}." in report


def test_resolver_has_no_negative_dependency_or_current_projection_imports():
    source = (
        ROOT / "app" / "services" / "setup_lifecycle" / "original_context_reconstruction.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "app.services.ceri",
        "app.services.winner_probability",
        "    PriceBar,",
        "    SetupLifecycleEpisode,",
        "    SetupSignalSnapshotCurrentSelection,",
        "SignalAlertRule,",
        "    SignalAlertEvent,",
    )
    assert all(item not in source for item in forbidden)
