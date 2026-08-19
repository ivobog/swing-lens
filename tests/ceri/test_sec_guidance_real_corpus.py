from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from scripts.certify_sec_guidance_real_corpus import certify

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests/ceri/fixtures/sec_guidance_real_corpus_v1.jsonl"
MANIFEST = ROOT / "tests/ceri/fixtures/sec_guidance_real_corpus_manifest_v1.json"


@pytest.mark.integration
def test_real_sec_guidance_corpus_meets_certification_gates() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    report = certify(CORPUS)

    assert report["corpus"] == {
        "issuers": 28,
        "filings": 117,
        "passages": 232,
        "positive": 33,
        "negative": 194,
        "review_required": 5,
    }
    assert report["certified"] is True
    assert all(gate["passed"] for gate in report["gates"].values())
    assert report["metrics"]["classification"] == {
        "TP": 33,
        "FP": 0,
        "FN": 0,
        "TN": 194,
        "precision": 1.0,
        "recall": 1.0,
        "specificity": 1.0,
        "f1": 1.0,
    }
    assert report["metrics"]["numeric_values"]["rate"] == 1.0
    assert report["metrics"]["structured_exact_match"]["rate"] == 1.0
    assert report["failure_severity"] == {
        "CRITICAL": 0,
        "HIGH": 0,
        "MEDIUM": 0,
        "LOW": 0,
    }
    assert report["corpus_sha256"] == manifest["corpus_sha256"]
    assert report["versions"]["processor_signature"] == manifest[
        "certification_reference"
    ]["processor_signature"]
    assert report["output_fingerprint_sha256"] == manifest[
        "certification_reference"
    ]["output_fingerprint_sha256"]


def test_real_sec_guidance_review_required_cases_are_excluded_from_scoring() -> None:
    cases = [
        json.loads(line)
        for line in CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    unresolved = [case for case in cases if case["label"] == "REVIEW_REQUIRED"]

    assert Counter(case["form"] for case in cases) == {
        "8-K": 112,
        "10-Q": 60,
        "10-K": 60,
    }
    assert max(Counter(case["form"] for case in cases).values()) * 2 < len(cases)
    assert len(unresolved) == 5
    assert all(case["review_state"] == "UNRESOLVED" for case in unresolved)
    assert all(case["annotation_answers"] is None for case in unresolved)
