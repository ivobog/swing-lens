from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import psutil
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.services.ib_gateway_health_service import check_status as check_ib_gateway_status
from app.services.ib_historical_capability import check_historical_data_capability
from app.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = REPO_ROOT / "test-results" / "production-runtime-certification"
sys.path.insert(0, str(REPO_ROOT / "tests" / "e2e"))
from single_run_certification.reporting import (  # noqa: E402
    EXACT_TEST_IDS,
    PIPELINE_SCOPE,
    finalize_pipeline_certificate,
)


@dataclass(frozen=True)
class TestGroupResult:
    name: str
    status: str
    exit_code: int
    duration_seconds: float
    command: list[str]
    stdout_log: str
    stderr_log: str


TEST_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "focused-regressions",
        (
            "tests/ceri/test_batched_workflow_v2.py",
            "tests/test_background_worker.py",
            "tests/test_background_job_service.py",
            "tests/test_pipeline_execution_authority.py",
            "tests/test_production_runtime_certification.py",
            "tests/test_pipeline_service.py",
            "tests/test_pre_enqueue_operational_gate.py",
            "tests/test_technical_consumer_eligibility.py",
            "tests/setup_lifecycle/test_snapshot_builder.py",
            "tests/setup_lifecycle/test_setup_lifecycle_repository.py",
            "tests/setup_lifecycle/test_transition_candidate_service.py",
        ),
    ),
    (
        "postgresql-transactions",
        (
            "tests/integration/test_ceri_batched_workflow_v2.py::test_run9_two_ticker_detached_checkpoint_does_not_self_lock_postgresql",
            "tests/integration/test_ceri_batched_workflow_v2.py::test_detached_control_write_has_bounded_postgresql_lock_timeout",
            "tests/integration/test_sec_readiness_repair_postgresql.py::test_worker_repair_continuation_restart_and_c2_drift_keep_c1",
            "tests/integration/test_pipeline_execution_authority_postgresql.py",
        ),
    ),
    (
        "watchdog-recovery",
        (
            "tests/test_worker_supervisor_reliability.py",
            "tests/integration/test_pipeline_authority_certification_postgresql.py::test_04_cancel_while_waiting_for_dependency",
            "tests/integration/test_pipeline_authority_certification_postgresql.py::test_05_cancel_while_child_running",
            "tests/integration/test_pipeline_authority_certification_postgresql.py::test_13_lock_timeout_cancellation_is_bounded_and_diagnostic",
            "tests/integration/test_supervisor_recovery_isolation_postgresql.py::test_normal_supervisor_recovers_non_cancelled_and_cancels_requested_job",
        ),
    ),
    (
        "certification-isolation",
        (
            "tests/test_certification_runtime.py",
            "tests/integration/test_certification_runtime_postgresql.py::test_due_unrelated_matrix_is_deferred_while_authorized_lineage_executes",
            "tests/integration/test_certification_runtime_postgresql.py::test_terminal_certification_root_cannot_reauthorize_historical_descendants",
            "tests/integration/test_certification_runtime_postgresql.py::test_certification_enqueue_boundary_rejects_unrelated_and_allows_lineage",
        ),
    ),
    (
        "production-runtime",
        (*EXACT_TEST_IDS,),
    ),
)


