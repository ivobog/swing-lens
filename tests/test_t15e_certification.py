from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from scripts.docs.build_t15e_artifacts import (
    BASELINES,
    FAILED_RUN_HASHES,
    corrected_findings,
)
from scripts.qa.committed_source_identity import (
    classify_legacy_checkout_hash,
    source_freeze,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs" / "remediation" / "calculation-lineage"


def _json(name: str):
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def test_t15e_corrected_source_freeze_and_artifact_integrity() -> None:
    certificate = _json("T15E_phase6_integration_certification.json")
    # Historical certificate creation happened in 2bafa33 while its recorded
    # HEAD was the parent T15D commit. Validate the committed artifact tree,
    # not whichever later revision pytest happens to run from.
    freeze = source_freeze(ROOT, "2bafa33")

    assert certificate["baselines"] == BASELINES
    assert certificate["source_freeze"]["head"] == BASELINES["T15D"]
    assert certificate["source_freeze"]["source_file_count"] == freeze["implementation_count"]
    assert certificate["source_freeze"]["test_file_count"] == freeze["test_count"]
    assert certificate["source_sha256"] == certificate["source_freeze"]["source_sha256"]
    assert certificate["test_source_sha256"] == certificate["source_freeze"]["test_source_sha256"]
    assert certificate["previous_failed_run"]["forensic_artifact_hashes"] == (FAILED_RUN_HASHES)
    for name, expected in certificate["artifact_hashes"].items():
        classify_legacy_checkout_hash(
            ROOT, BASELINES["T15D"], f"docs/remediation/calculation-lineage/{name}", expected
        )


def test_t15e_snapshot_is_fresh_complete_and_has_no_open_findings() -> None:
    with (ARTIFACTS / "T15E_finding_status_after_phase6.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))

    assert rows == corrected_findings()
    assert len(rows) == len({row["finding_id"] for row in rows}) == 70
    assert Counter(row["status"] for row in rows) == Counter({"CLOSED": 58, "PARTIAL": 12})
    assert not [row for row in rows if row["status"] == "OPEN"]


def test_t15e_machine_certificate_is_complete_and_phase6_passes() -> None:
    certificate = _json("T15E_phase6_integration_certification.json")
    required = {
        "handoff_conservation_status",
        "phase6_assigned_finding_count",
        "accounted_finding_count",
        "lost_finding_count",
        "active_current_partial_defect_count",
        "recovered_ceri_findings",
    }

    assert required <= set(certificate)
    assert certificate["phase6_certified"] is True
    assert certificate["final_verdict"] == "PASS_PHASE6_OVERALL_CERTIFIED"
    assert certificate["phase6_assigned_finding_count"] == 25
    assert certificate["accounted_finding_count"] == 25
    assert certificate["lost_finding_count"] == 0
    assert certificate["lost_finding_ids"] == []
    assert certificate["active_current_partial_defect_count"] == 0
    assert certificate["recovered_ceri_findings"] == {
        "CERI-005": "CLOSED",
        "CERI-006": "CLOSED",
        "CERI-009": "CLOSED",
        "CERI-011": "CLOSED",
    }
    assert certificate["finding_summary"] == {
        "CLOSED": 58,
        "OPEN": 0,
        "PARTIAL": 12,
        "TOTAL": 70,
        "open_ids": [],
        "partial_ids": [
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
        ],
    }
    assert certificate["unexpected_algorithm_deltas"] == []
    assert all(
        section["status"] == "PASS"
        for section in certificate["test_evidence"].values()
        if isinstance(section, dict) and "status" in section
    )


def test_t15e_phase7_handoff_preserves_safe_boundaries() -> None:
    handoff = _json("T15E_phase7_exact_handoff.json")
    certificate = _json("T15E_phase6_integration_certification.json")

    assert handoff["status"] == "READY_FOR_PHASE_7"
    assert handoff["finding_count"] == 12
    assert {row["finding_id"] for row in handoff["findings"]} == set(
        certificate["finding_summary"]["partial_ids"]
    )
    assert all(row["supported_current_path_safe"] for row in handoff["findings"])
    assert {"SETUP-006", "SETUP-007", "SETUP-010"} <= set(handoff["primary_phase7_targets"])
    assert "WIN-006" in handoff["explicit_unavailable_boundaries"]
    assert "XINT-010" in handoff["external_governance_targets"]
    assert "current configuration" in handoff["hard_boundary"]
