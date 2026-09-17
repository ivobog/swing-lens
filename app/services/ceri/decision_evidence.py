from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCatalystSource,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriPriceResponseFeature,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSecDocumentExtraction,
    CeriSecFilingDocument,
    CeriSourceRecord,
)
from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import CoreCalculationEvidence, PriceBar, PriceBarRevision
from app.services.calculation_identity import IdentityState
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.config import CeriConfig, ceri_config_hash, ceri_config_payload
from app.services.combined_ranking_identity import calculation_identity_from_debug
from app.services.contextual_calculation_identity import build_ibmi_feature_identity
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    persist_core_evidence,
)
from app.services.ib_market_intelligence.decision_evidence import (
    get_certified_ibmi_evidence,
)
from app.services.price_bar_repository import project_price_bar_rows_as_of

CERI_DECISION_EVIDENCE_SCHEMA_VERSION = "ceri-decision-evidence-v1"
CERI_DECISION_RULE_VERSION = "ceri-decision-rules-v1"


def persist_ceri_decision_evidence(
    db: Session,
    *,
    snapshot: CeriScoreSnapshot,
    config: CeriConfig,
    effective_configuration=None,
) -> CoreCalculationEvidence | None:
    """Seal one identity-aware CERI decision in the shared Phase-2 ledger."""

    effective_configuration = effective_configuration or getattr(
        config, "_effective_configuration", None
    )

    if snapshot.config_hash != config.config_hash or (
        snapshot.calculation_version != config.engine.calculation_version
    ):
        raise EvidenceUnavailableError(
            "EVIDENCE_UNAVAILABLE: CERI snapshot/config identity mismatch"
        )
    if effective_configuration is not None:
        from app.services.contextual_effective_configuration import ceri_calculation_values

        if ceri_calculation_values(config) != effective_configuration.values["config"]:
            raise EvidenceUnavailableError(
                "EVIDENCE_UNAVAILABLE: CERI frozen values/config mismatch"
            )
    elif ceri_config_hash(config) != config.config_hash:
        raise EvidenceUnavailableError(
            "EVIDENCE_UNAVAILABLE: CERI effective config payload/hash mismatch"
        )
    identity = calculation_identity_from_debug(snapshot.evidence_lineage_json)
    if identity is not None:
        effective = identity.configuration.effective_configuration
        if (
            effective.state is not IdentityState.KNOWN
            or effective.value is None
            or effective.value.fingerprint.digest
            != (
                effective_configuration.snapshot.semantic_hash
                if effective_configuration is not None
                else config.config_hash
            )
        ):
            raise EvidenceUnavailableError(
                "EVIDENCE_UNAVAILABLE: CERI Calculation Identity/config mismatch"
            )

    source_manifest = build_ceri_source_manifest(db, snapshot)
    ibmi_sources = {
        f"ibmi_{feature.module.lower()}": feature for feature in _ibmi_features(db, snapshot)
    }
    payload = {
        "schema_version": CERI_DECISION_EVIDENCE_SCHEMA_VERSION,
        "decision_output": _row_payload(snapshot, excluded={"id", "evidence_id"}),
        "source_manifest": source_manifest,
        "effective_rule_payload": {
            "schema_version": CERI_DECISION_RULE_VERSION,
            "declared_config_hash": config.config_hash,
            "declared_config_version": config.engine.config_version,
            "calculation_version": config.engine.calculation_version,
            "resolved_config": (
                effective_configuration.values["config"]
                if effective_configuration is not None
                else ceri_config_payload(config)
            ),
            "posture_thresholds": {
                "insufficient_or_unrated": "Unrated",
                **(
                    effective_configuration.values["native_policy"]["posture"]
                    if effective_configuration is not None
                    else {
                        "binary_risk_min": 6.0,
                        "positive_min": 7.0,
                        "improving_min": 5.0,
                        "mixed_min": 3.0,
                    }
                ),
            },
        },
    }
    return persist_core_evidence(
        db,
        kind=CoreEvidenceKind.CERI,
        effective_configuration=effective_configuration.snapshot
        if effective_configuration is not None and identity is not None
        else None,
        current_row=snapshot,
        sources=ibmi_sources,
        payload=payload,
        scope_profile=(
            f"controlled-replay:{snapshot.controlled_replay_id}"
            if snapshot.controlled_replay_id is not None
            else None
        ),
    )


