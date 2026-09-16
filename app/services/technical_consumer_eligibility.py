"""Versioned Technical decision permissions, bound to exact frozen evidence."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Numeric, inspect
from sqlalchemy.orm import object_session
from sqlalchemy.orm.attributes import NO_VALUE

from app.models.tables import TechnicalScore
from app.services.combined_ranking_identity import calculation_identity_from_debug
from app.services.core_calculation_evidence import CoreEvidenceKind, get_certified_evidence_for_row
from app.services.producer_readiness import (
    ConsumerEligibilityDecision,
    ConsumerEligibilityStatus,
    EligibilityReason,
    NativeReadinessMetrics,
    ProducerReadinessEnvelope,
    ReadinessStatus,
    legacy_readiness,
    readiness_from_evidence,
)

TECHNICAL_ELIGIBILITY_KEY = "technical_consumer_eligibility"


@dataclass(frozen=True)
class TechnicalConsumerPolicy:
    consumer: str
    policy_version: str

    def evaluate(
        self,
        readiness: ProducerReadinessEnvelope,
        *,
        consumer: str | None = None,
        config: NativeReadinessMetrics | None = None,
    ) -> ConsumerEligibilityDecision:
        # No configurable threshold or permissive degraded override exists in v1.
        if readiness.producer != "TECHNICAL" or consumer not in {None, self.consumer}:
            raise ValueError("Technical eligibility policy producer/consumer mismatch")
        if config is not None and config.canonical_payload():
            raise ValueError("Technical eligibility v1 has no configurable overrides")
        status = ConsumerEligibilityStatus.POLICY_UNDECIDED
        reasons: tuple[EligibilityReason, ...] = ()
        if readiness.blocking_reasons or readiness.status in {
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
            ReadinessStatus.ERROR,
            ReadinessStatus.STALE,
        }:
            status = ConsumerEligibilityStatus.INELIGIBLE
            reasons = (EligibilityReason.PRODUCER_READINESS_BLOCKED,)
        elif readiness.status is ReadinessStatus.READY:
            status = ConsumerEligibilityStatus.ELIGIBLE
        elif readiness.status is ReadinessStatus.DEGRADED:
            reasons = (EligibilityReason.DEGRADED_POLICY_UNDECIDED,)
        else:
            reasons = (EligibilityReason.PRODUCER_READINESS_UNKNOWN,)
        return ConsumerEligibilityDecision(
            self.consumer,
            status,
            reasons,
            self.policy_version,
            readiness.fingerprint(),
            readiness.evidence_id,
        )


TECHNICAL_TO_COMBINED = TechnicalConsumerPolicy("COMBINED", "technical-to-combined-v1")
TECHNICAL_TO_RANKING = TechnicalConsumerPolicy("RANKING", "technical-to-ranking-v1")
TECHNICAL_TO_SETUP = TechnicalConsumerPolicy("SETUP", "technical-to-setup-v1")


def technical_decision_input(
    row: TechnicalScore | None,
    policy: TechnicalConsumerPolicy,
) -> tuple[TechnicalScore | None, dict[str, Any]]:
    """Only eligible frozen values cross the boundary; mutable numerics are diagnostics.

    Production cohort readers select-in load calculation_evidence once for all tickers.
    A single-row call can resolve its exact pointer in its owning session. Detached rows
    must carry the explicitly loaded evidence; an unresolved pointer fails, never falls back.
    """
    evidence = None
    if row is not None and row.evidence_id is not None:
        loaded = inspect(row).attrs.calculation_evidence.loaded_value
        if loaded is not NO_VALUE:
            evidence = loaded
        else:
            db = object_session(row)
            if db is None:
                raise LookupError("EVIDENCE_UNAVAILABLE: detached Technical evidence not loaded")
            with db.no_autoflush:
                evidence = get_certified_evidence_for_row(
                    db,
                    kind=CoreEvidenceKind.TECHNICAL,
                    current_row=row,
                )
        if (
            evidence is None
            or evidence.id != row.evidence_id
            or (
                evidence.artifact_kind != "TECHNICAL"
                or evidence.run_id != row.run_id
                or evidence.ticker != row.ticker.strip().upper()
            )
        ):
            raise ValueError("EVIDENCE_UNAVAILABLE: Technical pointer/scope mismatch")
        identity = calculation_identity_from_debug(row.debug_json)
        if (
            identity is not None
            and str(identity.fingerprint()) != evidence.calculation_identity_fingerprint
        ):
            raise ValueError("EVIDENCE_UNAVAILABLE: Technical identity mismatch")
        readiness = readiness_from_evidence(evidence)
    else:
        readiness = legacy_readiness("TECHNICAL")
    decision = policy.evaluate(readiness)
    frozen = {
        "decision": decision.to_dto(),
        "producer_readiness": readiness.to_dto(),
        "calculation_identity_fingerprint": readiness.calculation_identity_fingerprint,
    }
    if decision.status is not ConsumerEligibilityStatus.ELIGIBLE:
        return None, frozen
    columns = {attribute.key for attribute in inspect(TechnicalScore).column_attrs}
    values = {key: value for key, value in evidence.payload_json.items() if key in columns}
    for column in inspect(TechnicalScore).columns:
        if isinstance(column.type, Numeric) and values.get(column.key) is not None:
            values[column.key] = Decimal(str(values[column.key]))
            native = getattr(row, column.key)
            if isinstance(native, Decimal) and native == values[column.key]:
                # Equal immutable values may retain their native display precision.
                values[column.key] = native
        elif isinstance(column.type, DateTime) and isinstance(values.get(column.key), str):
            values[column.key] = datetime.fromisoformat(values[column.key])
        elif isinstance(column.type, Date) and isinstance(values.get(column.key), str):
            values[column.key] = date.fromisoformat(values[column.key])
    return TechnicalScore(**{**values, "id": row.id, "evidence_id": evidence.id}), frozen


def setup_technical_blocked(snapshot: Any) -> bool:
    lineage = snapshot.source_lineage
    frozen = lineage.get(TECHNICAL_ELIGIBILITY_KEY)
    if frozen is None:
        # Generic unbound state-machine DTOs have no Technical edge. Identity-bound
        # Setup evidence without a frozen decision cannot certify a new advancement.
        source_ids = getattr(snapshot, "source_ids", {}) or lineage.get("source_ids", {})
        return bool(lineage.get("calculation_identity") or source_ids.get("technical_score_id"))
    decision = frozen["decision"]
    readiness = ProducerReadinessEnvelope.from_payload(frozen["producer_readiness"])
    if decision["producer_readiness_fingerprint"] != readiness.fingerprint():
        raise ValueError("Setup Technical eligibility/readiness fingerprint mismatch")
    if decision["consumer"] != "SETUP":
        raise ValueError("Setup Technical eligibility consumer mismatch")
    decoded = ConsumerEligibilityDecision(
        decision["consumer"],
        ConsumerEligibilityStatus(decision["status"]),
        tuple(EligibilityReason(reason) for reason in decision["reasons"]),
        decision["policy_version"],
        decision["producer_readiness_fingerprint"],
        decision["producer_evidence_id"],
    )
    if decoded.producer_evidence_id != readiness.evidence_id:
        raise ValueError("Setup Technical eligibility evidence mismatch")
    return decoded.status is not ConsumerEligibilityStatus.ELIGIBLE
