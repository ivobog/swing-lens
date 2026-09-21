"""Refresh the finite Phase-5 review for T15C-reviewed Winner source changes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / "docs" / "remediation" / "calculation-lineage"
REVIEW_PATH = ARTIFACT_DIR / "T14D_semantic_family_review.json"
PINS_PATH = ARTIFACT_DIR / "T14D_semantic_review_source_pins.json"

REVIEWED_SOURCES = (
    "app/models/tables.py",
    "app/routers/winner_probability_routes.py",
    "app/services/winner_probability/capture_service.py",
    "app/services/winner_probability/cohort_generation_service.py",
    "app/services/winner_probability/cohort_refresh_planner.py",
    "app/services/winner_probability/estimate_publication_service.py",
    "app/services/winner_probability/job_handlers.py",
    "app/services/winner_probability/outcome_authority.py",
    "app/services/winner_probability/outcome_orchestration_service.py",
    "app/services/winner_probability/outcome_service.py",
    "app/services/winner_probability/scope_refresh.py",
)


def _sha(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def _scope_admission_entry(source_sha256: str) -> dict:
    return {
        "adoption_status": "T14D_ADOPTION",
        "authority_proof": (
            "T15C resolves exact Winner target members before durable execution, persists "
            "canonical scope/refresh/plan authority, and requires retries and continuations "
            "to consume retained membership. PostgreSQL adversarial tests certify the boundary."
        ),
        "authority_review_is_foundation_gate": False,
        "authority_supplied": (
            "db: Session, exact Winner members, cycle key, cutoff, configuration and policy"
        ),
        "behavior": "SYNCHRONOUS_OR_ENQUEUE_NATIVE_OPERATION",
        "decorators": [],
        "discovery_status": "DISCOVERED",
        "disposition": "T14D",
        "domains": [
            "CONFIGURATION",
            "WINNER_COHORT",
            "WINNER_GENERATION",
            "WINNER_OUTCOME",
            "WINNER_PREDICTION",
        ],
        "entry_point_family_id": "EF_T15C_WINNER_SCOPE_ADMISSION",
        "entrypoints": [
            "app/services/winner_probability/scope_refresh.py:admit_cohort_refresh",
            "app/services/winner_probability/scope_refresh.py:admit_historical_backfill",
            "app/services/winner_probability/scope_refresh.py:admit_maturation",
            "app/services/winner_probability/scope_refresh.py:admit_prediction_capture",
        ],
        "kind": "SERVICE_PUBLIC",
        "modes": ["CANONICAL_CALCULATION", "MAINTENANCE"],
        "proof_boundary": (
            "Reviewed T15C Winner scope-authority admission only; calculation writer authority "
            "and truth-revision contracts remain independently certified."
        ),
        "reachability": "Public services invoked by reviewed Winner admission paths.",
        "selectors": [],
        "semantic_adoption_status": "T15C_SCOPE_AUTHORITY_ADOPTED",
        "semantic_review_complete": True,
        "source_sha256": source_sha256,
        "status": "T15C_SCOPE_AUTHORITY_ADOPTED",
        "writer_adoption_dependencies": ["T14B", "T14C", "T14D", "T15A"],
        "writer_families": ["WF_SCOPE_IDENTITY_FOUNDATION"],
    }


def main() -> None:
    review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    pins = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    current = {path: _sha(path) for path in REVIEWED_SOURCES}
    review["source_pins"].update(current)
    pins["sources"].update(current)
    for entry in review["entry_families"].values():
        source = entry["entrypoints"][0].split(":", 1)[0]
        if source in current:
            entry["source_sha256"] = current[source]
    review["entry_families"]["EF_T15C_WINNER_SCOPE_ADMISSION"] = _scope_admission_entry(
        current["app/services/winner_probability/scope_refresh.py"]
    )
    review["entry_families"].pop("EF_T15C_WINNER_FROZEN_EXECUTION", None)
    review["t15c_reviewed_source_delta"] = {
        "task": "T15C",
        "reviewed_sources": list(REVIEWED_SOURCES),
        "scope": (
            "Winner population scope/refresh/checkpoint and exact-or-unavailable price revision "
            "binding; no weakening of Phase-5 financial mutation authority."
        ),
    }
    pins["task"] = "T14D retained source pins plus T15B/T15C reviewed adoption deltas"
    pins["t15c_reviewed_source_delta"] = list(REVIEWED_SOURCES)
    REVIEW_PATH.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    PINS_PATH.write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
