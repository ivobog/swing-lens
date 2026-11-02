"""Release decisions from controlled artifacts; these are not application runs."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from single_run_certification.reporting import EXACT_TEST_IDS, PINNED_SOURCE, REQUIRED_ARTIFACTS
from single_run_certification.test_exact_pipeline_certification import specimen as a04_specimen

from scripts import production_runtime_certification as runtime
from scripts.qa import check_certification_registry as registry
from scripts.qa import release_evidence as release


def xml_report(path, cases, kind):
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root,
        "testsuite",
        tests=str(len(cases)),
        failures=str(sum(s == "failed" for s in cases.values())),
        errors=str(sum(s == "errored" for s in cases.values())),
        skipped=str(sum(s == "skipped" for s in cases.values())),
    )
    for node, status in cases.items():
        class_name, name = (
            release.junit_key(node).rsplit("::", 1)
            if kind == "pytest"
            else ("native", node.split("::", 1)[1])
        )
        case = ET.SubElement(suite, "testcase", name=name, classname=class_name)
        if status != "passed":
            ET.SubElement(
                case, {"failed": "failure", "errored": "error", "skipped": "skipped"}[status]
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    from single_run_certification import candidate_validation

    # These controlled receipts describe the retained v3 oracle. They must not
    # silently claim verification of the repository's current candidate.
    monkeypatch.setattr(candidate_validation, "candidate_available", lambda: False)
    policy = copy.deepcopy(release.policy())
    for name, spec in policy["base_lanes"].items():
        if spec["kind"] == "pytest":
            spec.update(
                selectors=["tests/probe_" + name.replace("-", "_") + ".py"], markers="", ignores=[]
            )
    monkeypatch.setattr(release, "policy", lambda: policy)
    monkeypatch.setattr(
        release,
        "identity",
        lambda: {"revision": PINNED_SOURCE, "fingerprint": "f" * 64, "dirty": False},
    )
    plan = release.prepare_plan("synthetic-contract-only", "1", "local", False)
    root = tmp_path / "lanes"
    for name, spec in plan["required_lanes"].items():
        directory = root / name
        directory.mkdir(parents=True)
        if spec["kind"] == "pytest":
            selected = [spec["selectors"][0] + "::test_ok"]
            if spec["browsers"]:
                selected = [
                    spec["selectors"][0] + "::test_ok[" + browser + "]"
                    for browser in spec["browsers"]
                ]
            release.write_json(
                directory / "collection.json",
                {
                    "context": release.context(plan, name),
                    "selected": selected,
                    "selection": {
                        k: spec[k] for k in ("selectors", "markers", "ignores", "browsers")
                    },
                    "collect_only": True,
                    "exit_code": 0,
                    "errors": [],
                },
            )
            release.write_json(
                directory / "execution.json",
                {
                    "context": release.context(plan, name),
                    "selected": selected,
                    "started": selected,
                    "session_finished": True,
                    "exit_code": 0,
                    "errors": [],
                    "phases": {
                        node: {
                            phase: {"outcome": "passed", "seconds": 0.001, "wasxfail": False}
                            for phase in ("setup", "call", "teardown")
                        }
                        for node in selected
                    },
                    "environment": {
                        "kind": "synthetic-unit-control",
                        "python": "3.12.0",
                        "pytest": "synthetic-version",
                        "provider_mode": "deterministic",
                        "database_safety_context": "DISPOSABLE_TEST",
                        "database": {"host": "127.0.0.1", "name": "swinglens_ci_synthetic"},
                        "postgresql": {
                            "version": "PostgreSQL 18.0 synthetic",
                            "postmaster_started_at": "synthetic-identity",
                        },
                    },
                    "browsers_observed": {b: "synthetic-version" for b in spec["browsers"]},
                },
            )
        elif spec["kind"] != "a04":
            release.begin_native(directory, plan, name)
            (directory / "version.txt").write_text(
                "v22.14.0" if name == "node" else "promtool, version 3.5.0 (synthetic)"
            )
            selected = spec["case_ids"]
        else:
            continue
        xml_report(directory / "results.xml", dict.fromkeys(selected, "passed"), spec["kind"])
    # Reuse A04's own existing full specimens and finalizer, never a new oracle.
    certificates = []
    for mode in (False, True):
        specimen = a04_specimen.__wrapped__(SimpleNamespace(param=mode))
        specimen = json.loads(
            json.dumps(specimen).replace("isolated-specimen", "isolated-specimen-" + str(mode))
        )
        directory = root / "a04" / "a04" / str(mode)
        for relative in REQUIRED_ARTIFACTS:
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            value = specimen["bundle"].get(relative, {})
            if relative.endswith(".jsonl"):
                target.write_text("\n".join(json.dumps(row) for row in value), encoding="utf-8")
            elif relative.endswith(".png"):
                target.write_bytes(b"A04 existing isolated screenshot specimen")
            else:
                target.write_text(json.dumps(value), encoding="utf-8")
        from single_run_certification.reporting import finalize_pipeline_certificate

        certificate = finalize_pipeline_certificate(
            directory, invocation=specimen["invocation"], expected_identity=specimen["identity"]
        )
        assert certificate["verdict"] == "CERTIFIED", certificate["reasons"]
        certificates.append(
            {
                "path": str(mode) + "/certificate.json",
                "test_id": EXACT_TEST_IDS[int(mode)],
                "execution_id": specimen["identity"]["execution_id"],
                "verdict": "CERTIFIED",
            }
        )
    release.write_json(
        root / "a04/a04/report.json",
        {
            "release_context": release.context(plan, "a04"),
            "working_tree_fingerprint": plan["source"]["fingerprint"],
            "git_sha": PINNED_SOURCE,
            "execution_id": "specimen-invocation",
            "invocation": specimen["invocation"],
            "certificates": certificates,
            "provider_mode": "deterministic",
            "verdict": "CERTIFIED",
        },
    )
    jobs = {
        job: {"result": "success"}
        for job in {"release-plan", *(spec["job"] for spec in plan["required_lanes"].values())}
    }
    return SimpleNamespace(plan=plan, root=root, jobs=jobs, policy=policy)


def record_all(experiment):
    return {
        name: release.record_lane(experiment.root / name, experiment.plan, name, "success")
        for name in experiment.plan["required_lanes"]
    }


def test_all_required_evidence_certifies_only_the_synthetic_contract(experiment):
    receipts = record_all(experiment)
    assert all(r["verdict"] == "CERTIFIED" for r in receipts.values())
    result = release.aggregate(experiment.plan, experiment.root, experiment.jobs)
    assert result["verdict"] == "CERTIFIED", result["reasons"]
    assert not result["safe_for_normal_run"]
    assert result["gates"]["SMALL_LIVE_PROVIDER_CANARY"] == "NOT_RUN"
    assert all(row["status"] == "NOT_RUN" for row in result["advisory_manual"].values())


def test_current_candidate_cannot_reuse_historical_a04_receipts(experiment, monkeypatch):
    from single_run_certification import candidate_validation

    monkeypatch.setattr(candidate_validation, "candidate_available", lambda: True)
    receipt = release.record_lane(
        experiment.root / "a04", experiment.plan, "a04", "success",
    )
    assert receipt["verdict"] != "CERTIFIED"
    assert "candidate" in str(receipt["reasons"])


@pytest.mark.parametrize("fault", ["skip", "no-body", "missing", "stale", "foreign"])
def test_specialized_prerequisite_receipts_cannot_be_skipped_or_borrowed(experiment, fault):
    plan = release.prepare_core_plan(
        "synthetic-prerequisite-control", "1", "local", False,
        registry.source_impact_plan(
            registry.load_manifest(), ["app/services/pipeline_service.py"],
        ),
    )
    spec = plan["a04_prerequisites"]
    assert len(spec["selectors"]) == 10
    directory = experiment.root / "a04/a04/source-impact-prerequisites"
    directory.mkdir(parents=True)
    source = experiment.root / "python-regressions"
    collection = release.read_json(source / "collection.json")
    execution = release.read_json(source / "execution.json")
    nodes = spec["selectors"]
    scope = release.context(plan, "a04-prerequisites")
    collection.update(
        context=scope, selected=nodes,
        selection={key: spec[key] for key in ("selectors", "markers", "ignores", "browsers")},
    )
    execution.update(
        context=scope, selected=nodes, started=nodes,
        browsers_observed={browser: True for browser in spec["browsers"]},
        phases={node: {phase: {"outcome": "passed", "seconds": .001, "wasxfail": False}
                       for phase in ("setup", "call", "teardown")} for node in nodes},
    )
    cases = {node: "passed" for node in nodes}
    if fault == "skip":
        execution["phases"][nodes[0]]["call"]["outcome"] = "skipped"
        cases[nodes[0]] = "skipped"
    elif fault == "no-body":
        execution["phases"][nodes[0]].pop("call")
    elif fault == "stale":
        execution["context"] = {**scope, "attempt": "different-attempt"}
    elif fault == "foreign":
        collection["selected"] = ["tests/foreign.py::test_ok"]
    release.write_json(directory / "collection.json", collection)
    release.write_json(directory / "execution.json", execution)
    xml_report(directory / "results.xml", cases, "pytest")
    if fault == "missing":
        (directory / "collection.json").unlink()
    report_path = experiment.root / "a04/a04/report.json"
    report = release.read_json(report_path)
    report.update(
        release_context=release.context(plan, "a04"), specialized_prerequisites=nodes,
    )
    release.write_json(report_path, report)
    with pytest.raises(release.EvidenceError):
        release.a04_results(experiment.root / "a04", plan, "a04")


@pytest.mark.parametrize(
    "fault",
    [
        "failed",
        "setup_error",
        "skip",
        "xfail",
        "not_collected",
        "interrupted",
        "false_zero_exit",
        "missing_report",
        "wrong_a04_source",
        "missing_browser",
        "missing_node",
        "missing_promql",
        "partial_a04",
        "missing_a04_gate",
        "invalid_seal",
        "stale_execution",
        "inconsistent_count",
        "zero_tests",
        "failed_job",
        "different_selection",
        "foreign_case",
        "blocked",
        "cancelled_job",
        "missing_environment",
        "wrong_postgres",
        "wrong_tool",
        "native_failure",
        "stale_native_xml",
        "wrong_attempt",
    ],
)
def test_required_violations_never_certify(experiment, fault):
    root, plan = experiment.root, experiment.plan
    directory = root / "python-regressions"
    collection = release.read_json(directory / "collection.json")
    node = collection["selected"][0]
    if fault in {"failed", "setup_error", "skip", "xfail", "false_zero_exit"}:
        state = (
            "failed"
            if fault in {"failed", "false_zero_exit"}
            else "errored"
            if fault == "setup_error"
            else "skipped"
        )
        xml_report(directory / "results.xml", {node: state}, "pytest")
        execution = release.read_json(directory / "execution.json")
        phase = "setup" if fault in {"setup_error", "skip"} else "call"
        execution["phases"][node][phase].update(
            outcome="failed" if state in {"failed", "errored"} else "skipped",
            wasxfail=fault == "xfail",
        )
        if phase == "setup":
            execution["phases"][node].pop("call")
        release.write_json(directory / "execution.json", execution)
    elif fault in {"not_collected", "interrupted", "foreign_case"}:
        execution = release.read_json(directory / "execution.json")
        if fault == "not_collected":
            execution["selected"] = []
        elif fault == "foreign_case":
            execution["selected"] = ["tests/foreign.py::test_ok"]
        else:
            execution["phases"][node].pop("teardown")
        release.write_json(directory / "execution.json", execution)
    elif fault in {"missing_report", "zero_tests", "inconsistent_count"}:
        if fault == "missing_report":
            (directory / "results.xml").unlink()
        elif fault == "zero_tests":
            xml_report(directory / "results.xml", {}, "pytest")
        else:
            xml = ET.parse(directory / "results.xml")
            xml.find("testsuite").set("tests", "5000")
            xml.write(directory / "results.xml")
    elif fault in {"wrong_a04_source", "partial_a04", "missing_a04_gate"}:
        path = root / "a04/a04/False/certificate.json"
        certificate = release.read_json(path)
        if fault == "wrong_a04_source":
            certificate["identity"]["source_fingerprint"] = "stale"
        elif fault == "partial_a04":
            certificate["verdict"] = "PARTIAL"
        else:
            certificate["gates"].pop(next(iter(certificate["gates"])))
        release.write_json(path, certificate)
    elif fault == "different_selection":
        collection["selection"]["markers"] = "unit"
        release.write_json(directory / "collection.json", collection)
    elif fault in {"blocked", "missing_environment", "wrong_postgres"}:
        path = directory / "execution.json"
        execution = release.read_json(path)
        if fault == "blocked":
            execution.update(started=[], phases={})
            (directory / "results.xml").unlink()
        elif fault == "missing_environment":
            execution["environment"] = {}
        else:
            execution["environment"]["postgresql"]["version"] = "PostgreSQL 17.0"
        release.write_json(path, execution)
    elif fault in {"wrong_tool", "native_failure", "stale_native_xml"}:
        directory = root / "node"
        if fault == "wrong_tool":
            (directory / "version.txt").write_text("v18.0.0")
        elif fault == "native_failure":
            xml_report(
                directory / "results.xml",
                {
                    n: "failed" if i == 0 else "passed"
                    for i, n in enumerate(plan["required_lanes"]["node"]["case_ids"])
                },
                "node",
            )
        else:
            os.utime(directory / "results.xml", (1, 1))
    record_all(experiment)
    if fault.startswith("missing_") and fault in {
        "missing_browser",
        "missing_node",
        "missing_promql",
    }:
        shutil.rmtree(
            root
            / {
                "missing_browser": "browser-smoke",
                "missing_node": "node",
                "missing_promql": "promql",
            }[fault]
        )
    elif fault == "invalid_seal":
        (directory / "results.xml").write_text("changed after receipt")
    elif fault == "stale_execution":
        path = directory / "lane.json"
        receipt = release.read_json(path)
        receipt["context"]["execution_id"] = "old-run"
        release.write_json(path, receipt)
    elif fault == "failed_job":
        experiment.jobs["lint-test-migrations"]["result"] = "failure"
    elif fault == "cancelled_job":
        experiment.jobs["browser-smoke"]["result"] = "cancelled"
    elif fault == "wrong_attempt":
        path = directory / "lane.json"
        receipt = release.read_json(path)
        receipt["context"]["attempt"] = "old-attempt"
        release.write_json(path, receipt)
    elif fault == "blocked":
        path = directory / "lane.json"
        path.unlink()
        release.record_lane(directory, plan, "python-regressions", "skipped")
    result = release.aggregate(plan, root, experiment.jobs)
    assert result["verdict"] == "NONCERTIFIED", fault
    assert (
        result["gates"]["FULL_RELEASE_REGRESSIONS"] == "FAIL" and not result["safe_for_normal_run"]
    )
    assert result["reasons"]
    if fault in {"failed", "false_zero_exit", "native_failure"}:
        lane = "node" if fault == "native_failure" else "python-regressions"
        assert result["lanes"][lane]["counts"]["failed"] == 1
    if fault == "blocked":
        assert result["lanes"]["python-regressions"]["counts"]["blocked"] == 1


def test_no_terminal_receipt_may_be_overwritten(experiment):
    record_all(experiment)
    with pytest.raises(release.EvidenceError, match="replace"):
        release.record_lane(experiment.root / "node", experiment.plan, "node", "success")


def test_extended_applicability_is_explicit_and_manual_live_is_never_selected(experiment):
    for event, enabled, extra in [
        ("pull_request", False, set()),
        ("schedule", False, {"ceri-extended"}),
        ("workflow_dispatch", True, {"ceri-extended", "population-extended"}),
    ]:
        plan = release.prepare_plan("synthetic", "1", event, enabled)
        assert set(plan["required_lanes"]) == release.BASE_LANES | extra
        assert "live-provider-canary" not in plan["required_lanes"]
        assert plan["verdict"] == "NOT_RUN"


@pytest.mark.parametrize("lane", ["node", "promql", "browser-smoke", "windows-recovery", "a04"])
def test_policy_cannot_drop_required_language_browser_or_native_lanes(lane):
    policy = copy.deepcopy(release.policy())
    policy["base_lanes"].pop(lane)
    with pytest.raises(release.EvidenceError, match="omitted"):
        release.validate_policy(policy)


def test_existing_ci_jobs_and_authoritative_producer_are_wired():
    manifest = registry.load_manifest()
    registry._check_full_release(manifest, registry.ROOT)
    workflow = yaml.load(
        (registry.ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    browser = next(
        s
        for s in workflow["jobs"]["browser-smoke"]["steps"]
        if s.get("name") == "Run browser smoke"
    )["run"]
    assert "--browser chromium --browser firefox" in browser
    assert "not live_provider" in browser
    assert all(
        "--ignore=" + path in browser
        for path in manifest["full_release"]["base_lanes"]["windows-recovery"]["selectors"]
    )
    assert (
        "--aggregate-release"
        in registry.registered_gate(manifest, "FULL_RELEASE_REGRESSIONS")["command"]
    )
    assert runtime.EXACT_TEST_IDS == EXACT_TEST_IDS


@pytest.mark.parametrize(
    "fault",
    ["node", "promql", "collection", "preflight", "soft_job", "soft_gate", "aggregate", "needs"],
)
def test_ci_wiring_omissions_and_downgrades_are_rejected(tmp_path, fault):
    manifest = registry.load_manifest()
    workflow = yaml.load(
        (registry.ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    jobs = workflow["jobs"]
    if fault in {"node", "promql"}:
        title = manifest["full_release"]["base_lanes"][fault]["step"]
        jobs["cross-language"]["steps"] = [
            s for s in jobs["cross-language"]["steps"] if s.get("name") != title
        ]
    elif fault == "collection":
        jobs["browser-smoke"]["steps"] = [
            s
            for s in jobs["browser-smoke"]["steps"]
            if s.get("name") != "Collect release browser-smoke"
        ]
    elif fault in {"preflight", "soft_gate"}:
        step = next(s for s in jobs["lint-test-migrations"]["steps"] if s.get("name") == "Lint")
        step["if" if fault == "preflight" else "continue-on-error"] = "true"
    elif fault == "soft_job":
        jobs["cross-language"]["continue-on-error"] = "true"
    elif fault == "aggregate":
        jobs["full-release"]["if"] = "success()"
    else:
        jobs["full-release"]["needs"].remove("cross-language")
    target = tmp_path / ".github/workflows/ci.yml"
    target.parent.mkdir(parents=True)
    target.write_text(yaml.safe_dump(workflow))
    with pytest.raises(registry.RegistryError):
        registry._check_full_release(manifest, tmp_path)


@pytest.mark.parametrize(
    "fault", ["pass", "setup_error", "skip", "xfail", "missing_collection", "interrupted"]
)
def test_actual_pytest_terminal_observer(experiment, tmp_path, monkeypatch, fault):
    """Run only tiny disposable tests against an isolated copy of the observer.

    The identity stub is a test-side provenance specimen, not an application or
    worker substitute. It lets each failure test reach the intended boundary.
    """
    synthetic = tmp_path / "observer-project"
    adapter = synthetic / "scripts/qa/release_evidence.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_bytes(Path(release.__file__).read_bytes())
    (synthetic / "scripts/production_runtime_certification.py").write_text(
        "def _git(*args): return " + repr(PINNED_SOURCE) + "\n"
        "def _working_tree_identity(): return " + repr(("f" * 64, False)) + "\n"
    )
    policy = copy.deepcopy(experiment.policy)
    policy["base_lanes"]["python-regressions"].update(
        selectors=["tests/probe.py"], markers="", ignores=[], browsers=[], postgres_major=None
    )
    release.write_json(synthetic / "config/certification_registry.json", {"full_release": policy})
    monkeypatch.setattr(release, "policy", lambda: policy)
    plan = release.prepare_plan("synthetic-observer-" + fault, "1", "local", False)
    plan_path = synthetic / "plan.json"
    release.write_json(plan_path, plan)
    probe = synthetic / "tests/probe.py"
    probe.parent.mkdir()
    body = "import pytest\ndef test_a(): pass\n"
    if fault == "setup_error":
        body += (
            "@pytest.fixture\ndef missing(): raise AssertionError('controlled setup error')\n"
            "def test_target(missing): pass\n"
        )
    elif fault == "skip":
        body += "@pytest.mark.skip(reason='controlled skip')\ndef test_target(): pass\n"
    elif fault == "xfail":
        body += "@pytest.mark.xfail(reason='controlled xfail')\ndef test_target(): assert False\n"
    elif fault == "interrupted":
        body += (
            "def test_target(): pytest.exit('controlled interruption', returncode=2)\n"
            "def test_z(): pass\n"
        )
    else:
        body += "def test_target(): pass\n"
    probe.write_text(body)
    directory = synthetic / "evidence"
    env = {
        **os.environ,
        "PYTHONPATH": str(synthetic),
        "PYTEST_PLUGINS": "scripts.qa.release_evidence",
        "SWINGLENS_RELEASE_PLAN": str(plan_path),
        "SWINGLENS_RELEASE_LANE": "python-regressions",
        "SWINGLENS_RELEASE_RECORD_DIR": str(directory),
        "SWINGLENS_RELEASE_CAPTURE": "collection",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/probe.py"]
    collected = subprocess.run(
        [*command, "--collect-only"],
        cwd=synthetic,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    if fault == "missing_collection":
        probe.write_text("def test_a(): pass\n")
    env["SWINGLENS_RELEASE_CAPTURE"] = "execution"
    executed = subprocess.run(
        [*command, "--junitxml=" + str(directory / "results.xml")],
        cwd=synthetic,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    receipt = release.record_lane(
        directory, plan, "python-regressions", "success" if executed.returncode == 0 else "failure"
    )
    assert receipt["verdict"] == ("CERTIFIED" if fault == "pass" else "NONCERTIFIED"), (
        executed.stdout + executed.stderr
    )
    if fault in {"skip", "xfail"}:
        assert receipt["counts"]["skipped"] == 1
    if fault == "setup_error":
        assert receipt["counts"]["errored"] == 1
    if fault in {"missing_collection", "interrupted"}:
        assert receipt["counts"]["incomplete"] >= 1



def test_real_playwright_session_cleanup_keeps_source_bound_plan(experiment, tmp_path):
    import subprocess
    import sys

    import yaml

    workspace = tmp_path / "cleanup-workspace"
    workspace.mkdir()
    original = release.ROOT
    workflow = yaml.load(
        (original / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    scratch = tmp_path / "owned runner temp"
    addopts = workflow["env"]["PYTEST_ADDOPTS"].replace("${{ runner.temp }}", str(scratch))
    plan_path = workspace / "test-results/release-plan/plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_bytes = json.dumps(experiment.plan, sort_keys=True).encode()
    release.validate_plan(experiment.plan)
    plan_path.write_bytes(plan_bytes)
    output = scratch / "swinglens-playwright-output"
    output.mkdir(parents=True)
    stale = output / "prior-scratch-marker"
    stale.write_text("owned scratch only")
    (workspace / "test_cleanup_probe.py").write_text(
        "from pathlib import Path\n"
        "def test_plan_is_still_available_after_session_cleanup():\n"
        "    assert Path('test-results/release-plan/plan.json').is_file()\n"
    )
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SWINGLENS_RELEASE_", "SWINGLENS_TEST_OUTPUT_"))
           and k not in {"PYTEST_PLUGINS", "PYTEST_ADDOPTS"}}
    env["PYTEST_ADDOPTS"] = addopts
    for collect in (True, False):
        args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "test_cleanup_probe.py"]
        if collect:
            args.append("--collect-only")
        child = subprocess.run(args, cwd=workspace, env=env, capture_output=True,
                               text=True, timeout=45)
        assert child.returncode == 0, child.stdout + child.stderr
        assert plan_path.read_bytes() == plan_bytes
    assert not stale.exists(), "The real Playwright session cleanup must have executed"
