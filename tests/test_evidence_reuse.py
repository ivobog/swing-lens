"""Controlled receipt/adversarial inputs; no application certification is inferred."""

import copy
import json
import shlex
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.qa import check_certification_registry as registry
from scripts.qa import evidence_reuse as reuse
from scripts.qa import release_evidence as release


def _xml(path, cases, status="passed"):
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root,
        "testsuite",
        tests=str(len(cases)),
        failures=str(len(cases) if status == "failed" else 0),
        skipped=str(len(cases) if status == "skipped" else 0),
        errors="0",
    )
    for case in cases:
        child = ET.SubElement(suite, "testcase", name=case.split("::", 1)[1])
        if status != "passed":
            ET.SubElement(child, "failure" if status == "failed" else "skipped")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


@pytest.fixture
def native_reuse(tmp_path, monkeypatch):
    repo = tmp_path / "git"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()

    git("init", "-q")
    git("config", "user.email", "controlled@example.invalid")
    git("config", "user.name", "Controlled receipt test")
    for name in (
        "app/static/input.js",
        "tests/contract.cjs",
        "config/oracle.json",
        "tests/conftest.py",
        "tests/test_shared_fixture.py",
        "scripts/qa/source_bound.py",
        "scripts/qa/release_evidence.py",
        "pyproject.toml",
        "uv.lock",
    ):
        file = repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("original\n")
    git("add", ".")
    git("commit", "-qm", "controlled original inputs")
    policy = copy.deepcopy(release.policy())
    monkeypatch.setattr(release, "ROOT", repo)
    monkeypatch.setattr(release, "controlled_environment", lambda variables=None: "c" * 64)

    def source():
        sha = git("rev-parse", "HEAD")
        return {
            "revision": sha,
            "dirty": False,
            "fingerprint": release.hashlib.sha256(
                sha.encode() + b"\0status\0\0tracked-diff\0"
            ).hexdigest(),
        }

    monkeypatch.setattr(release, "identity", source)
    policy["base_lanes"]["node"]["case_ids"] = ["node::first", "node::second"]
    policy["base_lanes"]["node"]["configuration"]["files"] = ["tests/contract.cjs"]
    monkeypatch.setattr(release, "policy", lambda: policy)
    old = release.prepare_plan("101", "1", "pull_request", False)
    original = tmp_path / "native"
    original.mkdir()
    release.begin_native(original, old, "node")
    (original / "version.txt").write_text("v22.14.0")
    _xml(original / "results.xml", old["required_lanes"]["node"]["case_ids"])
    receipt = release.record_lane(original, old, "node", "success")
    assert receipt["verdict"] == "CERTIFIED"
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with zipfile.ZipFile(bundle / "plan.zip", "w") as archive:
        archive.writestr("plan.json", json.dumps(old))
    with zipfile.ZipFile(bundle / "evidence.zip", "w") as archive:
        for file in original.iterdir():
            archive.write(file, "node/" + file.name)
    origin = {
        "repository": reuse.REPOSITORY,
        "run_id": 101,
        "head_sha": old["source"]["revision"],
        "artifact_id": 201,
        "plan_artifact_id": 202,
    }
    release.write_json(bundle / "origin.json", origin)
    metadata = {
        201: {
            "expired": False,
            "workflow_run": {"id": 101},
            "digest": "sha256:" + release.file_digest(bundle / "evidence.zip"),
        },
        202: {
            "expired": False,
            "workflow_run": {"id": 101},
            "digest": "sha256:" + release.file_digest(bundle / "plan.zip"),
        },
    }

    def github(endpoint):
        if "/actions/runs/" in endpoint:
            return {
                "event": "pull_request",
                "head_repository": {"full_name": reuse.REPOSITORY},
                "head_sha": origin["head_sha"],
                "path": ".github/workflows/ci.yml",
            }
        return metadata[int(endpoint.rsplit("/", 1)[1])]

    monkeypatch.setattr(reuse, "_gh_json", github)

    def commit(name, contents):
        file = repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(contents)
        git("add", ".")
        git("commit", "-qm", "controlled dependency change")
        return release.prepare_plan("102", "1", "pull_request", False)

    new = commit("README.md", "unrelated documentation\n")
    return SimpleNamespace(
        old=old,
        plan=new,
        repo=repo,
        original=original,
        bundle=bundle,
        target=tmp_path / "carried",
        metadata=metadata,
        commit=commit,
        policy=policy,
        github=github,
        environment=receipt["results"]["environment"],
    )


