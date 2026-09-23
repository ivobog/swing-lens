"""Create deterministic bridges from legacy worktree pins to Git source identity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.qa.committed_source_identity import (
    classify_legacy_checkout_hash,
    committed_blob_sha256,
    source_freeze,
)

ROOT = Path(__file__).resolve().parents[2]
LINEAGE = ROOT / "docs/remediation/calculation-lineage"
RELEASE = ROOT / "docs/remediation/release"
BASE = "d5066e979ac3958c7e5a4cd125c08106856f1026"
PHASE5 = "f587b63e4e35e81486e53ffa0f13b9cd7369f963"
T15B = "0c8e5664c52ff19d80d30f33c64ade8772c955a8"
T15D = "c562f4bd6e723c8256b3d4a54c076e7f24fa786c"
T15E = "2bafa33e0a2cf869c1ac651ec8e2bf30a581c2ab"
T16D = "5a2f92b14f18303c6cb83295d14dc5c2b9dcdb86"


def _artifact(name: str) -> dict:
    return json.loads((LINEAGE / name).read_text(encoding="utf-8"))


def build() -> tuple[dict, dict]:
    phase6 = _artifact("T15E_phase6_integration_certification.json")
    phase7 = _artifact("T16E_phase7_integration_certification.json")
    phase5 = _artifact("T14D_operation_family_certification.json")
    phase7_freeze = source_freeze(ROOT, BASE)
    phase6_freeze = source_freeze(ROOT, T15E)
    phase5_freeze = source_freeze(ROOT, PHASE5)
    if (
        phase6_freeze["implementation_count"] != phase6["source_freeze"]["source_file_count"]
        or phase6_freeze["test_count"] != phase6["source_freeze"]["test_file_count"]
    ):
        raise ValueError("T15E committed path set does not match historical source counts")
    if (
        phase7_freeze["implementation_count"] != phase7["source_file_count"]
        or phase7_freeze["test_count"] != phase7["test_source_file_count"]
    ):
        raise ValueError("T16E committed path set does not match historical source counts")
    phase6_path = "docs/remediation/calculation-lineage/T15E_phase6_integration_certification.json"
    phase6_hash_mode = classify_legacy_checkout_hash(
        ROOT, T15E, phase6_path, phase7["phase6_certificate_sha256"]
    )
    restore = next(
        row
        for row in phase5["families"]
        if row["member_initiator_ids"] == ["EF_PRIVILEGED_DATABASE_RESTORE"]
    )
    restore_path = "scripts/ops/restore_postgres.ps1"
    restore_hash_mode = classify_legacy_checkout_hash(
        ROOT,
        PHASE5,
        restore_path,
        restore["inheritance_proofs"][0]["source"]["file_sha256"],
    )
    if phase6_hash_mode != "GIT_CHECKOUT_AUTOCRLF_TRUE" or (
        restore_hash_mode != "GIT_CHECKOUT_AUTOCRLF_TRUE"
    ):
        raise ValueError("Expected historical CRLF materialization was not demonstrated")
    t15b_path = (
        "docs/remediation/calculation-lineage/T15B_pipeline_ceri_scope_refresh_certification.json"
    )
    t15b_hash_mode = classify_legacy_checkout_hash(
        ROOT,
        T15B,
        t15b_path,
        "a41b3632ccbb90cfb5126b20edb6a5b7ccc9f64956c40f9ed7b05a3003cf86f3",
    )
    if t15b_hash_mode != "GIT_CHECKOUT_AUTOCRLF_TRUE":
        raise ValueError("Expected T15B handoff CRLF materialization was not demonstrated")
    records = [
        {
            "certificate": "T14D_operation_family_certification.json",
            "historical_hash_scheme": "LEGACY_WORKTREE_HASH; raw PowerShell file proof",
            "historical_commit": PHASE5,
            "artifact_commit": PHASE5,
            "portable": False,
            "canonical_current_validation": phase5_freeze["fingerprint_sha256"],
            "mismatch_reason": (
                "Privileged restore file hash used CRLF checkout bytes; committed blob unchanged"
            ),
            "legacy_materialization_proof": restore_hash_mode,
            "semantic_status": "REVALIDATED_BY_PHASE5_DERIVATIVE_AND_T16E",
        },
        {
            "certificate": "T15E_phase6_integration_certification.json",
            "historical_hash_scheme": "LEGACY_WORKTREE_HASH; path-null-file-sha256-newline-v1",
            "historical_commit": T15D,
            "artifact_commit": T15E,
            "portable": False,
            "canonical_current_validation": phase6_freeze["fingerprint_sha256"],
            "mismatch_reason": (
                "Recorded HEAD is the precommit T15D parent while path counts match the "
                "T15E artifact commit; mixed checkout bytes do not reproduce from a clean tree"
            ),
            "legacy_source_hash_reproduced": False,
            "legacy_source_sha256": phase6["source_sha256"],
            "legacy_test_sha256": phase6["test_source_sha256"],
            "semantic_status": "REVALIDATED_FROM_T15E_COMMITTED_TREE_AND_PHASE6_TESTS",
        },
        {
            "certificate": "T16E_phase7_integration_certification.json",
            "historical_hash_scheme": "LEGACY_WORKTREE_HASH; path-null-file-sha256-newline-v1",
            "historical_commit": T16D,
            "artifact_commit": BASE,
            "portable": False,
            "canonical_current_validation": phase7_freeze["fingerprint_sha256"],
            "mismatch_reason": (
                "Recorded frozen HEAD is the precommit T16D parent; path counts match "
                "terminal Phase-7 commit and checkout materialization is not canonical"
            ),
            "legacy_source_hash_reproduced": False,
            "legacy_source_sha256": phase7["source_sha256"],
            "legacy_test_sha256": phase7["test_source_sha256"],
            "semantic_status": "REVALIDATED_FROM_T16E_COMMITTED_TREE_AND_PHASE7_TESTS",
        },
        {
            "certificate": "T16A_validation_summary.json / T16E Phase-6 input pin",
            "historical_hash_scheme": "LEGACY_WORKTREE_HASH; artifact bytes",
            "historical_commit": T15E,
            "artifact_commit": T15E,
            "portable": False,
            "canonical_current_validation": committed_blob_sha256(ROOT, T15E, phase6_path),
            "mismatch_reason": "Stored Phase-6 artifact hash is CRLF checkout, not Git blob bytes",
            "legacy_materialization_proof": phase6_hash_mode,
            "semantic_status": "EXACT_COMMITTED_ARTIFACT_PROVEN_WITH_GIT_FILTER",
        },
        {
            "certificate": "T15C winner scope truth / T15B handoff pin",
            "historical_hash_scheme": "LEGACY_WORKTREE_HASH; raw JSON artifact bytes",
            "historical_commit": T15B,
            "artifact_commit": T15B,
            "portable": False,
            "canonical_current_validation": committed_blob_sha256(ROOT, T15B, t15b_path),
            "mismatch_reason": "Stored T15B handoff hash is CRLF checkout, not Git blob bytes",
            "legacy_materialization_proof": t15b_hash_mode,
            "semantic_status": "EXACT_COMMITTED_ARTIFACT_PROVEN_WITH_GIT_FILTER",
        },
    ]
    reconciliation = {
        "schema_version": "release-certificate-hash-reconciliation-v1",
        "certificate_types": [
            "HISTORICAL_IMMUTABLE_CERTIFICATE",
            "CURRENT_DERIVATIVE_REVALIDATION_CERTIFICATE",
            "CURRENT_RELEASE_CERTIFICATE",
        ],
        "canonical_source_freeze_version": 2,
        "canonical_scheme": "ordered Git committed blob IDs + commit SHA, SHA-256 aggregate",
        "terminal_main_commit": BASE,
        "terminal_main_fingerprint_sha256": phase7_freeze["fingerprint_sha256"],
        "phase5_fingerprint_sha256": phase5_freeze["fingerprint_sha256"],
        "phase6_artifact_commit_fingerprint_sha256": phase6_freeze["fingerprint_sha256"],
        "historical_certificates_preserved": True,
        "records": records,
    }
    return reconciliation, phase7_freeze


def write() -> None:
    reconciliation, freeze = build()
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("RELEASE_certificate_hash_reconciliation.json", reconciliation),
        ("RELEASE_source_freeze_v2.json", freeze),
    ):
        (RELEASE / name).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        write()
    else:
        reconciliation, _ = build()
        print(json.dumps(reconciliation, indent=2, sort_keys=True))