def main() -> int:
    if os.environ.get("SWINGLENS_VERIFY_SOURCE_ROOT"):
        from scripts.qa.source_bound import record_origins

        record_origins(Path(os.environ["SWINGLENS_VERIFY_SOURCE_ROOT"]),
            Path(os.environ["SWINGLENS_SOURCE_PROVENANCE_DIR"]),
            ("scripts.production_runtime_certification", "app.main", "app.settings",
             "app.database_safety"))
    parser = argparse.ArgumentParser(description="Certify the production SwingLens runtime.")
    parser.add_argument(
        "--provider-mode", choices=("deterministic", "live"), default="deterministic"
    )
    parser.add_argument("--report-dir", type=Path)
    release = parser.add_mutually_exclusive_group()
    release.add_argument("--prepare-release", action="store_true")
    release.add_argument("--begin-release-lane")
    release.add_argument("--record-release-lane")
    release.add_argument("--aggregate-release", action="store_true")
    release.add_argument("--release-selectors")
    parser.add_argument("--release-profile", choices=("core", "diagnostic"), default="core")
    parser.add_argument("--impact-plan", type=Path)
    parser.add_argument("--release-plan", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--job-results", type=Path)
    parser.add_argument("--job-result", default="unknown")
    parser.add_argument("--ci-run-id")
    parser.add_argument("--ci-run-attempt", default="1")
    parser.add_argument("--ci-event", default="local")
    parser.add_argument("--extended-scale", choices=("true", "false"), default="false")
    args = parser.parse_args()
    execution_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    final_report_dir = args.report_dir or REPORT_ROOT / execution_id

    if (
        args.prepare_release
        or args.begin_release_lane
        or args.record_release_lane
        or args.aggregate_release
        or args.release_selectors
    ):
        return _release_action(args, final_report_dir)

    if args.provider_mode == "live":
        return _run_live_certification(final_report_dir, execution_id)

    return _run_exact_deterministic(final_report_dir, execution_id)


def _release_action(args, report_dir: Path) -> int:
    from scripts.qa import release_evidence as release

    plan = {}
    try:
        if args.prepare_release:
            if args.release_plan is None or args.release_plan.exists():
                raise release.EvidenceError("a new explicit release-plan path is required")
            parameters = (
                args.ci_run_id,
                args.ci_run_attempt,
                args.ci_event,
                args.extended_scale == "true",
            )
            if args.release_profile == "core":
                if args.impact_plan is None:
                    raise release.EvidenceError(
                        "reviewed source-impact plan required for core release"
                    )
                plan = release.prepare_core_plan(*parameters, release.read_json(args.impact_plan))
            else:
                plan = release.prepare_plan(*parameters)
            release.write_json(args.release_plan, plan)
            print(
                ("Core" if plan.get("profile") == "core" else "Diagnostic")
                + " release plan: NOT_RUN"
            )
            return 0
        if args.release_plan is None:
            raise release.EvidenceError("release plan required")
        plan = release.read_json(args.release_plan)
        release.validate_plan(plan, current=True)
        if args.release_selectors:
            spec = plan["required_lanes"][args.release_selectors]
            if spec["kind"] != "pytest" or not spec["selectors"]:
                raise release.EvidenceError("nonempty pytest selection required")
            print("\n".join(spec["selectors"]))
            return 0
        if args.begin_release_lane:
            release.begin_native(report_dir, plan, args.begin_release_lane)
            return 0
        if args.record_release_lane:
            result = release.record_lane(
                report_dir, plan, args.record_release_lane, args.job_result
            )
        else:
            if args.evidence_root is None or args.job_results is None:
                raise release.EvidenceError("explicit evidence root and CI job results required")
            result = release.aggregate(
                plan, args.evidence_root, release.read_json(args.job_results)
            )
            release.write_json(report_dir / "report.json", result)
        print("Release evidence: " + result.get("core_verdict", result["verdict"]))
        return 0 if result["verdict"] == "CERTIFIED" else 1
    except (release.EvidenceError, OSError, ValueError, TypeError, KeyError) as exc:
        core = plan.get("profile") == "core" or (
            args.prepare_release and args.release_profile == "core"
        )
        release.write_json(
            report_dir / "report.json",
            {
                "schema": "swinglens.core-release-certification.v1"
                if core
                else "swinglens.full-release-certification.v1",
                "verdict": "NONCERTIFIED",
                **({"core_verdict": "CORE_RELEASE_BLOCKED"} if core else {}),
                "reasons": [str(exc)],
                "gates": {
                    "FULL_RELEASE_REGRESSIONS": "NOT_RUN" if core else "FAIL",
                    "SMALL_LIVE_PROVIDER_CANARY": "NOT_RUN",
                },
                "safe_for_normal_run": False,
            },
        )
        print("Full-release evidence: NONCERTIFIED: " + str(exc))
        return 1


def _run_exact_deterministic(report_dir: Path, execution_id: str) -> int:
    """Supported fixed-specimen path. Other release gates remain explicitly unrun."""
    report_dir.mkdir(parents=True, exist_ok=False)
    resource_advisory = _test_resource_advisory()
    (report_dir / "resource-advisory.json").write_text(
        json.dumps(resource_advisory, indent=2), encoding="utf-8"
    )
    available = resource_advisory.get("available_ram_bytes")
    print(
        f"Available RAM: {available / 1024**3:.2f} GiB"
        if available is not None
        else "Available RAM: unavailable"
    )
    print(f"RAM assessment: {resource_advisory['ram_assessment']}")
    print("Certification execution: PROCEED")
    fingerprint, dirty = _working_tree_identity()
    environment = {
        **os.environ,
        "SWINGLENS_CERTIFICATION_CODE_FINGERPRINT": fingerprint,
        "SWINGLENS_CERTIFICATION_INVOCATION_ID": execution_id,
        "SWINGLENS_CERTIFICATION_PROVIDER_MODE": "deterministic",
        "SWINGLENS_CERTIFICATION_ARTIFACT_ROOT": str(report_dir / "runtime-evidence"),
    }
    # Release orchestration belongs to this parent producer. Its own regression
    # tests and native child runtime keep the unchanged A04 invocation contract.
    environment.pop("SWINGLENS_RELEASE_PLAN", None)
    # An explicit owned PostgreSQL endpoint is a prerequisite. Never fall back to
    # configured NORMAL storage or guess local administrator credentials.
    admin_url = environment.get("SWINGLENS_TEST_POSTGRES_ADMIN_URL")
    if not admin_url or make_url(admin_url).host not in {"127.0.0.1", "localhost", "::1"}:
        report = {
            "execution_id": execution_id,
            "provider_mode": "deterministic",
            "scope": PIPELINE_SCOPE,
            "verdict": "BLOCKED",
            "safe_for_normal_run": False,
            "reason": "explicit owned loopback PostgreSQL prerequisite unavailable",
        }
        _write_report(report_dir, report)
        return 2
    from single_run_certification import candidate_validation

    environment.pop(candidate_validation.CANDIDATE_ENV, None)
    from single_run_certification.golden_oracle import expected_manifest, require_complete

    from scripts.qa.golden_provenance import verify_baseline

    try:
        verify_baseline(REPO_ROOT)
        for mode in (False, True):
            require_complete(expected_manifest(mode))
            if candidate_validation.candidate_available():
                candidate_validation.require_complete(
                    candidate_validation.expected_manifest(mode, candidate=True)
                )
    except Exception as exc:
        report = {
            "execution_id": execution_id,
            "provider_mode": "deterministic",
            "scope": PIPELINE_SCOPE,
            "verdict": "BLOCKED",
            "safe_for_normal_run": False,
            "reason": "sealed oracle prerequisite unavailable: " + repr(exc),
        }
        _write_report(report_dir, report)
        return 2
    selected = list(EXACT_TEST_IDS)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
        "--browser",
        "chromium",
        *selected,
    ]
    collected = subprocess.run(
        command, cwd=REPO_ROOT, env=environment, capture_output=True, text=True, check=False
    )
    (report_dir / "collection.stdout.log").write_text(collected.stdout, encoding="utf-8")
    (report_dir / "collection.stderr.log").write_text(collected.stderr, encoding="utf-8")
    ids = [
        line.strip()
        for line in collected.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    ]
    collection_valid = collected.returncode == 0 and ids == selected and bool(ids)
    results = []
    specialized_prerequisites = []
    certificates = []
    reasons = []
    invocation = {
        "invocation_id": execution_id,
        "selected": selected,
        "collected": ids,
        "executed": [],
        "failed": [],
        "exit_code": None,
    }
    if collection_valid:
        focused = _run_test_group(
            report_dir,
            environment,
            "exact-gate-regressions",
            (
                "tests/test_production_runtime_certification.py",
                "tests/test_certification_runtime.py",
                "tests/e2e/single_run_certification/test_exact_pipeline_certification.py",
                "tests/e2e/single_run_certification/test_golden_completion.py",
                "tests/e2e/single_run_certification/test_golden_oracle.py",
            ),
        )
        results.append(focused)
        prerequisites_passed = focused.exit_code == 0
        if prerequisites_passed and os.environ.get("SWINGLENS_RELEASE_PLAN"):
            from scripts.qa import release_evidence as release

            prerequisite_plan = release.read_json(Path(os.environ["SWINGLENS_RELEASE_PLAN"]))
            release.validate_plan(prerequisite_plan, current=True)
            specialized_prerequisites = prerequisite_plan.get(
                "a04_prerequisites", {}
            ).get("selectors", [])
            if specialized_prerequisites:
                try:
                    prerequisite = _run_impact_prerequisites(
                        report_dir, environment, prerequisite_plan,
                    )
                    results.append(prerequisite)
                    prerequisites_passed = prerequisite.exit_code == 0
                except release.EvidenceError as exc:
                    reasons.append("source-impact prerequisite evidence invalid: " + str(exc))
                    prerequisites_passed = False
                if not prerequisites_passed:
                    reasons.append("required source-impact prerequisites failed")
        if prerequisites_passed:
            if candidate_validation.candidate_available():
                environment[candidate_validation.CANDIDATE_ENV] = "1"
            native = _run_test_group(report_dir, environment, "production-runtime", tuple(selected))
            results.append(native)
            invocation.update(_junit_execution(report_dir / "production-runtime.xml"))
            invocation["exit_code"] = native.exit_code
        elif focused.exit_code != 0:
            reasons.append("focused gate regressions failed")
    else:
        reasons.append("exact collection missing, duplicate, zero, or selector drift")
    (report_dir / "invocation.json").write_text(json.dumps(invocation, indent=2), encoding="utf-8")
    contracts = sorted((report_dir / "runtime-evidence").glob("*/certificate-contract.json"))
    seen = []
    current_fingerprint, _ = _working_tree_identity()
    for path in contracts:
        try:
            contract = json.loads(path.read_text(encoding="utf-8"))
            identity = contract["identity"]
            seen.append(identity["test_id"])
            mode = identity["test_id"] == EXACT_TEST_IDS[1]
            expected_identity = {
                **identity,
                "source_fingerprint": fingerprint,
                "source_revision": _git("rev-parse", "HEAD"),
                "invocation_id": execution_id,
                "handoff": mode,
                "runtime_instance_id": path.parent.name,
                "execution_id": path.parent.name,
            }
            if current_fingerprint != fingerprint:
                expected_identity["source_fingerprint"] = current_fingerprint
            certificate = finalize_pipeline_certificate(
                path.parent, invocation=invocation, expected_identity=expected_identity
            )
            certificates.append(
                {
                    "path": str((path.parent / "certificate.json").relative_to(report_dir)),
                    "test_id": identity["test_id"],
                    "verdict": certificate["verdict"],
                    "execution_id": identity["execution_id"],
                }
            )
        except Exception as exc:
            reasons.append(f"invalid scenario report {path.name}: {type(exc).__name__}: {exc}")
    if sorted(seen) != sorted(selected):
        reasons.append("missing, stale, duplicate, or foreign handoff scenario report")
    verdict = invocation_verdict(invocation, certificates, reasons)
    report = {
        "schema": "swinglens.production-runtime-certification.v2",
        "execution_id": execution_id,
        "provider_mode": "deterministic",
        "scope": PIPELINE_SCOPE,
        "verdict": verdict,
        "git_sha": _git("rev-parse", "HEAD"),
        "working_tree_fingerprint": fingerprint,
        "git_dirty": dirty,
        "resource_advisory": resource_advisory,
        "test_groups": [asdict(r) for r in results],
        "invocation": invocation,
        "certificates": certificates,
        "specialized_prerequisites": specialized_prerequisites,
        **({"candidate_validation": candidate_validation.candidate_binding()}
           if environment.get(candidate_validation.CANDIDATE_ENV) == "1" else {}),
        "reasons": reasons,
        "gates": {
            "EXACT_PIPELINE_CERTIFICATION": verdict,
            "FULL_RELEASE_REGRESSIONS": "NOT_RUN",
            "SMALL_LIVE_PROVIDER_CANARY": "NOT_RUN",
        },
        "safe_for_normal_run": False,
    }
    if os.environ.get("SWINGLENS_RELEASE_PLAN"):
        from scripts.qa import release_evidence as release

        plan = release.read_json(Path(os.environ["SWINGLENS_RELEASE_PLAN"]))
        release.validate_plan(plan, current=True)
        report["release_context"] = release.context(plan, "a04")
    _write_report(report_dir, report)
    print(f"{PIPELINE_SCOPE}: {verdict}")
    print(f"Report: {report_dir / 'report.json'}")
    return verdict_exit_code(verdict)


