"""Qualify the complete native handoff repair and bind it to frozen release source.

Retains the prior scheduler contract and requires real 159-company financial-tail
outputs from a disposable PostgreSQL database. Every selected test must execute.
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

from scripts.qa.qualify_pipeline_scheduler import SOURCES as SCHEDULER_SOURCES
from scripts.qa.qualify_pipeline_scheduler import TARGETS as SCHEDULER_TARGETS

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (*SCHEDULER_TARGETS,
    "tests/test_pipeline61_handoff_progress.py",
    "tests/test_pipeline61_price_projection.py",
    "tests/test_pipeline61_progress_diagnostics.py",
    "tests/test_pipeline_executor.py",
    "tests/setup_lifecycle/test_snapshot_builder.py",
    "tests/setup_lifecycle/test_source_loader.py",
    "tests/test_sector_serialization.py",
    "tests/test_contextual_consumer_eligibility.py",
    "tests/test_core_mutation_temporal_identity.py",
    "tests/integration/test_pipeline61_handoff_postgresql.py",
    "tests/integration/test_ib_bar_admission_receipts_postgresql.py",
    "tests/integration/test_run26_handoff_regression_postgresql.py",
    "tests/integration/test_transition_preflight_plan_postgresql.py",
    "tests/integration/test_resume_completed_company_postgresql.py",
    "tests/integration/test_run41_capture_handoff_postgresql.py::"
    "test_native_decision_sources_match_handoff_and_material_tamper_preserves_rows",
    "tests/integration/test_decision_configuration_delivery_postgresql.py::"
    "test_native_setup_lifecycle_winner_artifacts_retain_own_c1",
)
REQUIRED_NATIVE = {
    "pipeline61_population": "159",
    "pipeline61_native_handoff_complete": "true",
    "pipeline61_native_setup_canonical": "159",
    "pipeline61_native_winner_completed": "true",
    "pipeline61_source_seals_unchanged": "true",
    "pipeline61_real_watchdog_preserved": "true",
    "pipeline61_native_sector_fallback": "true",
    "pipeline61_native_progress_before_plan": "true",
    "pipeline61_native_replay_idempotent": "true",
}
_EXTRA_SOURCES = (
    "scripts/qa/qualify_pipeline61_handoff.py",
    "scripts/qa/verify_pipeline61_handoff_qualification.py",
    *(target.split("::", 1)[0] for target in TARGETS),
    "pyproject.toml", "uv.lock", "swinglens.ps1",
)


def source_hashes() -> dict[str, str]:
    """Bind financial/configuration sources and test dependencies, not only selectors."""
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    names = set(SCHEDULER_SOURCES) | set(_EXTRA_SOURCES)
    names.update(name for name in tracked if name.startswith(("app/", "config/", "tests/")))
    return {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in sorted(names)}


def tracked_source_clean() -> bool:
    return not subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True,
    ).strip()


def native_proof(cases) -> dict[str, str]:
    for case in cases:
        if case.get("name") == "test_native_fresh_handoff_and_complete_financial_tail[159]":
            return {item.get("name"): item.get("value", "")
                    for item in case.findall("properties/property")}
    return {}


def selected_target_coverage(cases) -> dict[str, bool]:
    """An empty selected file cannot silently satisfy its release obligation."""
    coverage = {}
    for target in TARGETS:
        path, _, node = target.partition("::")
        module = path.removesuffix(".py").replace("/", ".")
        coverage[target] = any(
            (case.get("file", "").replace("\\", "/") == path
             or case.get("classname", "").split("[", 1)[0] == module)
            and (not node or case.get("name", "").split("[", 1)[0] == node)
            for case in cases
        )
    return coverage


def native_proof_passed(proof: dict) -> bool:
    try:
        return (
            all(proof.get(key) == value for key, value in REQUIRED_NATIVE.items())
            and int(proof["pipeline61_winner_inserted"])
                + int(proof["pipeline61_winner_excluded"]) == 159
            and 0 < float(proof["pipeline61_handoff_seconds"]) < 1800
            and int(proof["pipeline61_checkpoints"]) > 1
        )
    except (KeyError, TypeError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    assert not any(output.iterdir()), "Preserve earlier qualification receipts"
    before = source_hashes()
    before_clean = tracked_source_clean()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    xml_path = output / "tests.xml"
    command = [sys.executable, "-m", "pytest", *TARGETS, "-q", "--tb=short",
               "-o", "junit_family=xunit1", f"--junitxml={xml_path}"]
    with (output / "tests.txt").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    cases = ET.parse(xml_path).findall(".//testcase") if xml_path.exists() else []
    failures = sum(any(case.find(tag) is not None for tag in ("error", "failure", "skipped"))
                   for case in cases)
    unchanged = before == source_hashes()
    clean = before_clean and tracked_source_clean()
    proof = native_proof(cases)
    proof_passed = native_proof_passed(proof)
    coverage = selected_target_coverage(cases)
    passed = result.returncode == 0 and bool(cases) and not failures and unchanged and clean
    passed = passed and proof_passed and all(coverage.values())
    report = {
        "status": "PASS" if passed else "FAIL",
        "contract": "pipeline61-handoff-v1",
        "scope": "Native 159-company fresh handoff, setup/lifecycle/Winner consumers, "
                 "receipt/PIT parity, integrity/cancellation and retained scheduler contract; "
                 "deterministic providers and real production watchdog "
                 "with explicit observed times",
        "completed_at": datetime.now(UTC).isoformat(),
        "git_commit": revision,
        "source_sha256": before,
        "source_unchanged_during_checks": unchanged,
        "tracked_source_clean": clean,
        "hash_basis": "SHA256 with CRLF normalized to LF for cross-checkout verification",
        "selected_targets": list(TARGETS),
        "selected_target_coverage": coverage,
        "tests": len(cases),
        "failed_errored_or_skipped": failures,
        "pytest_exit_code": result.returncode,
        "native_159_proof": proof,
        "native_159_proof_passed": proof_passed,
        "tests_xml_sha256": hashlib.sha256(xml_path.read_bytes()).hexdigest()
                            if xml_path.exists() else None,
    }
    (output / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in (
        "status", "tests", "failed_errored_or_skipped", "native_159_proof_passed",
        "source_unchanged_during_checks", "tracked_source_clean",
    )}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