def build_ceri_source_manifest(
    db: Session,
    snapshot: CeriScoreSnapshot,
) -> dict[str, Any]:
    """Freeze the exact native CERI/IBMI/PIT rows named by a score snapshot."""

    lineage = dict(snapshot.evidence_lineage_json or {})
    revision_features = _required_rows(
        db,
        CeriRevisionFeature,
        _ints(lineage.get("revision_feature_ids")),
        "revision features",
    )
    estimate_ids = {
        value
        for feature in revision_features
        for value in (feature.current_snapshot_id, feature.baseline_snapshot_id)
        if value is not None
    }
    earnings = _required_rows(
        db,
        CeriEarningsActual,
        _ints(lineage.get("earnings_ids")),
        "earnings actuals",
    )
    estimate_ids.update(
        int(row.consensus_snapshot_id) for row in earnings if row.consensus_snapshot_id is not None
    )
    estimates = _required_rows(db, CeriEstimateSnapshot, estimate_ids, "estimate snapshots")
    guidance = _required_rows(
        db,
        CeriGuidanceEvent,
        _ints(lineage.get("guidance_ids")),
        "guidance events",
    )
    catalyst_events = _required_rows(
        db,
        CeriCatalystEvent,
        _ints(lineage.get("catalyst_event_ids")),
        "catalyst events",
    )
    catalyst_revisions = _required_rows(
        db,
        CeriCatalystEventRevision,
        _ints(lineage.get("catalyst_revision_ids")),
        "catalyst revisions",
    )
    catalyst_revision_ids = [row.id for row in catalyst_revisions if row.id is not None]
    catalyst_sources = (
        _rows_where(
            db,
            CeriCatalystSource,
            CeriCatalystSource.catalyst_revision_id.in_(catalyst_revision_ids),
        )
        if catalyst_revision_ids
        else []
    )

    source_ids = set(_ints((snapshot.component_json or {}).get("source_ids")))
    for key in (
        "revision_source_ids",
        "earnings_source_ids",
        "guidance_source_ids",
        "catalyst_source_ids",
    ):
        source_ids.update(_ints(lineage.get(key)))
    source_ids.update(
        int(value)
        for feature in revision_features
        for value in (
            feature.current_source_record_id,
            feature.baseline_source_record_id,
            feature.provider_retrospective_source_record_id,
        )
        if value is not None
    )
    source_ids.update(
        int(row.conversion_source_record_id)
        for row in estimates
        if row.conversion_source_record_id is not None
    )
    source_ids.update(
        int(row.source_record_id)
        for row in [*estimates, *earnings, *guidance, *catalyst_revisions, *catalyst_sources]
        if row.source_record_id is not None
    )
    sources = _required_rows(db, CeriSourceRecord, source_ids, "source records")

    price_features = _required_rows(
        db,
        CeriPriceResponseFeature,
        _ints(lineage.get("price_response_feature_ids")),
        "price-response features",
    )
    price_bar_ids = set(_ints(lineage.get("price_bar_ids")))
    price_bar_ids.update(
        int(value) for feature in price_features for value in (feature.price_bar_ids_json or [])
    )
    current_bars = _required_rows(db, PriceBar, price_bar_ids, "price bars")
    projected_bars = (
        project_price_bar_rows_as_of(db, current_bars, as_of=snapshot.cutoff_at)
        if current_bars
        else []
    )
    if len(projected_bars) != len(current_bars):
        raise EvidenceUnavailableError(
            "EVIDENCE_UNAVAILABLE: CERI price-bar PIT projection is incomplete"
        )
    all_price_revisions = (
        _rows_where(
            db,
            PriceBarRevision,
            PriceBarRevision.price_bar_id.in_(sorted(price_bar_ids)),
        )
        if price_bar_ids
        else []
    )
    price_revisions = [row for row in all_price_revisions if row.observed_at <= snapshot.cutoff_at]
    projection_boundaries = []
    for price_bar_id in sorted(price_bar_ids):
        later = [
            row
            for row in all_price_revisions
            if row.price_bar_id == price_bar_id and row.observed_at > snapshot.cutoff_at
        ]
        if later:
            projection_boundaries.append(
                min(later, key=lambda row: (row.observed_at, row.revision_number, row.id))
            )

    ibmi_features = _ibmi_features(db, snapshot)
    ibmi_payload = []
    for feature in ibmi_features:
        identity = build_ibmi_feature_identity(feature)
        evidence = get_certified_ibmi_evidence(db, feature)
        ibmi_payload.append(
            {
                "feature_id": int(feature.id),
                "module": feature.module,
                "immutable_evidence_id": int(evidence.id),
                "immutable_evidence_key": evidence.evidence_key,
                "immutable_payload_fingerprint": evidence.payload_fingerprint,
                "constituent_fingerprint": evidence.payload_json.get("constituent_fingerprint"),
                "calculation_identity": identity.canonical_payload(),
                "calculation_identity_fingerprint": str(identity.fingerprint()),
                "phase2_constituent_certification": "CERTIFIED_T11B3",
            }
        )

    accessions = sorted({row.filing_accession for row in guidance if row.filing_accession})
    processor_signatures = sorted(
        {row.processor_signature for row in guidance if row.processor_signature}
    )
    sec_documents = (
        _rows_where(
            db,
            CeriSecFilingDocument,
            CeriSecFilingDocument.accession_number.in_(accessions),
        )
        if accessions
        else []
    )
    sec_extractions = (
        _rows_where(
            db,
            CeriSecDocumentExtraction,
            CeriSecDocumentExtraction.document_id.in_(
                [row.id for row in sec_documents if row.id is not None]
            )
            & CeriSecDocumentExtraction.processor_signature.in_(processor_signatures),
        )
        if sec_documents and processor_signatures
        else []
    )

    return CanonicalEvidenceSerializer.canonicalize(
        {
            "historical_view_mode": lineage.get("historical_view_mode", "AS_KNOWN"),
            "source_records": [_source_record_payload(row) for row in sources],
            "estimate_snapshots": _payloads(estimates),
            "earnings_actuals": _payloads(earnings),
            "guidance_events": _payloads(guidance),
            "catalyst_events": _payloads(catalyst_events),
            "catalyst_revisions": _payloads(catalyst_revisions),
            "catalyst_sources": _payloads(catalyst_sources),
            "sec_documents": _payloads(sec_documents),
            "sec_extractions": _payloads(sec_extractions),
            "revision_features": _payloads(revision_features),
            "price_response_features": _payloads(price_features),
            "price_bars_as_known": _payloads(projected_bars),
            "price_bar_revisions_known_at_cutoff": _payloads(price_revisions),
            "price_bar_projection_boundaries": _payloads(projection_boundaries),
            "ibmi_features": ibmi_payload,
        }
    )


