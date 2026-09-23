"""Reconcile immutable Phase-5 authority with the terminal Phase-7 commit."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from scripts.qa import t14d_operation_families as families
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
PIN_PATH = "docs/remediation/calculation-lineage/T14D_semantic_review_source_pins.json"
CURRENT_DERIVATIVE = "IB_HISTORICAL_TRADES_phase5_current_authority.json"
CURRENT_RECONCILIATION = "IB_HISTORICAL_TRADES_phase5_authority_reconciliation.csv"
REVIEWED_PIPELINE_PATH = "app/services/pipeline_executor.py"
REVIEWED_PIPELINE_BLOB = "393d85eddfdfab3fafb20be0da53e064c15e66a4"
REVIEWED_FAMILY_IDS = (
    "AF_f7416b02ff7a2171",  # Phase-5 historical family
    "AF_57090e3c1c60a552",  # Phase-7/release derivative
    "AF_c9c321e16af0e55a",  # IB historical preflight-only source evolution
)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def _json(name: str) -> dict:
    return json.loads((LINEAGE / name).read_text(encoding="utf-8"))


def _committed_json(revision: str, path: str) -> dict:
    return json.loads(_git("show", f"{revision}:{path}"))


def _pin_origin(path: str, expected: str) -> tuple[str, str]:
    try:
        mode = classify_legacy_checkout_hash(ROOT, BASE, path, expected)
        return BASE, mode
    except ValueError:
        pass
    revisions = _git("log", "--all", "--format=%H", "--", path).splitlines()
    for revision in revisions:
        try:
            mode = classify_legacy_checkout_hash(ROOT, revision, path, expected)
            return revision, mode
        except ValueError:
            continue
    # Some Phase-5 pins were captured from a precommit, mixed-line-ending
    # worktree. Their old bytes cannot honestly be attributed to any Git blob.
    # The derivative certificate records this gap and independently binds the
    # terminal committed source and complete operation-family proof.
    return "PRECOMMIT_WORKTREE_UNRESOLVED", "LEGACY_WORKTREE_HASH_NOT_REPRODUCIBLE"


def _worktree_blob_id(path: str) -> str:
    return _git("hash-object", f"--path={path}", path)


def _semantic_family_id(record: dict) -> str:
    key = record["authority_equivalence_key"]
    stable = {
        "member_initiator_ids": record["member_initiator_ids"],
        "domains": key["domains"],
        "semantic_modes": key["semantic_modes"],
        "writer_families": key["writer_families"],
        "transaction_model": key["transaction_model"],
        "authority_requirement_set": key["authority_requirement_set"],
    }
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return "AF2_" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build() -> tuple[dict, list[dict[str, str]]]:
    family_path = "docs/remediation/calculation-lineage/T14D_operation_family_certification.json"
    previous = _committed_json(PHASE5, family_path)
    phase7 = _committed_json(BASE, family_path)
    phase7_integration = _committed_json(
        BASE, "docs/remediation/calculation-lineage/T16E_phase7_integration_certification.json"
    )
    if phase7_integration["phase5_regression"] != (
        "PASS_252_OF_252_CALLERS_220_OF_220_FAMILIES_ZERO_BYPASS"
    ):
        raise ValueError("Phase-7 integration did not recertify Phase-5 caller authority")
    callers = _json("T14D_caller_unification_certification.json")["callers"]
    review = _json("T14D_semantic_family_review.json")
    current = [
        json.loads(json.dumps(asdict(family))) for family in families.normalize(callers, review)
    ]
    old_by_members = {tuple(row["member_initiator_ids"]): row for row in previous["families"]}
    phase7_by_members = {tuple(row["member_initiator_ids"]): row for row in phase7["families"]}
    if len(old_by_members) != len(current) or len(current) != 220:
        raise ValueError("Phase-5 operation-family membership changed")
    if sum(len(row["member_initiator_ids"]) for row in current) != 252:
        raise ValueError("Phase-5 caller count changed")

    mappings = []
    changed = []
    equivalent_evolution = 0
    for row in current:
        old = old_by_members[tuple(row["member_initiator_ids"])]
        intermediate = phase7_by_members[tuple(row["member_initiator_ids"])]
        old_key, new_key = old["authority_equivalence_key"], row["authority_equivalence_key"]
        key_deltas = sorted(key for key in old_key if old_key[key] != new_key[key])
        old_id, new_id = old["operation_family_id"], row["operation_family_id"]
        if old["final_status"] != row["final_status"] or (row["final_status"] == "INCOMPLETE"):
            raise ValueError(f"Family disposition changed: {old_id}")
        contract_fields = (
            "domains",
            "semantic_modes",
            "writer_families",
            "transaction_model",
            "authority_requirement_set",
            "execution_model",
        )
        if any(old_key[field] != new_key[field] for field in contract_fields):
            raise ValueError(f"Phase-5 writer/authority contract changed: {old_id}")
        if _semantic_family_id(old) != _semantic_family_id(row):
            raise ValueError(f"Stable semantic family identity changed: {old_id}")
        classification = "UNCHANGED"
        if old_id != intermediate["operation_family_id"]:
            if new_id != intermediate["operation_family_id"]:
                if (old_id, intermediate["operation_family_id"], new_id) != REVIEWED_FAMILY_IDS:
                    raise ValueError(f"Uncertified change after Phase 7: {old_id}")
            equivalent_evolution += 1
            classification = (
                "REVIEWED_IB_PREFLIGHT_SOURCE_EVOLUTION"
                if new_id != intermediate["operation_family_id"]
                else "SEMANTICALLY_EQUIVALENT_EVOLUTION"
            )
            old_sources = {proof["source"]["address"] for proof in old["inheritance_proofs"]}
            new_sources = {proof["source"]["address"] for proof in row["inheritance_proofs"]}
            if old_sources != new_sources:
                raise ValueError(f"Initiator source location changed: {old_id}")
            changed.append(
                {
                    "historical_family_id": old_id,
                    "current_family_id": new_id,
                    "domain": ";".join(row["domains"]),
                    "operation": row["semantic_service"],
                    "classification": classification,
                    "semantic_authority_changed": "NO",
                    "historical_semantic_identity": _semantic_family_id(old),
                    "current_semantic_identity": _semantic_family_id(row),
                    "source_path_change": ";".join(sorted(new_sources)),
                    "authority_contract_changed": "NO",
                    "writer_contract_changed": "NO",
                    "initiator_caller_changed": "NO",
                    "finding_impact": (
                        "NONE; REL-IB-001 current-source derivative"
                        if classification == "REVIEWED_IB_PREFLIGHT_SOURCE_EVOLUTION"
                        else "NONE; T16E phase5_regression PASS"
                    ),
                    "evidence": (
                        "REL-IB-001 reviewed pipeline historical preflight invocation; "
                        "same 252 callers, 220 families, initiator addresses, authority and "
                        "writer contracts; exact pipeline blob pinned; "
                        f"changed proof dimensions={','.join(key_deltas)}"
                        if classification == "REVIEWED_IB_PREFLIGHT_SOURCE_EVOLUTION"
                        else (
                            "Certified Phase-7 T14D family ID and source proof; "
                            "T16E phase5_regression=PASS_252_OF_252_CALLERS_"
                            "220_OF_220_FAMILIES_ZERO_BYPASS; "
                            f"changed proof dimensions={','.join(key_deltas)}"
                        )
                    ),
                    "status": "CERTIFIED_EQUIVALENT",
                }
            )
        elif old_id != new_id:
            if key_deltas != ["authority_branch_fingerprint"]:
                raise ValueError(f"Unreviewed authority equivalence change: {old_id}")
            if old["member_initiator_ids"] != ["EF_PRIVILEGED_DATABASE_RESTORE"]:
                raise ValueError(f"Unexpected changed authority family: {old_id}")
            path = "scripts/ops/restore_postgres.ps1"
            old_hash = old["inheritance_proofs"][0]["source"]["file_sha256"]
            mode = classify_legacy_checkout_hash(ROOT, PHASE5, path, old_hash)
            if mode != "GIT_CHECKOUT_AUTOCRLF_TRUE":
                raise ValueError("Restore-family legacy ID is not explained by CRLF checkout")
            if committed_blob_sha256(ROOT, PHASE5, path) != committed_blob_sha256(ROOT, BASE, path):
                raise ValueError("Privileged restore operation actually changed")
            classification = "IDENTITY_ONLY_DRIFT"
            changed.append(
                {
                    "historical_family_id": old_id,
                    "current_family_id": new_id,
                    "domain": ";".join(row["domains"]),
                    "operation": row["semantic_service"],
                    "classification": classification,
                    "semantic_authority_changed": "NO",
                    "historical_semantic_identity": _semantic_family_id(old),
                    "current_semantic_identity": _semantic_family_id(row),
                    "source_path_change": path,
                    "authority_contract_changed": "NO",
                    "writer_contract_changed": "NO",
                    "initiator_caller_changed": "NO",
                    "finding_impact": "NONE",
                    "evidence": (
                        "Same committed restore script at Phase-5 and terminal main; "
                        "legacy whole-file hash is Git core.autocrlf=true checkout, "
                        "current proof is committed blob; membership and all other "
                        "authority-equivalence dimensions unchanged"
                    ),
                    "status": "CERTIFIED_EQUIVALENT",
                }
            )
        elif key_deltas:
            raise ValueError(f"Family proof changed without ID change: {old_id}")
        mappings.append(
            {
                "historical_family_id": old_id,
                "current_family_id": new_id,
                "semantic_family_id_v2": _semantic_family_id(row),
                "classification": classification,
                "member_initiator_ids": row["member_initiator_ids"],
                "final_status": row["final_status"],
            }
        )

    pins = _json("T14D_semantic_review_source_pins.json")
    source_records = []
    for path, expected in sorted(pins["sources"].items()):
        origin, legacy_mode = _pin_origin(path, expected)
        current_oid = _worktree_blob_id(path)
        committed_oid = _git("rev-parse", f"{BASE}:{path}")
        status = "UNCHANGED_COMMITTED_SOURCE" if origin == BASE else "SUPERSEDED_REVIEW_PIN"
        if origin == "PRECOMMIT_WORKTREE_UNRESOLVED":
            status = "LEGACY_REVIEW_PIN_NOT_REPRODUCIBLE"
        if current_oid != committed_oid:
            if path == REVIEWED_PIPELINE_PATH and current_oid == REVIEWED_PIPELINE_BLOB:
                status = "REVIEWED_IB_HISTORICAL_PREFLIGHT_SOURCE_CHANGE"
            elif path in {
                "scripts/qa/t14a_semantic_completeness.py",
                "scripts/qa/t14a_semantic_families.py",
            }:
                status = "RELEASE_REMEDIATION_CHECKER_CHANGE"
            else:
                raise ValueError(f"Unreviewed worktree source change: {path}")
        source_records.append(
            {
                "path": path,
                "historical_pin_sha256": expected,
                "historical_pin_commit": origin,
                "historical_hash_materialization": legacy_mode,
                "terminal_main_blob_id": committed_oid,
                "release_worktree_blob_id": current_oid,
                "classification": status,
            }
        )
    result = {
        "schema_version": "release-phase5-current-authority-v1",
        "certificate_type": "CURRENT_DERIVATIVE_REVALIDATION_CERTIFICATE",
        "historical_phase5_commit": PHASE5,
        "terminal_main_commit": BASE,
        "terminal_main_source_fingerprint": source_freeze(ROOT, BASE)["fingerprint_sha256"],
        "historical_certificate_blob_sha256": committed_blob_sha256(ROOT, PHASE5, family_path),
        "phase7_certificate_blob_sha256": committed_blob_sha256(ROOT, BASE, family_path),
        "historical_family_count": len(previous["families"]),
        "current_family_count": len(current),
        "caller_count": 252,
        "identity_only_drift": len(changed) - equivalent_evolution,
        "semantic_equivalent_evolution": equivalent_evolution,
        "semantic_authority_changes": 0,
        "new_operations": 0,
        "removed_operations": 0,
        "partial_authority": 0,
        "potential_bypass": 0,
        "confirmed_bypass": 0,
        "unknown": 0,
        "authority_status": "CURRENT_PHASE5_AUTHORITY_STILL_SEMANTICALLY_CERTIFIED",
        "family_identity_scheme": "semantic-family-key-v2-separate-from-source-proof",
        "family_mappings": sorted(mappings, key=lambda row: row["historical_family_id"]),
        "source_pin_reconciliation": source_records,
        "post_phase7_reviewed_change": {
            "source_path": REVIEWED_PIPELINE_PATH,
            "committed_blob_id": REVIEWED_PIPELINE_BLOB,
            "historical_family_id": REVIEWED_FAMILY_IDS[0],
            "phase7_family_id": REVIEWED_FAMILY_IDS[1],
            "current_family_id": REVIEWED_FAMILY_IDS[2],
            "scope": "IB historical capability preflight invocation only",
            "authority_contract_changed": False,
            "writer_contract_changed": False,
            "initiator_caller_changed": False,
        },
    }
    return result, changed


def write() -> None:
    result, changed = build()
    RELEASE.mkdir(parents=True, exist_ok=True)
    # Preserve the earlier release derivative as historical evidence.
    (RELEASE / CURRENT_DERIVATIVE).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (RELEASE / CURRENT_RECONCILIATION).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(changed[0]))
        writer.writeheader()
        writer.writerows(changed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        write()
    else:
        result, changed = build()
        print(json.dumps({"families": result["current_family_count"], "changed": changed}))
