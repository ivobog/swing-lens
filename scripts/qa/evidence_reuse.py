"""Dependency checks for the existing native release receipts, never A04 carryover."""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from scripts.qa import release_evidence as release

SCHEMA = "swinglens.native-evidence-reuse.v1"
REPOSITORY = "ivobog/swing-lens"
PREREQUISITES = "a04-prerequisites"
PR_SELECTED = "a04-prerequisites-pr"
PR_FRESH_MATRIX = "a04-prerequisites-pr-fresh-matrix"
UI_BOUNDARY = "native-cardinality-ui"
CARDINALITY_RULE = "approved-pr-native-100-88-handoff-v1"
LIFECYCLE_SCALE_RULE = "approved-pr-native-100-88-lifecycle-scale-v1"
LIFECYCLE_SCALE_CASE = (
    "tests/integration/test_slse_performance_certification.py"
    "::test_slse_phase_12_performance_certification"
)
REQUIRED_NATIVE_CASES = frozenset({
    "tests/integration/test_run40_lifecycle_remediation_postgresql.py"
    "::test_native_lifecycle_interruption_replay_and_scale[2]",
    "tests/integration/test_run40_lifecycle_remediation_postgresql.py"
    "::test_native_lifecycle_interruption_replay_and_scale[88]",
    *("tests/integration/test_run8_setup_performance_postgresql.py"
      f"::test_setup_capture_run8_scale_is_bounded[{size}]" for size in (10, 25, 107)),
    "tests/integration/test_winner_jobs_reliability_postgresql.py"
    "::test_390_row_42_cohort_heartbeat_commits_have_bounded_selects",
})
PAIR_SUFFIXES = ("[chromium-False-100]", "[chromium-True-88]")
# These producers do not supply financial/test inputs. Their *current* validators
# must still accept every original native outcome. Everything else in scripts/
# remains a dependency, including source binding and oracle validation helpers.
PRODUCERS = {
    "scripts/production_runtime_certification.py",
    "scripts/qa/release_evidence.py",
    "scripts/qa/evidence_reuse.py",
    "scripts/qa/owned_certification.py",
}
PREREQUISITE_NON_INPUTS = PRODUCERS | {
    "config/certification_registry.json",
    "docs/testing/standalone_lifecycle.csv",
    "docs/testing/source_contract_policy.md",
    "tests/test_full_release_certification.py",
    "tests/test_production_runtime_certification.py",
    "tests/test_core_release_selection_policy.py",
    "tests/test_impact_validation.py",
    "tests/test_evidence_reuse.py",
    "tests/test_evidence_write_boundaries.py",
}


def controlled_environment(variables=None) -> str:
    """Seal settings overrides without publishing secrets or disposable addresses."""
    return release.controlled_environment(variables)


def _git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(release.ROOT), *args])


def _clean_source(source: dict) -> str:
    revision = source.get("revision", "")
    if source.get("dirty") is not False or not release.re.fullmatch("[0-9a-f]{40}", revision):
        raise release.EvidenceError("reuse requires an immutable clean Git source")
    if _git("rev-parse", "--verify", revision + "^{commit}").decode().strip() != revision:
        raise release.EvidenceError("unknown original source")
    fingerprint = release.hashlib.sha256(
        revision.encode() + b"\0status\0\0tracked-diff\0"
    ).hexdigest()
    if source.get("fingerprint") != fingerprint:
        raise release.EvidenceError("original clean-source identity does not match Git")
    return revision


