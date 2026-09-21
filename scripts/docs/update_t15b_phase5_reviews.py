"""Refresh the finite Phase-5 review for T15B-reviewed source changes."""

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
    "app/routers/ceri_routes.py",
    "app/services/background_worker.py",
    "app/services/ceri/backfill_service.py",
    "app/services/ceri/job_handlers.py",
    "app/services/ceri/orchestration.py",
    "app/services/ceri/sec/readiness_repair.py",
    "app/services/ceri/source_record_service.py",
    "app/services/ib_fetch_job_service.py",
    "app/services/market_data_prewarm_service.py",
    "app/services/pipeline_executor.py",
    "app/services/pipeline_service.py",
    "app/services/scope_refresh_adoption.py",
)


def _sha(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def _entry(
    *,
    family_id: str,
    entrypoints: list[str],
    source: str,
    authority_supplied: str,
    domains: list[str],
) -> dict:
    return {
        "adoption_status": "T14D_ADOPTION",
        "authority_proof": (
            "T15B persists canonical T15A plan, frozen scope membership, and refresh cycle in "
            "the same Session before durable work; retries reuse retained IDs and legacy work "
            "fails closed. PostgreSQL and focused negative tests certify the boundary."
        ),
        "authority_review_is_foundation_gate": False,
        "authority_supplied": authority_supplied,
        "behavior": "SYNCHRONOUS_OR_ENQUEUE_NATIVE_OPERATION",
        "decorators": [],
        "discovery_status": "DISCOVERED",
        "disposition": "T14D",
        "domains": domains,
        "entry_point_family_id": family_id,
        "entrypoints": entrypoints,
        "kind": "SERVICE_PUBLIC",
        "modes": ["CANONICAL_CALCULATION", "MAINTENANCE"],
        "proof_boundary": (
            "Reviewed T15B scope-authority admission only; calculation writer authority and "
            "domain-specific truth contracts remain independently certified."
        ),
        "reachability": "Public service invoked by reviewed Pipeline/CERI/IB admission paths.",
        "selectors": [],
        "semantic_adoption_status": "T15B_SCOPE_AUTHORITY_ADOPTED",
        "semantic_review_complete": True,
        "source_sha256": _sha(source),
        "status": "T15B_SCOPE_AUTHORITY_ADOPTED",
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

    review["entry_families"]["EF_T15B_IB_FETCH_AUTHORITY"] = _entry(
        family_id="EF_T15B_IB_FETCH_AUTHORITY",
        entrypoints=[
            "app/services/ib_fetch_job_service.py:create_queued_fetch_run",
            "app/services/ib_fetch_job_service.py:resume_fetch_job",
        ],
        source="app/services/ib_fetch_job_service.py",
        authority_supplied=(
            "db: Session, retained FetchPlan/FetchJobOptions, optional canonical semantic authority"
        ),
        domains=["CONFIGURATION", "IBMI_SOURCE", "PRICE"],
    )
    review["entry_families"]["EF_T15B_SCOPE_ADMISSION"] = _entry(
        family_id="EF_T15B_SCOPE_ADMISSION",
        entrypoints=["app/services/scope_refresh_adoption.py:admit_frozen_operation"],
        source="app/services/scope_refresh_adoption.py",
        authority_supplied=(
            "db: Session, exact members, cycle key, cutoff, policy, requirements, and lineage"
        ),
        domains=["CONFIGURATION"],
    )
    review["t15b_reviewed_source_delta"] = {
        "task": "T15B",
        "reviewed_sources": list(REVIEWED_SOURCES),
        "scope": (
            "Canonical scope/refresh/acquisition metadata adoption; no weakening of Phase-5 "
            "calculation/source mutation authority."
        ),
    }
    pins["task"] = "T14D retained source pins plus T15B reviewed adoption delta"
    pins["t15b_reviewed_source_delta"] = list(REVIEWED_SOURCES)

    REVIEW_PATH.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    PINS_PATH.write_text(
        json.dumps(pins, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
