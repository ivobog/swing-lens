from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.services.ib_gateway_health_service import check_status as check_ib_gateway_status
from app.services.ib_historical_capability import check_historical_data_capability
from app.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = REPO_ROOT / "test-results" / "production-runtime-certification"


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
        (
            "tests/e2e/single_run_certification/test_single_run_certification.py::test_single_run_comprehensive_e2e_certification",
            "tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_frozen_real_writer_cardinality[chromium-10]",
        ),
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the production SwingLens runtime.")
    parser.add_argument(
        "--provider-mode", choices=("deterministic", "live"), default="deterministic"
    )
    args = parser.parse_args()
    execution_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    final_report_dir = REPORT_ROOT / execution_id

    if args.provider_mode == "live":
        return _run_live_certification(final_report_dir, execution_id)

    # Some legacy focused tests clean the repository's test-results directory.
    # Keep the running evidence package outside that tree, then publish it
    # atomically after every subprocess has exited.
    report_dir = Path(tempfile.mkdtemp(prefix=f"swinglens-cert-{execution_id}-"))

    environment = dict(os.environ)
    environment.setdefault("SWINGLENS_TEST_POSTGRES_ADMIN_URL", _postgres_admin_url())
    working_tree_fingerprint, git_dirty = _working_tree_identity()
    environment["SWINGLENS_CERTIFICATION_CODE_FINGERPRINT"] = working_tree_fingerprint
    environment["SWINGLENS_CERTIFICATION_ARTIFACT_ROOT"] = str(report_dir / "runtime-evidence")
    started = datetime.now(UTC)
    results = [
        _run_test_group(report_dir, environment, name, selectors) for name, selectors in TEST_GROUPS
    ]
    finished = datetime.now(UTC)
    reports = sorted((report_dir / "runtime-evidence").glob("*/report.json"))
    runtime_evidence = [_read_runtime_report(path, report_dir) for path in reports]
    by_name = {result.name: result for result in results}
    runtime_pass = (
        by_name["production-runtime"].status == "PASS"
        and bool(runtime_evidence)
        and all(item.get("verdict") == "PASS" for item in runtime_evidence)
        and _runtime_invariants_pass(reports)
    )
    performance_pass, performance_baseline = _performance_gate(
        runtime_evidence,
        runtime_duration_seconds=by_name["production-runtime"].duration_seconds,
    )
    gates = {
        "FOCUSED_REGRESSION_TESTS": by_name["focused-regressions"].status,
        "POSTGRESQL_TRANSACTION_CERTIFICATION": (by_name["postgresql-transactions"].status),
        "PRODUCTION_RUNTIME_CERTIFICATION": "PASS" if runtime_pass else "FAIL",
        "WATCHDOG_AND_RECOVERY_CERTIFICATION": (by_name["watchdog-recovery"].status),
        "RUN_ISOLATION_CERTIFICATION": (
            "PASS"
            if runtime_pass and by_name["certification-isolation"].status == "PASS"
            else "FAIL"
        ),
        "PERFORMANCE_REGRESSION_GATE": ("PASS" if runtime_pass and performance_pass else "FAIL"),
        # A deterministic invocation never implies that external providers
        # were exercised. Promotion remains closed until the explicit canary.
        "SMALL_LIVE_PROVIDER_CANARY": "NOT_RUN",
    }
    safe = all(value == "PASS" for value in gates.values())
    deterministic_pass = all(
        value == "PASS" for key, value in gates.items() if key != "SMALL_LIVE_PROVIDER_CANARY"
    )
    report = {
        "schema": "swinglens.production-runtime-certification.v1",
        "execution_id": execution_id,
        "provider_mode": args.provider_mode,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "git_sha": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "git_dirty": git_dirty,
        "working_tree_fingerprint": working_tree_fingerprint,
        "migration_head": ScriptDirectory.from_config(
            Config(str(REPO_ROOT / "alembic.ini"))
        ).get_current_head(),
        "effective_configuration_fingerprint": _configuration_fingerprint(),
        "database_identity": _sanitized_database_identity(),
        "test_groups": [asdict(result) for result in results],
        "runtime_reports": [str(path.relative_to(report_dir)) for path in reports],
        "runtime_evidence": runtime_evidence,
        "performance_baseline": performance_baseline,
        "gates": gates,
        "safe_for_normal_run": safe,
    }
    _write_report(report_dir, report)
    final_report_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(report_dir, final_report_dir)
    if report_dir.name.startswith("swinglens-cert-"):
        shutil.rmtree(report_dir)
    print(
        "Deterministic production runtime certification: "
        f"{'PASS' if deterministic_pass else 'FAIL'}"
    )
    print("Small live provider canary: NOT RUN")
    print("SAFE_FOR_NORMAL_RUN: NO")
    print(f"Report: {final_report_dir / 'report.json'}")
    return 0 if deterministic_pass else 1


def _run_test_group(
    report_dir: Path,
    environment: dict[str, str],
    name: str,
    selectors: tuple[str, ...],
) -> TestGroupResult:
    command = [sys.executable, "-m", "pytest", "-q", "-rs", *selectors]
    started = monotonic()
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