def test_reuse_retains_original_source_and_revalidates_unchanged_native_cases(native_reuse):
    case = native_reuse
    proof = reuse.materialize(case.bundle, case.target, case.plan, "node", case.environment)
    assert proof["passed"] == ["node::first", "node::second"]
    assert proof["carry_forward"]["original_context"] == release.context(case.old, "node")
    assert release.read_json(case.target / "original/lane.json")["context"] != (
        release.context(case.plan, "node")
    )
    assert (case.target / "original/results.xml").read_bytes() == (
        case.original / "results.xml"
    ).read_bytes()
    recorded = release.record_lane(case.target, case.plan, "node", "success")
    assert recorded["verdict"] == "CERTIFIED"
    assert recorded == release.evaluate_lane(case.target, case.plan, "node", "success")
    assert recorded["results"]["carry_forward"]["origin"]["run_id"] == 101


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "selection",
        "environment",
        "artifact",
        "original-plan",
        "run",
        "failed-job",
        "missing-proof",
    ],
)
def test_reuse_faults_keep_the_lane_noncertified(native_reuse, fault):
    case = native_reuse
    reuse.materialize(case.bundle, case.target, case.plan, "node", case.environment)
    if fault == "source":
        case.plan = case.commit("app/static/input.js", "changed\n")
    elif fault == "selection":
        case.plan["required_lanes"]["node"]["case_ids"].append("node::omitted")
    elif fault == "environment":
        file = case.target / "current-environment.json"
        value = release.read_json(file)
        value["environment"]["tool_version"] = "v22.99.0"
        release.write_json(file, value)
    elif fault == "artifact":
        (case.target / "original/results.xml").write_text("changed native report")
    elif fault == "original-plan":
        file = case.target / "source-plan.json"
        value = release.read_json(file)
        value["attempt"] = "foreign"
        release.write_json(file, value)
    elif fault == "run":
        case.metadata[201]["workflow_run"]["id"] = 999
    elif fault == "missing-proof":
        (case.target / "current-environment.json").unlink()
    result = release.evaluate_lane(
        case.target, case.plan, "node", "failure" if fault == "failed-job" else "success"
    )
    assert result["verdict"] == "NONCERTIFIED"
    assert result["reasons"]


@pytest.mark.parametrize("name", ["app/static/input.js", "tests/contract.cjs", "uv.lock"])
def test_changed_native_inputs_require_a_fresh_run(native_reuse, name):
    case = native_reuse
    plan = case.commit(name, "changed tested dependency\n")
    with pytest.raises(release.EvidenceError, match="dependencies changed"):
        reuse.materialize(case.bundle, case.target, plan, "node", case.environment)
    assert not case.target.exists()


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_failing_original_outcomes_cannot_be_carried(native_reuse, status):
    case = native_reuse
    _xml(case.original / "results.xml", case.old["required_lanes"]["node"]["case_ids"], status)
    time_file = case.original / "native-file-time.json"
    times = release.read_json(time_file)
    times["results_sha256"] = release.file_digest(case.original / "results.xml")
    release.write_json(time_file, times)
    with pytest.raises(release.EvidenceError, match="failed/skipped/incomplete"):
        reuse.validate_original(case.original, case.old, "node")


@pytest.mark.parametrize(
    "name",
    [
        "config/oracle.json",
        "tests/conftest.py",
        "tests/test_shared_fixture.py",
        "scripts/qa/source_bound.py",
    ],
)
def test_prerequisite_dependencies_include_oracles_and_imported_helpers(native_reuse, name):
    case = native_reuse
    before = reuse.dependency_manifest(case.plan["source"], reuse.PREREQUISITES)
    plan = case.commit(name, "changed financial/test input\n")
    assert before != reuse.dependency_manifest(plan["source"], reuse.PREREQUISITES)


