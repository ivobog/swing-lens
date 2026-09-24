from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from app.services.canonical_evidence import CanonicalEvidenceSerializer
from scripts.qa.committed_source_identity import classify_legacy_checkout_hash

ROOT = Path(__file__).resolve().parents[1]
LINEAGE = ROOT / "docs" / "remediation" / "calculation-lineage"
INVENTORY_PATH = LINEAGE / "T16A_reconstruction_feasibility_inventory.json"
HANDOFF_PATH = LINEAGE / "T16A_phase7_handoff.json"
MATRIX_PATH = LINEAGE / "T16A_historical_authority_matrix.csv"
VALIDATION_PATH = LINEAGE / "T16A_validation_summary.json"
T15E_HANDOFF_PATH = LINEAGE / "T15E_phase7_exact_handoff.json"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_t16a_conserves_the_exact_t15e_phase7_finding_set() -> None:
    source = _json(T15E_HANDOFF_PATH)
    inventory = _json(INVENTORY_PATH)
    source_ids = {row["finding_id"] for row in source["findings"]}
    records = inventory["records"]
    inventory_ids = {row["finding_id"] for row in records}

    assert source["finding_count"] == 12
    assert len(records) == len(inventory_ids) == 12
    assert inventory_ids == source_ids
    assert inventory["accounted_count"] == 12
    assert inventory["lost_count"] == 0
    assert inventory["unknown_reconstructability_count"] == 0


def test_every_inventory_record_has_complete_typed_disposition() -> None:
    inventory = _json(INVENTORY_PATH)
    allowed_classes = {
        "EXACTLY_RECONSTRUCTABLE",
        "BOUNDED_RECONSTRUCTABLE",
        "CURRENT_RULES_ONLY",
        "EVIDENCE_INCOMPLETE",
        "PERMANENTLY_UNAVAILABLE",
        "EXTERNAL_GOVERNANCE_ONLY",
    }
    required = {
        "finding_id",
        "operation_family_ids",
        "target_artifact",
        "required_authority",
        "available_authority",
        "unavailable_authority",
        "reconstruction_class",
        "current_safe_mode",
        "target_task",
        "canonical_finding_status",
    }
    for row in inventory["records"]:
        assert required <= set(row)
        assert row["operation_family_ids"]
        assert row["required_authority"]
        assert row["reconstruction_class"] in allowed_classes
        assert row["target_task"] in {"T16B", "T16C", "T16D"}
        assert row["canonical_finding_status"] == "PARTIAL"


def test_classification_counts_are_derived_and_unknown_is_impossible() -> None:
    inventory = _json(INVENTORY_PATH)
    derived: dict[str, int] = {}
    for row in inventory["records"]:
        classification = row["reconstruction_class"]
        derived[classification] = derived.get(classification, 0) + 1

    assert derived == {
        key: value for key, value in inventory["classification_counts"].items() if value
    }
    assert sum(inventory["classification_counts"].values()) == 12
    assert "UNKNOWN" not in inventory["classification_counts"]


def test_t16a_handoff_assigns_each_finding_exactly_once() -> None:
    handoff = _json(HANDOFF_PATH)
    assigned = [
        row["finding_id"] for task in ("t16b", "t16c", "t16d") for row in handoff[task]["findings"]
    ]
    inventory_ids = {row["finding_id"] for row in _json(INVENTORY_PATH)["records"]}

    assert len(assigned) == len(set(assigned)) == 12
    assert set(assigned) == inventory_ids
    assert handoff["conservation"] == {
        "phase7_routed_findings": 12,
        "accounted": 12,
        "lost": 0,
        "lost_finding_ids": [],
        "unknown_reconstructability": 0,
        "status": "PASS_INV_HANDOFF_001",
    }


def test_handoff_and_inventory_agree_on_required_machine_fields() -> None:
    inventory = {row["finding_id"]: row for row in _json(INVENTORY_PATH)["records"]}
    handoff = _json(HANDOFF_PATH)
    compared = (
        "operation_family_ids",
        "target_artifact",
        "required_authority",
        "reconstruction_class",
        "target_task",
        "canonical_finding_status",
    )
    for task in ("t16b", "t16c", "t16d"):
        for row in handoff[task]["findings"]:
            source = inventory[row["finding_id"]]
            for field in compared:
                assert row[field] == source[field], (row["finding_id"], field)


def test_authority_matrix_has_one_row_per_finding_and_matches_classification() -> None:
    rows = list(csv.DictReader(MATRIX_PATH.open(encoding="utf-8-sig", newline="")))
    inventory = {row["finding_id"]: row for row in _json(INVENTORY_PATH)["records"]}

    assert len(rows) == len({row["finding_id"] for row in rows}) == 12
    assert {row["finding_id"] for row in rows} == set(inventory)
    for row in rows:
        assert row["reconstruction_class"] == inventory[row["finding_id"]]["reconstruction_class"]
        assert row["target_task"] == inventory[row["finding_id"]]["target_task"]


def test_all_mapped_operation_families_exist_in_certified_phase5_phase6_inventories() -> None:
    with (LINEAGE / "T14D_operation_family_matrix.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        certified = {row["operation_family_id"] for row in csv.DictReader(stream)}
    phase6_inventory = _json(LINEAGE / "T15A_scope_operation_inventory.json")
    certified.update(row["operation_family_id"] for row in phase6_inventory["records"])
    mapped = {
        family for row in _json(INVENTORY_PATH)["records"] for family in row["operation_family_ids"]
    }

    assert mapped <= certified


def test_inventory_fingerprint_is_deterministic() -> None:
    inventory = _json(INVENTORY_PATH)
    first = CanonicalEvidenceSerializer.fingerprint(inventory)
    second = CanonicalEvidenceSerializer.fingerprint(_json(INVENTORY_PATH))

    assert first == second
    assert len(first) == 64


def test_t15e_authoritative_artifacts_retain_captured_hashes() -> None:
    validation = _json(VALIDATION_PATH)["source_freeze"]

    for name, expected in (
        ("T15E_phase7_exact_handoff.json", validation["t15e_phase7_handoff_sha256"]),
        ("T15E_finding_status_after_phase6.csv", validation["t15e_finding_snapshot_sha256"]),
        ("T15E_phase6_integration_certification.json", validation["t15e_certificate_sha256"]),
    ):
        classify_legacy_checkout_hash(
            ROOT, "2bafa33", f"docs/remediation/calculation-lineage/{name}", expected
        )


def test_required_documents_define_proof_and_no_silent_downgrade_boundaries() -> None:
    architecture = (
        ROOT / "docs" / "architecture" / "SWINGLENS_ORIGINAL_CONTEXT_RECONSTRUCTION_CONTRACT.md"
    ).read_text(encoding="utf-8")
    report = (LINEAGE / "T16A_original_context_reconstruction_foundation.md").read_text(
        encoding="utf-8"
    )

    assert "Original-context reconstruction is a proof problem" in architecture
    assert "Current state may be used only when independently proven" in architecture
    assert "INV-RECONSTRUCT-001" in architecture
    assert "INV-RECONSTRUCT-002" in architecture
    assert "INV-RECONSTRUCT-003" in architecture
    assert "## 33. Final verdict" in report


def test_t16a_requires_no_migration_or_production_mutation() -> None:
    validation = _json(VALIDATION_PATH)

    assert validation["migration_required"] is False
    assert validation["migration_head"] == "0084_technical_recovery"
    assert validation["production_runtime_mutations"] == "NONE"