def _junit_execution(path: Path) -> dict:
    if not path.is_file():
        return {"executed": [], "failed": ["missing pytest JUnit result"]}
    cases = list(ET.parse(path).getroot().iter("testcase"))
    ids = [
        case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"] for case in cases
    ]
    return {
        "executed": ids,
        "failed": [
            node
            for node, case in zip(ids, cases, strict=True)
            if any(child.tag in {"failure", "error", "skipped"} for child in case)
        ],
    }


def invocation_verdict(invocation: dict, certificates: list[dict], reasons: list[str]) -> str:
    statuses = {row["verdict"] for row in certificates}
    if (
        reasons
        or "FAILED" in statuses
        or invocation.get("exit_code") not in {0, None}
        or invocation.get("failed")
        or invocation.get("selected") != list(EXACT_TEST_IDS)
        or invocation.get("collected") != list(EXACT_TEST_IDS)
        or (invocation.get("exit_code") == 0 and invocation.get("executed") != list(EXACT_TEST_IDS))
        or sorted(row["test_id"] for row in certificates) != sorted(EXACT_TEST_IDS)
    ):
        return "FAILED"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "INCOMPLETE" in statuses or invocation.get("exit_code") is None:
        return "INCOMPLETE"
    return "CERTIFIED" if statuses == {"CERTIFIED"} else "INCOMPLETE"


