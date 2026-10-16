"""Generate and validate the current Phase 2.9 semantic-review source pins."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.qa.t14a_mutation_inventory import ROOT
from scripts.qa.t14a_phase2_9_semantic_review import (
    ENTRY_ASSIGNMENTS,
    EXISTING_FAMILY_SINKS,
    NEW_WRITER_FAMILIES,
)

PIN_FILE = (
    ROOT / "docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json"
)
HISTORICAL_PIN_FILE = (
    ROOT / "docs/remediation/calculation-lineage/T14D_semantic_review_source_pins.json"
)
RAW_DISCOVERY = ROOT / "docs/remediation/calculation-lineage/T14A_raw_discovery.json"


def reviewed_source_paths(root: Path = ROOT) -> tuple[str, ...]:
    historical = json.loads(
        (root / HISTORICAL_PIN_FILE.relative_to(ROOT)).read_text(encoding="utf-8")
    )
    paths = set(historical["sources"])
    addresses = set(ENTRY_ASSIGNMENTS) | set(EXISTING_FAMILY_SINKS)
    for family in NEW_WRITER_FAMILIES.values():
        addresses.add(family["owner"])
        addresses.update(family["member_sites"])
    paths.update(address.split(":", 1)[0] for address in addresses)
    paths.update(
        {
            "scripts/qa/t14a_phase2_9_semantic_review.py",
            "scripts/qa/t14a_reviewed_inventory.py",
            "scripts/qa/t14a_semantic_completeness.py",
            "scripts/qa/t14a_semantic_families.py",
            "scripts/qa/t14a_semantic_review.py",
        }
    )
    missing = sorted(path for path in paths if not (root / path).is_file())
    if missing:
        raise ValueError(f"Phase 2.9 review source path is missing: {missing}")
    return tuple(sorted(paths))


def build(root: Path = ROOT) -> dict[str, Any]:
    sources = {
        path: hashlib.sha256((root / path).read_bytes()).hexdigest()
        for path in reviewed_source_paths(root)
    }
    return {
        "schema_version": "phase2.9-semantic-review-source-pins-v1",
        "basis": (
            "Current release review overlay; historical T14D source pins and authority "
            "certificates remain unchanged."
        ),
        "historical_pin_artifact": HISTORICAL_PIN_FILE.relative_to(ROOT).as_posix(),
        "raw_discovery_sha256": hashlib.sha256(RAW_DISCOVERY.read_bytes()).hexdigest(),
        "sources": sources,
    }


def validate(root: Path = ROOT) -> list[str]:
    path = root / PIN_FILE.relative_to(ROOT)
    if not path.exists():
        return ["MISSING_PHASE2_9_REVIEW_SOURCE_PINS"]
    retained = json.loads(path.read_text(encoding="utf-8"))
    stale: list[str] = []
    raw = root / RAW_DISCOVERY.relative_to(ROOT)
    if (
        not raw.exists()
        or hashlib.sha256(raw.read_bytes()).hexdigest()
        != retained.get("raw_discovery_sha256")
    ):
        stale.append("RAW_DISCOVERY_CHANGED")
    for relative, expected in retained["sources"].items():
        source = root / relative
        if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            stale.append(relative)
    return stale


def write(root: Path = ROOT) -> None:
    path = root / PIN_FILE.relative_to(ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(root), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        write()
    else:
        print(json.dumps({"stale": validate()}, indent=2))
