from __future__ import annotations

import json
from pathlib import Path

from scripts.check_architecture_registry import validate_registry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "architecture_registry.json"
SCOPE_CONTRACT = ROOT / "docs" / "architecture" / "SWINGLENS_SCOPE_REFRESH_ACQUISITION_CONTRACT.md"


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_ceri_remediation_architecture_entries_are_precisely_registered() -> None:
    registry = _registry()
    tables = {row["table"]: row for row in registry["tables"]}
    manifest = tables["ceri_feature_source_manifests"]
    assert manifest["writer_ids"] == ["WRITE-074"]
    assert manifest["authority"] == "FEATURE_BATCH_CALCULATION_CONTEXT_RETRY_AUTHORITY"

    candidates = {
        (row["path"], row["function"], row["kind"]): row for row in registry["writer_candidates"]
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

    table_contracts = {
        "ceri_ingestion_run_source_records": (
            "WRITE-076",
            "INGESTION_RUN_SOURCE_MEMBERSHIP_AUTHORITY",
        ),
        "instrument_lifecycle_records": (
            "WRITE-077",
            "OBSERVED_CONTRACT_LIFECYCLE_REVISION_AUTHORITY",
        ),
        "market_data_session_dispositions": (
            "WRITE-078",
            "FROZEN_SESSION_TICKER_DISPOSITION_AUTHORITY",
        ),
        "pipeline_dependencies": (
            "WRITE-079",
            "PIPELINE_EXECUTION_DEPENDENCY_AUTHORITY",
        ),
    }
    for table_name, (writer_id, authority) in table_contracts.items():
        assert tables[table_name]["writer_ids"] == [writer_id]
        assert tables[table_name]["authority"] == authority

    families = {row["id"]: row for row in registry["writer_families"]}
    assert families["WRITE-075"]["tables"] == [
        "upload_runs",
        "pipeline_runs",
        "pipeline_steps",
    ]
    assert families["WRITE-075"]["authority_transaction"].endswith("TX-28")
    for table_name in families["WRITE-075"]["tables"]:
        assert "WRITE-075" in tables[table_name]["writer_ids"]
    assert "WRITE-075" not in tables["raw_company_rows"]["writer_ids"]
    assert {row["id"] for row in registry["transaction_families"]} >= {"TX-27", "TX-28"}

    candidate_contracts = {
        (
            "app/services/ceri/feature_certification_workflow.py",
            "admit_ceri_feature_certification",
            "add",
        ): "WRITE-075",
        (
            "app/services/ceri/source_record_service.py",
            "_associate_source_record",
            "add",
        ): "WRITE-076",
        (
            "app/services/instrument_lifecycle_service.py",
            "record_instrument_lifecycle",
            "add",
        ): "WRITE-077",
        (
            "app/services/market_data_session_readiness.py",
            "evaluate_and_persist_session_readiness",
            "add",
        ): "WRITE-078",
    }
    for key, writer_id in candidate_contracts.items():
        assert candidates[key]["writer_ids"] == [writer_id]
    dependency_candidates = [
        row
        for row in registry["writer_candidates"]
        if row["path"] == "app/services/pipeline_dependency_service.py"
    ]
    assert dependency_candidates
    assert all(row["writer_ids"] == ["WRITE-079"] for row in dependency_candidates)

    jobs = {row["job_type"]: row for row in registry["jobs"]}
    certification_job = jobs["CERI_FEATURE_CERTIFICATION"]
    assert certification_job["retry"] == "NO_ROOT_RETRY_CHILDREN_BOUNDED"
    assert certification_job["modes"]["NORMAL"] == "DISABLED"
    assert certification_job["tests"] == ["tests/test_ceri_feature_certification.py"]

    transitions = {row["id"]: row for row in registry["state_transitions"]}
    sibling_transition = transitions["STATE-071"]
    assert "QUEUED/STALLED/RECOVERING -> BLOCKED" in sibling_transition["transition"]
    assert "CERI_PARENT_PIPELINE_TERMINAL" in sibling_transition["authority"]
    assert "FEATURE_CERTIFIED" in transitions["STATE-072"]["transition"]


def test_architecture_admission_checker_has_no_findings() -> None:
    findings, _counts = validate_registry(_registry(), root=ROOT)
    assert findings == []


def test_ceri_certification_scope_authority_invariant_is_documented() -> None:
    contract = SCOPE_CONTRACT.read_text(encoding="utf-8")
    assert "INV-CERI-CERTIFICATION-SCOPE-AUTHORITY" in contract
    assert "INV-CERI-CERTIFICATION-PROVIDER-IDENTITY-ADMISSION" in contract
    assert "INV-CERI-CERTIFICATION-CANONICAL-SESSION" in contract
    assert "ceri-feature-certification" in contract
    assert "full-pipeline-run" in contract
    assert "does not consult\n`raw_company_rows`" in contract
