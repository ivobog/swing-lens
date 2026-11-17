"""Reject activation unless this exact clean source passed the native handoff contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.qa.qualify_pipeline61_handoff import (
    ROOT,
    TARGETS,
    native_proof,
    native_proof_passed,
    selected_target_coverage,
    source_hashes,
    tracked_source_clean,
)


def verify(report: dict, *, revision: str, hashes: dict) -> None:
    if not (
        report.get("status") == "PASS"
        and report.get("contract") == "pipeline61-handoff-v1"
        and report.get("pytest_exit_code") == 0
        and report.get("tests", 0) > 0
        and report.get("failed_errored_or_skipped") == 0
        and report.get("source_unchanged_during_checks") is True
        and report.get("tracked_source_clean") is True
        and report.get("selected_targets") == list(TARGETS)
        and report.get("selected_target_coverage") == dict.fromkeys(TARGETS, True)
        and report.get("native_159_proof_passed") is True
        and native_proof_passed(report.get("native_159_proof") or {})
    ):
        raise RuntimeError("HANDOFF_QUALIFICATION_REQUIRED: every selected native check must pass")
    if report.get("git_commit") != revision or report.get("source_sha256") != hashes:
        raise RuntimeError("HANDOFF_QUALIFICATION_STALE: qualify this exact release source")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.receipt.read_text())
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    verify(report, revision=revision, hashes=source_hashes())
    if not tracked_source_clean():
        raise RuntimeError("HANDOFF_QUALIFICATION_STALE: deployed tracked source changed")
    xml_path = args.receipt.with_name("tests.xml")
    if hashlib.sha256(xml_path.read_bytes()).hexdigest() != report.get("tests_xml_sha256"):
        raise RuntimeError("HANDOFF_QUALIFICATION_EVIDENCE_CHANGED: retained JUnit receipt differs")
    cases = ET.parse(xml_path).findall(".//testcase")
    if (len(cases) != report.get("tests")
        or any(case.find(tag) is not None for case in cases
               for tag in ("error", "failure", "skipped"))
        or native_proof(cases) != report.get("native_159_proof")
        or selected_target_coverage(cases) != report.get("selected_target_coverage")):
        raise RuntimeError("HANDOFF_QUALIFICATION_EVIDENCE_CHANGED: retained test proof differs")
    print("Native handoff qualification verified for " + revision[:7])


if __name__ == "__main__":
    main()