def verdict_exit_code(verdict: str) -> int:
    return {"CERTIFIED": 0, "FAILED": 1, "BLOCKED": 2, "INCOMPLETE": 3}[verdict]


def _test_resource_advisory() -> dict:
    """Host RAM is diagnostic only, never a test admission or cancellation gate."""
    result = {
        "execution": "PROCEED",
        "low_ram_cancellation": False,
        "warning_threshold_bytes": 6 * 1024**3,
        "policy": (
            "Advisory host RAM only; production worker safeguards and actual failures "
            "remain authoritative."
        ),
    }
    try:
        memory = psutil.virtual_memory()
        result.update(
            available_ram_bytes=memory.available,
            total_ram_bytes=memory.total,
            ram_assessment=(
                "WARNING" if memory.available < result["warning_threshold_bytes"] else "OK"
            ),
        )
    except (OSError, psutil.Error) as exc:
        result.update(ram_assessment="WARNING", telemetry_error=f"{type(exc).__name__}: {exc}")
    return result


def _sample_test_resources() -> dict:
    """Sample this producer and its descendants without controlling any process."""
    result = {"timestamp": datetime.now(UTC).isoformat(), **_test_resource_advisory()}
    try:
        result["host_cpu_percent"] = psutil.cpu_percent(interval=None)
        processes = [psutil.Process()]
        processes.extend(processes[0].children(recursive=True))
        rows = []
        for process in processes:
            try:
                memory = process.memory_info()
                rows.append(
                    {
                        "pid": process.pid,
                        "parent_pid": process.ppid(),
                        "name": process.name(),
                        "created_epoch": process.create_time(),
                        "rss_bytes": memory.rss,
                        "private_bytes": getattr(memory, "private", None),
                    }
                )
            except (OSError, psutil.Error):
                continue
        result["processes"] = rows
        result["tree_rss_bytes"] = sum(row["rss_bytes"] for row in rows)
        result["tree_private_bytes"] = sum(row["private_bytes"] or 0 for row in rows)
        result["scope"] = (
            "Producer and descendants; aggregate RSS may count shared pages more than once."
        )
    except (OSError, psutil.Error) as exc:
        result["telemetry_error"] = f"{type(exc).__name__}: {exc}"
    return result


@contextmanager
def _record_test_resources(path: Path):
    """Observe tests; low host RAM never skips, blocks, signals or cancels them."""
    stop = Event()
    with path.open("w", encoding="utf-8") as stream:

        def record():
            stream.write(json.dumps(_sample_test_resources()) + "\n")
            stream.flush()

        def observe():
            while not stop.wait(1):
                record()

        record()
        observer = Thread(target=observe, name="certification-resource-telemetry", daemon=True)
        observer.start()
        try:
            yield
        finally:
            stop.set()
            observer.join()
            record()