def _ibmi_features(db: Session, snapshot: CeriScoreSnapshot) -> list[IBIntelligenceFeature]:
    lineage = dict(snapshot.evidence_lineage_json or {})
    return _required_rows(
        db,
        IBIntelligenceFeature,
        {
            *_ints(lineage.get("ib_volatility_feature_ids")),
            *_ints(lineage.get("ib_short_pressure_feature_ids")),
            *_ints(lineage.get("ib_context_selected_feature_ids")),
        },
        "IBMI features",
    )


def referenced_ceri_evidence_ids(db: Session, source_ids: Iterable[int]) -> list[int]:
    """Return sealed CERI evidence that prevents provider payload tombstoning."""

    wanted = {int(value) for value in source_ids}
    if not wanted:
        return []
    if not isinstance(db, Session):
        # Lightweight legacy purge adapters do not model the Phase-2 ledger.
        return []
    evidence_rows = list(
        db.scalars(
            select(CoreCalculationEvidence).where(
                CoreCalculationEvidence.artifact_kind == CoreEvidenceKind.CERI.value
            )
        )
    )
    referenced: list[int] = []
    for evidence in evidence_rows:
        records = (evidence.payload_json or {}).get("source_manifest", {}).get("source_records", [])
        if wanted.intersection(
            int(row["id"]) for row in records if isinstance(row, dict) and row.get("id") is not None
        ):
            referenced.append(int(evidence.id))
    return sorted(referenced)


def _required_rows(
    db: Session,
    model: type,
    ids: Iterable[int],
    label: str,
) -> list[Any]:
    wanted = sorted({int(value) for value in ids})
    if not wanted:
        return []
    rows = list(db.scalars(select(model).where(model.id.in_(wanted)).order_by(model.id)))
    found = {int(row.id) for row in rows}
    missing = sorted(set(wanted) - found)
    if missing:
        raise EvidenceUnavailableError(f"EVIDENCE_UNAVAILABLE: CERI {label} missing ids={missing}")
    return rows


def _rows_where(db: Session, model: type, predicate: Any) -> list[Any]:
    return list(db.scalars(select(model).where(predicate).order_by(model.id)))


def _ints(values: Any) -> set[int]:
    return {int(value) for value in (values or []) if value is not None}


def _payloads(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [_row_payload(row) for row in sorted(rows, key=lambda value: value.id or 0)]


def _row_payload(row: Any, *, excluded: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    values = {
        attribute.key: _normalize_db_datetime(getattr(row, attribute.key))
        for attribute in inspect(row).mapper.column_attrs
        if attribute.key not in {"created_at", "updated_at", *excluded}
    }
    return CanonicalEvidenceSerializer.canonicalize(values)


def _source_record_payload(row: CeriSourceRecord) -> dict[str, Any]:
    payload = _row_payload(row)
    payload.pop("raw_json", None)
    payload.pop("source_url", None)
    return payload


def _normalize_db_datetime(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    if isinstance(value, dict):
        return {key: _normalize_db_datetime(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_db_datetime(item) for item in value]
    return value
