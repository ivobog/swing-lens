from __future__ import annotations

import copy
import csv
import json
import subprocess
import sys

import pytest

from scripts import production_runtime_certification as runtime
from scripts.qa import check_certification_registry as registry

# The existing runner makes its tests/e2e helper package importable.
# isort: split
from single_run_certification.reporting import CertificationRecorder, pipeline_gate_inventory
from single_run_certification.test_exact_pipeline_certification import (
    evaluate,
)
from single_run_certification.test_exact_pipeline_certification import (
    specimen as a04_specimen,  # noqa: F401
)


@pytest.fixture
def manifest():
    return registry.load_manifest()


def lifecycle_rows(manifest):
    with (registry.ROOT / manifest["standalone_registry"]).open(
        encoding="utf-8", newline=""
    ) as handle:
        return list(csv.DictReader(handle))


def test_registry_conserves_156_tools_29_incidents_and_a04_authority(manifest):
    result = registry.validate_registry(manifest)
    assert result["baseline_entries"] == 156
    assert result["historical_entries"] == 29
    assert result["a04"]["declared_obligations"] == 620
    assert result["a04"]["selectors"] == list(runtime.EXACT_TEST_IDS)
    assert result["pipeline_result"] == "NOT_EVALUATED"
    assert result["live_promotion"] == "CLOSED_PENDING_SEPARATE_REQUIRED_EVIDENCE"
    rows = lifecycle_rows(manifest)
    assert len({row["entry_point"] for row in rows}) == 156
    assert all(row["target_lifecycle"] in registry.LIFECYCLES for row in rows)
    for row in rows:
        if row["historical_decision"]:
            assert row["automatic_certification"] == "DENIED"
            assert "NOT AUTHORIZED OR EXECUTED" in row["physical_cleanup_approval"]


def test_current_runner_selection_is_bound_without_changing_the_runner(manifest):
    gate = registry.registered_gate(manifest, "EXACT_PIPELINE_CERTIFICATION")
    assert gate["selectors"] == list(runtime.EXACT_TEST_IDS)
    assert dict(runtime.TEST_GROUPS)["production-runtime"] == runtime.EXACT_TEST_IDS
    assert gate["command"] == [
        "python",
        "-m",
        "scripts.production_runtime_certification",
        "--provider-mode",
        "deterministic",
    ]
    assert registry.source_sha256(registry.ROOT / gate["entry_point"]) == gate["source_sha256"]


@pytest.mark.parametrize(
    "source",
    (
        "scripts/qa/certify_ceri_run101_golden.py",
        "ordinary-unit-regression",
    ),
)
def test_historical_or_ordinary_pass_does_not_replace_a04_evidence(a04_specimen, source):  # noqa: F811
    # Use the unchanged A04 evaluator, not another registry verdict aggregator.
    a04_specimen["bundle"]["db-results/independent-complete-oracle.json"] = {
        "producer": source,
        "verdict": "PASS",
        "tests_passed": 5302,
    }
    result = evaluate(a04_specimen)
    assert result.verdict == "FAILED"


def test_unknown_gate_cannot_manufacture_certification(manifest, a04_specimen):  # noqa: F811
    with pytest.raises(registry.RegistryError, match="unknown"):
        registry.registered_gate(manifest, "historical-script-PASS")
    recorder = CertificationRecorder(
        "current", required_gates=pipeline_gate_inventory(a04_specimen["manifest"])
    )
    recorder.gate("historical-script-PASS", "PASS", evidence={"verdict": "PASS"})
    assert recorder.verdict == "FAILED"


def test_historical_name_patterns_and_manual_tools_are_not_automatic_members(manifest, tmp_path):
    commands = registry.automatic_commands(manifest)
    new_file = tmp_path / "certify_run999_current_PASS.py"
    new_file.write_text("raise RuntimeError('must never be executed')\n")
    assert commands == registry.automatic_commands(manifest)
    assert not any(str(new_file) in command for command in commands)
    for row in lifecycle_rows(manifest):
        if row["target_lifecycle"] != "ACTIVE_RELEASE_CERTIFICATION":
            assert not any(row["entry_point"] in command for command in commands)
    assert not registry.registered_gate(manifest, "SMALL_LIVE_PROVIDER_CANARY")["automatic"]


@pytest.mark.parametrize("field", sorted(registry.GATE_FIELDS))
def test_missing_required_active_information_fails_closed(manifest, field):
    del manifest["gates"][0][field]
    with pytest.raises(registry.RegistryError):
        registry.validate_registry(manifest)


