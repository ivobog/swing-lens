from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
)
from app.services.calculation_identity import CalculationIdentity, IdentityState
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.combined_ranking_identity import calculation_identity_from_debug


class CoreEvidenceKind(StrEnum):
    FUNDAMENTAL = "FUNDAMENTAL"
    TECHNICAL = "TECHNICAL"
    COMBINED = "COMBINED"
    RANKING = "RANKING"
    REGIME = "REGIME"
    SECTOR = "SECTOR"


class EvidenceUnavailableError(LookupError):
    pass


class EvidenceAmbiguousError(LookupError):
    pass


_PAYLOAD_EXCLUDED_COLUMNS = {"id", "evidence_id", "created_at", "updated_at"}
_SCOPE_UNSET = object()


def persist_core_evidence(
    db: Session,
    *,
    kind: CoreEvidenceKind,
    current_row: Any,
    sources: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    scope_ticker: str | None | object = _SCOPE_UNSET,
    scope_profile: str | None | object = _SCOPE_UNSET,
) -> CoreCalculationEvidence | None:
    """Persist/reuse immutable evidence and advance its independent current pointer.

    Rows without a Phase-1 Calculation Identity remain explicit legacy current rows.
    They are never promoted into the evidence ledger.
    """

    identity = calculation_identity_from_debug(getattr(current_row, "debug_json", None))
    if identity is None:
        return None

    source_rows = sources or {}
    source_ids: dict[str, int] = {}
    for role, source in sorted(source_rows.items()):
        evidence_id = getattr(source, "evidence_id", None)
        if evidence_id is None:
            raise EvidenceUnavailableError(
                "EVIDENCE_UNAVAILABLE: "
                f"{kind.value} source role={role} has no immutable evidence"
            )
        source_ids[role] = int(evidence_id)

    payload = CanonicalEvidenceSerializer.canonicalize(
        payload if payload is not None else calculation_evidence_payload(current_row)
    )
    payload_fingerprint = CanonicalEvidenceSerializer.fingerprint(payload)
    identity_fingerprint = str(identity.fingerprint())
    key_payload = {
        "artifact_kind": kind.value,
        "calculation_identity_fingerprint": identity_fingerprint,
        "payload_fingerprint": payload_fingerprint,
        "source_evidence_ids": source_ids,
    }
    evidence_key = CanonicalEvidenceSerializer.fingerprint(key_payload)
    evidence = db.scalar(
        select(CoreCalculationEvidence).where(
            CoreCalculationEvidence.evidence_key == evidence_key
        )
    )
    if evidence is None:
        run_id = getattr(current_row, "run_id", None)
        ticker = (
            getattr(current_row, "ticker", None)
            if scope_ticker is _SCOPE_UNSET
            else scope_ticker
        )
        profile = (
            getattr(current_row, "ranking_profile", None)
            if scope_profile is _SCOPE_UNSET
            else scope_profile
        )
        evidence = CoreCalculationEvidence(
            artifact_kind=kind.value,
            run_id=int(run_id) if run_id is not None else None,
            ticker=_normalized_ticker(ticker),
            ranking_profile=str(profile) if profile is not None else None,
            calculation_identity_fingerprint=identity_fingerprint,
            calculation_identity_json=identity.canonical_payload(),
            payload_fingerprint=payload_fingerprint,
            payload_json=payload,
            source_evidence_ids_json=source_ids,
            evidence_key=evidence_key,
            calculated_at=_calculated_at(identity),
        )
        db.add(evidence)
        db.flush()
        for role, source_evidence_id in source_ids.items():
            db.add(
                CoreCalculationEvidenceSource(
                    evidence_id=evidence.id,
                    source_role=role,
                    source_evidence_id=source_evidence_id,
                )
            )
        if source_ids:
            db.flush()

    current_row.evidence_id = evidence.id
    _advance_current_projection(db, evidence)
    return evidence