def _run_test_group(
    report_dir: Path,
    environment: dict[str, str],
    name: str,
    selectors: tuple[str, ...],
) -> TestGroupResult:
    browsers = ["chromium"]
    if environment.get("SWINGLENS_RELEASE_LANE") == "a04-prerequisites":
        from scripts.qa import release_evidence as release

        browsers = release.read_json(Path(environment["SWINGLENS_RELEASE_PLAN"]))[
            "a04_prerequisites"
        ]["browsers"]
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-rs",
        "-p",
        "no:cacheprovider",
        *(argument for browser in browsers for argument in ("--browser", browser)),
        "--output",
        str(report_dir / "browser-output"),
        "--junitxml",
        str(report_dir / f"{name}.xml"),
        *selectors,
    ]
    if environment.get("SWINGLENS_CERTIFICATION_PROVIDER_MODE") == "deterministic":
        bootstrap = """
import runpy, sys
def guard(event, args):
    if event not in {'socket.connect', 'socket.getaddrinfo'}:
        return
    address = args[1] if event == 'socket.connect' else args[:2]
    if (not isinstance(address, tuple) or len(address) < 2 or
        address[0] not in {'127.0.0.1', 'localhost', '::1'} or
        address[1] in {7496, 7497, 4001, 4002}):
        raise RuntimeError('CERTIFICATION_UNEXPECTED_NETWORK_ACCESS')
sys.addaudithook(guard)
import os
from pathlib import Path
if os.environ.get('SWINGLENS_VERIFY_SOURCE_ROOT'):
    from scripts.qa.source_bound import record_origins
    record_origins(Path(os.environ['SWINGLENS_VERIFY_SOURCE_ROOT']),
        Path(os.environ['SWINGLENS_SOURCE_PROVENANCE_DIR']),
        ('app.main', 'app.settings', 'app.database_safety',
         'scripts.production_runtime_certification'))
sys.argv = ['pytest', *sys.argv[1:]]
runpy.run_module('pytest', run_name='__main__')
"""
        command = [sys.executable, "-c", bootstrap, *command[3:]]
    started = monotonic()
    with _record_test_resources(report_dir / f"{name}.resources.jsonl"):
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
    duration = monotonic() - started
    stdout_path = report_dir / f"{name}.stdout.log"
    stderr_path = report_dir / f"{name}.stderr.log"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    status = "PASS" if completed.returncode == 0 else "FAIL"
    print(f"{name}: {status} ({duration:.1f}s)")
    return TestGroupResult(
        name=name,
        status=status,
        exit_code=completed.returncode,
        duration_seconds=round(duration, 3),
        command=command,
        stdout_log=stdout_path.name,
        stderr_log=stderr_path.name,
    )


def _run_impact_prerequisites(report_dir: Path, environment: dict[str, str], plan: dict):
    """Execute triggered retained cases with the original release evidence protocol."""
    from scripts.qa import release_evidence as release

    name = "source-impact-prerequisites"
    directory = report_dir / name
    spec = plan["a04_prerequisites"]
    _prepare_prerequisite_browsers(report_dir, environment, spec["browsers"])
    plugins = environment.get("PYTEST_PLUGINS", "").split(",")
    plugins = [plugin for plugin in plugins if plugin]
    if "scripts.qa.release_evidence" not in plugins:
        plugins.append("scripts.qa.release_evidence")
    child = {
        **environment,
        "PYTEST_PLUGINS": ",".join(plugins),
        "SWINGLENS_RELEASE_PLAN": os.environ["SWINGLENS_RELEASE_PLAN"],
        "SWINGLENS_RELEASE_LANE": "a04-prerequisites",
        "SWINGLENS_RELEASE_CAPTURE": "collection",
        "SWINGLENS_RELEASE_RECORD_DIR": str(directory),
    }
    command = [
        sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
        *(argument for browser in spec["browsers"] for argument in ("--browser", browser)),
        "--output", str(report_dir / "prerequisite-browser-scratch"),
        *spec["selectors"],
    ]
    if child.get("SWINGLENS_VERIFY_SOURCE_ROOT"):
        command = [sys.executable, "-m", "scripts.qa.source_bound", "--receipt-dir",
            child["SWINGLENS_SOURCE_PROVENANCE_DIR"], "--module", "pytest", "--",
            *command[3:]]
    collected = subprocess.run(
        command, cwd=REPO_ROOT, env=child, capture_output=True, text=True, check=False,
    )
    (report_dir / (name + ".collection.stdout.log")).write_text(collected.stdout)
    (report_dir / (name + ".collection.stderr.log")).write_text(collected.stderr)
    if collected.returncode:
        return TestGroupResult(name, "FAIL", collected.returncode, 0, command, "", "")
    child["SWINGLENS_RELEASE_CAPTURE"] = "execution"
    result = _run_test_group(report_dir, child, name, tuple(spec["selectors"]))
    shutil.copyfile(report_dir / (name + ".xml"), directory / "results.xml")
    observed = release.junit_cases(directory / "results.xml", "pytest")
    proof = release._pytest_results(directory, plan, "a04-prerequisites", observed)
    if (
        proof["process_exit_code"] != 0
        or set(proof["passed"]) != set(spec["selectors"])
        or set(proof["body_executed"]) != set(spec["selectors"])
        or any(proof[key] for key in release.CATEGORIES if key != "passed")
    ):
        raise release.EvidenceError("specialized prerequisite failed, skipped or never executed")
    return result


