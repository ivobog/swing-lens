"""Contextual source permission, evaluated against exact frozen producer evidence."""

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Numeric, inspect
from sqlalchemy.orm import object_session
from sqlalchemy.orm.attributes import NO_VALUE

from app.models.tables import (
    CoreCalculationEvidence,
    SectorRotationRow,
    SectorRotationSnapshot,
)
from app.services.contextual_calculation_identity import (
    artifact_identity,
    build_ibmi_feature_identity,
)
from app.services.core_calculation_evidence import EvidenceUnavailableError
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

CONTEXTUAL_ELIGIBILITY_KEY = "contextual_consumer_eligibility"


@dataclass(frozen=True)
class ContextualConsumerPolicy:
    producer: str
    consumer: str
    policy_version: str
    module: str | None = None

    def evaluate(
        self,
        readiness: ProducerReadinessEnvelope,
        *,
        consumer: str | None = None,
        config: NativeReadinessMetrics | None = None,
    ) -> ConsumerEligibilityDecision:
        if readiness.producer != self.producer or consumer not in {None, self.consumer}:
            raise ValueError("Contextual eligibility producer/consumer mismatch")
        if config is not None and config.canonical_payload():
            raise ValueError("Contextual eligibility v1 has no configurable overrides")
        status = ConsumerEligibilityStatus.POLICY_UNDECIDED
        if readiness.blocking_reasons or readiness.status in {
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
            ReadinessStatus.ERROR,
            ReadinessStatus.STALE,
        }:
            status = ConsumerEligibilityStatus.INELIGIBLE
            reasons = (EligibilityReason.PRODUCER_READINESS_BLOCKED,)
        elif readiness.status is ReadinessStatus.READY:
            status = ConsumerEligibilityStatus.ELIGIBLE
            reasons = ()
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


IBMI_LIQUIDITY_TO_RANKING = ContextualConsumerPolicy(
    "IBMI",
    "RANKING",
    "ibmi-liquidity-to-ranking-v1",
    "LIQUIDITY",
)
IBMI_VOLATILITY_TO_CERI = ContextualConsumerPolicy(
    "IBMI",
    "CERI",
    "ibmi-volatility-to-ceri-v1",
    "VOLATILITY",
)
IBMI_SHORT_PRESSURE_TO_CERI = ContextualConsumerPolicy(
    "IBMI",
    "CERI",
    "ibmi-short-pressure-to-ceri-v1",
    "SHORT_PRESSURE",
)
REGIME_TO_SETUP = ContextualConsumerPolicy("REGIME", "SETUP", "regime-to-setup-v1")
SECTOR_TO_SETUP = ContextualConsumerPolicy("SECTOR", "SETUP", "sector-to-setup-v1")


def contextual_decision_input(
    row: Any | None,
    policy: ContextualConsumerPolicy,
) -> tuple[Any | None, dict[str, Any]]:
    evidence = None
    if row is not None and getattr(row, "evidence_id", None) is not None:
        loaded = inspect(row).attrs.calculation_evidence.loaded_value
        if loaded is NO_VALUE:
            db = object_session(row)
            if db is None:
                raise EvidenceUnavailableError("EVIDENCE_UNAVAILABLE: detached context not loaded")
            with db.no_autoflush:
                evidence = db.get(CoreCalculationEvidence, row.evidence_id)
        else:
            evidence = loaded
        if evidence is None or evidence.id != row.evidence_id:
            raise EvidenceUnavailableError("EVIDENCE_UNAVAILABLE: contextual pointer unresolved")
        if evidence.artifact_kind != policy.producer:
            raise EvidenceUnavailableError("EVIDENCE_UNAVAILABLE: contextual producer mismatch")
        if policy.producer == "IBMI":
            identity = build_ibmi_feature_identity(row)
            if (
                row.module != policy.module
                or evidence.ranking_profile != policy.module
                or evidence.ticker != row.ticker.upper()
            ):
                raise EvidenceUnavailableError(
                    "EVIDENCE_UNAVAILABLE: contextual module/scope mismatch"
                )
        else:
            identity = artifact_identity(row)
            if evidence.run_id != row.run_id:
                raise EvidenceUnavailableError("EVIDENCE_UNAVAILABLE: contextual owner mismatch")
        if str(identity.fingerprint()) != evidence.calculation_identity_fingerprint:
            raise EvidenceUnavailableError("EVIDENCE_UNAVAILABLE: contextual identity mismatch")
        readiness = readiness_from_evidence(evidence)
    else:
        readiness = legacy_readiness(policy.producer)
    decision = policy.evaluate(readiness)
    included = decision.status is ConsumerEligibilityStatus.ELIGIBLE
    frozen = {
        "decision": decision.to_dto(),
        "producer_readiness": readiness.to_dto(),
        "included": included,
        "source_feature_id": getattr(row, "id", None),
    }
    if not included:
        return None, frozen
    payload = evidence.payload_json
    if policy.producer == "IBMI":
        payload = payload["derived_output"]
    return _project_frozen(row, payload, evidence.id, evidence=evidence), frozen


