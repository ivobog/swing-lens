"""Native complete Core inputs for independent consumer scenarios.

Generic ORM constructors remain unsealed for legacy/unknown campaigns.
"""

import csv
from datetime import datetime
from itertools import count
from pathlib import Path

from sqlalchemy import inspect

from app.models.tables import (
    CombinedResult,
    CoreCalculationEvidence,
    FundamentalScore,
    RankingResult,
)
from app.services.calculation_identity import IdentityState
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.contextual_calculation_identity import artifact_identity
from app.services.producer_readiness import normalize_producer_readiness

_ids = count(500000)


def seal_core(row, kind=None, *, calculated_at: datetime | None = None, evidence_id=None):
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
    identity = artifact_identity(row)
    cutoff = identity.temporal.calculation_cutoff
    if cutoff.state is IdentityState.KNOWN:
        if calculated_at is not None and calculated_at != cutoff.value:
            raise ValueError("Core fixture calculation time must match its frozen identity cutoff")
        calculated_at = cutoff.value
    if calculated_at is not None and (
        calculated_at.tzinfo is None or calculated_at.utcoffset() is None
    ):
        raise ValueError("Core fixture calculation time must be timezone-aware")
    fingerprint = str(identity.fingerprint())
    payload["producer_readiness"] = normalize_producer_readiness(
        kind,
        payload,
        identity_fingerprint=fingerprint,
    ).canonical_payload()
    row.evidence_id = evidence_id if evidence_id is not None else next(_ids)
    row.calculation_evidence = CoreCalculationEvidence(
        id=row.evidence_id,
        artifact_kind=kind,
        run_id=row.run_id,
        ticker=row.ticker.upper(),
        ranking_profile=getattr(row, "ranking_profile", None),
        calculation_identity_fingerprint=fingerprint,
        payload_json=payload,
        calculated_at=calculated_at,
    )
    return row


def populate_fundamental_source(row, *, raw_row=None, raw_values=None):
    """Parse a declared synthetic source, retaining explicit consumer score inputs.

    Consumer threshold tests inject score/label independently of producer math.
    Model, coverage, components and parse facts still come from the real producer.
    This opt-in is never applied by generic sealing or incomplete constructors.
    """
    from app.services.column_mapper import map_csv_rows
    from app.services.fundamental_ranker_v2 import score_row_v2
    from app.services.upload_service import _fundamental_score_from_v2

    if raw_values is None:
        source = Path(__file__).parent / "e2e/single_run_certification/golden/v3/universe.csv"
        with source.open(newline="", encoding="utf-8-sig") as handle:
            raw = dict(next(csv.DictReader(handle)))
        raw["Symbol"] = row.ticker
    else:
        raw = dict(raw_values)
    if raw_row is not None:
        if raw_row.run_id != row.run_id or raw_row.ticker.upper() != row.ticker.upper():
            raise ValueError("Synthetic Fundamental source must match its run/ticker owner")
        if raw_values is None:
            raw.update(raw_row.raw_json or {})
        raw_row.raw_json = raw
    mapped = map_csv_rows([raw])[0]
    if mapped.ticker.upper() != row.ticker.upper():
        raise ValueError("Synthetic Fundamental CSV symbol must match its owner")
    calculated = _fundamental_score_from_v2(row.run_id, score_row_v2(mapped))
    supplied_score, supplied_label = row.fundamental_score, row.fundamental_label
    old_debug = row.debug_json or {}
    for column in inspect(FundamentalScore).column_attrs:
        if column.key not in {"id", "run_id", "ticker", "evidence_id", "created_at", "updated_at"}:
            setattr(row, column.key, getattr(calculated, column.key))
    if supplied_score is not None:
        row.fundamental_score = supplied_score
    if supplied_label is not None:
        row.fundamental_label = supplied_label
    row.debug_json = {
        **old_debug,
        **calculated.debug_json,
        "synthetic_consumer_source": {
            "raw_csv_values": raw,
            "raw_fingerprint": Canonical.fingerprint(raw),
            "producer_score": str(calculated.fundamental_score),
            "producer_label": calculated.fundamental_label,
            "consumer_score": str(row.fundamental_score),
            "consumer_label": row.fundamental_label,
            "purpose": "Explicit consumer boundary inputs; producer accounting is recalculated",
        },
    }
    return row


def certified_fundamental(*, calculated_at: datetime | None = None, source_backed=False, **values):
    values.setdefault("data_coverage_score", 10)
    values.setdefault("run_id", 1)
    row = FundamentalScore(**values)
    if source_backed:
        populate_fundamental_source(row)
    return seal_core(row, calculated_at=calculated_at)


def certified_combined(**values):
    values.setdefault("is_complete", True)
    values.setdefault("run_id", 1)
    return seal_core(CombinedResult(**values))


def certified_ranking(**values):
    values.setdefault("is_complete", True)
    values.setdefault("run_id", 1)
    return seal_core(RankingResult(**values))