def test_producer_only_change_preserves_native_prerequisite_inputs(native_reuse):
    case = native_reuse
    before = reuse.dependency_manifest(case.plan["source"], reuse.PREREQUISITES)
    plan = case.commit("scripts/qa/release_evidence.py", "changed current validator\n")
    assert before == reuse.dependency_manifest(plan["source"], reuse.PREREQUISITES)


def test_A04_dirty_source_and_missing_environment_never_reuse(native_reuse):
    case = native_reuse
    with pytest.raises(release.EvidenceError, match="reviewed reusable"):
        reuse.dependency_manifest(case.plan["source"], "a04")
    with pytest.raises(release.EvidenceError, match="immutable clean"):
        reuse.dependency_manifest({**case.plan["source"], "dirty": True}, "node")
    with pytest.raises(release.EvidenceError, match="not recorded"):
        reuse._stable_profile({"python": "3.12.16"})
    with pytest.raises(release.EvidenceError, match="current whole-source"):
        reuse.check_reuse(case.target, case.plan, "a04", {})


def test_unknown_reuse_keeps_original_selection_without_replacing_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    plan = {"event": "pull_request", "a04_prerequisites": {"selectors": ["original"]}}
    assert not reuse.try_previous_run(tmp_path / "missing", plan, reuse.PREREQUISITES)
    assert plan["a04_prerequisites"]["selectors"] == ["original"]
    assert not (tmp_path / "missing").exists()


