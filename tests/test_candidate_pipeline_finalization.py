"""Full finalizer specimens preserve each sealed ownership contract."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "e2e"))
from single_run_certification import candidate_validation as validation  # noqa: E402
from single_run_certification.reporting import (  # noqa: E402
    REQUIRED_ARTIFACTS,
    finalize_pipeline_certificate,
    pipeline_contract,
)
from single_run_certification.test_exact_pipeline_certification import (  # noqa: E402
    evaluate,
)
from single_run_certification.test_exact_pipeline_certification import (  # noqa: E402
    specimen as historical_specimen,  # noqa: F401
)


@pytest.fixture
def candidate_specimen(historical_specimen):  # noqa: F811
    specimen = copy.deepcopy(historical_specimen)
    manifest = validation.expected_manifest(specimen["identity"]["handoff"], candidate=True)
    identity = {
        **specimen["identity"],
        "input_sha256": manifest["input_manifest_sha256"],
        "oracle_sha256": manifest["completion"]["manifest_sha256"],
        "configuration_sha256": manifest["configuration_envelope_sha256"],
    }
    contract = {
        **pipeline_contract(manifest, identity),
        "database_ownership": specimen["contract"]["database_ownership"],
    }
    specimen.update(manifest=manifest, identity=identity, contract=contract)
    bundle = specimen["bundle"]
    bundle["expected-manifest.json"] = copy.deepcopy(manifest)
    bundle["certificate-contract.json"] = copy.deepcopy(contract)
    bundle["pipeline-evidence.json"]["delivered_settings"]["sec_document_incremental_mode"] = (
        manifest["flags"]["sec_document_incremental_mode"]
    )
    oracle = bundle["db-results/independent-complete-oracle.json"]
    oracle["expected"] = copy.deepcopy(manifest["completion"])
    # Seed current-candidate unit doubles, as the retained specimen seeds v3.
    # This exercises finalizer structure/faults; native A04 supplies financial proof.
    oracle["actual"] = copy.deepcopy(manifest["completion"]["domains"])
    bundle["db-results/independent-golden-subset.json"]["actual"].update(
        copy.deepcopy(manifest["domains"])
    )
    # These are explicit unit-double observations, not copied production data.
    for row in oracle["actual"]["captures"]:
        row["pipeline_ownership_state"] = "KNOWN"
    for row in oracle["component_details"]["immutable_evidence"]:
        if row["kind"] == "CERI":
            row["ownership"]["pipeline_id"] = {"state": "KNOWN", "value": 2}
    return specimen


def test_candidate_file_finalizer_requires_exact_known_capture_owner(candidate_specimen, tmp_path):
    specimen = candidate_specimen
    for relative in REQUIRED_ARTIFACTS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        value = specimen["bundle"].get(relative, {})
        if relative.endswith(".jsonl"):
            target.write_text("\n".join(json.dumps(row) for row in value), encoding="utf-8")
        elif relative.endswith(".png"):
            target.write_bytes(b"unit-double screenshot")
        else:
            target.write_text(json.dumps(value), encoding="utf-8")
    result = finalize_pipeline_certificate(
        tmp_path, invocation=specimen["invocation"], expected_identity=specimen["identity"]
    )
    assert result["verdict"] == "CERTIFIED", result["reasons"]
    assert len(result["gates"]) == 620
    assert result["relationship_count"] == 541
    assert sum(result["actual_counts"].values()) == 402
    assert result["candidate_validation"]["input_version"] == specimen["manifest"]["version"]
    assert result["candidate_validation"]["owner_authorization"] == "NOT_EVALUATED"
    assert result["candidate_validation"]["activation_authorized"] is False


@pytest.mark.parametrize("fault", [
    "unknown-owner", "foreign-owner", "foreign-run", "foreign-context",
    "foreign-cutoff", "changed-configuration", "changed-sources", "missing-member",
])
def test_candidate_immutable_lineage_refuses_owner_and_source_faults(candidate_specimen, fault):
    specimen = candidate_specimen
    details = specimen["bundle"]["db-results/independent-complete-oracle.json"][
        "component_details"
    ]["immutable_evidence"]
    row = next(row for row in details if row["kind"] == "CERI")
    if fault == "unknown-owner":
        row["ownership"]["pipeline_id"] = {"state": "UNKNOWN"}
    elif fault == "foreign-owner":
        row["ownership"]["pipeline_id"]["value"] = 999
    elif fault == "foreign-run":
        row["ownership"]["run_id"]["value"] = 999
    elif fault == "foreign-context":
        row["expected_context_id"] = 999
    elif fault == "foreign-cutoff":
        row["temporal"]["calculation_cutoff"]["value"] = "2026-10-25T00:00:00+00:00"
    elif fault == "changed-configuration":
        row["effective_configuration"]["semantic_hash"] = "0" * 64
    elif fault == "changed-sources":
        row["sources"] = {"foreign": 999}
    elif fault == "missing-member":
        details.remove(row)
    result = evaluate(specimen)
    assert result.verdict == "FAILED"
    assert result.gate_results["immutable_lineage"]["status"] == "FAILED"
    assert len(result.gate_results) == 620


def test_historical_capture_ownership_is_not_reinterpreted(historical_specimen):  # noqa: F811
    assert historical_specimen["manifest"]["version"] == "a05-v3"
    assert evaluate(historical_specimen).verdict == "CERTIFIED"
    details = historical_specimen["bundle"]["db-results/independent-complete-oracle.json"][
        "component_details"
    ]["immutable_evidence"]
    row = next(row for row in details if row["kind"] == "CERI")
    row["ownership"]["pipeline_id"] = {"state": "KNOWN", "value": 2}
    result = evaluate(historical_specimen)
    assert result.verdict == "FAILED"
    assert result.gate_results["immutable_lineage"]["status"] == "FAILED"
