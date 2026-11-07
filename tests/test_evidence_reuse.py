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

_REAL_CHECK_REUSE = reuse.check_reuse


@pytest.fixture
def fresh_native(conditional_native):
    case = conditional_native
    plan = case.plan
    plan.update(schema="swinglens.full-release-plan.v1",
                conditional_pr_lifecycle_scale=reuse.LIFECYCLE_SCALE_RULE)
    plan["a04_prerequisites"]["selectors"] = (
        reuse.cardinality_cases(plan["a04_prerequisites"])
        + sorted(reuse.REQUIRED_NATIVE_CASES) + [reuse.LIFECYCLE_SCALE_CASE]
    )
    plan["plan_sha256"] = release.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    spec = reuse.fresh_matrix_spec(plan)
    scope = release.context(plan, reuse.PR_FRESH_MATRIX)
    collection = release.read_json(case.directory / "collection.json")
    collection.update(context=scope, selected=spec["selectors"], selection={
        key: spec[key] for key in ("selectors", "markers", "ignores", "browsers")})
    release.write_json(case.directory / "collection.json", collection)
    execution = release.read_json(case.directory / "execution.json")
    execution.update(context=scope, selected=spec["selectors"], started=spec["selectors"],
        browsers_observed={name: "controlled-version" for name in spec["browsers"]},
        phases={node: {phase: {"outcome": "passed", "wasxfail": False, "seconds": 0.1}
                       for phase in ("setup", "call", "teardown")} for node in spec["selectors"]})
    release.write_json(case.directory / "execution.json", execution)
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests="14", failures="0", errors="0", skipped="0")
    for node in spec["selectors"]:
        classname, name = release.junit_key(node).rsplit("::", 1)
        ET.SubElement(suite, "testcase", classname=classname, name=name)
    ET.ElementTree(root).write(case.directory / "results.xml")
    return case


def test_current_full_matrix_defers_only_scale_after_all_fourteen_cases_pass(
    fresh_native, monkeypatch,
):
    case = fresh_native

    def forbidden(*_args, **_kwargs):
        pytest.fail("current matrix must not trust historical runner evidence")

    monkeypatch.setattr(reuse, "check_reuse", forbidden)
    proof = reuse.validate_pr_selection(case.directory, case.plan, feedback_root=case.feedback)
    assert len(proof["current_native"]["passed"]) == 14
    assert set(proof["current_native"]["passed"]) == set(proof["selected"])
    assert len(proof["basis"]["validated_ui"]) == 8
    assert proof["context"] == release.context(case.plan, reuse.PR_FRESH_MATRIX)
    assert proof["deferred"] == [{"test_id": reuse.LIFECYCLE_SCALE_CASE,
        "outcome": "NOT_RUN", "reason": reuse.LIFECYCLE_SCALE_RULE}]
    assert proof["deferred_is_pass"] is False
    assert len(case.plan["a04_prerequisites"]["selectors"]) == 15


@pytest.mark.parametrize("index", range(14))
@pytest.mark.parametrize("fault", ["missing-call", "failed", "skipped"])
def test_current_matrix_requires_every_native_and_ui_case(fresh_native, index, fault):
    case = fresh_native
    path = case.directory / "execution.json"
    execution = release.read_json(path)
    node = execution["selected"][index]
    if fault == "missing-call":
        execution["phases"][node].pop("call")
    else:
        execution["phases"][node]["call"]["outcome"] = fault
    release.write_json(path, execution)
    with pytest.raises(release.EvidenceError):
        reuse.validate_pr_selection(case.directory, case.plan, feedback_root=case.feedback)
    assert len(case.plan["a04_prerequisites"]["selectors"]) == 15


@pytest.mark.parametrize("fault", ["wrong-source", "missing-pair", "over-budget", "no-firefox",
                                   "foreign", "unfinished", "unknown-impact", "unapproved"])
