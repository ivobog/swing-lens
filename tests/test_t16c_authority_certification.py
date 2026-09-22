from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs/remediation/calculation-lineage"

EXPECTED_HASHES = {
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
}


def _json(name: str):
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def test_t16a_and_t16b_authoritative_artifacts_are_unchanged() -> None:
    for name, expected in EXPECTED_HASHES.items():
        assert hashlib.sha256((DOCS / name).read_bytes()).hexdigest() == expected


def test_certificate_is_complete_and_closes_exactly_xint_007() -> None:
    certificate = _json("T16C_cross_domain_authority_certification.json")
    required = {
        "finding",
        "dependency_edges",
        "manifest_contract",
        "compatibility_contracts",
        "exact_edges",
        "legacy_unavailable_edges",
        "same_value_attack",
        "temporal_attack",
        "config_attack",
        "source_substitution_attack",
        "prospective_reconstructability",
        "tests",
        "status",
    }
    assert required <= set(certificate)
    assert certificate["verdict"] == "PASS"
    assert certificate["finding"]["assigned"] == ["XINT-007"]
    assert certificate["finding"]["reconciled"] == ["XINT-007"]
    assert certificate["finding"]["lost"] == 0
    assert certificate["finding"]["status"] == "CLOSED"
    assert certificate["dependency_edges"] == {
        "matrix": "T16C_cross_domain_compatibility_matrix.csv",
        "real_edges": 31,
        "forbidden_absent_edges": 7,
        "unknown_edges": 0,
        "canonical_graph_unchanged": True,
    }


def test_report_has_all_required_numbered_sections() -> None:
    report = (DOCS / "T16C_cross_domain_authority_composition.md").read_text(encoding="utf-8")
    required_titles = [
        "Executive verdict",
        "Baselines",
        "XINT-007 original finding",
        "T16A/T16B handoff",
        "Historical dependency graph",
        "Proof-boundary model",
        "Manifest composition",
        "Compatibility contracts",
        "Temporal compatibility",
        "Configuration compatibility",
        "Scope compatibility",
        "Source-lineage compatibility",
        "Readiness compatibility",
        "Predecessor compatibility",
        "Schema/algorithm compatibility",
        "Fundamental→Combined",
        "Technical→Combined",
        "Ranking inputs",
        "Ranking→Sector",
        "Setup composition",
        "Winner composition",
        "Negative dependencies",
        "Legacy behavior",
        "Prospective reconstructability",
        "Performance",
        "PostgreSQL certification",
        "Phase-6 regression",
        "XINT-007 reconciliation",
        "T16D handoff impact",
        "Residual risks",
        "Final verdict",
    ]
    for number, title in enumerate(required_titles, 1):
        assert f"## {number}. {title}" in report


def test_t16d_handoff_conserves_its_five_findings_without_transfer() -> None:
    handoff = _json("T16C_phase7_handoff.json")
    assert handoff["conservation"]["assigned"] == 1
    assert handoff["conservation"]["reconciled"] == 1
    assert handoff["conservation"]["lost"] == 0
    assert handoff["t16d"]["findings"] == [
        "CORE-005",
        "CERI-010",
        "RANK-007",
        "WIN-006",
        "XINT-010",
    ]
    assert handoff["t16d"]["transferred_from_t16c"] == []


def test_contract_document_formalizes_both_new_invariants() -> None:
    contract = (
        ROOT / "docs/architecture/SWINGLENS_ORIGINAL_CONTEXT_RECONSTRUCTION_CONTRACT.md"
    ).read_text(encoding="utf-8")
    assert "`INV-RECONSTRUCT-004`" in contract
    assert "`INV-PROOF-001`" in contract