@pytest.mark.parametrize(
    "mutation", ("id", "command", "promotion_requirement", "missing_a04", "missing_live")
)
def test_duplicate_ambiguous_or_missing_required_registration_is_rejected(manifest, mutation):
    if mutation in {"id", "command"}:
        duplicate = copy.deepcopy(manifest["gates"][0])
        if mutation == "command":
            duplicate["id"] = "ANOTHER_GATE"
        manifest["gates"].append(duplicate)
    elif mutation == "promotion_requirement":
        manifest["promotion_requirements"].append(manifest["promotion_requirements"][0])
    else:
        target = (
            "EXACT_PIPELINE_CERTIFICATION"
            if mutation == "missing_a04"
            else "SMALL_LIVE_PROVIDER_CANARY"
        )
        manifest["gates"] = [gate for gate in manifest["gates"] if gate["id"] != target]
    with pytest.raises(registry.RegistryError):
        registry.validate_registry(manifest)


@pytest.mark.parametrize(
    "entry",
    (
        "scripts/forensics/repair_run43_market_data.py",
        "scripts/qa/certify_ceri_run101_golden.py",
        "scripts/inspect_ceri_backlog.py",
    ),
)
def test_operational_and_historical_producers_cannot_be_registered_as_active(manifest, entry):
    gate = copy.deepcopy(manifest["gates"][0])
    gate.update(
        id="UNAPPROVED",
        entry_point=entry,
        command=["python", entry],
        ci_command="",
        source_sha256=registry.source_sha256(registry.ROOT / entry),
    )
    manifest["gates"].append(gate)
    with pytest.raises(registry.RegistryError, match="unapproved/historical/operational"):
        registry.validate_registry(manifest)


@pytest.mark.parametrize(
    "mutation",
    ("wrong_command", "wrong_authority", "source_hash", "selector", "live_auto", "full_release"),
)
def test_registration_drift_cannot_claim_pipeline_authority(manifest, mutation):
    if mutation == "wrong_command":
        manifest["gates"][0]["command"] = ["python", "scripts/ops/lifecycle_probe.py", "start"]
    elif mutation == "wrong_authority":
        manifest["gates"][0]["certificate_authority"] = "A04_FINALIZER"
    elif mutation == "source_hash":
        manifest["gates"][0]["source_sha256"] = "0" * 64
    elif mutation == "selector":
        registry.registered_gate(manifest, "EXACT_PIPELINE_CERTIFICATION")["selectors"] = [
            "historical-PASS"
        ]
    elif mutation == "live_auto":
        registry.registered_gate(manifest, "SMALL_LIVE_PROVIDER_CANARY")["automatic"] = True
    else:
        manifest["promotion_requirements"][1]["producer"] = "HISTORICAL_PASS_AGGREGATOR"
    with pytest.raises(registry.RegistryError):
        registry.validate_registry(manifest)


def test_live_promotion_remains_closed_without_separate_release_evidence(
    manifest, tmp_path, monkeypatch
):
    monkeypatch.setattr(runtime, "REPORT_ROOT", tmp_path)
    target = tmp_path / "current"
    target.mkdir()
    (target / "report.json").write_text(
        json.dumps(
            {
                "provider_mode": "deterministic",
                "working_tree_fingerprint": "current",
                "verdict": "CERTIFIED",
                "gates": {
                    "EXACT_PIPELINE_CERTIFICATION": "CERTIFIED",
                    "FULL_RELEASE_REGRESSIONS": "NOT_RUN",
                    "SMALL_LIVE_PROVIDER_CANARY": "NOT_RUN",
                },
            }
        )
    )
    assert runtime._matching_deterministic_report("current") is None
    required = {item["id"] for item in manifest["promotion_requirements"] if item["required"]}
    assert required == {
        "EXACT_PIPELINE_CERTIFICATION",
        "FULL_RELEASE_REGRESSIONS",
        "SMALL_LIVE_PROVIDER_CANARY",
    }


def test_canonical_historical_helper_consumers_remain_regressions(manifest):
    rows = {row["entry_point"]: row for row in lifecycle_rows(manifest)}
    for script, consumer in (
        ("scripts/qa/certify_ceri_run101_golden.py", "tests/ceri/test_run101_golden_vertical.py"),
        (
            "scripts/qa/certify_ceri_run102_golden.py",
            "tests/ceri/test_run102_golden_certification.py",
        ),
    ):
        row = rows[script]
        assert row["historical_decision"] == "EXTRACT_REUSABLE_REGRESSION"
        assert consumer in row["python_import_consumers"]
        assert (registry.ROOT / consumer).is_file()
        assert row["automatic_certification"] == "DENIED"


