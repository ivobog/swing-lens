"""Orthogonal terminal pipeline execution, coverage, and integrity summary."""

from __future__ import annotations

from typing import Any

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical

PIPELINE_OUTCOME_CONTRACT_VERSION = "pipeline-outcome-v1"
NO_INTEGRITY_HAZARD = "NO_INTEGRITY_HAZARD"
INTEGRITY_HAZARD_DETECTED = "INTEGRITY_HAZARD_DETECTED"
INTEGRITY_NOT_CERTIFIED = "NOT_CERTIFIED"

_EXECUTION_BY_STATUS = {
    "COMPLETED": "SUCCEEDED",
    "PARTIAL": "SUCCEEDED_WITH_GAPS",
    "FAILED": "FAILED",
    "CANCELLED": "CANCELLED",
    "BLOCKED": "BLOCKED",
}
_HARD_CONTAINMENT_STATUSES = frozenset({"FAILED", "CANCELLED", "BLOCKED"})
_EXPLICIT_INTEGRITY_FLAGS = (
    "contamination_detected",
    "evidence_integrity_failure",
    "identity_mismatch_detected",
    "quarantine_required",
    "temporal_integrity_failure",
)
_GAP_COUNTS = (
    "incomplete_rows",
    "ib_failure_count",
    "technical_error_count",
    "ceri_failed",
    "setup_lifecycle_failed",
    "winner_prediction_failed",
)


def derive_pipeline_outcome(status: str, result: dict[str, Any]) -> dict[str, Any]:
    status = str(status).upper()
    execution = _EXECUTION_BY_STATUS.get(status, "UNKNOWN")
    hazards = _integrity_hazards(result)
    if hazards:
        integrity = INTEGRITY_HAZARD_DETECTED
    elif status in {"COMPLETED", "PARTIAL"}:
        integrity = NO_INTEGRITY_HAZARD
    else:
        integrity = INTEGRITY_NOT_CERTIFIED
    gaps = {
        key: int(result.get(key) or 0)
        for key in _GAP_COUNTS
        if int(result.get(key) or 0) > 0
    }
    body = {
        "contract_version": PIPELINE_OUTCOME_CONTRACT_VERSION,
        "pipeline_status": status,
        "execution_outcome": execution,
        "coverage": {
            "expected_rows": int(result.get("uploaded_rows") or 0),
            "combined_rows": int(result.get("combined_results") or 0),
            "warning_rows": int(result.get("warning_rows") or 0),
            "gaps": gaps,
        },
        "integrity": {
            "status": integrity,
            "hazards": hazards,
        },
    }
    return {**body, "summary_fingerprint": Canonical.fingerprint(body)}


def pipeline_requires_ceri_containment(
    status: str, result_json: dict[str, Any] | None
) -> bool:
    status = str(status).upper()
    if status in _HARD_CONTAINMENT_STATUSES:
        return True
    if status != "PARTIAL":
        return False
    summary = (result_json or {}).get("pipeline_outcome_summary")
    if not isinstance(summary, dict):
        return True
    return not (
        summary.get("contract_version") == PIPELINE_OUTCOME_CONTRACT_VERSION
        and (summary.get("integrity") or {}).get("status") == NO_INTEGRITY_HAZARD
    )


def _integrity_hazards(result: dict[str, Any]) -> list[str]:
    hazards = {
        name for name in _EXPLICIT_INTEGRITY_FLAGS if result.get(name) is True
    }
    raw = result.get("integrity_failures") or ()
    if isinstance(raw, (list, tuple, set, frozenset)):
        hazards.update(str(value).strip() for value in raw if str(value).strip())
    elif raw:
        hazards.add(str(raw).strip())
    return sorted(hazards)