def frozen_sector_row(snapshot: SectorRotationSnapshot | None, row: Any | None):
    if snapshot is None or row is None:
        return None
    evidence = snapshot.calculation_evidence
    candidates = [
        item
        for item in evidence.payload_json["rows"]
        if str(item["sector"]).casefold() == str(row.sector).casefold()
    ]
    if row.snapshot_id != snapshot.id or len(candidates) != 1:
        raise EvidenceUnavailableError("EVIDENCE_UNAVAILABLE: Sector row/snapshot mismatch")
    return _project_frozen(row, candidates[0], evidence.id)


def _project_frozen(
    row: Any,
    payload: dict[str, Any],
    evidence_id: int,
    *,
    evidence: Any | None = None,
):
    values = {}
    for column in inspect(type(row)).columns:
        key = column.key
        if key not in payload and key.endswith("_json"):
            key = key.removesuffix("_json")
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(column.type, Numeric) and value is not None:
            value = Decimal(str(value))
            native = getattr(row, column.key)
            if isinstance(native, Decimal) and native == value:
                value = native
        elif isinstance(column.type, DateTime) and isinstance(value, str):
            value = datetime.fromisoformat(value)
        elif isinstance(column.type, Date) and isinstance(value, str):
            value = date.fromisoformat(value)
        values[column.key] = value
    values["id"] = row.id
    if isinstance(row, SectorRotationRow):
        values["snapshot_id"] = row.snapshot_id
    else:
        values["evidence_id"] = evidence_id
    result = type(row)(**values)
    if evidence is not None:
        result.calculation_evidence = evidence
    return result


def setup_with_contextual_permission(snapshot: Any):
    """Apply recorded omission, never reevaluate historical decisions under today's policy.

    Optional missing context does not block the complete consumer. This removes only
    contextual influences, including mutable residual values in a Setup projection.
    """
    frozen = snapshot.source_lineage.get(CONTEXTUAL_ELIGIBILITY_KEY)
    if frozen is None:
        source_ids = getattr(snapshot, "source_ids", {}) or snapshot.source_lineage.get(
            "source_ids", {}
        )
        if not any(
            source_ids.get(key)
            for key in (
                "market_regime_snapshot_id",
                "sector_rotation_snapshot_id",
            )
        ) and not snapshot.source_lineage.get("calculation_identity"):
            return snapshot
        frozen = {}
    omitted_keys = []
    for edge, producer, keys in (
        ("regime", "REGIME", ("market_regime", "market_gate")),
        ("sector", "SECTOR", ("sector_rank", "sector_confidence")),
    ):
        permission = frozen.get(edge)
        if permission is None:
            omitted_keys.extend(keys)
            continue
        dto = permission["decision"]
        readiness = ProducerReadinessEnvelope.from_payload(permission["producer_readiness"])
        decoded = ConsumerEligibilityDecision(
            dto["consumer"],
            ConsumerEligibilityStatus(dto["status"]),
            tuple(EligibilityReason(reason) for reason in dto["reasons"]),
            dto["policy_version"],
            dto["producer_readiness_fingerprint"],
            dto["producer_evidence_id"],
        )
        if (
            decoded.consumer != "SETUP"
            or readiness.producer != producer
            or decoded.producer_readiness_fingerprint != readiness.fingerprint()
            or decoded.producer_evidence_id != readiness.evidence_id
        ):
            raise ValueError("Setup contextual eligibility/readiness binding mismatch")
        if decoded.status is not ConsumerEligibilityStatus.ELIGIBLE:
            omitted_keys.extend(keys)
    if not omitted_keys:
        return snapshot
    signals = dict(snapshot.signals)
    lineage = dict(snapshot.source_lineage)
    if "market_regime" in omitted_keys:
        lineage["market_regime_as_of"] = None
    if "sector_rank" in omitted_keys:
        lineage["sector_rotation_as_of"] = None
    for key in omitted_keys:
        if key in signals:
            signals[key] = replace(signals[key], raw_value=None, normalized_value=None)
    return replace(snapshot, signals=signals, source_lineage=lineage)
