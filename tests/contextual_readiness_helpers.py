"""Explicit frozen contextual preconditions; native invalid inputs remain invalid."""

from itertools import count

from sqlalchemy import inspect

from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import CoreCalculationEvidence, MarketRegimeSnapshot
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.contextual_calculation_identity import (
    artifact_identity,
    build_ibmi_feature_identity,
)
from app.services.producer_readiness import normalize_producer_readiness

_ids = count(200000)


def seal_contextual(row, *, rows=(), evidence_id=None):
    output = {
        column.key: getattr(row, column.key)
        for column in inspect(type(row)).column_attrs
        if column.key not in {"id", "evidence_id", "created_at", "updated_at"}
    }
    if isinstance(row, IBIntelligenceFeature):
        kind = "IBMI"
        identity = build_ibmi_feature_identity(row)
        payload = {"derived_output": output}
        ticker, profile = row.ticker.upper(), row.module
        run_id = None
    else:
        kind = "REGIME" if isinstance(row, MarketRegimeSnapshot) else "SECTOR"
        identity = artifact_identity(row)
        payload = output
        ticker, profile = None, getattr(row, "mode", None)
        run_id = row.run_id
        if kind == "SECTOR":
            payload["rows"] = [
                {
                    column.key: getattr(item, column.key)
                    for column in inspect(type(item)).column_attrs
                }
                for item in rows
            ]
    payload = Canonical.canonicalize(payload)
    fingerprint = str(identity.fingerprint())
    readiness = normalize_producer_readiness(kind, payload, identity_fingerprint=fingerprint)
    payload["producer_readiness"] = readiness.canonical_payload()
    address = evidence_id or row.evidence_id or next(_ids)
    row.evidence_id = address
    row.calculation_evidence = CoreCalculationEvidence(
        id=address,
        artifact_kind=kind,
        run_id=run_id,
        ticker=ticker,
        ranking_profile=profile,
        calculation_identity_fingerprint=fingerprint,
        payload_json=payload,
    )
    return row


def ibmi_feature(module="LIQUIDITY", *, ticker="MSFT", **values):
    from datetime import UTC, date, datetime

    defaults = dict(
        id=next(_ids),
        ticker=ticker,
        module=module,
        as_of_session=date(2026, 9, 11),
        calculated_at=datetime(2026, 9, 11, 18, tzinfo=UTC),
        calculation_cutoff_at=datetime(2026, 9, 11, 18, tzinfo=UTC),
        calendar_version="swinglens-us-equities-v1",
        classification="VERY_POOR",
        confidence="HIGH",
        freshness_status="AVAILABLE",
        coverage_status="AVAILABLE",
        components_json={"dollar_volume": 2_000_000, "iv_hv_ratio": 2.5},
        warnings_json=[],
        reasons_json=[],
        source_evidence_hashes_json=["a" * 64],
        source_version="ibkr-tws-flex-v1",
        calculation_version="ibmi-1.0.0",
        config_hash="b" * 64,
        input_signature="fixture",
    )
    defaults.update(values)
    return seal_contextual(IBIntelligenceFeature(**defaults))
