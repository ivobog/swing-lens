from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models.ib_market_intelligence_tables import (
    IBHistogramBin,
    IBHistogramSnapshot,
    IBHistoricalMetricBar,
    IBHistoricalMetricRevision,
    IBIntelligenceFeature,
    IBIntelligenceRequestItem,
    IBMarketIntelligenceSnapshot,
)
from app.models.tables import CoreCalculationEvidence, PriceBar, PriceBarRevision
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.contextual_calculation_identity import build_ibmi_feature_identity
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    persist_core_evidence,
)
from app.services.ib_market_intelligence.config import IBMarketIntelligenceConfig

IBMI_FEATURE_EVIDENCE_SCHEMA_VERSION = "ibmi-feature-evidence-v1"


@dataclass(frozen=True)
class IbmiFeatureConstituents:
    """Exact, already-selected source facts consumed by one IBMI calculation."""

    metric_bars: tuple[IBHistoricalMetricBar, ...] = ()
    live_observations: tuple[IBMarketIntelligenceSnapshot, ...] = ()
    availability_observations: tuple[IBIntelligenceRequestItem, ...] = ()
    price_bars: tuple[PriceBar, ...] = ()
    price_roles: tuple[tuple[int, str], ...] = ()
    price_basis: tuple[tuple[str, str], ...] = ()
    histogram_snapshot: IBHistogramSnapshot | None = None
    histogram_bins: tuple[IBHistogramBin, ...] = ()


