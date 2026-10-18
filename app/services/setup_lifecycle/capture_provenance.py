"""Capture-attempt membership, separate from immutable snapshot creator provenance.

The terminal capture audit binds exact artifact addresses. It grants no financial
authority: native snapshot/source validation remains mandatory at publication.
Legacy captures without this record retain the strict single-creator contract.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.tables import CoreCalculationEvidence, SetupLifecycleEvaluationRun
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.setup_lifecycle.errors import SetupCaptureHandoffError

CAPTURE_MEMBERSHIP_KEY = "capture_snapshot_membership"
_CONTRACT = "setup-capture-membership-v1"


def capture_snapshot_pins(db, snapshots):
    evidence_ids = {row.evidence_id for row in snapshots}
    evidence = {
        row.id: row
        for row in db.scalars(
            select(CoreCalculationEvidence)
            .where(CoreCalculationEvidence.id.in_(evidence_ids))
            .with_for_update(read=True)
        )
    }
    pins = {}
    for row in snapshots:
        source = evidence.get(row.evidence_id)
        if source is None or source.artifact_kind != "SETUP":
            raise SetupCaptureHandoffError(
                "EVIDENCE_MISSING",
                "Captured snapshot has no SETUP evidence.",
                run_id=row.run_id,
                snapshot_id=row.id,
                ticker=row.ticker,
            )
        pins[str(row.id)] = {
            "creator_evaluation_run_id": row.evaluation_run_id,
            "evidence_id": row.evidence_id,
            "evidence_payload_fingerprint": source.payload_fingerprint,
            "source_data_hash": row.source_data_hash,
            "calculation_context_id": row.calculation_context_id,
            "calculation_cutoff_at": row.calculation_cutoff_at,
            "input_as_of_session": row.input_as_of_session,
            "calendar_version": row.calendar_version,
        }
    return Canonical.canonicalize(pins)


def capture_membership(db, attempt, snapshots):
    body = {
        "contract": _CONTRACT,
        "capture_attempt_id": attempt.id,
        "run_id": attempt.source_run_id,
        "config_hash": attempt.config_hash,
        "engine_version": attempt.engine_version,
        "snapshots": capture_snapshot_pins(db, snapshots),
    }
    return {**body, "fingerprint": Canonical.fingerprint(body)}


def validate_capture_membership(
    db, *, capture_attempt_id, run_id, config, snapshots, pipeline_run_id=None
):
    attempt = db.scalar(
        select(SetupLifecycleEvaluationRun)
        .where(SetupLifecycleEvaluationRun.id == capture_attempt_id)
        .with_for_update(read=True)
    )
    context = {
        "run_id": run_id,
        "pipeline_run_id": pipeline_run_id,
        "capture_attempt_id": capture_attempt_id,
    }
    if (
        attempt is None
        or attempt.source_run_id != run_id
        or attempt.config_hash != config.config_hash
        or attempt.engine_version != config.engine.version
        or attempt.status not in {"COMPLETED", "PARTIAL"}
    ):
        raise SetupCaptureHandoffError(
            "CAPTURE_SCOPE_MISMATCH",
            "Capture attempt is missing or outside evaluation scope.",
            **context,
        )
    retained = (attempt.audit_json or {}).get(CAPTURE_MEMBERSHIP_KEY)
    if retained is None:
        return None
    if not isinstance(retained, dict):
        raise SetupCaptureHandoffError(
            "MALFORMED_MEMBERSHIP", "Invalid capture membership.", **context
        )
    expected = capture_membership(db, attempt, snapshots)
    if Canonical.dumps(retained) != Canonical.dumps(expected):
        members = retained.get("snapshots")
        offending = next(
            (
                row
                for row in snapshots
                if not isinstance(members, dict)
                or members.get(str(row.id)) != expected["snapshots"][str(row.id)]
            ),
            None,
        )
        raise SetupCaptureHandoffError(
            "MEMBERSHIP_MISMATCH",
            "Capture membership does not match exact snapshot provenance.",
            **context,
            snapshot_id=offending.id if offending is not None else None,
            ticker=offending.ticker if offending is not None else None,
            actual_creator_id=offending.evaluation_run_id if offending is not None else None,
            expected_provenance=(
                members.get(str(offending.id))
                if isinstance(members, dict) and offending is not None
                else None
            ),
        )
    return expected["snapshots"]
