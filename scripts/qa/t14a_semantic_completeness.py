"""Finite semantic closure, with raw counts explicitly outside PASS gates."""

from __future__ import annotations

import hashlib
import json

from scripts.qa.t14a_mutation_inventory import ROOT


def review_source_status(root=ROOT):
    path = root / "docs/remediation/calculation-lineage/T14A_semantic_review_source_pins.json"
    adoption = root / "docs/remediation/calculation-lineage/T14B_semantic_review_source_pins.json"
    if adoption.exists():
        path = adoption
    decision_adoption = (
        root / "docs/remediation/calculation-lineage/T14C_semantic_review_source_pins.json"
    )
    if decision_adoption.exists():
        path = decision_adoption
    if not path.exists():
        return ["MISSING_REVIEW_SOURCE_PINS"]
    retained = json.loads(path.read_text(encoding="utf-8"))
    stale = []
    raw_digest = retained.get("raw_discovery_sha256")
    if raw_digest:
        raw = root / "docs/remediation/calculation-lineage/T14A_raw_discovery.json"
        if not raw.exists() or hashlib.sha256(raw.read_bytes()).hexdigest() != raw_digest:
            stale.append("RAW_DISCOVERY_CHANGED")
    for relative, expected in retained["sources"].items():
        source = root / relative
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            stale.append(relative)
    return stale


def semantic_completeness(inventory):
    from scripts.qa.t14a_semantic_families import edge_closure, normalize

    normalized = normalize(inventory)
    edges = edge_closure(inventory, normalized)
    blockers = {**normalized["blockers"], "missing_or_stale_source_review": review_source_status()}
    for field in ("sink", "writer_family", "entry_family"):
        blockers["raw_edges_with_unidentified_" + field] = [
            (r["caller"], r["target"]) for r in edges if r["unidentified_" + field]
        ]
    inventory["semantic_normalization"] = normalized
    inventory["normalized_inferred_edges"] = edges
    blocked = any(blockers.values())
    return {
        "verdict": "FAIL" if blocked else "PASS",
        "blockers": blockers,
        "actual_semantic_writer_count": None
        if blocked
        else normalized["counts"]["writer_families"],
        "provisional_writer_family_count": normalized["counts"]["writer_families"],
        "provisional_entry_family_count": normalized["counts"]["entry_families"],
        "actual_production_entrypoint_count": None
        if blocked
        else normalized["counts"]["entry_families"],
        "certification_unit": "WRITER_OWNERSHIP_AND_INITIATOR_DISPOSITION_FOUNDATION",
        "caller_authority_adoption_is_pass_gate": False,
        "raw_path_or_edge_count_is_pass_gate": False,
    }
