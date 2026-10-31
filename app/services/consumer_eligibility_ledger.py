"""Append-only persistence for eligibility, acquisition, and shadow decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.models.tables import (
    ConsumerEligibilityDecisionRecord,
    EligibilityShadowEvaluationRecord,
    WinnerAcquisitionDecisionRecord,
)
from app.observability.transaction_metrics import publish_after_commit
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.eligibility_policy import (
    ConsumerEligibilityOutcome,
    OrthogonalConsumerDecision,
)
from app.services.warning_taxonomy import ClassifiedWarning

WINNER_ACQUISITION_POLICY_VERSION = "winner-source-acquisition-v1"
SHADOW_EVALUATION_CONTRACT_VERSION = "eligibility-shadow-evaluation-v1"


class WinnerAcquisitionOutcome(StrEnum):
    INSERTED = "INSERTED"
    DUPLICATE = "DUPLICATE"
    EXCLUDED = "EXCLUDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WinnerAcquisitionDecision:
    upload_run_id: int
    ticker: str
    decision_cutoff: datetime
    outcome: WinnerAcquisitionOutcome
    source_identities: dict[str, Any]
    source_decisions: tuple[dict[str, Any], ...]
    warning_classifications: tuple[ClassifiedWarning, ...] = ()
    eligibility_reason: str | None = None
    pipeline_run_id: int | None = None
    raw_row_id: int | None = None
    prediction_id: int | None = None
    policy_version: str = WINNER_ACQUISITION_POLICY_VERSION

    def __post_init__(self) -> None:
        _require_aware(self.decision_cutoff)
        if not self.ticker.strip():
            raise ValueError("Winner acquisition decision requires a ticker")
        if self.outcome is WinnerAcquisitionOutcome.INSERTED and self.prediction_id is None:
            raise ValueError("inserted Winner acquisition requires a prediction id")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "contract_version": WINNER_ACQUISITION_POLICY_VERSION,
            "policy_version": self.policy_version,
            "pipeline_run_id": self.pipeline_run_id,
            "upload_run_id": self.upload_run_id,
            "raw_row_id": self.raw_row_id,
            "prediction_id": self.prediction_id,
            "ticker": self.ticker.strip().upper(),
            "decision_cutoff": self.decision_cutoff.astimezone(UTC),
            "acquisition_outcome": self.outcome.value,
            "eligibility_reason": self.eligibility_reason,
            "source_identities": self.source_identities,
            "source_decisions": list(self.source_decisions),
            "warning_classifications": [
                item.canonical_payload() for item in self.warning_classifications
            ],
        }

    def fingerprint(self) -> str:
        return Canonical.fingerprint(self.canonical_payload())


@dataclass(frozen=True)
class EligibilityShadowEvaluation:
    consumer_edge: str
    policy_version: str
    source_identity_fingerprint: str
    decision_cutoff: datetime
    legacy_outcome: ConsumerEligibilityOutcome
    proposed_decision: OrthogonalConsumerDecision
    frozen_input: dict[str, Any]
    pipeline_run_id: int | None = None
    upload_run_id: int | None = None
    ticker: str | None = None

    def __post_init__(self) -> None:
        _require_aware(self.decision_cutoff)
        if self.pipeline_run_id is None and self.upload_run_id is None:
            raise ValueError("shadow evaluation requires a pipeline or upload run id")
        if self.proposed_decision.authoritative:
            raise ValueError("shadow decisions must never be authoritative")
        if self.proposed_decision.consumer_edge != self.consumer_edge:
            raise ValueError("shadow evaluation edge must match proposed decision")
        if (
            self.proposed_decision.source_identity_fingerprint
            != self.source_identity_fingerprint
        ):
            raise ValueError("shadow evaluation source identity must match proposed decision")

    @property
    def proposed_outcome(self) -> ConsumerEligibilityOutcome:
        return self.proposed_decision.eligibility

    @property
    def differs(self) -> bool:
        return self.legacy_outcome is not self.proposed_outcome

    def frozen_input_fingerprint(self) -> str:
        return Canonical.fingerprint(self.frozen_input)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "contract_version": SHADOW_EVALUATION_CONTRACT_VERSION,
            "pipeline_run_id": self.pipeline_run_id,
            "upload_run_id": self.upload_run_id,
            "ticker": self.ticker.strip().upper() if self.ticker else None,
            "consumer_edge": self.consumer_edge,
            "policy_version": self.policy_version,
            "source_identity_fingerprint": self.source_identity_fingerprint,
            "decision_cutoff": self.decision_cutoff.astimezone(UTC),
            "legacy_outcome": self.legacy_outcome.value,
            "proposed_outcome": self.proposed_outcome.value,
            "differs": self.differs,
            "frozen_input_fingerprint": self.frozen_input_fingerprint(),
            "frozen_input": self.frozen_input,
            "proposed_decision": self.proposed_decision.canonical_payload(),
        }

    def fingerprint(self) -> str:
        return Canonical.fingerprint(self.canonical_payload())


def persist_consumer_decision(
    db: Session,
    *,
    decision: OrthogonalConsumerDecision,
    source_artifact_kind: str,
    pipeline_run_id: int | None,
    upload_run_id: int | None,
) -> ConsumerEligibilityDecisionRecord:
    if pipeline_run_id is None and upload_run_id is None:
        raise ValueError("consumer eligibility persistence requires a run identity")
    payload = decision.canonical_payload()
    fingerprint = decision.fingerprint()
    values = {
        "pipeline_run_id": pipeline_run_id,
        "upload_run_id": upload_run_id,
        "producer": decision.producer,
        "consumer": decision.consumer,
        "consumer_edge": decision.consumer_edge,
        "policy_version": decision.policy_version,
        "source_artifact_kind": source_artifact_kind,
        "source_evidence_id": decision.source_evidence_id,
        "source_identity_fingerprint": decision.source_identity_fingerprint,
        "decision_cutoff": decision.decision_cutoff,
        "execution_outcome": decision.execution_outcome.value,
        "integrity_status": decision.integrity_status.value,
        "completeness_status": decision.completeness_status.value,
        "analytical_confidence": decision.analytical_confidence.value,
        "eligibility": decision.eligibility.value,
        "reason_codes_json": list(decision.reason_codes),
        "warning_classifications_json": [
            item.canonical_payload() for item in decision.warnings
        ],
        "decision_payload_json": Canonical.canonicalize(payload),
        "decision_fingerprint": fingerprint,
        "authoritative": decision.authoritative,
    }
    record = _insert_once(
        db,
        model=ConsumerEligibilityDecisionRecord,
        fingerprint_column="decision_fingerprint",
        fingerprint=fingerprint,
        values=values,
    )
    publish_after_commit(
        db,
        "increment",
        "swinglens_consumer_eligibility_decisions_total",
        producer=decision.producer,
        consumer=decision.consumer,
        outcome=decision.eligibility.value,
        reason=decision.reason_codes[0] if decision.reason_codes else "NONE",
    )
    return record


def persist_winner_acquisition_decision(
    db: Session, decision: WinnerAcquisitionDecision
) -> WinnerAcquisitionDecisionRecord:
    payload = decision.canonical_payload()
    fingerprint = decision.fingerprint()
    values = {
        "pipeline_run_id": decision.pipeline_run_id,
        "upload_run_id": decision.upload_run_id,
        "raw_row_id": decision.raw_row_id,
        "prediction_id": decision.prediction_id,
        "ticker": decision.ticker.strip().upper(),
        "decision_cutoff": decision.decision_cutoff,
        "policy_version": decision.policy_version,
        "acquisition_outcome": decision.outcome.value,
        "eligibility_reason": decision.eligibility_reason,
        "source_identities_json": Canonical.canonicalize(decision.source_identities),
        "source_decisions_json": Canonical.canonicalize(list(decision.source_decisions)),
        "warning_classifications_json": [
            item.canonical_payload() for item in decision.warning_classifications
        ],
        "decision_payload_json": Canonical.canonicalize(payload),
        "decision_fingerprint": fingerprint,
    }
    record = _insert_once(
        db,
        model=WinnerAcquisitionDecisionRecord,
        fingerprint_column="decision_fingerprint",
        fingerprint=fingerprint,
        values=values,
    )
    publish_after_commit(
        db,
        "increment",
        "swinglens_winner_acquisition_decisions_total",
        outcome=decision.outcome.value,
        reason=decision.eligibility_reason or "NONE",
    )
    return record


def persist_shadow_evaluation(
    db: Session, evaluation: EligibilityShadowEvaluation
) -> EligibilityShadowEvaluationRecord:
    fingerprint = evaluation.fingerprint()
    proposed = evaluation.proposed_decision
    values = {
        "pipeline_run_id": evaluation.pipeline_run_id,
        "upload_run_id": evaluation.upload_run_id,
        "ticker": evaluation.ticker.strip().upper() if evaluation.ticker else None,
        "consumer_edge": evaluation.consumer_edge,
        "policy_version": evaluation.policy_version,
        "source_identity_fingerprint": evaluation.source_identity_fingerprint,
        "decision_cutoff": evaluation.decision_cutoff,
        "legacy_outcome": evaluation.legacy_outcome.value,
        "proposed_outcome": evaluation.proposed_outcome.value,
        "differs": evaluation.differs,
        "frozen_input_fingerprint": evaluation.frozen_input_fingerprint(),
        "frozen_input_json": Canonical.canonicalize(evaluation.frozen_input),
        "proposed_decision_json": Canonical.canonicalize(
            proposed.canonical_payload()
        ),
        "reason_codes_json": list(proposed.reason_codes),
        "warning_classifications_json": [
            item.canonical_payload() for item in proposed.warnings
        ],
        "evaluation_fingerprint": fingerprint,
    }
    return _insert_once(
        db,
        model=EligibilityShadowEvaluationRecord,
        fingerprint_column="evaluation_fingerprint",
        fingerprint=fingerprint,
        values=values,
    )


def _insert_once(
    db: Session,
    *,
    model: type[Any],
    fingerprint_column: str,
    fingerprint: str,
    values: dict[str, Any],
) -> Any:
    result = db.execute(
        postgresql_insert(model)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[fingerprint_column])
        .returning(model.id)
    ).scalar_one_or_none()
    if result is not None:
        return db.get(model, result)
    return db.execute(
        select(model).where(getattr(model, fingerprint_column) == fingerprint)
    ).scalar_one()


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("decision cutoff must be timezone-aware")