def test_current_matrix_rejects_missing_foreign_or_unapproved_proof(fresh_native, fault):
    case = fresh_native
    if fault in {"wrong-source", "missing-pair", "over-budget"}:
        path = case.feedback / "0/validation-runtime.json"
        if fault == "missing-pair":
            path.unlink()
        else:
            runtime = release.read_json(path)
            if fault == "wrong-source":
                runtime["source_fingerprint"] = "foreign"
            else:
                runtime["pipeline_steps"][0]["completed_at"] = "2026-11-06T22:20:00+00:00"
            release.write_json(path, runtime)
    elif fault == "unknown-impact":
        case.plan["source_impact"]["unknown_impact"] = [{"path": "unreviewed.py"}]
    elif fault == "unapproved":
        case.plan["conditional_pr_lifecycle_scale"] = "unapproved"
    else:
        path = case.directory / "execution.json"
        execution = release.read_json(path)
        if fault == "no-firefox":
            execution["browsers_observed"].pop("firefox")
        elif fault == "foreign":
            execution["context"]["execution_id"] = "foreign"
        else:
            execution["session_finished"] = False
        release.write_json(path, execution)
    with pytest.raises(release.EvidenceError):
        reuse.validate_pr_selection(case.directory, case.plan, feedback_root=case.feedback)


@pytest.mark.parametrize("fault", [None, "local", "push", "schedule", "workflow_dispatch",
                                   "unknown", "unapproved"])
def test_prerequisite_selection_acquires_current_matrix_without_relaxing_history(
    fresh_native, fault,
):
    plan = fresh_native.plan
    if fault in {"local", "push", "schedule", "workflow_dispatch"}:
        plan["event"] = fault
    elif fault == "unknown":
        plan["source_impact"]["unknown_impact"] = [{"path": "unreviewed.py"}]
    elif fault == "unapproved":
        plan.pop("conditional_pr_lifecycle_scale")
    before = copy.deepcopy(plan)
    spec, lane = reuse.prerequisite_selection(plan, False)
    assert plan == before
    if fault is None:
        assert lane == reuse.PR_FRESH_MATRIX
        assert len(spec["selectors"]) == 14
        assert reuse.LIFECYCLE_SCALE_CASE not in spec["selectors"]
        assert len(reuse.cardinality_cases(spec)) == 8
        assert reuse.REQUIRED_NATIVE_CASES <= set(spec["selectors"])
        historical, historical_lane = reuse.prerequisite_selection(plan, True)
        assert historical_lane == reuse.PR_SELECTED
        assert len(historical["selectors"]) == 8
    else:
        assert lane == reuse.PREREQUISITES
        assert spec == plan["a04_prerequisites"]
        assert len(spec["selectors"]) == 15


def test_current_matrix_original_scope_can_supply_only_ui_reuse(
    native_reuse, fresh_native, tmp_path, monkeypatch,
):
    case = native_reuse
    monkeypatch.setattr(reuse, "check_reuse", _REAL_CHECK_REUSE)
    ui = reuse.cardinality_cases(fresh_native.plan["a04_prerequisites"])[0].split("::")[0]
    case.commit(ui, "# Controlled original UI source\n")
    case.policy.update(a04_prerequisites=copy.deepcopy(fresh_native.plan["a04_prerequisites"]),
        conditional_pr_lifecycle_scale=reuse.LIFECYCLE_SCALE_RULE, selection_manifest={})
    monkeypatch.setattr(release, "core_policy", lambda *_args, **_kwargs: case.policy)
    impact = fresh_native.plan["source_impact"]
    old = release.prepare_core_plan("101", "1", "pull_request", False, impact)
    directory = tmp_path / "original-current-matrix"
    directory.mkdir()
    for name in ("collection.json", "execution.json"):
        capture = release.read_json(fresh_native.directory / name)
        capture["context"] = release.context(old, reuse.PR_FRESH_MATRIX)
        if name == "execution.json":
            capture.update(started_at=old["created_at"], finished_at=datetime.now(UTC).isoformat())
            profile = capture["environment"]
            profile.update(platform="controlled-native", input_date=old["created_at"][:10],
                controlled_environment_sha256="c" * 64,
                hardware={"cpu_count": 4, "machine": "controlled", "processor": "controlled",
                          "memory_bytes": 16 * 1024**3})
        release.write_json(directory / name, capture)
    (directory / "results.xml").write_bytes((fresh_native.directory / "results.xml").read_bytes())
    with zipfile.ZipFile(case.bundle / "plan.zip", "w") as archive:
        archive.writestr("plan.json", json.dumps(old))
    with zipfile.ZipFile(case.bundle / "evidence.zip", "w") as archive:
        for path in directory.iterdir():
            archive.write(path, reuse.artifact_prefix(reuse.PREREQUISITES) + path.name)
    for artifact, name in ((201, "evidence.zip"), (202, "plan.zip")):
        case.metadata[artifact]["digest"] = "sha256:" + release.file_digest(case.bundle / name)
    case.commit("scripts/qa/evidence_reuse.py", "# Changed producer only\n")
    current = release.prepare_core_plan("102", "1", "pull_request", False, impact)
    original_execution = release.read_json(directory / "execution.json")
    environment = reuse._original_environment(original_execution, old, "pytest")
    checked = reuse.materialize(case.bundle, case.target, current, reuse.PREREQUISITES,
                                environment, purpose="matrix-basis")
    assert len(checked["validated_subset"]) == 8
    assert len(checked["passed"]) == 14
    assert checked["carry_forward"]["original_context"] == release.context(
        old, reuse.PR_FRESH_MATRIX
    )
    assert release.read_json(case.target / "original/execution.json") == original_execution
    assert (case.target / "original/results.xml").read_bytes() == (
        directory / "results.xml"
    ).read_bytes()
    with pytest.raises(release.EvidenceError):
        reuse.validate_original(case.target / "original", old, reuse.PREREQUISITES)


