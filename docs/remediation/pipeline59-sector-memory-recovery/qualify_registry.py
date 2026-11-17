"""Add only the reviewed Sector recovery sites to the current architecture census."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.check_architecture_registry import (  # noqa: E402
    discover_background_statuses,
    discover_recovery,
    discover_writer_candidates,
)


def main():
    path = ROOT / "config/architecture_registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    reviews = {
        ("app/services/background_job_service.py", "_finalize_terminal_pipeline_publication",
         "state_assignment"): (["WRITE-007", "WRITE-008"],
            "Private operational finalizer under typed recovery authority and the locked job. "
            "Exact active pipeline, semantic scope, dependency and ancestry are rechecked. "
            "Closes the job after terminal pipeline publication; no financial mutation."),
        ("app/services/pipeline_executor.py", "_execute_resumed_pipeline", "state_assignment"):
            (["WRITE-003"], "Marks Sector complete inside the native pipeline-step boundary "
             "only after exact sealed publication, serving-child and source-manifest validation."),
        ("app/services/core_mutation_authority.py", "__setitem__", "add"):
            ([], "IN_MEMORY_ONLY: adds an exact evidence address to a bounded local read cache; "
             "does not call Session.add or write financial values."),
        ("app/services/core_mutation_authority.py", "_decoded_evidence_size", "add"):
            ([], "IN_MEMORY_ONLY: adds object addresses to a temporary seen set for decoded-size "
             "accounting; no database mutation."),
        ("app/services/sector_serialization.py", "canonical_sector_payload", "add"):
            ([], "IN_MEMORY_ONLY: fresh per-call canonical copy and cycle detection; no DML."),
        ("app/services/sector_serialization.py", "sector_dataclass_payload", "add"):
            ([], "IN_MEMORY_ONLY: fresh per-call dataclass conversion and cycle detection; "
             "no DML."),
        ("app/services/sector_serialization.py", "copy", "add"):
            ([], "IN_MEMORY_ONLY: nested copy helpers add object addresses to local cycle sets; "
             "they do not persist or authorize evidence."),
    }
    def key(row):
        return row["path"], row["function"], row["kind"]
    existing = {key(row) for row in registry["writer_candidates"]}
    discovered = {key(row) for row in discover_writer_candidates(ROOT)}
    additions = discovered - existing
    if additions - reviews.keys() or existing - discovered:
        raise RuntimeError("Unreviewed writer census drift: " + repr(additions - reviews.keys()))
    for identity in sorted(additions):
        writer_ids, reason = reviews[identity]
        registry["writer_candidates"].append(dict(zip(("path", "function", "kind"), identity,
                                                    strict=True),
                                                writer_ids=writer_ids, reason=reason))
    _, candidates = discover_recovery(ROOT)
    expected_recovery = {
        "app/routers/run_routes.py:_pipeline_recovery_fields",
        "app/services/background_job_service.py:_job_recovery_authority_predicate",
        "app/services/background_job_service.py:_observed_pipeline_stage_before_fence",
        "app/services/pipeline_executor.py:_mark_sector_recovery_validation",
    }
    new_recovery = set(candidates) - set(registry["recovery_candidate_functions"])
    removed_recovery = set(registry["recovery_candidate_functions"]) - set(candidates)
    if new_recovery - expected_recovery or removed_recovery:
        raise RuntimeError("Unreviewed recovery census drift: " + repr(new_recovery))
    registry["recovery_candidate_functions"] = candidates
    _, writers = discover_background_statuses(ROOT)
    current_sites = {(row["path"], row["function"]) for row in registry["state_writer_sites"]}
    new_sites = {(row["path"], row["function"]) for row in writers} - current_sites
    expected_sites = {(identity[0], identity[1]) for identity in reviews
                      if identity[2] == "state_assignment"}
    if new_sites - expected_sites:
        raise RuntimeError("Unreviewed status census drift: " + repr(new_sites))
    registry["state_writer_sites"].extend(
        row for row in writers if (row["path"], row["function"]) in new_sites
    )
    for family in registry["writer_families"]:
        if family["id"] in {"WRITE-007", "WRITE-008"}:
            addition = "_finalize_terminal_pipeline_publication"
        elif family["id"] == "WRITE-003":
            addition = "_execute_resumed_pipeline"
        else:
            continue
        if addition not in family["functions"]:
            family["functions"] += ", " + addition
    path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"writer_sites_reviewed": len(additions),
                      "new_recovery_names": sorted(new_recovery),
                      "new_status_writers": sorted(new_sites)}))


if __name__ == "__main__":
    main()
