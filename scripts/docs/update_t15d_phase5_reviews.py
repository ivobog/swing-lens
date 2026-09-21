"""Refresh the finite Phase-5 review for T15D-reviewed source changes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / "docs" / "remediation" / "calculation-lineage"
REVIEW_PATH = ARTIFACT_DIR / "T14D_semantic_family_review.json"
PINS_PATH = ARTIFACT_DIR / "T14D_semantic_review_source_pins.json"

REVIEWED_SOURCES = (
    "app/services/background_worker.py",
    "app/services/ceri/backfill_service.py",
    "app/services/ceri/batched_job_handlers.py",
    "app/services/ceri/batched_workflow.py",
    "app/services/ceri/capture_service.py",
    "app/services/ceri/change_rebuild_service.py",
    "app/services/ceri/confidence_service.py",
    "app/services/ceri/config.py",
    "app/services/ceri/deployment_identity.py",
    "app/services/ceri/feature_rebuild_service.py",
    "app/services/ceri/job_handlers.py",
    "app/services/ceri/pit_eligibility.py",
    "app/services/ceri/point_in_time_query.py",
    "app/services/ceri/price_response_service.py",
    "app/services/ceri/processing_run_service.py",
    "app/services/ceri/revision_feature_service.py",
    "app/services/ceri/source_record_service.py",
    "app/services/ceri/surprise_feature_service.py",
    "app/services/ceri/upcoming_earnings_authority.py",
    "app/services/fundamental_score_service.py",
    "app/services/market_regime_command_center.py",
    "app/services/market_regime_policy.py",
    "app/services/ohlcv_coverage_service.py",
    "app/services/pipeline_executor.py",
    "app/services/ranking_profile_service.py",
    "app/services/setup_lifecycle/snapshot_builder.py",
    "app/services/technical_indicators.py",
    "scripts/qa/t13e_deployed_probe.py",
)


def _sha(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


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

    review["t15d_reviewed_source_delta"] = {
        "task": "T15D",
        "reviewed_sources": list(REVIEWED_SOURCES),
        "scope": (
            "Provider/source provenance, historical temporal eligibility, bounded frozen-scope "
            "selection, calculation correctness, and exact semantic-authority propagation; no "
            "weakening of Phase-5 financial mutation authority."
        ),
    }
    pins["task"] = "T14D retained source pins plus T15B/T15C/T15D reviewed adoption deltas"
    pins["t15d_reviewed_source_delta"] = list(REVIEWED_SOURCES)

    REVIEW_PATH.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    PINS_PATH.write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