def test_native_producer_obtains_current_matrix_when_history_is_ineligible(
    fresh_native, monkeypatch,
):
    import shutil

    from scripts import production_runtime_certification as runtime

    case = fresh_native
    root = case.directory.parent
    selected = reuse.fresh_matrix_spec(case.plan)["selectors"]
    before = copy.deepcopy(case.plan)
    monkeypatch.setenv("SWINGLENS_RELEASE_PLAN", str(root / "plan.json"))
    monkeypatch.setattr(reuse, "try_previous_run", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runtime, "_prepare_prerequisite_browsers", lambda *_args: None)

    def forbidden(*_args, **_kwargs):
        pytest.fail("current proof acquisition must not consult a foreign historical matrix")

    monkeypatch.setattr(reuse, "check_reuse", forbidden)
    directory = root / "source-impact-prerequisites"
    directory.mkdir()
    for name in ("collection.json", "execution.json"):
        shutil.copyfile(case.directory / name, directory / name)
    feedback = root / "runtime-evidence/validation-feedback"
    shutil.copytree(case.feedback, feedback)

    def collection(command, **kwargs):
        assert "--collect-only" in command
        assert command[-14:] == selected
        assert kwargs["env"]["SWINGLENS_RELEASE_LANE"] == reuse.PR_FRESH_MATRIX
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def execute(output, environment, name, cases):
        assert environment["RUN_RUN8_REMEDIATION_BENCHMARK"] == "1"
        assert "RUN_SLSE_PERFORMANCE_CERTIFICATION" not in environment
        assert environment["SWINGLENS_FAIL_ON_REQUIRED_SKIP"] == "true"
        assert cases == ("--maxfail=1", *selected)
        shutil.copyfile(case.directory / "results.xml", output / (name + ".xml"))
        return runtime.TestGroupResult(name, "PASS", 0, .1, [], "", "")

    monkeypatch.setattr(runtime.subprocess, "run", collection)
    monkeypatch.setattr(runtime, "_run_test_group", execute)
    result = runtime._run_impact_prerequisites(root, {}, case.plan)
    disposition = release.read_json(directory / "pr-disposition.json")
    assert result.exit_code == 0
    assert disposition["selected"] == selected
    assert disposition["deferred"] == [{"test_id": reuse.LIFECYCLE_SCALE_CASE,
        "outcome": "NOT_RUN", "reason": reuse.LIFECYCLE_SCALE_RULE}]
    assert case.plan == before


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


def test_aggregate_recovers_authenticated_original_commit_missing_from_checkout(
    native_reuse, tmp_path, monkeypatch,
):
    case = native_reuse
    aggregate = tmp_path / "aggregate-checkout"
    subprocess.run(["git", "clone", "--no-local", "--depth", "1", case.repo.as_uri(),
                    str(aggregate)], check=True, capture_output=True)
    revision = case.old["source"]["revision"]
    missing = subprocess.run(["git", "-C", str(aggregate), "cat-file", "-e",
                              revision + "^{commit}"], capture_output=True)
    assert missing.returncode != 0
    monkeypatch.setattr(release, "ROOT", aggregate)
    proof = reuse.materialize(case.bundle, case.target, case.plan, "node", case.environment)
    assert proof["passed"] == ["node::first", "node::second"]
    assert subprocess.run(["git", "-C", str(aggregate), "cat-file", "-e",
                           revision + "^{commit}"], capture_output=True).returncode == 0
    recorded = release.record_lane(case.target, case.plan, "node", "success")
    assert recorded["verdict"] == "CERTIFIED"
    assert recorded == release.evaluate_lane(case.target, case.plan, "node", "success")
    assert (case.target / "original/results.xml").read_bytes() == (
        case.original / "results.xml").read_bytes()


