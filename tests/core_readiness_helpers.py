"""Native complete Core inputs for independent consumer scenarios.

Generic ORM constructors remain unsealed for legacy/unknown campaigns.
"""

from itertools import count

from sqlalchemy import inspect

from app.models.tables import (
    CombinedResult,
    CoreCalculationEvidence,
    FundamentalScore,
    RankingResult,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.contextual_calculation_identity import artifact_identity
from app.services.producer_readiness import normalize_producer_readiness

_ids = count(500000)


def seal_core(row, kind=None):
    kind = (
        kind
        or {FundamentalScore: "FUNDAMENTAL", CombinedResult: "COMBINED", RankingResult: "RANKING"}[
            type(row)
        ]
    )
    payload = Canonical.canonicalize(
        {
            column.key: getattr(row, column.key)
            for column in inspect(type(row)).column_attrs
            if column.key not in {"id", "evidence_id", "created_at", "updated_at"}
        }
    )
    fingerprint = str(artifact_identity(row).fingerprint())
    payload["producer_readiness"] = normalize_producer_readiness(
        kind,
        payload,
        identity_fingerprint=fingerprint,
    ).canonical_payload()
    row.evidence_id = next(_ids)
    row.calculation_evidence = CoreCalculationEvidence(
        id=row.evidence_id,
        artifact_kind=kind,
        run_id=row.run_id,
        ticker=row.ticker.upper(),
        ranking_profile=getattr(row, "ranking_profile", None),
        calculation_identity_fingerprint=fingerprint,
        payload_json=payload,
    )
    return row


def certified_fundamental(**values):
    values.setdefault("data_coverage_score", 10)
    values.setdefault("run_id", 1)
    return seal_core(FundamentalScore(**values))


def certified_combined(**values):
    values.setdefault("is_complete", True)
    values.setdefault("run_id", 1)
    return seal_core(CombinedResult(**values))


def certified_ranking(**values):
    values.setdefault("is_complete", True)
    values.setdefault("run_id", 1)
    return seal_core(RankingResult(**values))