def test_checker_never_imports_app_or_runs_historical_tools_or_network():
    bootstrap = r"""
import importlib.abc, runpy, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ('app.', 'scripts.forensics.', 'scripts.qa.certify_ceri_run')
        if fullname == 'app' or fullname.startswith(forbidden):
            raise RuntimeError('registry checker imported a forbidden module')
sys.meta_path.insert(0, Guard())
def guard(event, args):
    if event in {'socket.connect','socket.getaddrinfo'}:
        raise RuntimeError('registry checker attempted network access')
sys.addaudithook(guard)
sys.argv = ['check_certification_registry.py', '--check']
runpy.run_path('scripts/qa/check_certification_registry.py', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=registry.ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["registry_valid"] and report["pipeline_result"] == "NOT_EVALUATED"


def test_registration_digest_tolerates_git_line_endings_but_not_source_changes(tmp_path):
    source = tmp_path / "source.py"
    source.write_bytes(b"approved = True\n")
    digest = registry.source_sha256(source)
    source.write_bytes(b"approved = True\r\n")
    assert registry.source_sha256(source) == digest
    source.write_bytes(b"approved = False\r\n")
    assert registry.source_sha256(source) != digest


@pytest.mark.parametrize(
    "field",
    (
        "historical_final_category",
        "historical_original_proposal",
        "historical_source_archive_sha256",
        "historical_replacement_tests",
    ),
)
def test_final_historical_lifecycle_cannot_drop_required_proof(manifest, field):
    rows = lifecycle_rows(manifest)
    row = next(r for r in rows if r.get("historical_final_category") == "B")
    row[field] = ""
    with pytest.raises(registry.RegistryError, match="historical"):
        registry._check_historical_tools(manifest, rows, registry.ROOT)


@pytest.mark.parametrize(
    "indirect",
    (
        "python -c \"import runpy; "
        "runpy.run_path('docs/verification/run161-sec-continuation/run_tests.py')\"",
        "python -m scripts.ceri_run104_controlled_replay",
    ),
)
def test_indirect_historical_workflow_execution_is_refused(
    manifest, tmp_path, monkeypatch, indirect
):
    workflow = tmp_path / ".github/workflows/injected.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("jobs:\n  unsafe:\n    steps:\n      - run: " + indirect + "\n")
    monkeypatch.setattr(registry, "_file", lambda root, name: registry.ROOT / name)
    with pytest.raises(registry.RegistryError, match="historical tool referenced"):
        registry._check_historical_tools(manifest, lifecycle_rows(manifest), tmp_path)


def test_registration_hash_refresh_cannot_hide_removed_historical_guard(
    manifest, tmp_path, monkeypatch
):
    target = "scripts/ceri_run104_controlled_replay.py"
    forged = tmp_path / "forged.py"
    forged.write_text("from app.db import SessionLocal\nprint('PASS')\n")
    original_file = registry._file
    monkeypatch.setattr(
        registry,
        "_file",
        lambda root, name: forged if name == target else original_file(root, name),
    )
    rows = lifecycle_rows(manifest)
    next(r for r in rows if r["entry_point"] == target)["current_source_sha256"] = (
        registry.source_sha256(forged)
    )
    with pytest.raises(
        registry.RegistryError, match="historical early execution restriction missing"
    ):
        registry._check_historical_tools(manifest, rows, registry.ROOT)


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "substituted"))
def test_frozen_tool_membership_cannot_lose_or_replace_a_baseline_entry(
    manifest, tmp_path, mutation
):
    rows = lifecycle_rows(manifest)
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = rows[0]
    else:
        rows[-1]["entry_point"] = "scripts/new_unreviewed_tool.py"
    target = tmp_path / manifest["standalone_registry"]
    target.parent.mkdir(parents=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(registry.RegistryError, match="inventory"):
        registry.validate_registry(manifest, root=tmp_path)


@pytest.mark.parametrize(
    "command",
    (
        "python scripts/forensics/repair_run43_market_data.py",
        "python -m scripts.forensics.repair_run43_market_data",
        "python docs/remediation/run41-lifecycle/resume_verified_run41.py",
    ),
)
def test_new_direct_ci_invocation_of_manual_tool_is_rejected(manifest, tmp_path, command):
    # Overlay only the workflow directory; every source read still uses the real checkout.
    workflow = tmp_path / "unsafe.yml"
    workflow.write_text("run: " + command + "\n", encoding="utf-8")

    class OverlayRoot:
        def __truediv__(self, relative):
            if relative == ".github/workflows":
                return tmp_path
            return registry.ROOT / relative

    with pytest.raises(registry.RegistryError, match="unapproved direct CI"):
        registry.validate_registry(manifest, root=OverlayRoot())