def _prepare_prerequisite_browsers(report_dir: Path, environment: dict[str, str], browsers: list):
    """The existing A04 CI job provisions Chromium; triggered cases also owe Firefox."""
    from scripts.qa.release_evidence import EvidenceError

    if not browsers or environment.get("GITHUB_ACTIONS") != "true":
        return
    if not set(browsers) <= {"chromium", "firefox"}:
        raise EvidenceError("unreviewed prerequisite browser")
    command = [sys.executable, "-m", "playwright", "install", "--with-deps", *browsers]
    prepared = subprocess.run(command, cwd=REPO_ROOT, env=environment,
                              capture_output=True, text=True, check=False)
    (report_dir / "prerequisite-browser-install.stdout.log").write_text(prepared.stdout)
    (report_dir / "prerequisite-browser-install.stderr.log").write_text(prepared.stderr)
    if prepared.returncode:
        raise EvidenceError("required prerequisite browser installation failed")


def _read_runtime_report(path: Path, report_dir: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "path": str(path.relative_to(report_dir)),
        "execution": payload.get("execution"),
        "run_id": payload.get("run_id"),
        "verdict": payload.get("verdict"),
        "pipeline_steps": payload.get("pipeline_steps"),
        "failures": payload.get("failures"),
        "warnings": payload.get("warnings"),
    }


def _runtime_invariants_pass(reports: list[Path]) -> bool:
    if not reports:
        return False
    for report_path in reports:
        invariant_path = report_path.parent / "runtime-invariants.jsonl"
        if not invariant_path.exists():
            return False
        samples = [line for line in invariant_path.read_text(encoding="utf-8").splitlines() if line]
        if not samples:
            return False
        if any(json.loads(line).get("failures") for line in samples):
            return False
    return True


def _performance_gate(
    reports: list[dict[str, object]],
    *,
    runtime_duration_seconds: float,
) -> tuple[bool, dict[str, object]]:
    """Apply deliberately broad first-generation regression tripwires."""

    total_ceiling_seconds = 900.0
    stage_ceiling_seconds = 300.0
    stage_timings: list[dict[str, object]] = []
    passed = bool(reports) and runtime_duration_seconds <= total_ceiling_seconds
    for report in reports:
        for step in report.get("pipeline_steps") or []:
            started_at = step.get("started_at")
            completed_at = step.get("completed_at")
            duration_seconds = None
            if started_at and completed_at:
                duration_seconds = max(
                    0.0,
                    (
                        datetime.fromisoformat(str(completed_at))
                        - datetime.fromisoformat(str(started_at))
                    ).total_seconds(),
                )
                if duration_seconds > stage_ceiling_seconds:
                    passed = False
            elif step.get("status") in {"COMPLETED", "PARTIAL"}:
                passed = False
            stage_timings.append(
                {
                    "step_order": step.get("step_order"),
                    "step_name": step.get("step_name"),
                    "status": step.get("status"),
                    "duration_seconds": duration_seconds,
                    "ceiling_seconds": stage_ceiling_seconds,
                    "query_count": _observable_query_count(step.get("result_json")),
                    "recovery_count": int(step.get("retry_count") or 0),
                }
            )
    return passed, {
        "runtime_duration_seconds": runtime_duration_seconds,
        "runtime_ceiling_seconds": total_ceiling_seconds,
        "stage_ceiling_seconds": stage_ceiling_seconds,
        "stage_timings": stage_timings,
        "verdict": "PASS" if passed else "FAIL",
        "policy": "initial regression tripwires; not production SLA targets",
    }


def _observable_query_count(result: object) -> int | None:
    if not isinstance(result, dict):
        return None
    candidates = (
        result.get("query_count"),
        result.get("sql_select_count"),
        (result.get("telemetry") or {}).get("sql_select_count")
        if isinstance(result.get("telemetry"), dict)
        else None,
        (result.get("sql_monitor") or {}).get("statement_count")
        if isinstance(result.get("sql_monitor"), dict)
        else None,
    )
    return next((int(value) for value in candidates if value is not None), None)


