"""Run the retained scheduler release contract and bind receipts to exact source.

This qualifies queue behavior, not financial outputs or a complete live pipeline.
PostgreSQL tests must execute; a missing server or any selected skip blocks PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    "tests/test_background_queue.py",
    "tests/test_background_job_service.py",
    "tests/test_background_worker.py",
    "tests/test_pipeline_stage_registry.py",
    "tests/test_pipeline_progress_route.py",
    "tests/test_pipeline_dependency_progress.py",
    "tests/test_scheduler_release_qualification.py",
    "tests/ceri/test_batched_workflow_v2.py",
    "tests/integration/test_queue_fairness_postgresql.py",
    "tests/integration/test_pipeline_scheduler_readiness_postgresql.py",
    "tests/integration/test_ceri_batched_workflow_v2.py",
)
SOURCES = (
    "app/services/background_queue.py",
    "app/services/background_job_service.py",
    "app/services/background_worker.py",
    "app/services/ceri/batched_claims.py",
    "app/services/ceri/batched_workflow.py",
    "app/services/ceri/batched_job_handlers.py",
    "app/services/pipeline_execution_authority.py",
    "app/routers/run_routes.py",
    "app/templates/pipeline_progress.html",
    "app/static/app.js",
    "scripts/qa/qualify_pipeline_scheduler.py",
    "scripts/qa/verify_pipeline_scheduler_qualification.py",
    *TARGETS,
)


def source_hashes():
    return {
        name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in SOURCES
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    assert not any(output.iterdir()), "Preserve earlier qualification receipts"
    before = source_hashes()
    command = [
        sys.executable,
        "-m",
        "pytest",
        *TARGETS,
        "-q",
        "--tb=short",
        f"--junitxml={output / 'tests.xml'}",
    ]
    with (output / "tests.txt").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    cases = ET.parse(output / "tests.xml").findall(".//testcase")
    failures = sum(
        any(case.find(tag) is not None for tag in ("error", "failure", "skipped")) for case in cases
    )
    unchanged = before == source_hashes()
    passed = result.returncode == 0 and bool(cases) and not failures and unchanged
    report = {
        "status": "PASS" if passed else "FAIL",
        "scope": (
            "Scheduler, native CERI regressions and pipeline status; "
            "not full pipeline certification"
        ),
        "completed_at": datetime.now(UTC).isoformat(),
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip(),
        "source_sha256": before,
        "source_unchanged_during_checks": unchanged,
        "hash_basis": "SHA256 with CRLF normalized to LF for cross-checkout verification",
        "tests": len(cases),
        "failed_errored_or_skipped": failures,
        "pytest_exit_code": result.returncode,
    }
    (output / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "tests",
                    "failed_errored_or_skipped",
                    "source_unchanged_during_checks",
                )
            }
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
