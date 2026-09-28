from __future__ import annotations

import json
from pathlib import Path

from scripts.check_architecture_registry import validate_registry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "architecture_registry.json"


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_ceri_remediation_architecture_entries_are_precisely_registered() -> None:
    registry = _registry()
    tables = {row["table"]: row for row in registry["tables"]}
    manifest = tables["ceri_feature_source_manifests"]
    assert manifest["writer_ids"] == ["WRITE-074"]
    assert manifest["authority"] == "FEATURE_BATCH_CALCULATION_CONTEXT_RETRY_AUTHORITY"

    candidates = {
        (row["path"], row["function"], row["kind"]): row
        for row in registry["writer_candidates"]
    }
    assert candidates[
        (
            "app/services/ceri/source_manifest_service.py",
            "freeze_or_verify_feature_source_manifest",
            "add",
        )
    ]["writer_ids"] == ["WRITE-074"]
    assert candidates[
        (
            "app/services/pipeline_service.py",
            "_block_queued_ceri_siblings",
            "state_assignment",
        )
    ]["writer_ids"] == ["WRITE-006"]
    false_positive = candidates[
        (
            "app/services/source_mutation_authority.py",
            "__init__",
            "add",
        )
    ]
    assert false_positive["writer_ids"] == []
    assert "in-memory" in false_positive["reason"]

    transactions = {
        (row["path"], row["function"], row["primitive"]): row
        for row in registry["transaction_sites"]
    }
    manifest_tx = transactions[
        (
            "app/services/ceri/source_manifest_service.py",
            "freeze_or_verify_feature_source_manifest",
            "flush",
        )
    ]
    assert "TX-27" in manifest_tx["reason"]

    transitions = {row["id"]: row for row in registry["state_transitions"]}
    sibling_transition = transitions["STATE-071"]
    assert "QUEUED/STALLED/RECOVERING -> BLOCKED" in sibling_transition["transition"]
    assert "CERI_PARENT_PIPELINE_TERMINAL" in sibling_transition["authority"]


def test_architecture_admission_checker_has_no_findings() -> None:
    findings, _counts = validate_registry(_registry(), root=ROOT)
    assert findings == []