def _run_live_certification(report_dir: Path, execution_id: str) -> int:
    """Run the real-provider boundary through the certified production topology."""

    working_tree_fingerprint, git_dirty = _working_tree_identity()
    started = datetime.now(UTC)
    preflight = _live_provider_preflight()
    deterministic_report = _matching_deterministic_report(working_tree_fingerprint)
    if deterministic_report is None:
        preflight["deterministic_candidate"] = {
            "ready": False,
            "reason": "No passing deterministic certification exists for this fingerprint.",
        }
    else:
        preflight["deterministic_candidate"] = {
            "ready": True,
            "execution_id": deterministic_report.get("execution_id"),
            "working_tree_fingerprint": deterministic_report.get("working_tree_fingerprint"),
        }
    blockers = [
        value.get("reason")
        for value in preflight.values()
        if isinstance(value, dict) and not value.get("ready")
    ]
    if blockers:
        report_dir.mkdir(parents=True, exist_ok=False)
        report = _live_report_base(
            execution_id=execution_id,
            started=started,
            working_tree_fingerprint=working_tree_fingerprint,
            git_dirty=git_dirty,
            preflight=preflight,
        )
        report.update(
            {
                "finished_at": datetime.now(UTC).isoformat(),
                "verdict": "BLOCKED",
                "reason": "; ".join(str(value) for value in blockers if value),
                "gates": {"SMALL_LIVE_PROVIDER_CANARY": "BLOCKED"},
                "safe_for_normal_run": False,
            }
        )
        _write_report(report_dir, report)
        print("SMALL_LIVE_PROVIDER_CANARY: BLOCKED")
        print("SAFE_FOR_NORMAL_RUN: NO")
        print(f"Report: {report_dir / 'report.json'}")
        return 2

    temporary = Path(tempfile.mkdtemp(prefix=f"swinglens-live-cert-{execution_id}-"))
    environment = dict(os.environ)
    environment.setdefault("SWINGLENS_TEST_POSTGRES_ADMIN_URL", _postgres_admin_url())
    environment["SWINGLENS_CERTIFICATION_CODE_FINGERPRINT"] = working_tree_fingerprint
    environment["SWINGLENS_CERTIFICATION_ARTIFACT_ROOT"] = str(temporary / "runtime-evidence")
    environment["SWINGLENS_CERTIFICATION_PROVIDER_MODE"] = "live"
    result = _run_test_group(
        temporary,
        environment,
        "live-provider-canary",
        (
            "tests/e2e/single_run_certification/test_single_run_certification.py::"
            "test_live_provider_canary_uses_production_runtime",
        ),
    )
    report_paths = sorted((temporary / "runtime-evidence").glob("*/report.json"))
    runtime_evidence = [_read_runtime_report(path, temporary) for path in report_paths]
    live_pass = (
        result.status == "PASS"
        and bool(runtime_evidence)
        and all(item.get("verdict") == "PASS" for item in runtime_evidence)
        and _runtime_invariants_pass(report_paths)
    )
    gates = dict(deterministic_report.get("gates") or {})
    gates["SMALL_LIVE_PROVIDER_CANARY"] = "PASS" if live_pass else "FAIL"
    safe = bool(gates) and all(value == "PASS" for value in gates.values())
    report = _live_report_base(
        execution_id=execution_id,
        started=started,
        working_tree_fingerprint=working_tree_fingerprint,
        git_dirty=git_dirty,
        preflight=preflight,
    )
    report.update(
        {
            "finished_at": datetime.now(UTC).isoformat(),
            "verdict": "PASS" if live_pass else "FAIL",
            "test_groups": [asdict(result)],
            "runtime_reports": [str(path.relative_to(temporary)) for path in report_paths],
            "runtime_evidence": runtime_evidence,
            "deterministic_execution_id": deterministic_report.get("execution_id"),
            "gates": gates,
            "safe_for_normal_run": safe,
        }
    )
    _write_report(temporary, report)
    report_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(temporary, report_dir)
    shutil.rmtree(temporary)
    print(f"SMALL_LIVE_PROVIDER_CANARY: {'PASS' if live_pass else 'FAIL'}")
    print(f"SAFE_FOR_NORMAL_RUN: {'YES' if safe else 'NO'}")
    print(f"Report: {report_dir / 'report.json'}")
    return 0 if safe else 1


def _live_report_base(
    *,
    execution_id: str,
    started: datetime,
    working_tree_fingerprint: str,
    git_dirty: bool,
    preflight: dict[str, dict[str, object]],
) -> dict[str, object]:
    return {
        "schema": "swinglens.production-runtime-certification.v1",
        "execution_id": execution_id,
        "provider_mode": "live",
        "started_at": started.isoformat(),
        "git_sha": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "git_dirty": git_dirty,
        "working_tree_fingerprint": working_tree_fingerprint,
        "migration_head": ScriptDirectory.from_config(
            Config(str(REPO_ROOT / "alembic.ini"))
        ).get_current_head(),
        "effective_configuration_fingerprint": _configuration_fingerprint(),
        "database_identity": _sanitized_database_identity(),
        "provider_preflight": preflight,
    }


def _live_provider_preflight() -> dict[str, dict[str, object]]:
    settings = get_settings()
    sec_valid, sec_reason = _validate_sec_user_agent(settings.sec_user_agent)
    eodhd_ready = bool(
        settings.eodhd_api_key
        and str(settings.eodhd_api_key).strip()
        and "placeholder" not in str(settings.eodhd_api_key).lower()
    )
    ib_health = check_ib_gateway_status(settings)
    ib_historical = check_historical_data_capability(settings) if ib_health.api_ready else None
    return {
        "sec": {
            "ready": sec_valid,
            "source": "SEC_USER_AGENT environment / Settings.sec_user_agent",
            "reason": None if sec_valid else sec_reason,
        },
        "eodhd": {
            "ready": eodhd_ready,
            "source": "EODHD_API_KEY environment / Settings.eodhd_api_key",
            "reason": None if eodhd_ready else "EODHD_API_KEY is missing or a placeholder.",
        },
        "ib": {
            "ready": bool(
                ib_health.api_ready and ib_historical is not None and ib_historical.ready
            ),
            "host": settings.ib_host,
            "port": settings.ib_port,
            "client_id": ib_health.client_id,
            "health": ib_health.to_dict(),
            "historical": ib_historical.to_dict() if ib_historical else None,
            "reason": (
                None
                if ib_health.api_ready and ib_historical is not None and ib_historical.ready
                else "Configured IB endpoint did not pass API and historical-data probes."
            ),
        },
    }


def _validate_sec_user_agent(value: str | None) -> tuple[bool, str | None]:
    normalized = str(value or "").strip()
    lowered = normalized.lower()
    invalid_markers = (
        "example.invalid",
        "example.com",
        "placeholder",
        "changeme",
        "localhost",
    )
    if not normalized:
        return False, "SEC_USER_AGENT is not configured."
    if any(marker in lowered for marker in invalid_markers):
        return False, "SEC_USER_AGENT still contains a placeholder contact identity."
    tokens = normalized.replace("<", " ").replace(">", " ").split()
    emails = [token.strip('(),;"') for token in tokens if "@" in token]
    if not any(
        email.count("@") == 1 and "." in email.rsplit("@", 1)[1] and not email.endswith(".")
        for email in emails
    ):
        return False, "SEC_USER_AGENT must contain a real contact email address."
    return True, None


