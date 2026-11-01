"""Controlled copies only: content integrity is never reviewer authentication."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import production_runtime_certification as runtime
from scripts.qa import check_certification_registry as registry
from scripts.qa import golden_provenance as provenance


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def isolated(tmp_path):
    baseline = provenance.verify_baseline()
    for name in baseline["artifacts"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(provenance.ROOT / name, path)
    path = tmp_path / "config/certification_registry.json"
    shutil.copyfile(provenance.ROOT / "config/certification_registry.json", path)
    assert provenance.verify_baseline(tmp_path)["baseline"] == baseline["baseline"]
    return tmp_path, baseline


@pytest.fixture
def proposal(isolated):
    root, baseline = isolated
    artifacts, mappings = {}, {}
    changes = []
    for old_name in baseline["artifacts"]:
        if not old_name.endswith(("/expected-results.json", "-expected.json")):
            continue
        new_name = "proposal-only/" + Path(old_name).name
        values = json.loads((root / old_name).read_text())
        if old_name.endswith("/core-expected.json"):
            before = values["technical"][0]["dual_score"]
            values["technical"][0]["dual_score"] = "9.4321"
            changes.append(
                {
                    "artifact": old_name,
                    "pointer": "/technical/0/dual_score",
                    "before": before,
                    "after": "9.4321",
                    "reason": "Synthetic changed-contract witness; no real golden revision",
                }
            )
        artifacts[new_name] = save(root / new_name, values)
        mappings[old_name] = new_name
    input_manifest = {"version": "A13_SYNTHETIC_INPUT_ONLY", "fixture": "contract test"}
    input_identity = hashlib.sha256(
        json.dumps(input_manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    input_hash = save(root / "proposal-only/input.json", input_manifest)
    completion_hash = save(
        root / "proposal-only/completion.json",
        {
            "expectation_revision": "A13_SYNTHETIC_EXPECTATIONS_ONLY",
            "base_input_identity": input_identity,
        },
    )
    artifacts.update(
        {"proposal-only/input.json": input_hash, "proposal-only/completion.json": completion_hash}
    )
    derivation = "proposal-only/independent-worksheet.json"
    derivation_hash = save(
        root / derivation, {"method": "synthetic independent derivation witness"}
    )
    proposal = {
        "schema": "swinglens.golden-update-proposal.v1",
        "previous": baseline["baseline"],
        "candidate": {
            "input_version": input_manifest["version"],
            "input_manifest": "proposal-only/input.json",
            "input_manifest_sha256": input_hash,
            "input_identity": input_identity,
            "expectation_revision": "A13_SYNTHETIC_EXPECTATIONS_ONLY",
            "completion_manifest": "proposal-only/completion.json",
            "completion_manifest_sha256": completion_hash,
            "artifacts": artifacts,
            "expectation_artifacts": mappings,
        },
        "contract_change": {
            "reason": "Synthetic contract-only proposal",
            "source_revision": "a" * 40,
            "configuration_sha256": {
                name: digest
                for name, digest in baseline["artifacts"].items()
                if name.startswith("config/")
            },
        },
        "independent_derivation": {
            "method": "INDEPENDENT_FORMULA_REVIEW",
            "production_outputs_used": False,
            "artifacts": {derivation: derivation_hash},
        },
        "expectation_changes": changes,
        "affected_obligations": ["A04 technical persisted value; A05B technical contract"],
        "historical_artifacts": baseline["artifacts"],
    }
    proposal["review_request"] = {
        "candidate_digest": provenance.proposal_digest(proposal),
        "source_revision": proposal["contract_change"]["source_revision"],
        "input_version": proposal["candidate"]["input_version"],
        "expectation_revision": proposal["candidate"]["expectation_revision"],
        "status": "PROPOSED",
        "producer": "SYNTHETIC_PROPOSAL_ONLY",
    }
    assert provenance.inspect_proposal(proposal, root=root)["proposal_content_integrity"] == "PASS"
    return root, proposal


def test_current_oracle_provenance_and_scope_remain_unchanged():
    result = provenance.verify_baseline()
    assert result["content_integrity"] == "PASS"
    assert result["expected_rows"] == 402 and result["relationships"] == 541
    assert result["sections"] == 26 and len(result["contracts"]) == 7
    assert result["authenticated_update_approval"] == "NOT_VERIFIED"
    assert result["new_version_activation"] == "BLOCKED_PENDING_AUTHENTICATED_OWNER_ACK"


@pytest.mark.parametrize(
    "fault,needle",
    [
        ("actual_db_projection", "GOLDEN_INPUT_HASH_MISMATCH"),
        ("frozen_value", "COMPLETION_ARTIFACT_HASH_MISMATCH"),
        ("manifest", "GOLDEN_MANIFEST_HASH_MISMATCH"),
        ("completion_manifest", "COMPLETION_MANIFEST_HASH_MISMATCH"),
        ("mixed_input", "GOLDEN_INPUT_HASH_MISMATCH"),
        ("missing_section", "COMPLETION_ARTIFACT_HASH_MISMATCH"),
        ("missing_row", "COMPLETION_ARTIFACT_HASH_MISMATCH"),
        ("incomplete_oracle", "COMPLETION_ARTIFACT_HASH_MISMATCH"),
        ("derivation_record", "PROVENANCE_ARTIFACT_HASH_MISMATCH"),
        ("missing_authoring_record", "PROVENANCE_ARTIFACT_UNSAFE_OR_MISSING"),
        ("configuration", "PROVENANCE_ARTIFACT_HASH_MISMATCH"),
    ],
)
def test_unauthorized_baseline_changes_fail_at_the_intended_boundary(isolated, fault, needle):
    root, baseline = isolated
    data = root / provenance.GOLDEN
    if fault == "actual_db_projection":
        # A disposable database-shaped actual export replaces expected truth.
        # It is not a new financial oracle and never touches the live database.
        save(
            data / "expected-results.json",
            {"producer": "CURRENT_DATABASE_EXPORT", "actual": [{"fundamental_score": "8.9850"}]},
        )
    elif fault == "manifest":
        value = json.loads((data / "manifest.json").read_text())
        value["version"] = "UNAUTHORIZED"
        save(data / "manifest.json", value)
    elif fault == "completion_manifest":
        value = json.loads((data / "oracle-completion/manifest.json").read_text())
        value["authoring_provenance"] = {}
        save(data / "oracle-completion/manifest.json", value)
    elif fault == "mixed_input":
        (data / "universe.csv").write_bytes(b"Symbol\nFOREIGN\n")
    elif fault in {"frozen_value", "missing_section", "missing_row", "incomplete_oracle"}:
        path = data / "oracle-completion/core-expected.json"
        value = json.loads(path.read_text())
        if fault == "frozen_value":
            value["technical"][0]["dual_score"] = "9.9999"
        elif fault == "missing_section":
            del value["technical"]
        elif fault == "missing_row":
            value["technical"].pop()
        else:
            value["technical"] = []
        save(path, value)
    else:
        name = next(
            n
            for n in baseline["artifacts"]
            if n.startswith(
                "config/" if fault == "configuration" else "docs/testing/golden-provenance/"
            )
        )
        if fault == "missing_authoring_record":
            (root / name).unlink()
        else:
            with (root / name).open("ab") as stream:
                stream.write(b"\nUNAUTHORIZED CHANGE\n")
    with pytest.raises(provenance.ProvenanceError, match=needle):
        provenance.verify_baseline(root)


def test_valid_proposal_hashes_cannot_supply_approval_authority(proposal):
    root, candidate = proposal
    result = provenance.inspect_proposal(candidate, root=root)
    assert result["proposal_content_integrity"] == "PASS"
    assert result["financial_equivalence"] == "NOT_EVALUATED"
    assert not result["activation_authorized"]
    assert result["authenticated_human_approval"] == "NOT_VERIFIED"
    with pytest.raises(provenance.ProvenanceError, match="AUTHENTICATED_OWNER_ACK_REQUIRED"):
        provenance.require_authorized_update(candidate, root=root)


@pytest.mark.parametrize(
    "fault,needle",
    [
        ("production_snapshot", "PRODUCTION_OUTPUTS_CANNOT_AUTHOR_EXPECTATIONS"),
        ("automatic_approval", "AUTOMATIC_RESULT_PROCESS_CANNOT_APPROVE_EXPECTATIONS"),
        ("editable_approval", "EDITABLE_METADATA_CANNOT_PROVE_APPROVAL"),
        ("stale_approval", "GOLDEN_REVIEW_EVIDENCE_STALE_OR_WRONG_VERSION"),
        ("wrong_source_approval", "GOLDEN_REVIEW_EVIDENCE_STALE_OR_WRONG_VERSION"),
        ("missing_reason", "GOLDEN_EACH_EXPECTATION_CHANGE_REQUIRES_REASON"),
        ("omitted_difference", "GOLDEN_NUMERIC_SEMANTIC_DIFF_NOT_FULLY_JUSTIFIED"),
        ("incomplete_candidate", "GOLDEN_EXPECTATION_ARTIFACT_SET_INCOMPLETE"),
        ("forgot_history", "GOLDEN_HISTORY_PRESERVATION_REQUIRED"),
        ("wrong_input_version", "GOLDEN_CANDIDATE_INPUT_EXPECTATION_VERSION_MISMATCH"),
        ("wrong_manifest_hash", "GOLDEN_CANDIDATE_MANIFEST_HASH_UNBOUND"),
    ],
)
def test_candidate_contract_and_false_approvals_fail_closed(proposal, fault, needle):
    root, candidate = proposal
    candidate = copy.deepcopy(candidate)
    if fault == "production_snapshot":
        candidate["independent_derivation"]["production_outputs_used"] = True
    elif fault == "automatic_approval":
        candidate["review_request"]["producer"] = "ACTUAL_RESULT_GENERATOR"
    elif fault == "editable_approval":
        candidate["review_request"].update(
            status="APPROVED", reviewer="SYNTHETIC_CLAIM_ONLY", verified=True
        )
    elif fault == "stale_approval":
        candidate["review_request"]["expectation_revision"] = "ANOTHER_VERSION"
    elif fault == "wrong_source_approval":
        candidate["review_request"]["source_revision"] = "b" * 40
    elif fault == "missing_reason":
        candidate["expectation_changes"][0]["reason"] = ""
    elif fault == "omitted_difference":
        candidate["expectation_changes"][0]["before"] = "WRONG_OLD_VALUE"
    elif fault == "incomplete_candidate":
        candidate["candidate"]["expectation_artifacts"].pop(
            next(iter(candidate["candidate"]["expectation_artifacts"]))
        )
    elif fault == "forgot_history":
        candidate["historical_artifacts"] = {}
    elif fault == "wrong_input_version":
        candidate["candidate"]["input_version"] = "ANOTHER_INPUT"
    else:
        candidate["candidate"]["completion_manifest_sha256"] = "f" * 64
    if fault not in {"stale_approval", "wrong_source_approval"}:
        candidate["review_request"]["candidate_digest"] = provenance.proposal_digest(candidate)
    with pytest.raises(provenance.ProvenanceError, match=needle):
        provenance.require_authorized_update(candidate, root=root)


def test_registry_and_supported_producer_consume_the_same_read_only_adapter():
    result = registry.validate_registry(registry.load_manifest())
    assert result["a04"]["golden_provenance"]["content_integrity"] == "PASS"
    assert runtime.EXACT_TEST_IDS == tuple(result["a04"]["selectors"])
    assert result["a04"]["declared_obligations"] == 620


def test_provenance_verification_forbids_application_imports():
    code = (
        "import sys,importlib.abc\n"
        "class Guard(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self,fullname,path=None,target=None):\n"
        "  if fullname=='app' or fullname.startswith('app.'): raise AssertionError(fullname)\n"
        "sys.meta_path.insert(0,Guard())\n"
        "from scripts.qa.golden_provenance import verify_baseline\n"
        "assert verify_baseline()['expected_rows']==402\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=provenance.ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_conflicting_candidate_and_derivation_hashes_do_not_shadow_each_other(proposal):
    root, candidate = proposal
    name = next(iter(candidate["candidate"]["expectation_artifacts"].values()))
    actual_hash = candidate["candidate"]["artifacts"][name]
    candidate["candidate"]["artifacts"][name] = "f" * 64
    candidate["independent_derivation"]["artifacts"][name] = actual_hash
    candidate["review_request"]["candidate_digest"] = provenance.proposal_digest(candidate)
    with pytest.raises(provenance.ProvenanceError, match="PROVENANCE_ARTIFACT_HASH_MISMATCH"):
        provenance.require_authorized_update(candidate, root=root)


def test_supported_a04_stops_before_test_execution_when_provenance_is_invalid(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "SWINGLENS_TEST_POSTGRES_ADMIN_URL", "postgresql://postgres@127.0.0.1:1/postgres"
    )
    monkeypatch.setattr(
        runtime, "_working_tree_identity", lambda: ("synthetic-no-execution", False)
    )

    def invalid(*args, **kwargs):
        raise provenance.ProvenanceError("CONTROLLED_UNAUTHORIZED_EXPECTATION_CHANGE")

    def forbidden(*args, **kwargs):
        raise AssertionError("native execution must not start")

    monkeypatch.setattr(provenance, "verify_baseline", invalid)
    monkeypatch.setattr(runtime, "_run_test_group", forbidden)
    directory = tmp_path / "blocked"
    assert runtime._run_exact_deterministic(directory, "synthetic-only") == 2
    report = json.loads((directory / "report.json").read_text())
    assert report["verdict"] == "BLOCKED" and not report["safe_for_normal_run"]
    assert "CONTROLLED_UNAUTHORIZED_EXPECTATION_CHANGE" in report["reason"]