def test_original_commit_fetch_requires_authenticated_archives_first(native_reuse, monkeypatch):
    case = native_reuse
    reuse.materialize(case.bundle, case.target, case.plan, "node", case.environment)
    origin = release.read_json(case.target / "origin.json")
    origin["repository"] = "foreign/repository"
    release.write_json(case.target / "origin.json", origin)
    fetched = []
    monkeypatch.setattr(reuse, "_ensure_original_commit", lambda source: fetched.append(source))
    with pytest.raises(release.EvidenceError, match="foreign artifact repository"):
        reuse.check_reuse(case.target, case.plan, "node", case.environment)
    assert fetched == []


@pytest.mark.parametrize("change", ["reserved-page", "memory-capacity", "cpu-count", "cpu-model",
                                   "unknown-memory"])
def test_resource_profile_ignores_reserved_page_noise_but_rejects_changed_capacity(change):
    original = {"controlled_environment_sha256": "c" * 64,
                "hardware": {"cpu_count": 4, "machine": "x86_64",
                             "processor": "AMD EPYC 9V74 80-Core Processor",
                             "memory_bytes": 16759439360}}
    candidate = copy.deepcopy(original)
    if change == "reserved-page":
        candidate["hardware"]["memory_bytes"] += 4096
    elif change == "memory-capacity":
        candidate["hardware"]["memory_bytes"] -= 1024 * 1024
    elif change == "cpu-count":
        candidate["hardware"]["cpu_count"] = 2
    elif change == "cpu-model":
        candidate["hardware"]["processor"] = "different processor"
    else:
        candidate["hardware"].pop("memory_bytes")
        with pytest.raises(release.EvidenceError, match="memory capacity is unknown"):
            reuse._stable_profile(candidate)
        assert original["hardware"]["memory_bytes"] == 16759439360
        return
    assert (reuse._stable_profile(original) == reuse._stable_profile(candidate)) == (
        change == "reserved-page")
    assert original["hardware"]["memory_bytes"] == 16759439360
    assert "memory_mib" not in original["hardware"]


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
def conditional_native(tmp_path, monkeypatch, request):
    plan = _matrix_plan()
    if getattr(request, "param", False):
        plan["conditional_pr_lifecycle_scale"] = reuse.LIFECYCLE_SCALE_RULE
        plan["a04_prerequisites"]["selectors"] = (
            reuse.cardinality_cases(plan["a04_prerequisites"])
            + sorted(reuse.REQUIRED_NATIVE_CASES) + [reuse.LIFECYCLE_SCALE_CASE]
        )
        plan["plan_sha256"] = release.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
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


@pytest.mark.parametrize("conditional_native", [True], indirect=True)
def test_approved_lifecycle_pr_deferral_requires_eight_current_passes_and_seven_not_run(
    conditional_native,
):
    case = conditional_native
    result = reuse.validate_pr_selection(case.directory, case.plan, feedback_root=case.feedback)
    assert len(result["selected"]) == 8
    assert reuse.REQUIRED_NATIVE_CASES <= set(result["current_native"]["passed"])
    assert set(result["current_native"]["passed"]) == set(result["selected"])
    assert len(result["deferred"]) == 7
    assert {row["outcome"] for row in result["deferred"]} == {"NOT_RUN"}
    scale = next(row for row in result["deferred"]
                 if row["test_id"] == reuse.LIFECYCLE_SCALE_CASE)
    assert scale == {
        "test_id": reuse.LIFECYCLE_SCALE_CASE, "outcome": "NOT_RUN",
        "reason": reuse.LIFECYCLE_SCALE_RULE,
    }
    assert reuse.LIFECYCLE_SCALE_CASE not in result["current_native"]["passed"]
    assert result["deferred_is_pass"] is False
    assert len(case.plan["a04_prerequisites"]["selectors"]) == 15