def _ensure_original_commit(source: dict) -> None:
    """Recover an authenticated prior merge object in a different CI checkout."""
    revision = source.get("revision", "")
    if source.get("dirty") is not False or not release.re.fullmatch("[0-9a-f]{40}", revision):
        raise release.EvidenceError("reuse requires an immutable clean Git source")
    found = subprocess.run(
        ["git", "-C", str(release.ROOT), "cat-file", "-e", revision + "^{commit}"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if found.returncode:
        subprocess.run(
            ["git", "-C", str(release.ROOT), "fetch", "--no-tags", "origin", revision],
            check=True, capture_output=True, timeout=60,
        )
    _clean_source(source)


def dependency_manifest(source: dict, lane: str) -> dict:
    """Conservative native boundaries; additions, deletions and modes all matter."""
    revision = _clean_source(source)
    if lane == "a04" or lane not in release.BASE_LANES | {PREREQUISITES, UI_BOUNDARY}:
        raise release.EvidenceError("lane has no reviewed reusable dependency boundary")
    entries = []
    for row in _git("ls-tree", "-r", "-z", "--full-tree", revision).split(b"\0"):
        if not row:
            continue
        descriptor, raw = row.split(b"\t", 1)
        mode, kind, blob = descriptor.decode().split()
        name = raw.decode("utf-8")
        if kind != "blob":
            raise release.EvidenceError("unreviewed Git entry in native dependencies")
        common = name in {"pyproject.toml", "uv.lock", ".env.example", ".gitattributes"}
        common |= name.startswith(".github/")
        if lane == UI_BOUNDARY:
            include = name.startswith(
                ("app/static/", "app/templates/", "app/routers/", "tests/e2e/", "config/")
            )
            include |= name in {
                "app/main.py",
                "app/settings.py",
                "tests/conftest.py",
                "tests/evidence_output.py",
                "scripts/qa/source_bound.py",
                "scripts/qa/committed_source_identity.py",
                "scripts/qa/golden_provenance.py",
                "pyproject.toml",
                "uv.lock",
                ".env.example",
                ".gitattributes",
            }
            include &= name != "config/certification_registry.json"
        elif lane == "node":
            include = common or name.startswith(("app/static/", "app/templates/"))
            include |= name.endswith((".cjs", ".mjs", ".js", "package.json", "package-lock.json"))
        elif lane == "promql":
            include = common or name.startswith("monitoring/")
        else:
            # Retain every tracked native helper, imported root test fixture,
            # data file and oracle. Only explicitly reviewed orchestration and
            # governance controls may change without rerunning prerequisites.
            include = lane != PREREQUISITES or name not in PREREQUISITE_NON_INPUTS
        if include:
            if mode not in {"100644", "100755"}:
                raise release.EvidenceError("linked dependency cannot be reused")
            entries.append({"path": name, "mode": mode, "blob": blob})
    if not entries:
        raise release.EvidenceError("empty native dependency boundary")
    return {"schema": SCHEMA, "lane": lane, "entries": entries}


def environment(plan: dict, lane: str, variables=None) -> dict:
    """Compare actual software and deterministic input dates, not process IDs."""
    spec = release.lane_spec(plan, lane)
    values = os.environ if variables is None else variables
    if spec["kind"] in {"node", "promql"}:
        if lane == "node":
            command = ["node", "--version"]
        else:
            image = spec["configuration"]["image"]
            command = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--entrypoint",
                "/bin/promtool",
                image,
                "--version",
            ]
        version = subprocess.check_output(command, text=True, timeout=60, env=values).strip()
        return {
            "tool_version": version,
            "platform": platform.platform(),
            "declared_configuration": spec.get("configuration", {}),
            "controlled_environment_sha256": controlled_environment(values),
        }
    profile = release.environment_profile(values)
    if spec.get("postgres_major"):
        import psycopg

        url = values["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(url, connect_timeout=5) as db:
            profile["postgresql"] = {"version": db.execute("select version()").fetchone()[0]}
    browsers = {}
    if spec["browsers"]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            for name in spec["browsers"]:
                browser = getattr(playwright, name).launch(headless=True)
                try:
                    browsers[name] = browser.version
                finally:
                    browser.close()
    return {
        "profile": _stable_profile(profile),
        "browsers": browsers,
        "input_date": datetime.now(UTC).date().isoformat(),
    }


def _stable_profile(profile: dict) -> dict:
    if not profile.get("controlled_environment_sha256"):
        raise release.EvidenceError("original controlled environment was not recorded")
    hardware = profile.get("hardware")
    memory = hardware.get("memory_bytes") if isinstance(hardware, dict) else None
    if type(memory) is not int or memory <= 0:
        raise release.EvidenceError("native hardware memory capacity is unknown")
    # Kernel-reserved pages vary between otherwise identical hosted runners.
    # Compare physical capacity in MiB; retain every raw measurement in the
    # original immutable execution record and all other hardware fields exactly.
    hardware = {key: value for key, value in hardware.items() if key != "memory_bytes"}
    hardware["memory_mib"] = memory // (1024 * 1024)
    return {
        key: profile.get(key)
        for key in (
            "platform",
            "python",
            "pytest",
            "provider_mode",
            "database_safety_context",
            "controlled_environment_sha256",
        )
    } | {"hardware": hardware,
         "postgresql": {"version": profile.get("postgresql", {}).get("version")}}


def _original_environment(proof: dict, plan: dict, kind: str) -> dict:
    if kind in {"node", "promql"}:
        if not proof["environment"].get("controlled_environment_sha256"):
            raise release.EvidenceError("original controlled environment was not recorded")
        return proof["environment"]
    date = proof["environment"].get("input_date")
    if (
        not date
        or not proof.get("finished_at")
        or (datetime.fromisoformat(proof["finished_at"]).date().isoformat() != date)
    ):
        raise release.EvidenceError("native input date changed during execution or is unknown")
    return {
        "profile": _stable_profile(proof["environment"]),
        "browsers": proof["browsers_observed"],
        "input_date": date,
    }


def _check_plan_seal(plan: dict) -> None:
    if plan.get("schema") != "swinglens.full-release-plan.v1" or plan.get(
        "plan_sha256"
    ) != release.digest({k: v for k, v in plan.items() if k != "plan_sha256"}):
        raise release.EvidenceError("original plan seal invalid")
    _clean_source(plan["source"])


def _original_capture_lane(root: Path, lane: str, purpose: str) -> str:
    if lane == PREREQUISITES and purpose == "matrix-basis":
        capture = release.read_json(root / "collection.json")
        if capture.get("context", {}).get("lane") == PR_FRESH_MATRIX:
            return PR_FRESH_MATRIX
    return lane


def validate_original(root: Path, plan: dict, lane: str, *, purpose="native") -> dict:
    """Re-evaluate original outcomes with today's validator and original context."""
    _check_plan_seal(plan)
    if (root / "carry-forward.json").exists():
        raise release.EvidenceError("reuse must retain the original native run, not a carry chain")
    original_lane = _original_capture_lane(root, lane, purpose)
    spec = release.lane_spec(plan, original_lane)
    observed = release.junit_cases(release.safe_file(root, "results.xml"), spec["kind"])
    proof = (
        release._pytest_results(root, plan, original_lane, observed)
        if spec["kind"] == "pytest"
        else release._native_results(root, plan, lane, observed)
    )
    if purpose == "matrix-basis":
        if lane != PREREQUISITES:
            raise release.EvidenceError("matrix basis requires prerequisite evidence")
        # Validate the complete original session above, but reuse only the eight
        # reviewed UI bodies. Unrelated original skips remain skips in the proof.
        if not _reviewed_matrix(spec):
            raise release.EvidenceError("original native matrix selection is incomplete")
        required = set(cardinality_cases(spec))
        if (proof["process_exit_code"] != 0
                or not required <= set(proof["passed"])
                or not required <= set(proof["body_executed"])
                or any(required.intersection(proof[key]) for key in release.CATEGORIES
                       if key != "passed")):
            raise release.EvidenceError("original native matrix failed/skipped/incomplete")
        return {**proof, "validated_subset": sorted(required)}
    if purpose != "native":
        raise release.EvidenceError("unknown native proof purpose")
    if (
        proof["process_exit_code"] != 0
        or not proof["selected"]
        or set(proof["selected"]) != set(proof["passed"])
        or set(proof["selected"]) != set(proof["body_executed"])
        or any(proof[key] for key in release.CATEGORIES if key != "passed")
    ):
        raise release.EvidenceError("original native evidence failed/skipped/incomplete")
    if lane != PREREQUISITES:
        receipt = release.read_json(root / "lane.json")
        if (
            receipt != release.evaluate_lane(root, plan, lane, receipt["job_result"])
            or receipt["verdict"] != "CERTIFIED"
        ):
            raise release.EvidenceError("original terminal lane receipt does not certify")
    return proof


def check_reuse(
    root: Path, plan: dict, lane: str, expected_environment: dict, *, purpose="native"
) -> dict:
    """No original receipts or XML are rewritten to impersonate a fresh run."""
    if lane == "a04":
        raise release.EvidenceError("A04 always requires current whole-source execution")
    carry = release.read_json(root / "carry-forward.json")
    old = release.read_json(root / "source-plan.json")
    if carry.get("schema") != SCHEMA or carry.get("context") != release.context(plan, lane):
        raise release.EvidenceError("foreign carry-forward context")
    if purpose not in {"native", "matrix-basis"} or carry.get("purpose", "native") != purpose:
        raise release.EvidenceError("native proof purpose differs")
    if purpose == "matrix-basis":
        reduced_spec(plan)  # PR, known impact, reviewed eight-case obligation required.
    probe = release.read_json(root / "current-environment.json")
    if probe != {"context": release.context(plan, lane), "environment": expected_environment}:
        raise release.EvidenceError("current native environment probe differs")
    old_spec, spec = release.lane_spec(old, lane), release.lane_spec(plan, lane)
    if _comparable_spec(old_spec, purpose) != _comparable_spec(spec, purpose):
        raise release.EvidenceError("native selection/configuration changed")
    origin = release.read_json(root / "origin.json")
    authenticate_archives(root, origin)
    original = root / "original"
    _check_extracted_files(original, root / "evidence.zip", carry["archive_prefix"])
    _check_extracted_files(root / "original-plan", root / "plan.zip", "")
    if release.read_json(root / "original-plan/plan.json") != old:
        raise release.EvidenceError("original source plan differs from authenticated artifact")
    if str(old["execution_id"]) != str(origin["run_id"]):
        raise release.EvidenceError("artifact does not belong to original execution")
    original_lane = _original_capture_lane(original, lane, purpose)
    if carry.get("original_context") != release.context(old, original_lane):
        raise release.EvidenceError("original run provenance changed")
    # Prior PR merge commits need not be in a fresh aggregate job's history.
    # Authenticate the archives and exact original plan before fetching any object.
    _ensure_original_commit(old["source"])
    if subprocess.run(
        [
            "git",
            "-C",
            str(release.ROOT),
            "merge-base",
            "--is-ancestor",
            origin["head_sha"],
            old["source"]["revision"],
        ],
        check=False,
    ).returncode:
        raise release.EvidenceError("original source is unrelated to authenticated workflow head")
    boundary = UI_BOUNDARY if purpose == "matrix-basis" else lane
    dependencies = dependency_manifest(plan["source"], boundary)
    _assert_declared_inputs(spec, dependencies, matrix_only=purpose == "matrix-basis")
    if dependencies != dependency_manifest(old["source"], boundary) or carry.get(
        "dependencies_sha256"
    ) != release.digest(dependencies):
        raise release.EvidenceError("native source/test/helper/oracle/config dependencies changed")
    proof = validate_original(original, old, lane, purpose=purpose)
    actual_environment = _original_environment(proof, old, spec["kind"])
    if carry.get("environment") != actual_environment or actual_environment != expected_environment:
        raise release.EvidenceError("native environment/tool/input date changed or unknown")
    return {
        **proof,
        "carry_forward": {
            "original_context": release.context(old, original_lane),
            "origin": origin,
            "dependencies_sha256": carry["dependencies_sha256"],
            "native_execution": "retained original; revalidated for unchanged dependencies",
        },
    }


def _assert_declared_inputs(spec: dict, dependencies: dict, *, matrix_only=False) -> None:
    paths = {row["path"] for row in dependencies["entries"]}
    if spec["kind"] == "pytest":
        selectors = cardinality_cases(spec) if matrix_only else spec["selectors"]
        required = {node.split("::", 1)[0] for node in selectors}
    elif spec["kind"] == "node":
        required = set(spec["configuration"]["files"])
    else:
        required = {spec["configuration"]["rules"]}
    if not required or any(
        path not in paths and not any(p.startswith(path + "/") for p in paths) for path in required
    ):
        raise release.EvidenceError("declared native test/input is outside the dependency proof")


def _gh_json(endpoint: str) -> dict:
    return json.loads(subprocess.check_output(["gh", "api", endpoint], text=True, timeout=60))


def authenticate_archives(root: Path, origin: dict) -> None:
    if origin.get("repository") != REPOSITORY:
        raise release.EvidenceError("foreign artifact repository")
    run = _gh_json(f"repos/{REPOSITORY}/actions/runs/{origin['run_id']}")
    if (
        run["event"] != "pull_request"
        or run["head_repository"]["full_name"] != REPOSITORY
        or run["head_sha"] != origin["head_sha"]
        or run["path"] != ".github/workflows/ci.yml"
    ):
        raise release.EvidenceError("untrusted artifact execution")
    for key, filename in (("plan_artifact_id", "plan.zip"), ("artifact_id", "evidence.zip")):
        metadata = _gh_json(f"repos/{REPOSITORY}/actions/artifacts/{origin[key]}")
        if (
            metadata["expired"]
            or metadata["workflow_run"]["id"] != origin["run_id"]
            or metadata["digest"] != "sha256:" + release.file_digest(root / filename)
        ):
            raise release.EvidenceError("authenticated artifact digest/run differs")


def _check_extracted_files(root: Path, archive: Path, prefix: str) -> None:
    if any(
        p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in root.rglob("*")
    ):
        raise release.EvidenceError("linked original evidence prohibited")
    with zipfile.ZipFile(archive) as zipped:
        expected = {}
        for entry in zipped.infolist():
            if not entry.is_dir() and entry.filename.startswith(prefix):
                relative = entry.filename.removeprefix(prefix)
                expected[relative] = release.hashlib.sha256(zipped.read(entry)).hexdigest()
        actual = {
            p.relative_to(root).as_posix(): release.file_digest(p)
            for p in root.rglob("*")
            if p.is_file()
        }
        if not expected or actual != expected:
            raise release.EvidenceError("original artifact files missing/changed/unexpected")


def _extract(archive: Path, root: Path, prefix: str) -> None:
    root.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        names = set()
        for entry in zipped.infolist():
            if entry.is_dir() or not entry.filename.startswith(prefix):
                continue
            relative = entry.filename.removeprefix(prefix)
            path = root / relative
            if (
                not relative
                or relative in names
                or ":" in relative
                or "\\" in relative
                or Path(relative).is_absolute()
                or not path.resolve().is_relative_to(root.resolve())
                or stat.S_ISLNK(entry.external_attr >> 16)
            ):
                raise release.EvidenceError("unsafe or duplicate native archive entry")
            names.add(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(entry) as reader, path.open("xb") as writer:
                shutil.copyfileobj(reader, writer)


def materialize(
    candidate: Path,
    target: Path,
    plan: dict,
    lane: str,
    expected_environment: dict,
    *,
    purpose="native",
) -> dict:
    """Stage a complete validated carry-forward before installing it atomically."""
    release.validate_plan(plan, current=True)
    if target.exists():
        raise release.EvidenceError("refuse to replace existing native evidence")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reuse-", dir=target.parent) as temporary:
        staged = Path(temporary) / "receipt"
        staged.mkdir()
        for name in ("plan.zip", "evidence.zip", "origin.json"):
            shutil.copyfile(release.safe_file(candidate, name), staged / name)
        authenticate_archives(staged, release.read_json(staged / "origin.json"))
        _extract(staged / "plan.zip", staged / "original-plan", "")
        old = release.read_json(staged / "original-plan/plan.json")
        prefix = artifact_prefix(lane)
        _extract(staged / "evidence.zip", staged / "original", prefix)
        release.write_json(staged / "source-plan.json", old)
        if str(old["execution_id"]) != str(release.read_json(staged / "origin.json")["run_id"]):
            raise release.EvidenceError("artifact does not belong to original execution")
        _ensure_original_commit(old["source"])
        proof = validate_original(staged / "original", old, lane, purpose=purpose)
        original_lane = _original_capture_lane(staged / "original", lane, purpose)
        release.write_json(
            staged / "carry-forward.json",
            {
                "schema": SCHEMA,
                "context": release.context(plan, lane),
                "purpose": purpose,
                "original_context": release.context(old, original_lane),
                "archive_prefix": prefix,
                "dependencies_sha256": release.digest(
                    dependency_manifest(
                        plan["source"], UI_BOUNDARY if purpose == "matrix-basis" else lane
                    )
                ),
                "environment": _original_environment(
                    proof, old, release.lane_spec(plan, lane)["kind"]
                ),
            },
        )
        release.write_json(
            staged / "current-environment.json",
            {
                "context": release.context(plan, lane),
                "environment": expected_environment,
            },
        )
        checked = check_reuse(staged, plan, lane, expected_environment, purpose=purpose)
        staged.rename(target)
    return checked


def try_previous_run(
    target: Path,
    plan: dict,
    lane: str,
    variables=None,
    *,
    purpose="native",
) -> bool:
    """PR-only authenticated lookup; unknown proof leaves the ordinary run selected."""
    if plan.get("event") != "pull_request" or lane == "a04" or target.exists():
        return False
    if not os.environ.get("GH_TOKEN") or not os.environ.get("GITHUB_HEAD_REF"):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    notes = []
    with tempfile.TemporaryDirectory(prefix="prior-native-", dir=target.parent) as temporary:
        scratch = Path(temporary)
        try:
            runs = _gh_json(
                f"repos/{REPOSITORY}/actions/workflows/ci.yml/runs?"
                "event=pull_request&status=completed&per_page=20"
            )["workflow_runs"]
            candidates = [
                run
                for run in runs
                if (
                    str(run["id"]) != str(plan["execution_id"])
                    and run["head_branch"] == os.environ["GITHUB_HEAD_REF"]
                    and run["head_repository"]["full_name"] == REPOSITORY
                )
            ][:3]
            if purpose == "matrix-basis":
                reduced_spec(plan)
            current_environment = environment(plan, lane, variables)
            for run in candidates:
                try:
                    bundle = scratch / str(run["id"])
                    bundle.mkdir()
                    artifacts = _gh_json(f"repos/{REPOSITORY}/actions/runs/{run['id']}/artifacts")
                    artifacts = {
                        row["name"]: row for row in artifacts["artifacts"] if not row["expired"]
                    }
                    job = (
                        "production-runtime-certification"
                        if lane == PREREQUISITES
                        else release.lane_spec(plan, lane)["job"]
                    )
                    archive_name = "cross-language" if job == "cross-language" else job
                    evidence = artifacts["release-evidence-" + archive_name]
                    source = artifacts["full-release-plan"]
                    _download(source, bundle / "plan.zip")
                    _extract(bundle / "plan.zip", bundle / "plan", "")
                    old = release.read_json(bundle / "plan/plan.json")
                    revision = old["source"]["revision"]
                    found = subprocess.run(
                        ["git", "-C", str(release.ROOT), "cat-file", "-e", revision + "^{commit}"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    if found.returncode:
                        subprocess.run(
                            [
                                "git",
                                "-C",
                                str(release.ROOT),
                                "fetch",
                                "--no-tags",
                                "origin",
                                revision,
                            ],
                            check=True,
                            capture_output=True,
                            timeout=60,
                        )
                    boundary = UI_BOUNDARY if purpose == "matrix-basis" else lane
                    if _comparable_spec(release.lane_spec(old, lane), purpose) != _comparable_spec(
                        release.lane_spec(plan, lane), purpose
                    ) or dependency_manifest(old["source"], boundary) != dependency_manifest(
                        plan["source"], boundary
                    ):
                        raise release.EvidenceError("prior native dependencies/selection changed")
                    # Legacy receipts did not bind settings overrides. They are
                    # retained as diagnostics; never synthesize the missing proof.
                    producer = _git("show", revision + ":scripts/qa/release_evidence.py")
                    if b'"controlled_environment_sha256"' not in producer:
                        raise release.EvidenceError("prior producer omitted controlled environment")
                    _download(evidence, bundle / "evidence.zip")
                    release.write_json(
                        bundle / "origin.json",
                        {
                            "repository": REPOSITORY,
                            "run_id": run["id"],
                            "head_sha": run["head_sha"],
                            "artifact_id": evidence["id"],
                            "plan_artifact_id": source["id"],
                        },
                    )
                    if purpose == "matrix-basis":
                        bundle = _original_matrix_bundle(bundle)
                    materialize(bundle, target, plan, lane, current_environment, purpose=purpose)
                    return True
                except Exception as exc:
                    notes.append({"run_id": run["id"], "reason": _unavailable_reason(exc)})
        except Exception as exc:
            notes.append({"reason": _unavailable_reason(exc)})
    release.write_json(
        target.parent / (lane + ".reuse-unavailable.json"),
        {
            "context": release.context(plan, lane),
            "status": "NOT_REUSED",
            "ordinary_native_selection_required": True,
            "attempts": notes,
        },
    )
    return False


def _unavailable_reason(exc: Exception) -> str:
    from app.services.redaction import redact_text

    return type(exc).__name__ + ": " + redact_text(str(exc))[:500]


def _original_matrix_bundle(bundle: Path) -> Path:
    """Forward the *original* authenticated baseline, never a chain of pass claims."""
    prefix = artifact_prefix(PREREQUISITES) + "matrix-basis/"
    with zipfile.ZipFile(bundle / "evidence.zip") as archive:
        if prefix + "origin.json" not in archive.namelist():
            return bundle
        original = bundle / "original-matrix"
        original.mkdir()
        for name in ("plan.zip", "evidence.zip", "origin.json"):
            (original / name).write_bytes(archive.read(prefix + name))
        return original


def artifact_prefix(lane: str) -> str:
    # The owned A04 snapshot contains the producer's separate a04/ directory.
    return "a04/a04/source-impact-prerequisites/" if lane == PREREQUISITES else lane + "/"


def cardinality_cases(spec: dict) -> list[str]:
    prefix = (
        "tests/e2e/single_run_certification/test_real_writer_cardinality.py"
        "::test_frozen_real_writer_cardinality["
    )
    return [
        node
        for node in spec["selectors"]
        if node.startswith(prefix) and node.endswith(("-88]", "-100]"))
    ]


def _comparable_spec(spec: dict, purpose: str) -> dict:
    return {**spec, "selectors": cardinality_cases(spec)} if purpose == "matrix-basis" else spec


def _reviewed_matrix(spec: dict) -> bool:
    cases = cardinality_cases(spec)
    expected = {
        f"{engine}-{handoff}-{size}]"
        for engine in ("chromium", "firefox")
        for handoff in ("False", "True")
        for size in (88, 100)
    }
    return len(cases) == 8 and {node.rsplit("[", 1)[1] for node in cases} == expected


def reduced_spec(plan: dict) -> dict:
    impact = plan.get("source_impact", {})
    spec = plan.get("a04_prerequisites", {})
    cases = cardinality_cases(spec)
    if (
        plan.get("profile") != "core"
        or plan.get("event") != "pull_request"
        or impact.get("baseline_known") is not True
        or impact.get("review_required")
        or impact.get("unknown_impact")
        or spec.get("conditional_pr_cardinality") != CARDINALITY_RULE
        or not _reviewed_matrix(spec)
    ):
        raise release.EvidenceError("broader native browser obligation remains required")
    scale_rule = plan.get("conditional_pr_lifecycle_scale")
    if scale_rule is not None and (
        scale_rule != LIFECYCLE_SCALE_RULE
        or len(spec["selectors"]) != 15
        or set(spec["selectors"]) - set(cases) != REQUIRED_NATIVE_CASES | {LIFECYCLE_SCALE_CASE}
    ):
        raise release.EvidenceError("broader native lifecycle scale obligation remains required")
    selected = [
        node for node in spec["selectors"] if node not in cases or node.endswith(PAIR_SUFFIXES)
    ]
    if scale_rule == LIFECYCLE_SCALE_RULE:
        selected.remove(LIFECYCLE_SCALE_CASE)
    return {**spec, "selectors": release.prerequisite_order(selected), "browsers": ["chromium"]}


def fresh_matrix_spec(plan: dict) -> dict:
    """Obtain current UI proof when no historical runner-compatible matrix exists."""
    reduced_spec(plan)  # Retain the same PR, impact, approval and exact15 guards.
    if plan.get("conditional_pr_lifecycle_scale") != LIFECYCLE_SCALE_RULE:
        raise release.EvidenceError("broader native lifecycle scale obligation remains required")
    spec = plan["a04_prerequisites"]
    return {**spec, "selectors": [node for node in spec["selectors"]
                                if node != LIFECYCLE_SCALE_CASE]}


def prerequisite_selection(plan: dict, historical_matrix: bool) -> tuple[dict, str]:
    """Choose proof acquisition; no deferral is granted before native validation."""
    if historical_matrix:
        return reduced_spec(plan), PR_SELECTED
    try:
        return fresh_matrix_spec(plan), PR_FRESH_MATRIX
    except release.EvidenceError:
        return plan["a04_prerequisites"], PREREQUISITES


def validate_pr_selection(directory: Path, plan: dict, *, feedback_root: Path) -> dict:
    """Deferred cases stay NOT_RUN; complete current native proof remains required."""
    capture = release.read_json(directory / "collection.json")
    fresh = capture.get("context", {}).get("lane") == PR_FRESH_MATRIX
    lane = PR_FRESH_MATRIX if fresh else PR_SELECTED
    spec = fresh_matrix_spec(plan) if fresh else reduced_spec(plan)
    if not fresh:
        basis = directory / "matrix-basis"
        probe = release.read_json(basis / "current-environment.json")
        original = check_reuse(
            basis, plan, PREREQUISITES, probe["environment"], purpose="matrix-basis"
        )
        if set(cardinality_cases(plan["a04_prerequisites"])) - set(original["passed"]):
            raise release.EvidenceError("original cross-engine/handoff matrix proof is incomplete")
    observed = release.junit_cases(directory / "results.xml", "pytest")
    current = release._pytest_results(directory, plan, lane, observed)
    if (
        current["process_exit_code"] != 0
        or set(current["passed"]) != set(spec["selectors"])
        or set(current["body_executed"]) != set(spec["selectors"])
        or any(current[key] for key in release.CATEGORIES if key != "passed")
    ):
        raise release.EvidenceError("current native integration selection is incomplete")
    pair = {node for node in spec["selectors"] if node.endswith(PAIR_SUFFIXES)}
    records = [release.read_json(path) for path in feedback_root.glob("*/validation-runtime.json")]
    records = [row for row in records if row.get("nodeid") in pair]
    if (
        len(pair) != 2
        or len(records) != 2
        or {row["nodeid"] for row in records} != pair
        or any(
            row.get("source_fingerprint") != plan["source"]["fingerprint"]
            or row.get("original_outcome") != "passed"
            or row.get("ticker_count") != (100 if row["nodeid"].endswith("-100]") else 88)
            or release.integration_performance(row)["status"] != "PASS"
            for row in records
        )
    ):
        raise release.EvidenceError(
            "current 100/full-path, 88-tail/handoff or stage-budget proof missing"
        )
    deferred = sorted(set(plan["a04_prerequisites"]["selectors"]) - set(spec["selectors"]))
    return {
        "rule": LIFECYCLE_SCALE_RULE if fresh else CARDINALITY_RULE,
        "context": release.context(plan, lane),
        "selected": spec["selectors"],
        "deferred": [
            {"test_id": node, "outcome": "NOT_RUN", "reason": (
                LIFECYCLE_SCALE_RULE if node == LIFECYCLE_SCALE_CASE else CARDINALITY_RULE
            )} for node in deferred
        ],
        "basis": ({"context": release.context(plan, lane),
                   "native_execution": "current complete eight-case UI matrix",
                   "validated_ui": cardinality_cases(spec)}
                  if fresh else original["carry_forward"]),
        "current_native": current,
        "current_pipeline_records": records,
        "deferred_is_pass": False,
    }


def _download(metadata: dict, target: Path) -> None:
    with target.open("xb") as stream:
        subprocess.run(
            ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{metadata['id']}/zip"],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=True,
            timeout=120,
        )
    if metadata["digest"] != "sha256:" + release.file_digest(target):
        raise release.EvidenceError("downloaded native artifact digest differs")
