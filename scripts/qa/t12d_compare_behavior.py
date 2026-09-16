"""Strict complete READY Winner comparison; no expected-change allowlist."""

import argparse
import json
from collections import Counter
from pathlib import Path

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    current = json.loads(args.current.read_text(encoding="utf-8"))
    if baseline != current:
        raise AssertionError("Unexpected READY Winner business change")
    counts = Counter(item["producer"] for item in current)
    if not {"WinnerFeatures", "Winner", "WinnerProbability"} <= counts.keys():
        raise AssertionError("READY comparison lacks full vector/prediction/probability")
    summary = {
        "captures": len(current),
        "producer_counts": dict(counts),
        "unexpected_changes": 0,
        "ready_fingerprint": Canonical.fingerprint(current),
    }
    args.output.write_text(Canonical.dumps(summary), encoding="utf-8")
    print(Canonical.dumps(summary))


if __name__ == "__main__":
    main()
