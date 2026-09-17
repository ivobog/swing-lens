"""Explicit frozen Technical fixtures for tests of certified consumer calculations.

Generic legacy model constructors remain available for unknown/legacy regressions.
These fixtures use the real producer normalizer and a complete frozen value payload;
changing a source requires resealing it, just as recalculation creates new evidence.
"""

from itertools import count

from sqlalchemy import inspect

from app.models.tables import CoreCalculationEvidence, TechnicalScore
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.combined_ranking_identity import calculation_identity_from_debug
from app.services.producer_readiness import normalize_producer_readiness

_evidence_ids = count(100000)


def certified_technical(**values) -> TechnicalScore:
    values.setdefault("insufficient_data", False)
    values.setdefault("technical_confidence", "normal")
    values.setdefault("run_id", 1)
    return seal_technical(TechnicalScore(**values))


def seal_technical(score: TechnicalScore) -> TechnicalScore:
    payload = Canonical.canonicalize(
        {
            column.key: getattr(score, column.key)
            for column in inspect(TechnicalScore).column_attrs
            if column.key not in {"id", "evidence_id", "created_at", "updated_at"}
        }
    )
    identity = calculation_identity_from_debug(score.debug_json)
    fingerprint = (
        str(identity.fingerprint())
        if identity
        else Canonical.fingerprint(
            {
                "fixture": "technical-consumer",
                "run_id": score.run_id,
                "ticker": score.ticker,
            }
        )
    )
    readiness = normalize_producer_readiness("TECHNICAL", payload, identity_fingerprint=fingerprint)
    payload["producer_readiness"] = readiness.canonical_payload()
    evidence_id = next(_evidence_ids)
    score.evidence_id = evidence_id
    score.calculation_evidence = CoreCalculationEvidence(
        id=evidence_id,
        artifact_kind="TECHNICAL",
        run_id=score.run_id,
        ticker=score.ticker.upper(),
        calculation_identity_fingerprint=fingerprint,
        payload_json=payload,
    )
    return score


def ready_setup_permission() -> dict:
    """An explicit READY precondition for independent state-machine/gate fixtures."""
    from app.services.producer_readiness import (
        ConsumerEligibilityDecision,
        ConsumerEligibilityStatus,
        readiness_from_evidence,
    )

    score = certified_technical(ticker="MSFT", dual_score=8)
    readiness = readiness_from_evidence(score.calculation_evidence)
    decision = ConsumerEligibilityDecision(
        "SETUP", ConsumerEligibilityStatus.ELIGIBLE, (), "technical-to-setup-v1",
        readiness.fingerprint(), readiness.evidence_id,
    )
    return {"technical_consumer_eligibility": {
        "decision": decision.to_dto(), "producer_readiness": readiness.to_dto(),
        "calculation_identity_fingerprint": readiness.calculation_identity_fingerprint,
    }}
