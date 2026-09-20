from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.docs.build_t15b_artifacts import (
    CERTIFICATE_PATH,
    RECONCILIATION_PATH,
    build,
)

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = (
    ROOT / "docs" / "remediation" / "calculation-lineage" / "T15A_scope_operation_inventory.json"
)


def test_t15b_certificate_covers_exact_handoff_without_defects() -> None:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    expected = {
        row["operation_family_id"]
        for row in inventory["records"]
        if row["phase6_target_task"] == "T15B"
    }
    certificate = json.loads(CERTIFICATE_PATH.read_text(encoding="utf-8"))
    actual = {row["operation_family_id"] for row in certificate["operation_families"]}

    assert len(expected) == 19
    assert actual == expected
    assert certificate["counts"] == {
        "assigned_operation_families": 19,
        "assigned_operation_family_defects": 0,
        "reconciled_operation_families": 19,
        "unreconciled_operation_families": 0,
    }
    assert {row["status"] for row in certificate["operation_families"]} == {"CERTIFIED_ADOPTED"}


def test_t15b_reconciliation_and_certificate_are_deterministic() -> None:
    reconciliation, certificate = build()
    with RECONCILIATION_PATH.open(encoding="utf-8", newline="") as stream:
        retained_reconciliation = list(csv.DictReader(stream))
    retained_certificate = json.loads(CERTIFICATE_PATH.read_text(encoding="utf-8"))

    assert retained_reconciliation == reconciliation
    assert retained_certificate == certificate