def test_controlled_environment_hash_detects_overrides_without_exposing_values(
    tmp_path,
    monkeypatch,
):
    first = reuse.controlled_environment({"IB_CLIENT_ID": "1", "DATABASE_URL": "first-owned-db"})
    second = reuse.controlled_environment({"IB_CLIENT_ID": "2", "DATABASE_URL": "second-owned-db"})
    assert first != second
    assert first == reuse.controlled_environment({"IB_CLIENT_ID": "1", "DATABASE_URL": "other"})
    monkeypatch.setattr(release, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("untracked configuration")
    assert reuse.controlled_environment({}) == ""


def test_unsafe_native_archive_is_rejected_before_materialization(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../outside", "must not write")
    with pytest.raises(release.EvidenceError, match="unsafe"):
        reuse._extract(archive, tmp_path / "owned", "")
    assert not (tmp_path / "outside").exists()


def _matrix_plan():
    prefix = (
        "tests/e2e/single_run_certification/test_real_writer_cardinality.py"
        "::test_frozen_real_writer_cardinality"
    )
    nodes = [
        f"{prefix}[{engine}-{handoff}-{size}]"
        for engine in ("chromium", "firefox")
        for handoff in ("False", "True")
        for size in (88, 100)
    ]
    plan = {
        "profile": "core",
        "event": "pull_request",
        "execution_id": "controlled-current",
        "attempt": "1",
        "source": {"fingerprint": "f" * 64, "revision": "a" * 40},
        "source_impact": {"baseline_known": True, "review_required": False, "unknown_impact": []},
        "a04_prerequisites": {
            "kind": "pytest",
            "required": True,
            "selectors": nodes + ["tests/integration/probe.py::test_native"],
            "browsers": ["chromium", "firefox"],
            "markers": "",
            "ignores": [],
            "postgres_major": 18,
            "conditional_pr_cardinality": reuse.CARDINALITY_RULE,
        },
    }
    plan["plan_sha256"] = release.digest(plan)
    return plan


@pytest.mark.parametrize(
    "fault",
    [
        "local",
        "push",
        "schedule",
        "workflow_dispatch",
        "diagnostic",
        "unknown-impact",
        "review",
        "baseline",
        "missing-case",
        "rule",
    ],
)
def test_conditional_cadence_retains_broader_obligations_when_proof_is_ineligible(fault):
    plan = _matrix_plan()
    if fault in {"local", "push", "schedule", "workflow_dispatch"}:
        plan["event"] = fault
    elif fault == "diagnostic":
        plan["profile"] = "diagnostic"
    elif fault == "unknown-impact":
        plan["source_impact"]["unknown_impact"] = [{"risk": "HIGH"}]
    elif fault == "review":
        plan["source_impact"]["review_required"] = True
    elif fault == "baseline":
        plan["source_impact"]["baseline_known"] = False
    elif fault == "missing-case":
        plan["a04_prerequisites"]["selectors"].pop(0)
    else:
        plan["a04_prerequisites"].pop("conditional_pr_cardinality")
    with pytest.raises(release.EvidenceError, match="broader native browser obligation"):
        reuse.reduced_spec(plan)


@pytest.fixture
def conditional_native(tmp_path, monkeypatch):
    plan = _matrix_plan()
    original = copy.deepcopy(plan["a04_prerequisites"])
    spec = reuse.reduced_spec(plan)
    assert plan["a04_prerequisites"] == original
    assert len(reuse.cardinality_cases(spec)) == 2
    directory = tmp_path / "prerequisites"
    directory.mkdir()
    basis = directory / "matrix-basis"
    basis.mkdir()
    release.write_json(basis / "current-environment.json", {"environment": {"controlled": True}})
    original_proof = {
        "passed": original["selectors"],
        "carry_forward": {"original_context": {"execution_id": "controlled-original-matrix"}},
    }

    def validated_basis(*args, **kwargs):
        assert kwargs["purpose"] == "matrix-basis"
        return original_proof

    monkeypatch.setattr(reuse, "check_reuse", validated_basis)
    profile = {
        "python": "3.12.16",
        "pytest": "controlled-fixture",
        "provider_mode": "deterministic",
        "database_safety_context": "DISPOSABLE_TEST",
        "database": {"host": "127.0.0.1", "name": "swinglens_pytest_controlled"},
        "postgresql": {"version": "PostgreSQL 18.0 controlled", "postmaster_started_at": "owned"},
    }
    release.write_json(
        directory / "collection.json",
        {
            "context": release.context(plan, reuse.PR_SELECTED),
            "selection": {
                key: spec[key] for key in ("selectors", "markers", "ignores", "browsers")
            },
            "selected": spec["selectors"],
            "exit_code": 0,
            "errors": [],
        },
    )
    release.write_json(
        directory / "execution.json",
        {
            "context": release.context(plan, reuse.PR_SELECTED),
            "selected": spec["selectors"],
            "started": spec["selectors"],
            "session_finished": True,
            "exit_code": 0,
            "errors": [],
            "environment": profile,
            "browsers_observed": {"chromium": "controlled-version"},
            "phases": {
                node: {
                    phase: {"outcome": "passed", "wasxfail": False, "seconds": 0.1}
                    for phase in ("setup", "call", "teardown")
                }
                for node in spec["selectors"]
            },
        },
    )
    xml = ET.Element("testsuites")
    suite = ET.SubElement(
        xml, "testsuite", tests=str(len(spec["selectors"])), failures="0", errors="0", skipped="0"
    )
    for node in spec["selectors"]:
        classname, name = release.junit_key(node).rsplit("::", 1)
        ET.SubElement(suite, "testcase", classname=classname, name=name)
    ET.ElementTree(xml).write(directory / "results.xml")
    feedback = tmp_path / "feedback"
    for index, node in enumerate(reuse.cardinality_cases(spec)):
        release.write_json(
            feedback / str(index) / "validation-runtime.json",
            {
                "nodeid": node,
                "source_fingerprint": plan["source"]["fingerprint"],
                "original_outcome": "passed",
                "ticker_count": 100 if node.endswith("-100]") else 88,
                "pipeline_status": "COMPLETED",
                "pipeline_seconds": 900,
                "pipeline_steps": [
                    {
                        "step_name": "winner",
                        "status": "COMPLETED",
                        "started_at": "2026-11-06T22:00:00+00:00",
                        "completed_at": "2026-11-06T22:05:00+00:00",
                    }
                ],
            },
        )
    return SimpleNamespace(plan=plan, directory=directory, feedback=feedback, basis=original_proof)


def test_conditional_pr_deferral_requires_current_native_pair_and_never_marks_six_passed(
    conditional_native,
):
    case = conditional_native
    result = reuse.validate_pr_selection(case.directory, case.plan, feedback_root=case.feedback)
    assert len(result["deferred"]) == 6
    assert {row["outcome"] for row in result["deferred"]} == {"NOT_RUN"}
    assert result["deferred_is_pass"] is False
    assert len(result["current_native"]["passed"]) == 3
    assert len(case.plan["a04_prerequisites"]["selectors"]) == 9


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "wrong-source",
        "failed-native",
        "over-budget",
        "incomplete-matrix",
        "missing-call",
        "foreign-session",
    ],
)
def test_missing_or_failed_conditional_proofs_cannot_defer_the_matrix(conditional_native, fault):
    case = conditional_native
    file = case.feedback / "0/validation-runtime.json"
    if fault == "missing":
        file.unlink()
    elif fault in {"wrong-source", "failed-native", "over-budget"}:
        record = release.read_json(file)
        if fault == "wrong-source":
            record["source_fingerprint"] = "stale"
        elif fault == "failed-native":
            record["original_outcome"] = "failed"
        else:
            record["pipeline_steps"][0]["completed_at"] = "2026-11-06T22:20:00+00:00"
        release.write_json(file, record)
    elif fault == "incomplete-matrix":
        case.basis["passed"] = case.basis["passed"][1:]
    else:
        path = case.directory / "execution.json"
        execution = release.read_json(path)
        if fault == "missing-call":
            execution["phases"][execution["selected"][0]].pop("call")
        else:
            execution["context"]["execution_id"] = "foreign"
        release.write_json(path, execution)
    with pytest.raises(release.EvidenceError):
        reuse.validate_pr_selection(case.directory, case.plan, feedback_root=case.feedback)


