from __future__ import annotations

from enum import StrEnum

from sqlalchemy.orm import Session

from app.models.tables import (
    CoreCalculationEvidence,
    SetupLifecycleEpisode,
    SignalAlertDecisionEvidence,
    SignalAlertEvent,
    SignalAlertRuleEvidence,
)
from app.services.calculation_identity import CalculationIdentity
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    get_current_evidence,
    get_evidence_by_id,
    get_evidence_for_identity,
)


class ReadMode(StrEnum):
    """Explicit caller intent at a current/evidence repository boundary."""

    CURRENT = "CURRENT"
    EVIDENCE = "EVIDENCE"
    CURRENT_RULES_RETROSPECTIVE = "CURRENT_RULES_RETROSPECTIVE"
    ORIGINAL_CONTEXT = "ORIGINAL_CONTEXT"


class HistoricalReadError(EvidenceUnavailableError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def read_core_artifact(
    db: Session,
    *,
    kind: CoreEvidenceKind,
    mode: ReadMode,
    evidence_id: int | None = None,
    calculation_identity: CalculationIdentity | str | None = None,
    run_id: int | None = None,
    ticker: str | None = None,
    ranking_profile: str | None = None,
    payload_fingerprint: str | None = None,
) -> CoreCalculationEvidence:
    """Resolve a current pointer or immutable history without cross-mode fallback."""

    if mode is ReadMode.CURRENT:
        if evidence_id is not None or calculation_identity is not None:
            raise HistoricalReadError(
                "CURRENT_MODE_REJECTS_HISTORICAL_ANCHOR",
                "current reads cannot accept an evidence or Calculation Identity anchor",
            )
        return get_current_evidence(
            db,
            kind=kind,
            run_id=run_id,
            ticker=ticker,
            ranking_profile=ranking_profile,
        )

    if mode is ReadMode.ORIGINAL_CONTEXT:
        raise HistoricalReadError(
            "ORIGINAL_CONTEXT_RECONSTRUCTION_UNSUPPORTED",
            "original-context reconstruction is not implemented; request exact evidence",
        )
    if mode is ReadMode.CURRENT_RULES_RETROSPECTIVE:
        raise HistoricalReadError(
            "HISTORICAL_MODE_REQUIRES_IDENTITY",
            "current-rules retrospective execution is not an immutable evidence lookup",
        )
    if mode is not ReadMode.EVIDENCE:
        raise HistoricalReadError("INVALID_READ_MODE", f"unsupported read mode {mode!r}")
    if evidence_id is not None and calculation_identity is not None:
        raise HistoricalReadError(
            "HISTORICAL_MODE_AMBIGUOUS_ANCHOR",
            "provide either evidence_id or Calculation Identity, not both",
        )
    if evidence_id is not None:
        return get_evidence_by_id(db, evidence_id=evidence_id, kind=kind)
    if calculation_identity is None:
        raise HistoricalReadError(
            "HISTORICAL_MODE_REQUIRES_IDENTITY",
            "historical reads require evidence_id or Calculation Identity",
        )
    return get_evidence_for_identity(
        db,
        kind=kind,
        calculation_identity=calculation_identity,
        ticker=ticker,
        ranking_profile=ranking_profile,
        payload_fingerprint=payload_fingerprint,
    )


def evidence_view(evidence: CoreCalculationEvidence, *, mode: ReadMode) -> dict:
    return {
        "read_mode": mode.value,
        "evidence_status": "CERTIFIED_IMMUTABLE",
        "artifact_kind": evidence.artifact_kind,
        "evidence_id": evidence.id,
        "calculation_identity_fingerprint": evidence.calculation_identity_fingerprint,
        "payload_fingerprint": evidence.payload_fingerprint,
        "payload": dict(evidence.payload_json or {}),
        "source_evidence_ids": dict(evidence.source_evidence_ids_json or {}),
    }


def read_lifecycle_evidence(
    db: Session,
    *,
    evaluation_evidence_id: int | None = None,
    transition_evidence_id: int | None = None,
) -> dict:
    """Read an exact lifecycle evaluation or transition; projections are not fallback."""

    from app.services.setup_lifecycle.decision_evidence import (
        get_lifecycle_evaluation_evidence,
        get_lifecycle_transition_evidence,
    )

    supplied = sum(value is not None for value in (evaluation_evidence_id, transition_evidence_id))
    if supplied != 1:
        raise HistoricalReadError(
            "HISTORICAL_MODE_REQUIRES_IDENTITY",
            "provide exactly one lifecycle evaluation or transition evidence ID",
        )
    if evaluation_evidence_id is not None:
        row = get_lifecycle_evaluation_evidence(db, evaluation_evidence_id)
        artifact = "LIFECYCLE_EVALUATION"
    else:
        row = get_lifecycle_transition_evidence(db, int(transition_evidence_id))
        artifact = "LIFECYCLE_TRANSITION"
    return {
        "read_mode": ReadMode.EVIDENCE.value,
        "evidence_status": "CERTIFIED_IMMUTABLE",
        "artifact_kind": artifact,
        "evidence_id": row.id,
        "payload": dict(row.payload_json or {}),
        "payload_fingerprint": row.payload_fingerprint,
    }


def read_current_lifecycle(db: Session, *, episode_id: int) -> dict:
    episode = db.get(SetupLifecycleEpisode, int(episode_id))
    if episode is None:
        raise HistoricalReadError("CURRENT_PROJECTION_UNAVAILABLE", f"episode {episode_id}")
    return {
        "read_mode": ReadMode.CURRENT.value,
        "evidence_status": (
            "CERTIFIED_POINTER"
            if episode.latest_evaluation_evidence_id is not None
            else "LEGACY_CURRENT"
        ),
        "episode_id": episode.id,
        "state": episode.current_state,
        "phase": episode.current_phase,
        "latest_evaluation_evidence_id": episode.latest_evaluation_evidence_id,
        "latest_transition_evidence_id": episode.latest_transition_evidence_id,
    }


def read_alert_evidence(db: Session, *, decision_evidence_id: int) -> dict:
    decision = db.get(SignalAlertDecisionEvidence, int(decision_evidence_id))
    if decision is None:
        raise HistoricalReadError(
            "HISTORICAL_EVIDENCE_UNAVAILABLE",
            f"alert decision evidence {decision_evidence_id}",
        )
    rule = db.get(SignalAlertRuleEvidence, decision.rule_evidence_id)
    if rule is None:
        raise HistoricalReadError(
            "HISTORICAL_EVIDENCE_UNAVAILABLE",
            f"alert rule evidence {decision.rule_evidence_id}",
        )
    return {
        "read_mode": ReadMode.EVIDENCE.value,
        "evidence_status": "CERTIFIED_IMMUTABLE",
        "decision_evidence_id": decision.id,
        "decision": decision.decision,
        "decision_payload": dict(decision.payload_json or {}),
        "rule_evidence_id": rule.id,
        "rule_payload": dict(rule.payload_json or {}),
        "cooldown_predecessor_evidence_id": decision.cooldown_predecessor_evidence_id,
        "dedup_predecessor_evidence_id": decision.dedup_predecessor_evidence_id,
    }


def read_current_alert(db: Session, *, alert_event_id: int) -> dict:
    event = db.get(SignalAlertEvent, int(alert_event_id))
    if event is None:
        raise HistoricalReadError(
            "CURRENT_PROJECTION_UNAVAILABLE", f"alert event {alert_event_id}"
        )
    return {
        "read_mode": ReadMode.CURRENT.value,
        "evidence_status": (
            "CERTIFIED_POINTER" if event.decision_evidence_id is not None else "LEGACY_CURRENT"
        ),
        "alert_event_id": event.id,
        "notification_status": event.status,
        "decision_evidence_id": event.decision_evidence_id,
    }
