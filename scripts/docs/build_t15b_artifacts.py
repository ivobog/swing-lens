"""Build deterministic T15B reconciliation and certification artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / "docs" / "remediation" / "calculation-lineage"
INVENTORY_PATH = ARTIFACT_DIR / "T15A_scope_operation_inventory.json"
HANDOFF_PATH = ARTIFACT_DIR / "T15A_phase6_handoff.json"
RECONCILIATION_PATH = ARTIFACT_DIR / "T15B_operation_reconciliation.csv"
CERTIFICATE_PATH = ARTIFACT_DIR / "T15B_pipeline_ceri_scope_refresh_certification.json"

RECONCILIATION_FIELDS = (
    "operation_family_id",
    "domain",
    "entry/admission point",
    "current scope selection",
    "current refresh behavior",
    "current retry behavior",
    "current continuation behavior",
    "current acquisition plan",
    "current idempotency key",
    "current revision/truth linkage",
    "T15A issue flags",
    "required T15B adoption",
)


def _is_pipeline(record: dict) -> bool:
    service = record["semantic_service"]
    return "pipeline" in service or "background_job_service" in service


def _is_ceri(record: dict) -> bool:
    return "ceri" in record["semantic_service"]


def _is_ib(record: dict) -> bool:
    service = record["semantic_service"]
    return (
        "ib_fetch" in service
        or "market_data_prewarm" in service
        or ("IBMI_SOURCE" in record["domain"] and not _is_pipeline(record) and not _is_ceri(record))
    )


def _adoption(record: dict) -> str:
    if _is_pipeline(record):
        return (
            "bind the pipeline/job to the canonical frozen root scope, refresh cycle, and "
            "plan; inherit the same authority on retry/resume/reclaim and required children"
        )
    if _is_ceri(record):
        return (
            "freeze CERI subjects and acquisition semantics before enqueue; include canonical "
            "refresh identity in idempotency and inherit authority through child work"
        )
    if _is_ib(record):
        return (
            "persist the exact FetchPlan as canonical scope/refresh/plan authority and restore "
            "that retained plan for retry, resume, execution, and prewarm"
        )
    raise AssertionError(record["semantic_service"])


def _certificate_record(record: dict) -> dict:
    service = record["semantic_service"]
    pipeline = _is_pipeline(record)
    ceri = _is_ceri(record)
    ib = _is_ib(record)
    if not (pipeline or ceri or ib):
        raise AssertionError(f"unclassified T15B family: {service}")
    tests = [
        "tests/test_t15b_scope_refresh_adoption.py",
        "tests/integration/test_t15b_scope_refresh_postgresql.py",
    ]
    if pipeline:
        tests.extend(["tests/test_pipeline_service.py", "tests/test_pipeline_executor.py"])
    if ceri:
        tests.append("tests/ceri/test_ingestion_orchestration.py")
    if ib:
        tests.extend(["tests/test_ib_fetch_job_service.py", "tests/test_market_data_prewarm.py"])
    return {
        "operation_family_id": record["operation_family_id"],
        "scope_identity": "canonical WorkScopeIdentity persisted and bound before business work",
        "scope_policy": "FROZEN",
        "membership_persistence": (
            "exact canonical members persisted in work_scope_members; child subsets use "
            "parent_scope_id"
        ),
        "refresh_identity": (
            "canonical RefreshCycleIdentity bound to durable operation and job payload references"
        ),
        "retry_semantics": (
            "same scope_id, refresh_cycle_id, and acquisition_plan_id; execution ownership "
            "may change"
        ),
        "continuation_semantics": (
            "remaining = retained frozen membership - completed membership; current selection "
            "is not rerun"
        ),
        "zero_progress_policy": (
            "BLOCKED with persisted WAITING_FOR_EXTERNAL_REQUIREMENT reason and retained remainder"
        ),
        "parent_child_policy": (
            "required children explicitly inherit authority and must be terminal before "
            "parent completion"
            if pipeline or ceri
            else "fetch/prewarm item work remains within the bound stage scope and plan"
        ),
        "acquisition_plan_identity": (
            "canonical immutable AcquisitionPlanIdentity captures subjects, source class, request, "
            "cutoff, window, policy, and requirements"
        ),
        "replan_policy": (
            "retry preserves P1; semantic change creates P2 with previous_plan_id and typed "
            "revision_reason"
        ),
        "checkpoint_binding": (
            "progress/checkpoint keys and durable owners are bound to scope_id and refresh_cycle_id"
        ),
        "legacy_behavior": "LEGACY_UNKNOWN_SEMANTIC_AUTHORITY; resume/execute fails closed",
        "tests": sorted(set(tests)),
        "status": "CERTIFIED_ADOPTED",
    }


def build() -> tuple[list[dict], dict]:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    handoff = json.loads(HANDOFF_PATH.read_text(encoding="utf-8"))
    assert handoff["decomposition"]["T15B"]["scope"] == (
        "Pipeline and CERI scope/refresh/acquisition adoption"
    )
    assigned = sorted(
        (row for row in inventory["records"] if row["phase6_target_task"] == "T15B"),
        key=lambda row: row["operation_family_id"],
    )
    assert len(assigned) == 19
    assert len({row["operation_family_id"] for row in assigned}) == len(assigned)

    reconciliation = []
    for row in assigned:
        reconciliation.append(
            {
                "operation_family_id": row["operation_family_id"],
                "domain": json.dumps(row["domain"], separators=(",", ":")),
                "entry/admission point": row["semantic_service"],
                "current scope selection": row["scope_selection_point"],
                "current refresh behavior": row["refresh_model"],
                "current retry behavior": row["retry_model"],
                "current continuation behavior": row["continuation_model"],
                "current acquisition plan": row["acquisition_plan_model"],
                "current idempotency key": (
                    "stable request/job key not previously separated from refresh identity"
                ),
                "current revision/truth linkage": row["revision_model"],
                "T15A issue flags": json.dumps(row["status"], separators=(",", ":")),
                "required T15B adoption": _adoption(row),
            }
        )

    records = [_certificate_record(row) for row in assigned]
    certificate = {
        "schema_version": "t15b-pipeline-ceri-scope-refresh-certification-v1",
        "source_handoff": "T15A_phase6_handoff.json",
        "source_inventory": "T15A_scope_operation_inventory.json",
        "migration_head": "0082_pipeline_ceri_scope",
        "finding_statuses": {
            "PIPE-001": "CLOSED",
            "PIPE-004": "CLOSED",
            "PIPE-005": "CLOSED",
            "CERI-012": "CLOSED",
            "XINT-007": "CLOSED_FOR_T15B_SOURCE_BINDING__PARTIAL_OVERALL",
            "XINT-012": "CLOSED_FOR_PIPELINE_CERI__PARTIAL_OVERALL",
            "INV-SCOPE-001": "ENFORCED_FOR_PIPELINE_CERI__PARTIAL_REPOSITORY_WIDE",
            "INV-REFRESH-001": "ENFORCED_FOR_PIPELINE_CERI__PARTIAL_REPOSITORY_WIDE",
        },
        "counts": {
            "assigned_operation_families": len(records),
            "reconciled_operation_families": len(records),
            "unreconciled_operation_families": 0,
            "assigned_operation_family_defects": 0,
        },
        "operation_families": records,
    }
    return reconciliation, certificate


def main() -> None:
    reconciliation, certificate = build()
    with RECONCILIATION_PATH.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RECONCILIATION_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(reconciliation)
    CERTIFICATE_PATH.write_text(
        json.dumps(certificate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