def _matching_deterministic_report(fingerprint: str) -> dict[str, object] | None:
    if not REPORT_ROOT.exists():
        return None
    for path in sorted(REPORT_ROOT.glob("*/report.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        gates = payload.get("gates") or {}
        deterministic_pass = bool(gates) and all(
            value == "PASS" for key, value in gates.items() if key != "SMALL_LIVE_PROVIDER_CANARY"
        )
        if (
            payload.get("provider_mode") == "deterministic"
            and payload.get("working_tree_fingerprint") == fingerprint
            and deterministic_pass
            and payload.get("verdict") == "CERTIFIED"
            and gates.get("FULL_RELEASE_REGRESSIONS") == "PASS"
            and gates.get("EXACT_PIPELINE_CERTIFICATION") == "PASS"
        ):
            return payload
    return None


def _write_report(report_dir: Path, report: dict[str, object]) -> None:
    (report_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    gates = report.get("gates") or {}
    gate_lines = "\n".join(f"- `{key}`: **{value}**" for key, value in gates.items())
    group_lines = "\n".join(
        f"- `{item['name']}`: **{item['status']}** in "
        f"{item['duration_seconds']}s ([stdout]({item['stdout_log']}), "
        f"[stderr]({item['stderr_log']}))"
        for item in report.get("test_groups") or []
    )
    runtime_lines = "\n".join(
        f"- `{item['path']}`: **{item['verdict']}**, run `{item['run_id']}`"
        for item in report.get("runtime_evidence") or []
    )
    performance = report.get("performance_baseline") or {}
    identity_lines = "\n".join(
        f"- {label}: `{report.get(key, 'unavailable')}`"
        for label, key in (
            ("Git SHA", "git_sha"),
            ("Branch", "branch"),
            ("Git dirty", "git_dirty"),
            ("Working tree fingerprint", "working_tree_fingerprint"),
            ("Migration head", "migration_head"),
            ("Configuration fingerprint", "effective_configuration_fingerprint"),
            ("Database", "database_identity"),
        )
    )
    (report_dir / "REPORT.md").write_text(
        "# SwingLens Production Runtime Certification\n\n"
        f"Execution: `{report['execution_id']}`\n\n"
        f"Verdict: **{report.get('verdict', 'INCOMPLETE')}**\n\n"
        f"Scope: {report.get('scope', 'separate live-provider promotion')}\n\n"
        f"Provider mode: `{report['provider_mode']}`\n\n"
        f"SAFE_FOR_NORMAL_RUN: **{'YES' if report.get('safe_for_normal_run') else 'NO'}**\n\n"
        f"## Runtime identity\n\n{identity_lines}\n\n"
        f"## Promotion gates\n\n{gate_lines or '- No gates executed.'}\n\n"
        f"## Test groups\n\n{group_lines or '- No test groups executed.'}\n\n"
        f"## Production runtime evidence\n\n"
        f"{runtime_lines or '- No production runtime report was produced.'}\n\n"
        f"## Performance baseline\n\n"
        f"- Verdict: **{performance.get('verdict', 'NOT_RUN')}**\n"
        f"- Runtime: `{performance.get('runtime_duration_seconds', 'n/a')}s`\n"
        f"- Runtime tripwire: `{performance.get('runtime_ceiling_seconds', 'n/a')}s`\n"
        f"- Per-stage tripwire: `{performance.get('stage_ceiling_seconds', 'n/a')}s`\n"
        f"- Policy: {performance.get('policy', 'not available')}\n",
        encoding="utf-8",
    )


def _postgres_admin_url() -> str:
    url = make_url(get_settings().database_url).set(database="postgres", drivername="postgresql")
    return url.render_as_string(hide_password=False)


def _sanitized_database_identity() -> str:
    url = make_url(get_settings().database_url)
    return f"{url.host}:{url.port or 5432}/disposable-per-certification"


def _configuration_fingerprint() -> str:
    settings = get_settings()
    values = {
        "runtime_mode": "CERTIFICATION",
        "use_durable_pipeline": True,
        "durable_worker_process_enabled": True,
        "embedded_job_worker_enabled": False,
        "ceri_enabled": settings.ceri_enabled,
        "setup_lifecycle_enabled": settings.setup_lifecycle_enabled,
        "winner_probability_enabled": settings.winner_probability_enabled,
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _working_tree_identity() -> tuple[str, bool]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    digest = hashlib.sha256()
    digest.update(_git("rev-parse", "HEAD").encode("utf-8"))
    digest.update(b"\0status\0")
    digest.update(status)
    digest.update(b"\0tracked-diff\0")
    digest.update(
        subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--", "."],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    for encoded in sorted(value for value in untracked if value):
        relative = encoded.decode("utf-8")
        digest.update(b"\0untracked\0")
        digest.update(encoded)
        digest.update((REPO_ROOT / relative).read_bytes())
    return digest.hexdigest(), bool(status)


if __name__ == "__main__":
    raise SystemExit(main())
