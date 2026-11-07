"""Affected validation gives early feedback without inheriting release authority."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import production_runtime_certification as runtime
from scripts.qa import check_certification_registry as registry
from scripts.qa import release_evidence as release


def test_levels_separate_fast_native_and_full_path_proof():
    impact = registry.source_impact_plan(
        registry.load_manifest(), ["app/services/winner_probability/probability_estimator.py"]
    )
    fast = release.validation_selection(impact, "developer")
    integration = release.validation_selection(impact, "integration")
    assert set(fast) == {"affected-fast"}
    assert set(fast["affected-fast"]) <= set(integration["affected-fast"])
    assert integration["affected-postgresql"]
    pair = integration["native-pipeline-100-and-88-handoff"]
    assert len(pair) == 2
    assert any(node.endswith("[chromium-False-100]") for node in pair)
    assert any(node.endswith("[chromium-True-88]") for node in pair)
    # The required release selection is still broader until conditional proof exists.
    core = release.core_policy(impact)
    assert len([n for n in core["a04_prerequisites"]["selectors"]
                if "test_frozen_real_writer_cardinality" in n]) == 8
    assert set(core["base_lanes"]) == release.BASE_LANES


def test_unknown_impact_cannot_run_a_smaller_approved_selection():
    impact = registry.source_impact_plan(
        registry.load_manifest(), ["app/services/unowned_new_financial_writer.py"]
    )
    for level in ("developer", "integration"):
        with pytest.raises(release.EvidenceError, match="requires review"):
            release.validation_selection(impact, level)


@pytest.mark.parametrize("fault", ("none", "failure", "skip", "source-change"))
def test_validation_stops_invalid_groups_without_certifying(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(registry, "validate_registry", lambda _: None)
    monkeypatch.setattr(registry, "validate_source_contracts", lambda _: None)
    groups = {"first": ["tests/probe.py::test_first"],
              "second": ["tests/probe.py::test_second"]}
    monkeypatch.setattr(release, "validation_selection", lambda *args: groups)
    identities = iter([{"fingerprint": "before"},
                       {"fingerprint": "changed" if fault == "source-change" else "before"}])
    monkeypatch.setattr(release, "identity", lambda: next(identities))
    monkeypatch.setattr(runtime, "_test_resource_advisory", lambda: {})
    monkeypatch.setenv("SWINGLENS_RELEASE_LANE", "must-not-be-inherited")
    calls = []

    def execute(directory, environment, name, selectors):
        calls.append(name)
        assert "-x" in selectors
        assert not any(key.startswith("SWINGLENS_RELEASE_") for key in environment)
        outcome = '<skipped/>' if fault == "skip" else ''
        (directory / f"{name}.xml").write_text(
            '<testsuites><testsuite><testcase classname="tests.probe" name="test_'
            + name + '">' + outcome + '</testcase></testsuite></testsuites>'
        )
        code = 1 if fault == "failure" else 0
        return runtime.TestGroupResult(name, "FAIL" if code else "PASS", code, .1, [], "", "")

    monkeypatch.setattr(runtime, "_run_test_group", execute)
    impact_path = tmp_path / "impact.json"
    impact_path.write_text("{}")
    args = SimpleNamespace(validate_impact="developer", provider_mode="deterministic",
                           impact_plan=impact_path)
    directory = tmp_path / "validation"
    code = runtime._run_impact_validation(args, directory, "unit-specimen")
    report = json.loads((directory / "validation.json").read_text())
    assert report["certification"] == "NOT_EVALUATED"
    assert report["safe_for_normal_run"] is False
    assert (code == 0) == (fault == "none")
    if fault in {"failure", "skip"}:
        assert calls == ["first"]
        assert report["groups"]["second"]["status"] == "NOT_RUN"
    elif fault == "source-change":
        assert report["status"] == "BLOCKED" and "source changed" in report["reason"]


@pytest.mark.parametrize("fault", ("none", "slow", "unfinished", "missing", "negative"))
def test_integration_budgets_require_completed_native_stages(fault):
    snapshot = {"ticker_count": 100, "pipeline_status": "COMPLETED", "pipeline_seconds": 1500,
                "pipeline_steps": [{"step_name": "CAPTURING_WINNER_PREDICTIONS",
                    "status": "COMPLETED", "started_at": "2026-11-06T12:00:00+00:00",
                    "completed_at": "2026-11-06T12:05:00+00:00"}]}
    if fault == "slow":
        snapshot["pipeline_steps"][0]["completed_at"] = "2026-11-06T12:10:01+00:00"
    elif fault == "unfinished":
        snapshot["pipeline_status"] = "RUNNING"
    elif fault == "missing":
        snapshot["pipeline_steps"] = []
    elif fault == "negative":
        snapshot["pipeline_steps"][0]["completed_at"] = "2026-11-06T11:59:00+00:00"
    result = release.integration_performance(snapshot)
    assert (result["status"] == "PASS") == (fault == "none")
    assert result["pipeline_budget_seconds"] == 3600


def test_retention_rejects_normal_storage_before_connecting(tmp_path):
    release.write_json(tmp_path / ".test-output-owner.json",
                       {"owner": "SwingLens pytest", "execution_id": "test"})
    release.write_json(tmp_path / "environment.json", {"database_name": "swinglens",
                       "execution_id": "test", "database_safety_context": "NORMAL"})
    item = SimpleNamespace(funcargs={"certification_environment": SimpleNamespace(
        artifact_dir=tmp_path, execution_id="test",
        database_url="postgresql://unopened@127.0.0.1:1/swinglens",
    )})
    with pytest.raises(release.EvidenceError, match="exact owned disposable"):
        release._retain_integration_runtime(item, SimpleNamespace(when="call", outcome="failed"))


def test_prerequisites_fail_fast_without_dropping_required_cases(tmp_path, monkeypatch):
    directory = tmp_path / "source-impact-prerequisites"
    directory.mkdir()
    selectors = ["tests/native.py::test_first", "tests/native.py::test_second"]
    plan = {"a04_prerequisites": {"browsers": [], "selectors": selectors.copy()}}
    monkeypatch.setenv("SWINGLENS_RELEASE_PLAN", str(tmp_path / "plan.json"))
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    calls = []

    def execute(root, environment, name, selected):
        calls.append(selected)
        assert environment["SWINGLENS_RETAIN_NATIVE_RUNTIME"] == "true"
        (root / (name + ".xml")).write_text(
            '<testsuites><testsuite><testcase classname="tests.native" name="test_first">'
            '<failure/></testcase></testsuite></testsuites>'
        )
        return runtime.TestGroupResult(name, "FAIL", 1, .1, [], "", "")

    def incomplete(*args):
        raise release.EvidenceError("original failed and remaining native cases incomplete")

    monkeypatch.setattr(runtime, "_run_test_group", execute)
    monkeypatch.setattr(release, "_pytest_results", incomplete)
    with pytest.raises(release.EvidenceError, match="remaining native cases incomplete"):
        runtime._run_impact_prerequisites(tmp_path, {}, plan)
    assert calls == [("--maxfail=1", *selectors)]
    assert plan["a04_prerequisites"]["selectors"] == selectors


def test_required_benchmarks_enable_original_guards_without_mutating_environment():
    run8 = ("tests/integration/test_run8_setup_performance_postgresql.py"
            "::test_setup_capture_run8_scale_is_bounded")
    slse = ("tests/integration/test_slse_performance_certification.py"
            "::test_slse_phase_12_performance_certification")
    original = {"UNCHANGED": "value"}
    enabled = runtime._prerequisite_environment(original, [run8 + "[10]", slse])
    assert original == {"UNCHANGED": "value"}
    assert enabled == {**original, "SWINGLENS_FAIL_ON_REQUIRED_SKIP": "true",
                       "RUN_RUN8_REMEDIATION_BENCHMARK": "1",
                       "RUN_SLSE_PERFORMANCE_CERTIFICATION": "1"}
    assert runtime._prerequisite_environment(original, ["tests/other.py::test_case"]) == {
        **original, "SWINGLENS_FAIL_ON_REQUIRED_SKIP": "true"}


def test_required_prerequisites_check_native_and_small_populations_first():
    nodes = ["tests/e2e/native.py::test_case[chromium-False-100]",
             "tests/integration/native.py::test_scale[107]",
             "tests/integration/native.py::test_scale[10]",
             "tests/integration/native.py::test_scale[25]"]
    assert release.prerequisite_order(nodes) == [nodes[2], nodes[3], nodes[1], nodes[0]]
    assert nodes[0].startswith("tests/e2e/")


@pytest.mark.parametrize("path", ["tests/integration/test_unreviewed_runtime.py",
                                 "docs/testing/unreviewed-runtime.md",
                                 "tests/test_market_sector_future.py"])
def test_reviewed_impact_associations_do_not_swallow_new_unmapped_paths(path):
    from scripts.qa.evidence_reuse import cardinality_cases, reduced_spec

    impact = registry.source_impact_plan(
        registry.load_manifest(),
        ["app/services/winner_probability/probability_estimator.py", path])
    assert [item["path"] for item in impact["unknown_impact"]] == [path]
    plan = {"profile": "core", "event": "pull_request", "source_impact": impact,
            "a04_prerequisites": release.core_policy(impact)["a04_prerequisites"]}
    assert len(cardinality_cases(plan["a04_prerequisites"])) == 8
    with pytest.raises(release.EvidenceError, match="broader native browser obligation"):
        reduced_spec(plan)


@pytest.mark.parametrize("guard", ["true", "false"])
def test_required_skip_stops_after_original_teardown_and_preserves_junit(tmp_path, guard):
    conftest = "\n".join([
        "import importlib.util", "from pathlib import Path", "import pytest",
        "spec = importlib.util.spec_from_file_location('isolated_skip_adapter', "
        + repr(str(release.__file__)) + ")",
        "adapter = importlib.util.module_from_spec(spec)", "spec.loader.exec_module(adapter)",
        "adapter._capture = {'directory': Path(__file__).parent / 'diagnostics',",
        "                    'mode': 'execution', 'context': {'lane': 'a04-prerequisites-pr'}}",
        "@pytest.hookimpl(hookwrapper=True)",
        "def pytest_runtest_makereport(item, call):",
        "    yield from adapter.pytest_runtest_makereport(item, call)", "",
    ])
    (tmp_path / "conftest.py").write_text(conftest)
    (tmp_path / "test_probe.py").write_text("\n".join([
        "from pathlib import Path", "import pytest", "@pytest.fixture", "def resource():",
        "    yield", "    Path(__file__).with_name('teardown.done').touch()",
        "def test_a_required(resource):", "    pytest.skip('original required benchmark guard')",
        "def test_b_expensive():", "    Path(__file__).with_name('later.done').touch()", "",
    ]))
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith("SWINGLENS_RELEASE_")
                   and k not in {"PYTEST_PLUGINS", "PYTEST_ADDOPTS"}}
    environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", SWINGLENS_FAIL_ON_REQUIRED_SKIP=guard)
    child = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-o", "addopts=",
                            "--confcutdir", str(tmp_path),
                            "--junitxml", str(tmp_path / "result.xml"),
                            str(tmp_path / "test_probe.py")], cwd=tmp_path, env=environment,
                           capture_output=True, text=True, timeout=30)
    assert child.returncode == (1 if guard == "true" else 0), child.stdout + child.stderr
    assert (tmp_path / "teardown.done").exists()
    assert (tmp_path / "later.done").exists() == (guard == "false")
    import xml.etree.ElementTree as ET

    cases = ET.parse(tmp_path / "result.xml").findall(".//testcase")
    assert cases[0].find("skipped") is not None
    assert cases[0].find("failure") is None
    diagnostics = list((tmp_path / "diagnostics/failures").glob("*.json"))
    assert len(diagnostics) == (1 if guard == "true" else 0)
    if diagnostics:
        assert release.read_json(diagnostics[0])["outcome"] == "skipped"


@pytest.mark.parametrize("native_outcome,slow", (("failed", False), ("passed", True),
                                                ("passed", False)))
def test_failed_input_retention_preserves_original_report_and_certificate_files(
    tmp_path, monkeypatch, native_outcome, slow,
):
    from contextlib import contextmanager

    import psycopg

    artifact = tmp_path / "native-certificate"
    execution = "retention-test"
    database = "swinglens_pytest_cert_0123456789ab"
    release.write_json(artifact / ".test-output-owner.json",
                       {"owner": "SwingLens pytest", "execution_id": execution})
    release.write_json(artifact / "environment.json", {"database_name": database,
                       "execution_id": execution, "database_safety_context": "DISPOSABLE_TEST"})
    before = {p.name: p.read_bytes() for p in artifact.iterdir()}
    statements = []

    class Query:
        def __init__(self, text):
            self.text = text

        def fetchone(self):
            if "pipeline_runs" in self.text:
                return {"id": 2, "upload_run_id": 3,
                        "status": "COMPLETED" if native_outcome == "passed" else "FAILED",
                        "seconds": 1200}
            return {"total": 100}

        def fetchall(self):
            return [{"step_name": "CERI_PROVIDER_INGEST", "status": "COMPLETED",
                     "started_at": "2026-11-07T12:00:00+00:00",
                     "completed_at": ("2026-11-07T12:15:40+00:00" if slow
                                      else "2026-11-07T12:05:00+00:00"),
                     "retry_count": 0}]

    class Database:
        def execute(self, text, *args):
            statements.append(text)
            return Query(text)

    @contextmanager
    def connect(*args, **kwargs):
        yield Database()

    def dump(command, **kwargs):
        assert command[command.index("-d") + 1] == database
        assert kwargs["timeout"] == 60
        from pathlib import Path

        Path(command[-1]).write_bytes(b"controlled dump fixture; not native execution evidence")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(release.shutil, "which", lambda _: "pg_dump")
    monkeypatch.setattr(release.subprocess, "run", dump)
    item = SimpleNamespace(nodeid="tests/native.py::test_original_failure", funcargs={
        "certification_environment": SimpleNamespace(artifact_dir=artifact,
            execution_id=execution, database_url="postgresql://unopened@127.0.0.1/" + database),
    })
    report = SimpleNamespace(outcome=native_outcome, failed=native_outcome == "failed",
                             longrepr="original assertion")
    original_report = vars(report).copy()
    release._retain_integration_runtime(item, report)
    assert vars(report) == original_report
    assert {p.name: p.read_bytes() for p in artifact.iterdir()} == before
    feedback = tmp_path / "validation-feedback" / execution
    record = release.read_json(feedback / "validation-runtime.json")
    assert record["certification"] == "NOT_EVALUATED"
    assert record["original_outcome"] == native_outcome
    assert (record["performance"]["status"] == "PASS") == (
        native_outcome == "passed" and not slow)
    if native_outcome == "failed" or slow:
        assert record["retained_input"]["sha256"] == release.file_digest(
            feedback / "failed-input.dump")
    else:
        assert "retained_input" not in record
        assert not (feedback / "failed-input.dump").exists()
    assert any("READ ONLY" in statement for statement in statements)
