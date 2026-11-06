"""Affected validation gives early feedback without inheriting release authority."""

import json
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


def test_failed_input_retention_preserves_original_report_and_certificate_files(
    tmp_path, monkeypatch,
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
                return {"id": 2, "upload_run_id": 3, "status": "FAILED", "seconds": 8}
            return {"total": 100}

        def fetchall(self):
            return []

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
    report = SimpleNamespace(outcome="failed", failed=True, longrepr="original assertion")
    release._retain_integration_runtime(item, report)
    assert report.outcome == "failed" and report.failed
    assert {p.name: p.read_bytes() for p in artifact.iterdir()} == before
    feedback = tmp_path / "validation-feedback" / execution
    record = release.read_json(feedback / "validation-runtime.json")
    assert record["certification"] == "NOT_EVALUATED"
    assert record["original_outcome"] == "failed"
    assert record["retained_input"]["sha256"] == release.file_digest(feedback / "failed-input.dump")
    assert any("READ ONLY" in statement for statement in statements)
