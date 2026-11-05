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
    (synthetic / "pytest.ini").write_text("[pytest]\n")
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
    import shlex
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
    output = Path(next(
        option.removeprefix("--output=")
        for option in shlex.split(addopts) if option.startswith("--output=")
    ))
    if not output.is_absolute():
        output = workspace / output
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


def test_verification_detects_conflicting_editable_import_and_records_bound_origin(tmp_path):
    from scripts.qa.source_bound import bound_environment

    foreign = tmp_path / "conflicting-editable"
    (foreign / "app").mkdir(parents=True)
    (foreign / "app/__init__.py").write_text("")
    (foreign / "app/main.py").write_text("identity = 'another checkout'\n")
    receipts = tmp_path / "origins"
    root = release.ROOT
    env = bound_environment(root)
    env["PYTHONPATH"] = os.pathsep.join((str(foreign), str(root)))
    arguments = [
        "--checkout",
        str(root),
        "--receipt-dir",
        str(receipts),
        "--module",
        "scripts.docs.check_route_inventory",
    ]
    conflicting = subprocess.run(
        [sys.executable, str(root / "scripts/qa/source_bound.py"), *arguments],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert conflicting.returncode != 0
    assert "verification imported app.main" in conflicting.stderr
    assert str(foreign / "app/main.py") in conflicting.stderr
    assert not receipts.exists()
    correct = subprocess.run(
        [sys.executable, "-m", "scripts.qa.source_bound", *arguments],
        cwd=root,
        env=bound_environment(root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert correct.returncode == 0, correct.stdout + correct.stderr
    record = json.loads(next(receipts.glob("*.json")).read_text())
    assert record["checkout"] == str(root.resolve())
    assert Path(record["interpreter"]).resolve() == Path(sys.executable).resolve()
    assert record["origins"]["app.main"] == str((root / "app/main.py").resolve())
    assert record["origins"]["scripts.docs.check_route_inventory"] == str(
        (root / "scripts/docs/check_route_inventory.py").resolve()
    )


def test_native_node_reporter_inventory_matches_reviewed_exact_identities(tmp_path):
    spec = release.policy()["base_lanes"]["node"]
    result = subprocess.run(
        ["node", "--test", "--test-reporter=junit", *spec["configuration"]["files"]],
        cwd=release.ROOT,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()
    path = tmp_path / "node.xml"
    path.write_bytes(result.stdout)
    actual = release.junit_cases(path, "node")
    expected = spec["case_ids"]
    assert len(expected) == len(set(expected))
    assert set(actual) == set(expected)
    assert set(actual.values()) == {"passed"}
    assert {
        "node::" + prefix + outcome + suffix
        for outcome in ("resolve", "reject")
        for prefix, suffix in [
            ("old completion ", " cannot overwrite a resumed distinct refresh"),
            ("current completion ", " permits the next deliberate refresh exactly once"),
        ]
    } <= set(actual)


@pytest.mark.parametrize("fault", ["omitted", "unexpected", "duplicate"])
def test_native_inventory_rejects_incomplete_unexpected_and_duplicate_cases(experiment, fault):
    directory = experiment.root / "node"
    path = directory / "results.xml"
    cases = dict.fromkeys(experiment.plan["required_lanes"]["node"]["case_ids"], "passed")
    if fault == "omitted":
        cases.pop(next(iter(cases)))
    if fault == "unexpected":
        cases["node::undeclared native assertion"] = "passed"
    xml_report(path, cases, "node")
    if fault == "duplicate":
        tree = ET.parse(path)
        suite = tree.getroot().find("testsuite")
        suite.append(copy.deepcopy(suite.find("testcase")))
        tree.write(path)
        with pytest.raises(release.EvidenceError, match="duplicate test identity"):
            release.junit_cases(path, "node")
    else:
        observed = release.junit_cases(path, "node")
        with pytest.raises(release.EvidenceError, match="missing, uncollected or unexpected"):
            release._native_results(directory, experiment.plan, "node", observed)


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_owned_appenders_drain_before_real_recording_and_seal_validation(tmp_path, outcome):
    import signal
    import time

    import psutil

    from scripts.qa import owned_certification as owned
    from scripts.qa.source_bound import bound_environment

    plan = release.prepare_plan("owned-logger-" + outcome, "1", "local", False)
    plan_path = tmp_path / "plan.json"
    release.write_json(plan_path, plan)
    live, snapshot = tmp_path / "live", tmp_path / "sealed/a04"
    program = tmp_path / "appenders.py"
    program.write_text("""import json, os, signal, subprocess, sys, threading, time
from pathlib import Path
root=Path(sys.argv[1]); role=sys.argv[2]
stop=threading.Event()
signal.signal(signal.SIGINT, lambda *_: stop.set())
if hasattr(signal, "SIGBREAK"): signal.signal(signal.SIGBREAK, lambda *_: stop.set())
def append(name):
    with (root/name).open("a") as stream:
        while not stop.wait(.01):
            stream.write("owned append\\n"); stream.flush()
if role == "nested":
    append("child.log")
else:
    nested_env=dict(os.environ); nested_env.pop("SWINGLENS_CERTIFICATION_PROCESS_OWNER",None)
    child=subprocess.Popen([sys.executable,__file__,str(root),"nested"],env=nested_env)
    telemetry=threading.Thread(target=append,args=("telemetry.log",)); telemetry.start()
    (root/"ready.json").write_text(json.dumps({"pid":os.getpid(),"child":child.pid}))
    while not stop.wait(.01) and not (root/"finish").exists(): pass
    stop.set(); telemetry.join()
    raise SystemExit(3 if role == "failure" else 0)
""")
    runner = """import sys
from pathlib import Path
from scripts.qa import owned_certification as owner, release_evidence as release
raise SystemExit(owner.run_owned(Path(sys.argv[2]),release.read_json(Path(sys.argv[1])),"a04",
    [sys.executable,sys.argv[3],sys.argv[2],sys.argv[4]]))
"""
    guardian_path = tmp_path / "guardian.log"
    guardian_log = guardian_path.open("wb")
    guard = subprocess.Popen(
        [sys.executable, "-c", runner, str(plan_path), str(live), str(program), outcome],
        cwd=release.ROOT,
        env=bound_environment(release.ROOT),
        stdout=guardian_log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        deadline = time.monotonic() + 60
        while not (live / "ready.json").exists() and time.monotonic() < deadline:
            if guard.poll() is not None:
                pytest.fail(guardian_path.read_text(errors="replace"))
            time.sleep(0.05)
        assert (live / "ready.json").is_file()
        child_pid = json.loads((live / "ready.json").read_text())["child"]
        deadline = time.monotonic() + 10
        while child_pid not in {
            item["pid"] for item in release.read_json(live / owned.OWNER_FILE)["members"]
        }:
            assert time.monotonic() < deadline, "owned descendant was not observed"
            time.sleep(.05)
        deadline = time.monotonic() + 10
        while not (live / "child.log").exists() or not (live / "telemetry.log").exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        if outcome == "cancelled":
            guard.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        else:
            (live / "finish").write_text("complete the owned parent")
        guard.wait(timeout=30)
        assert guard.returncode == {"success": 0, "failure": 3, "cancelled": 130}[outcome], (
            guardian_path.read_text(errors="replace")
        )
        assert unrelated.poll() is None
        result = owned.snapshot_and_record(live, snapshot, plan, "a04", outcome)
        assert result["cleanup"]["quiescent"]
        assert result["receipt"]["verdict"] == "NONCERTIFIED"  # logger is never financial evidence
        assert result["receipt"]["job_result"] == outcome
        ownership = release.read_json(live / owned.OWNER_FILE)
        assert not owned._members(ownership, recover_orphans=True)[0]
        assert not owned._members(ownership, recover_orphans=True)[1]
        hashes = {
            name: release.file_digest(snapshot / name) for name in result["receipt"]["artifacts"]
        }
        assert hashes == result["receipt"]["artifacts"]
        with (live / "child.log").open("a") as stream:
            stream.write("late live-only diagnostic; snapshot must remain independent\n")
        assert hashes == {name: release.file_digest(snapshot / name) for name in hashes}
        assert psutil.pid_exists(unrelated.pid)
        jobs = {
            name: {"result": "cancelled" if outcome == "cancelled" else "failure"}
            for name in {"release-plan", *(x["job"] for x in plan["required_lanes"].values())}
        }
        verdict = release.aggregate(plan, snapshot.parent, jobs)
        assert verdict["verdict"] == "NONCERTIFIED"
        assert not any("artifact seal invalid" in reason for reason in verdict["reasons"])
        target = snapshot / "child.log"
        target.chmod(0o644)
        with target.open("a") as stream:
            stream.write("intentional post-seal mutation\n")
        invalid = release.aggregate(plan, snapshot.parent, jobs)
        assert any("a04: artifact seal invalid" in reason for reason in invalid["reasons"])
    finally:
        if guard.poll() is None:
            guard.terminate()
            guard.wait(timeout=10)
        if (live / owned.OWNER_FILE).exists():
            owned.drain(release.read_json(live / owned.OWNER_FILE))
        unrelated.terminate()
        unrelated.wait(timeout=10)
        guardian_log.close()


def test_active_guardian_refuses_to_seal_live_diagnostics(tmp_path):
    import psutil

    from scripts.qa import owned_certification as owned

    plan = release.prepare_plan("active-owner-refusal", "1", "local", False)
    live = tmp_path / "live"
    live.mkdir()
    release.write_json(
        live / owned.OWNER_FILE,
        {
            "context": release.context(plan, "a04"),
            "token": "not-inherited-by-any-child",
            "guardian": owned._identity(psutil.Process()),
            "members": [],
            "exit_code": None,
        },
    )
    (live / "growing.log").write_text("unsealed live data")
    result = owned.snapshot_and_record(live, tmp_path / "snapshot", plan, "a04", "cancelled")
    assert not result["cleanup"]["quiescent"]
    assert result["receipt"]["verdict"] == "NONCERTIFIED"
    assert (live / "UNSEALED.json").is_file()
    assert not (tmp_path / "snapshot/growing.log").exists()


@pytest.mark.parametrize("fault", ["same-root", "forced-success", "missing-owner"])
def test_a04_owned_evidence_wiring_cannot_bypass_cleanup(tmp_path, fault):
    manifest = registry.load_manifest()
    workflow = yaml.load(
        (registry.ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["production-runtime-certification"]["steps"]
    record = next(step for step in steps if step.get("name") == "Record release a04")
    if fault == "same-root":
        record["run"] = record["run"].replace("--snapshot-root test-results/release/a04",
            "--snapshot-root test-results/release-live/a04")
    elif fault == "forced-success":
        record["run"] = record["run"].replace('"${{ steps.release_a04.outcome }}"', '"success"')
    else:
        run = next(step for step in steps
                   if step.get("name") == "Run supported deterministic A04 certification")
        run["run"] = (
            "uv run python -m scripts.production_runtime_certification "
            "--provider-mode deterministic")
    target = tmp_path / ".github/workflows"
    target.mkdir(parents=True)
    (target / "ci.yml").write_text(yaml.safe_dump(workflow))
    with pytest.raises(registry.RegistryError):
        registry._check_full_release(manifest, tmp_path)


def test_missing_process_ownership_metadata_preserves_only_unsealed_diagnostics(tmp_path):
    from scripts.qa import owned_certification as owned

    plan = release.prepare_plan("missing-owner-metadata", "1", "local", False)
    live = tmp_path / "live"
    live.mkdir()
    (live / "growing.log").write_text("live writer cannot be proven stopped")
    result = owned.snapshot_and_record(live, tmp_path / "snapshot", plan, "a04", "cancelled")
    assert not result["cleanup"]["quiescent"]
    assert result["receipt"]["job_result"] == "cancelled"
    assert result["receipt"]["verdict"] == "NONCERTIFIED"
    assert (live / "UNSEALED.json").exists()
    assert not (tmp_path / "snapshot/growing.log").exists()