@pytest.mark.parametrize("conditional_native", [True], indirect=True)
@pytest.mark.parametrize("node", sorted(reuse.REQUIRED_NATIVE_CASES))
@pytest.mark.parametrize("fault", ["missing-call", "failed", "skipped"])
def test_lifecycle_deferral_rejects_every_nonpassing_current_native_prerequisite(
    conditional_native, node, fault,
):
    case = conditional_native
    path = case.directory / "execution.json"
    execution = release.read_json(path)
    if fault == "missing-call":
        execution["phases"][node].pop("call")
    else:
        execution["phases"][node]["call"]["outcome"] = fault
    release.write_json(path, execution)
    with pytest.raises(release.EvidenceError):
        reuse.validate_pr_selection(case.directory, case.plan, feedback_root=case.feedback)
    assert len(case.plan["a04_prerequisites"]["selectors"]) == 15


@pytest.mark.parametrize("conditional_native", [True], indirect=True)
@pytest.mark.parametrize("fault", ["unknown-rule", "missing-native", "missing-scale",
                                   "extra-native", "duplicate-native"])
def test_lifecycle_deferral_requires_exact_reviewed_obligation(conditional_native, fault):
    plan = conditional_native.plan
    spec = plan["a04_prerequisites"]
    if fault == "unknown-rule":
        plan["conditional_pr_lifecycle_scale"] = "unapproved"
    elif fault == "missing-native":
        spec["selectors"].remove(sorted(reuse.REQUIRED_NATIVE_CASES)[0])
    elif fault == "missing-scale":
        spec["selectors"].remove(reuse.LIFECYCLE_SCALE_CASE)
    elif fault == "extra-native":
        spec["selectors"].append("tests/integration/unknown.py::test_unreviewed")
    else:
        spec["selectors"].append(sorted(reuse.REQUIRED_NATIVE_CASES)[0])
    with pytest.raises(release.EvidenceError, match="broader native lifecycle scale obligation"):
        reuse.reduced_spec(plan)


@pytest.mark.parametrize("replacement", [None, "unapproved", False])
def test_lifecycle_cadence_is_bound_to_reviewed_plan_even_after_resealing(replacement):
    impact = registry.source_impact_plan(
        registry.load_manifest(), ["app/services/setup_lifecycle/evaluation_service.py",
                                   "app/services/winner_probability/cohort_authority.py"]
    )
    plan = release.prepare_core_plan("controlled-cadence-seal", "1", "pull_request", False, impact)
    assert plan["conditional_pr_lifecycle_scale"] == reuse.LIFECYCLE_SCALE_RULE
    release.validate_plan(plan)
    if replacement is None:
        plan.pop("conditional_pr_lifecycle_scale")
    else:
        plan["conditional_pr_lifecycle_scale"] = replacement
    plan["plan_sha256"] = release.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises(release.EvidenceError, match="lifecycle scale cadence"):
        release.validate_plan(plan)


@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
def test_release_cadences_retain_original_lifecycle_scale_case_and_all_fifteen(event):
    impact = registry.source_impact_plan(
        registry.load_manifest(), ["app/services/setup_lifecycle/evaluation_service.py",
                                   "app/services/winner_probability/cohort_authority.py"]
    )
    plan = release.prepare_core_plan("controlled-release-cadence", "1", event, False, impact)
    assert len(plan["a04_prerequisites"]["selectors"]) == 15
    assert reuse.LIFECYCLE_SCALE_CASE in plan["a04_prerequisites"]["selectors"]
    assert reuse.REQUIRED_NATIVE_CASES <= set(plan["a04_prerequisites"]["selectors"])
    release.validate_plan(plan)
    with pytest.raises(release.EvidenceError, match="broader native browser obligation"):
        reuse.reduced_spec(plan)
    assert "conditional_pr_lifecycle_scale" not in release.prepare_plan(
        "controlled-full-release", "1", event, False
    )


def test_lifecycle_cadence_does_not_expand_other_pr_obligations():
    impact = registry.source_impact_plan(
        registry.load_manifest(), ["app/services/winner_probability/probability_estimator.py"]
    )
    plan = release.prepare_core_plan(
        "controlled-existing-cadence", "1", "pull_request", False, impact
    )
    assert plan.get("conditional_pr_lifecycle_scale") is None
    assert len(plan["a04_prerequisites"]["selectors"]) == 10
    assert reuse.LIFECYCLE_SCALE_CASE not in plan["a04_prerequisites"]["selectors"]
    assert len(reuse.reduced_spec(plan)["selectors"]) == 4
    release.validate_plan(plan)


@pytest.mark.parametrize("fault", ["unrelated-skip", "ui-skip", "ui-failure", "missing-call",
                                   "incomplete-session", "nonzero-exit"])
