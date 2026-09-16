"""Certify exact READY parity and account for every intentional core output change."""

import json
from collections import Counter
from pathlib import Path

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical


def compare(baseline_path: Path, current_path: Path) -> dict:
    baseline = json.loads(baseline_path.read_text())
    current = json.loads(current_path.read_text())
    assert len(baseline) == len(current)
    expected_changes = []
    ready = []
    for before, after in zip(baseline, current, strict=True):
        assert (before["test"], before["producer"]) == (after["test"], after["producer"])
        if "test_t12b_ready_scenarios.py" in before["test"]:
            assert before == after, "fully READY business DTO drift"
            ready.append(after)
        if before == after:
            continue
        if before["producer"] == "Combined":
            assert before["test"].endswith(
                "test_combined_decision_carries_v4_warning_flags_to_cockpit_payload"
            )
            assert after["output"]["has_technical"] is False
            assert after["output"]["dual_score"] is None
            assert after["output"]["is_complete"] is False
        elif before["producer"] == "Ranking":
            old_rows = {row["ticker"]: row for row in before["output"]}
            new_rows = {row["ticker"]: row for row in after["output"]}
            assert old_rows.keys() == new_rows.keys()
            for ticker, old in old_rows.items():
                new = new_rows[ticker]
                if new["has_technical"]:
                    # Only ordinal positions can change when invalid peers are removed.
                    assert {k: v for k, v in old.items() if k != "profile_rank"} == {
                        k: v for k, v in new.items() if k != "profile_rank"
                    }
                else:
                    assert new["profile_rank"] == 0
                    assert new["component_scores"] == {}
                    assert new["technical_profile_score"] is None
                    assert new["position_size_hint"] == "No new entry"
                    assert new["is_complete"] is False
                    assert new["decision_label"] not in {"Candidate", "Strong candidate"}
        else:
            raise AssertionError(f"unexpected behavior change: {before['test']}")
        expected_changes.append({"test": before["test"], "producer": before["producer"]})
    assert ready
    return {
        "captures": len(baseline),
        "unchanged": len(baseline) - len(expected_changes),
        "producer_counts": dict(Counter(row["producer"] for row in baseline)),
        "fully_ready_captures": len(ready),
        "fully_ready_fingerprint": Canonical.fingerprint(ready),
        "expected_changes": expected_changes,
        "unexpected_changes": 0,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare(args.baseline, args.current)
    encoded = Canonical.dumps(result)
    if args.output:
        args.output.write_text(encoded)
    print(encoded)
