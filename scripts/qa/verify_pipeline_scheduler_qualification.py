"""Reject startup when scheduler qualification belongs to different source."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from scripts.qa.qualify_pipeline_scheduler import ROOT, source_hashes


def verify(report: dict, *, revision: str, hashes: dict) -> None:
    if not (
        report.get("status") == "PASS"
        and report.get("pytest_exit_code") == 0
        and report.get("tests", 0) > 0
        and report.get("failed_errored_or_skipped") == 0
        and report.get("source_unchanged_during_checks") is True
    ):
        raise RuntimeError("SCHEDULER_QUALIFICATION_REQUIRED: selected checks must all pass")
    if report.get("git_commit") != revision or report.get("source_sha256") != hashes:
        raise RuntimeError("SCHEDULER_QUALIFICATION_STALE: qualify this exact release source")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    verify(json.loads(args.receipt.read_text()), revision=revision, hashes=source_hashes())
    print("Scheduler qualification verified for " + revision[:7])


if __name__ == "__main__":
    main()
