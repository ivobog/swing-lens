"""Strict comparison of the identical fully READY T12B/T12C business probe."""

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
        raise AssertionError("Unexpected fully READY business-output change")
    counts = Counter(item["producer"] for item in current)
    if not {"Ranking", "CERI", "Setup", "Lifecycle", "Actionability"} <= counts.keys():
        raise AssertionError("READY comparison is missing a required consumer")
    summary = {
        "captures": len(current),
        "producer_counts": dict(counts),
        "unexpected_changes": 0,
        "fully_ready_fingerprint": Canonical.fingerprint(current),
    }
    args.output.write_text(Canonical.dumps(summary), encoding="utf-8")
    print(Canonical.dumps(summary))


if __name__ == "__main__":
    main()