def get_current_evidence(
    db: Session,
    *,
    kind: CoreEvidenceKind,
    run_id: int | None,
    ticker: str | None,
    ranking_profile: str | None = None,
) -> CoreCalculationEvidence:
    profile_key = ranking_profile or ""
    evidence = db.scalar(
        select(CoreCalculationEvidence)
        .join(
            CoreCalculationCurrentProjection,
            CoreCalculationCurrentProjection.evidence_id == CoreCalculationEvidence.id,
        )
        .where(
            CoreCalculationCurrentProjection.artifact_kind == kind.value,
            CoreCalculationCurrentProjection.run_id == run_id,
            CoreCalculationCurrentProjection.ticker == _normalized_ticker(ticker),
            CoreCalculationCurrentProjection.ranking_profile_key == profile_key,
        )
    )
    if evidence is None:
        raise EvidenceUnavailableError(
            f"EVIDENCE_UNAVAILABLE: no current {kind.value} evidence for "
            f"run={run_id} ticker={_normalized_ticker(ticker) or '-'} "
            f"profile={profile_key or '-'}"
        )
    return evidence


def get_evidence_for_identity(
    db: Session,
    *,
    kind: CoreEvidenceKind,
    calculation_identity: CalculationIdentity | str,
    ticker: str | None = None,
    ranking_profile: str | None = None,
    payload_fingerprint: str | None = None,
) -> CoreCalculationEvidence:
    """Resolve only immutable evidence; mutable/legacy current rows are never fallback."""

    identity_fingerprint = (
        str(calculation_identity.fingerprint())
        if isinstance(calculation_identity, CalculationIdentity)
        else calculation_identity
    )
    statement = select(CoreCalculationEvidence).where(
        CoreCalculationEvidence.artifact_kind == kind.value,
        CoreCalculationEvidence.calculation_identity_fingerprint == identity_fingerprint,
    )
    if ticker is not None:
        statement = statement.where(
            CoreCalculationEvidence.ticker == ticker.strip().upper()
        )
    if ranking_profile is not None:
        statement = statement.where(
            CoreCalculationEvidence.ranking_profile == ranking_profile
        )
    if payload_fingerprint is not None:
        statement = statement.where(
            CoreCalculationEvidence.payload_fingerprint == payload_fingerprint
        )
    rows = list(db.scalars(statement.order_by(CoreCalculationEvidence.id)))
    if not rows:
        raise EvidenceUnavailableError(
            f"EVIDENCE_UNAVAILABLE: no immutable {kind.value} evidence for identity "
            f"{identity_fingerprint}"
        )
    if len(rows) != 1:
        raise EvidenceAmbiguousError(
            f"EVIDENCE_AMBIGUOUS: identity {identity_fingerprint} resolved {len(rows)} rows; "
            "provide ticker/profile/payload fingerprint"
        )
    return rows[0]


def calculation_evidence_payload(
    row: Any, *, excluded_columns: set[str] | frozenset[str] = frozenset()
) -> dict[str, Any]:
    excluded = _PAYLOAD_EXCLUDED_COLUMNS | set(excluded_columns)
    values = {
        attribute.key: getattr(row, attribute.key)
        for attribute in inspect(row).mapper.column_attrs
        if attribute.key not in excluded
    }
    return CanonicalEvidenceSerializer.canonicalize(values)


def _calculated_at(identity: CalculationIdentity) -> datetime:
    cutoff = identity.temporal.calculation_cutoff
    if cutoff.state is IdentityState.KNOWN and cutoff.value is not None:
        return cutoff.value
    return datetime.now(UTC)


def _advance_current_projection(
    db: Session, evidence: CoreCalculationEvidence
) -> CoreCalculationCurrentProjection:
    profile_key = evidence.ranking_profile or ""
    projection = db.scalar(
        select(CoreCalculationCurrentProjection).where(
            CoreCalculationCurrentProjection.artifact_kind == evidence.artifact_kind,
            CoreCalculationCurrentProjection.run_id == evidence.run_id,
            CoreCalculationCurrentProjection.ticker == evidence.ticker,
            CoreCalculationCurrentProjection.ranking_profile_key == profile_key,
        )
    )
    if projection is None:
        projection = CoreCalculationCurrentProjection(
            artifact_kind=evidence.artifact_kind,
            run_id=evidence.run_id,
            ticker=evidence.ticker,
            ranking_profile_key=profile_key,
            evidence_id=evidence.id,
        )
        db.add(projection)
    else:
        projection.evidence_id = evidence.id
        projection.updated_at = datetime.now(UTC)
    db.flush()
    return projection


def _normalized_ticker(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip().upper()
    return cleaned or None