def build_ibmi_constituent_manifest(
    db: Session,
    constituents: IbmiFeatureConstituents,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Freeze source states without resolving mutable/latest rows later."""

    issues: list[str] = []
    metric_states: list[dict[str, Any]] = []
    for bar in sorted(
        constituents.metric_bars,
        key=lambda row: (row.metric_type, row.effective_session, int(row.id or 0)),
    ):
        revision = db.scalar(
            select(IBHistoricalMetricRevision).where(
                IBHistoricalMetricRevision.metric_bar_id == bar.id,
                IBHistoricalMetricRevision.revision_number == bar.revision_count,
                IBHistoricalMetricRevision.new_data_hash == bar.data_hash,
            )
        )
        if revision is None:
            issues.append(
                "LEGACY_UNKNOWN_METRIC_STATE:"
                f"bar={bar.id}:revision={bar.revision_count}:hash={bar.data_hash}"
            )
            continue
        metric_states.append(
            {
                "metric_bar_id": int(bar.id),
                "metric_revision_id": int(revision.id),
                "revision_number": int(bar.revision_count),
                "data_hash": bar.data_hash,
                "state": _row_payload(
                    bar,
                    excluded={
                        "id",
                        "intelligence_run_id",
                        "data_hash",
                        "revision_count",
                        "last_seen_at",
                        "revised_at",
                    },
                ),
            }
        )

    role_map: dict[int, list[str]] = {}
    for bar_id, role in constituents.price_roles:
        role_map.setdefault(int(bar_id), []).append(str(role))
    price_states = [
        _price_state(db, bar, roles=sorted(set(role_map.get(int(bar.id or 0), []))))
        for bar in sorted(
            constituents.price_bars,
            key=lambda row: (row.bar_date, row.what_to_show, int(row.id or 0)),
        )
    ]
    price_series = {
        "basis": dict(constituents.price_basis),
        "states": price_states,
    }
    price_series["canonical_series_fingerprint"] = CanonicalEvidenceSerializer.fingerprint(
        price_series
    )

    histogram = None
    if constituents.histogram_snapshot is not None:
        histogram = {
            "snapshot": _row_payload(
                constituents.histogram_snapshot, excluded={"intelligence_run_id"}
            ),
            "bins": [_row_payload(row) for row in constituents.histogram_bins],
        }

    manifest = CanonicalEvidenceSerializer.canonicalize(
        {
            "historical_metric_states": metric_states,
            "live_observations": [
                _row_payload(row, excluded={"intelligence_run_id"})
                for row in sorted(
                    constituents.live_observations,
                    key=lambda row: (row.observed_at, int(row.id or 0)),
                )
            ],
            "availability_observations": [
                _row_payload(row, excluded={"intelligence_run_id"})
                for row in sorted(
                    constituents.availability_observations,
                    key=lambda row: (row.completed_at or row.started_at, int(row.id or 0)),
                )
            ],
            "price_series": price_series,
            "histogram": histogram,
        }
    )
    return manifest, tuple(sorted(issues))


def constituent_fingerprint(manifest: dict[str, Any]) -> str:
    return CanonicalEvidenceSerializer.fingerprint(manifest)


def persist_ibmi_feature_evidence(
    db: Session,
    *,
    feature: IBIntelligenceFeature,
    config: IBMarketIntelligenceConfig,
    constituent_manifest: dict[str, Any],
) -> CoreCalculationEvidence:
    identity = build_ibmi_feature_identity(feature)
    payload = {
        "schema_version": IBMI_FEATURE_EVIDENCE_SCHEMA_VERSION,
        "derived_output": _row_payload(feature, excluded={"id", "evidence_id"}),
        "constituent_manifest": constituent_manifest,
        "constituent_fingerprint": constituent_fingerprint(constituent_manifest),
        "effective_configuration": {
            "config_hash": config.config_hash,
            "config_version": config.config_version,
            "calculation_version": config.calculation_version,
            "source_version": config.source_version,
            "resolved_config": config.raw,
        },
    }
    evidence = persist_core_evidence(
        db,
        kind=CoreEvidenceKind.IBMI,
        current_row=feature,
        payload=payload,
        scope_ticker=feature.ticker,
        scope_profile=feature.module,
        calculation_identity=identity,
    )
    if evidence is None:  # pragma: no cover - an explicit identity is supplied above
        raise EvidenceUnavailableError("EVIDENCE_UNAVAILABLE: IBMI Calculation Identity absent")
    return evidence


def get_certified_ibmi_evidence(
    db: Session,
    feature: IBIntelligenceFeature,
) -> CoreCalculationEvidence:
    """Resolve only the feature's pinned immutable envelope, never current/latest."""

    if feature.evidence_id is None:
        raise EvidenceUnavailableError(
            f"EVIDENCE_UNAVAILABLE: IBMI feature id={feature.id} is LEGACY_CURRENT/LEGACY_UNKNOWN"
        )
    evidence = get_ibmi_evidence(db, int(feature.evidence_id))
    if evidence.ticker != feature.ticker.upper() or evidence.ranking_profile != feature.module:
        raise EvidenceUnavailableError(
            f"EVIDENCE_UNAVAILABLE: IBMI feature id={feature.id} scope/evidence mismatch"
        )
    identity = build_ibmi_feature_identity(feature)
    if evidence.calculation_identity_fingerprint != str(identity.fingerprint()):
        raise EvidenceUnavailableError(
            f"EVIDENCE_UNAVAILABLE: IBMI feature id={feature.id} identity/evidence mismatch"
        )
    return evidence


def get_ibmi_evidence(db: Session, evidence_id: int) -> CoreCalculationEvidence:
    """Read one historical IBMI envelope by its immutable ID only."""

    evidence = db.get(CoreCalculationEvidence, int(evidence_id))
    if evidence is None or evidence.artifact_kind != CoreEvidenceKind.IBMI.value:
        raise EvidenceUnavailableError(
            f"EVIDENCE_UNAVAILABLE: no immutable IBMI evidence id={evidence_id}"
        )
    return evidence


def _price_state(db: Session, bar: PriceBar, *, roles: list[str]) -> dict[str, Any]:
    revision = db.scalar(
        select(PriceBarRevision).where(
            PriceBarRevision.price_bar_id == bar.id,
            PriceBarRevision.revision_number == bar.revision_count,
            PriceBarRevision.new_data_hash == bar.data_hash,
        )
    )
    return {
        "price_bar_id": int(bar.id),
        "revision_number": int(bar.revision_count),
        "price_bar_revision_id": int(revision.id) if revision is not None else None,
        "data_hash": bar.data_hash,
        "roles": roles,
        "state": _row_payload(
            bar,
            excluded={"created_at", "first_seen_at", "last_seen_at", "revised_at"},
        ),
    }


def _row_payload(row: Any, *, excluded: set[str] = frozenset()) -> dict[str, Any]:
    return CanonicalEvidenceSerializer.canonicalize(
        {
            attribute.key: getattr(row, attribute.key)
            for attribute in inspect(row).mapper.column_attrs
            if attribute.key not in excluded
        }
    )