@pytest.mark.parametrize(
    "name",
    ["app/static/input.js", "tests/conftest.py", "tests/e2e/single_run_certification/driver.py"],
)
def test_changed_ui_or_native_driver_requires_the_full_matrix(native_reuse, name):
    case = native_reuse
    before = reuse.dependency_manifest(case.plan["source"], reuse.UI_BOUNDARY)
    plan = case.commit(name, "changed UI/driver input\n")
    assert before != reuse.dependency_manifest(plan["source"], reuse.UI_BOUNDARY)


def test_current_backend_pair_can_refresh_without_invalidating_unchanged_ui_basis(native_reuse):
    case = native_reuse
    before = reuse.dependency_manifest(case.plan["source"], reuse.UI_BOUNDARY)
    plan = case.commit(
        "app/services/winner_probability/probability_estimator.py", "changed backend\n"
    )
    assert before == reuse.dependency_manifest(plan["source"], reuse.UI_BOUNDARY)
    assert reuse.dependency_manifest(case.plan["source"], reuse.PREREQUISITES) != (
        reuse.dependency_manifest(plan["source"], reuse.PREREQUISITES)
    )


def test_native_input_date_and_environment_must_be_complete():
    plan = {"created_at": datetime.now(UTC).isoformat()}
    with pytest.raises(release.EvidenceError, match="input date"):
        reuse._original_environment({"environment": {}, "browsers_observed": {}}, plan, "pytest")


@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
def test_release_cadences_keep_all_eight_native_browser_variants(event):
    impact = registry.source_impact_plan(
        registry.load_manifest(), ["config/certification_registry.json"]
    )
    contract = release.core_policy(impact, event=event)
    assert len(reuse.cardinality_cases(contract["a04_prerequisites"])) == 8
    assert contract["a04_prerequisites"]["browsers"] == ["chromium", "firefox"]
    assert set(contract["base_lanes"]) == release.BASE_LANES