def test_matrix_basis_reuses_only_complete_passing_ui_bodies(
    conditional_native, monkeypatch, fault,
):
    case = conditional_native
    plan = case.plan
    plan["schema"] = "swinglens.full-release-plan.v1"
    # Historical impact classification cannot erase eight observed native passes.
    # Current PR eligibility is still checked separately by check_reuse/reduced_spec.
    plan["source_impact"]["unknown_impact"] = [{"path": "historical-unmapped-doc.md"}]
    plan["plan_sha256"] = release.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    spec = plan["a04_prerequisites"]
    directory = case.directory
    execution = release.read_json(directory / "execution.json")
    execution.update(context=release.context(plan, reuse.PREREQUISITES),
                     selected=spec["selectors"], started=spec["selectors"])
    execution["phases"] = {node: {phase: {"outcome": "passed", "wasxfail": False, "seconds": .1}
                                  for phase in ("setup", "call", "teardown")}
                           for node in spec["selectors"]}
    native = spec["selectors"][-1]
    execution["phases"][native] = {
        "setup": {"outcome": "skipped", "wasxfail": False, "seconds": .1},
        "teardown": {"outcome": "passed", "wasxfail": False, "seconds": .1}}
    ui = reuse.cardinality_cases(spec)[0]
    if fault == "ui-skip":
        execution["phases"][ui]["call"]["outcome"] = "skipped"
    elif fault == "ui-failure":
        execution["phases"][ui]["call"]["outcome"] = "failed"
    elif fault == "missing-call":
        execution["phases"][ui].pop("call")
    elif fault == "incomplete-session":
        execution["session_finished"] = False
    elif fault == "nonzero-exit":
        execution["exit_code"] = 1
    release.write_json(directory / "execution.json", execution)
    release.write_json(directory / "collection.json", {
        "context": release.context(plan, reuse.PREREQUISITES),
        "selection": {key: spec[key] for key in ("selectors", "markers", "ignores", "browsers")},
        "selected": spec["selectors"], "exit_code": 0, "errors": []})
    execution["browsers_observed"]["firefox"] = "controlled-version"
    release.write_json(directory / "execution.json", execution)
    xml = ET.Element("testsuites")
    suite = ET.SubElement(xml, "testsuite")
    for node in spec["selectors"]:
        classname, name = release.junit_key(node).rsplit("::", 1)
        child = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if node == native or (node == ui and fault == "ui-skip"):
            ET.SubElement(child, "skipped")
        elif node == ui and fault in {"ui-failure", "missing-call"}:
            ET.SubElement(child, "failure")
    ET.ElementTree(xml).write(directory / "results.xml")
    monkeypatch.setattr(reuse, "_clean_source", lambda source: source["revision"])
    if fault == "unrelated-skip":
        proof = reuse.validate_original(
            directory, plan, reuse.PREREQUISITES, purpose="matrix-basis")
        assert proof["skipped"] == [native]
        assert native not in proof["passed"]
        assert proof["validated_subset"] == sorted(reuse.cardinality_cases(spec))
        with pytest.raises(release.EvidenceError, match="failed/skipped/incomplete"):
            reuse.validate_original(directory, plan, reuse.PREREQUISITES)
    else:
        with pytest.raises(release.EvidenceError):
            reuse.validate_original(directory, plan, reuse.PREREQUISITES, purpose="matrix-basis")


def test_matrix_comparison_ignores_unrelated_native_order_but_keeps_ui_rules():
    spec = _matrix_plan()["a04_prerequisites"]
    ordered = {**spec, "selectors": [spec["selectors"][-1], *spec["selectors"][:-1]]}
    assert spec != ordered
    assert reuse._comparable_spec(spec, "matrix-basis") == (
        reuse._comparable_spec(ordered, "matrix-basis"))
    assert reuse._comparable_spec(spec, "native") != reuse._comparable_spec(ordered, "native")
    changed = {**ordered, "selectors": ordered["selectors"][:-1]}
    assert reuse._comparable_spec(spec, "matrix-basis") != (
        reuse._comparable_spec(changed, "matrix-basis"))


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
@pytest.mark.parametrize("conditional_native", [False, True], indirect=True)
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
        "hardware": {"cpu_count": 4, "machine": "controlled-unit-machine",
                     "processor": "controlled-unit-processor", "memory_bytes": 16 * 1024**3},
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