def test_diagnostic_controls_keep_their_own_native_call_phase(tmp_path):
    """Reproduce the CI recorder failure with real collection and execution."""
    import os

    selected = [
        "tests/test_full_release_certification.py::" + name
        for name in (
            "test_failure_is_retained_before_session_finish_without_certifying",
            "test_failure_diagnostic_io_error_preserves_original_native_outcome",
            "test_passing_native_report_does_not_write_failure_diagnostics",
        )
    ]
    bootstrap = textwrap.dedent("""\
        import copy, os, json, sys
        from pathlib import Path
        from scripts.qa import release_evidence as release
        from scripts.qa.source_bound import record_origins
        mode, directory, selected_json = sys.argv[1:]
        directory = Path(directory)
        selected = json.loads(selected_json)
        policy = copy.deepcopy(release.policy())
        # A controlled recorder experiment, never an application/release proof.
        policy["base_lanes"]["python-regressions"].update(
            selectors=selected, markers="", ignores=[], browsers=[], postgres_major=None)
        release.policy = lambda: policy
        plan_file = directory / "plan.json"
        if not plan_file.exists():
            release.write_json(plan_file, release.prepare_plan(
                "native-recorder-protocol-control", "1", "local", False))
        record_origins(release.ROOT, directory / "origins", (
            "scripts.qa.release_evidence", "app.settings"))
        os.environ.update(SWINGLENS_RELEASE_PLAN=str(plan_file),
            SWINGLENS_RELEASE_LANE="python-regressions", SWINGLENS_RELEASE_CAPTURE=mode,
            SWINGLENS_RELEASE_RECORD_DIR=str(directory))
        import pytest
        args = ["-q", "-p", "scripts.qa.release_evidence", "-p", "no:cacheprovider",
            "--output", str(directory / "browser-scratch"),
            "--basetemp", str(directory / (mode + "-tmp")), *selected]
        args += (["--collect-only"] if mode == "collection" else
                 ["--junitxml", str(directory / "results.xml")])
        raise SystemExit(pytest.main(args))
    """)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SWINGLENS_RELEASE_")
        and key not in {"PYTEST_PLUGINS", "PYTEST_ADDOPTS"}
    }
    environment.update(
        PYTHONPATH=str(release.ROOT), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"
    )
    for mode in ("collection", "execution"):
        child = subprocess.run(
            [sys.executable, "-B", "-c", bootstrap, mode, str(tmp_path), json.dumps(selected)],
            cwd=release.ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        (tmp_path / (mode + ".log")).write_text(child.stdout + child.stderr, encoding="utf-8")
        assert child.returncode == 0, child.stdout + child.stderr
    plan = release.read_json(tmp_path / "plan.json")
    proof = release._pytest_results(
        tmp_path,
        plan,
        "python-regressions",
        release.junit_cases(tmp_path / "results.xml", "pytest"),
    )
    assert proof["passed"] == selected
    assert proof["body_executed"] == selected
    assert all(set(proof["phases"][node]) == {"setup", "call", "teardown"} for node in selected)
    assert not (tmp_path / "lane.json").exists()
    assert not (tmp_path / "certificate.json").exists()


def test_prerequisite_archive_prefix_matches_the_owned_ci_snapshot():
    workflow = yaml.load(
        (release.ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    steps = workflow["jobs"]["production-runtime-certification"]["steps"]
    run = shlex.split(
        next(
            s["run"]
            for s in steps
            if s.get("name") == "Run supported deterministic A04 certification"
        )
    )
    record = shlex.split(next(s["run"] for s in steps if s.get("name") == "Record release a04"))
    upload = next(
        s["with"]["path"] for s in steps if s.get("name") == "Publish source-bound release evidence"
    )
    live = Path(run[run.index("--live-root") + 1])
    producer = Path(run[run.index("--report-dir") + 1]).relative_to(live)
    snapshot = Path(record[record.index("--snapshot-root") + 1]).relative_to(Path(upload))
    prefix = (snapshot / producer / "source-impact-prerequisites").as_posix() + "/"
    assert reuse.artifact_prefix(reuse.PREREQUISITES) == prefix


def test_complete_postgresql_prerequisite_receipt_can_be_carried_from_owned_artifact(
    native_reuse,
    tmp_path,
    monkeypatch,
):
    case = native_reuse
    case.commit("tests/contract.py", "# Controlled native receipt fixture\n")
    nodes = ["tests/contract.py::test_first", "tests/contract.py::test_second"]
    spec = {
        "kind": "pytest",
        "required": True,
        "selectors": nodes,
        "markers": "",
        "ignores": [],
        "browsers": [],
        "postgres_major": 18,
    }
    case.policy["a04_prerequisites"] = spec

    def plan_for(execution):
        plan = release.prepare_plan(str(execution), "1", "pull_request", False)
        plan.pop("plan_sha256")
        plan["a04_prerequisites"] = copy.deepcopy(spec)
        plan["plan_sha256"] = release.digest(plan)
        return plan

    old, current = plan_for(103), plan_for(104)
    stamp = datetime.now(UTC)
    profile = {
        "python": "3.12.16",
        "pytest": "controlled-unit-receipt",
        "provider_mode": "deterministic",
        "platform": "controlled-unit-platform",
        "controlled_environment_sha256": "c" * 64,
        "hardware": {"controlled": True},
        "input_date": stamp.date().isoformat(),
        "database_safety_context": "DISPOSABLE_TEST",
        "database": {"host": "127.0.0.1", "name": "swinglens_pytest_controlled"},
        "postgresql": {
            "version": "PostgreSQL 18.0 controlled-unit-receipt",
            "postmaster_started_at": "controlled-owned-instance",
        },
    }
    original = tmp_path / "pg-native"
    original.mkdir()
    release.write_json(
        original / "collection.json",
        {
            "context": release.context(old, reuse.PREREQUISITES),
            "selected": nodes,
            "selection": {
                key: spec[key] for key in ("selectors", "markers", "ignores", "browsers")
            },
            "exit_code": 0,
            "errors": [],
        },
    )
    release.write_json(
        original / "execution.json",
        {
            "context": release.context(old, reuse.PREREQUISITES),
            "selected": nodes,
            "started": nodes,
            "session_finished": True,
            "exit_code": 0,
            "errors": [],
            "environment": profile,
            "finished_at": stamp.isoformat(),
            "browsers_observed": {},
            "phases": {
                node: {
                    phase: {"outcome": "passed", "wasxfail": False, "seconds": 0.1}
                    for phase in ("setup", "call", "teardown")
                }
                for node in nodes
            },
        },
    )
    xml = ET.Element("testsuites")
    suite = ET.SubElement(xml, "testsuite", tests="2", failures="0", errors="0", skipped="0")
    for node in nodes:
        classname, name = release.junit_key(node).rsplit("::", 1)
        ET.SubElement(suite, "testcase", classname=classname, name=name)
    ET.ElementTree(xml).write(original / "results.xml")
    bundle = tmp_path / "pg-bundle"
    bundle.mkdir()
    with zipfile.ZipFile(bundle / "plan.zip", "w") as archive:
        archive.writestr("plan.json", json.dumps(old))
    with zipfile.ZipFile(bundle / "evidence.zip", "w") as archive:
        for file in original.iterdir():
            archive.write(file, reuse.artifact_prefix(reuse.PREREQUISITES) + file.name)
    release.write_json(
        bundle / "origin.json",
        {
            "repository": reuse.REPOSITORY,
            "run_id": 103,
            "head_sha": old["source"]["revision"],
            "artifact_id": 201,
            "plan_artifact_id": 202,
        },
    )
    metadata = {
        index: {
            "expired": False,
            "workflow_run": {"id": 103},
            "digest": "sha256:" + release.file_digest(bundle / filename),
        }
        for index, filename in ((201, "evidence.zip"), (202, "plan.zip"))
    }

    def github(endpoint):
        return (
            {
                "event": "pull_request",
                "head_repository": {"full_name": reuse.REPOSITORY},
                "head_sha": old["source"]["revision"],
                "path": ".github/workflows/ci.yml",
            }
            if "/actions/runs/" in endpoint
            else metadata[int(endpoint.rsplit("/", 1)[1])]
        )

    monkeypatch.setattr(reuse, "_gh_json", github)
    expected = reuse._original_environment(
        reuse.validate_original(original, old, reuse.PREREQUISITES), old, "pytest"
    )
    target = tmp_path / "carried-prerequisites"
    proof = reuse.materialize(bundle, target, current, reuse.PREREQUISITES, expected)
    assert proof["passed"] == nodes
    assert proof["carry_forward"]["original_context"] == release.context(old, reuse.PREREQUISITES)
    assert reuse.check_reuse(target, current, reuse.PREREQUISITES, expected) == proof
