from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import TechnicalFeatureArtifact
from app.observability.transaction_metrics import publish_after_commit
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.core_mutation_authority import core_writer_transaction
from app.services.domain_write_fence import (
    assert_current_execution_ownership,
    current_domain_write_ownership,
)
from app.services.operational_metrics import operational_metrics
from app.services.redaction import redact_sensitive, redact_text

LOCAL_ARTIFACT_KIND = "LOCAL"
ARTIFACT_SCHEMA_VERSION = "2-temporal"
SHADOW_UNVALIDATED = "UNVALIDATED"
SHADOW_MATCH = "MATCH"
SHADOW_MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class LocalArtifactKey:
    ticker: str
    timeframe: str
    input_signature: str
    artifact_schema_version: str
    technical_engine_version: str
    feature_config_hash: str
    scoring_config_hash: str
    input_versions: dict[str, Any]


def canonical_json(value: Any) -> str:
    return CanonicalEvidenceSerializer.dumps(value)


def config_hash(config: Any) -> str:
    return CanonicalEvidenceSerializer.fingerprint(config)


def build_local_artifact_key(
    *,
    ticker: str,
    timeframe: str = "1 day",
    adjusted_series_version: int,
    trades_series_version: int,
    feature_config_hash: str,
    scoring_config_hash: str,
    technical_engine_version: str,
    input_as_of_session: date | None = None,
    calculation_cutoff_at: datetime | None = None,
    source_manifest_hash: str | None = None,
    artifact_schema_version: str = ARTIFACT_SCHEMA_VERSION,
) -> LocalArtifactKey:
    input_versions = {
        "adjusted_series_version": adjusted_series_version,
        "trades_series_version": trades_series_version,
        "feature_config_hash": feature_config_hash,
        "input_as_of_session": (
            input_as_of_session.isoformat() if input_as_of_session is not None else None
        ),
    }
    if calculation_cutoff_at is not None or source_manifest_hash is not None:
        input_versions["calculation_cutoff_at"] = CanonicalEvidenceSerializer.canonicalize(
            calculation_cutoff_at
        )
        input_versions["source_manifest_hash"] = source_manifest_hash
    signature_payload = {
        "ticker": ticker.upper(),
        "timeframe": timeframe,
        **input_versions,
        "technical_engine_version": technical_engine_version,
        "artifact_schema_version": artifact_schema_version,
    }
    signature = CanonicalEvidenceSerializer.fingerprint(signature_payload)
    return LocalArtifactKey(
        ticker=ticker.upper(),
        timeframe=timeframe,
        input_signature=signature,
        artifact_schema_version=artifact_schema_version,
        technical_engine_version=technical_engine_version,
        feature_config_hash=feature_config_hash,
        scoring_config_hash=scoring_config_hash,
        input_versions=input_versions,
    )


def _fence_cache_attempt(db: Session) -> None:
    """Native cache authority retains its supplied durable attempt through commit."""
    ownership = current_domain_write_ownership()
    if ownership is not None:
        assert_current_execution_ownership(
            db, job_id=ownership.job_id, execution_token=ownership.execution_token
        )


@core_writer_transaction
def get_local_artifact(
    db: Session,
    key: LocalArtifactKey,
    *,
    usage: Literal["active", "shadow"] = "active",
) -> TechnicalFeatureArtifact | None:
    _fence_cache_attempt(db)
    artifact = db.scalar(
        select(TechnicalFeatureArtifact).where(
            TechnicalFeatureArtifact.ticker == key.ticker,
            TechnicalFeatureArtifact.timeframe == key.timeframe,
            TechnicalFeatureArtifact.artifact_kind == LOCAL_ARTIFACT_KIND,
            TechnicalFeatureArtifact.input_signature == key.input_signature,
        )
    )
    if artifact is None:
        operational_metrics.increment(
            "swinglens_technical_artifact_cache_total",
            result="miss" if usage == "active" else "shadow_miss",
            reason="not_found",
        )
        return None
    if not _artifact_matches_key(artifact, key):
        return None
    if artifact.status != "READY" or (
        usage == "active" and artifact.shadow_validation_status != SHADOW_MATCH
    ):
        operational_metrics.increment(
            "swinglens_technical_artifact_cache_total",
            result="invalid",
            reason=("artifact_status" if artifact.status != "READY" else "shadow_not_certified"),
        )
        return None
    artifact.last_used_at = datetime.now(UTC)
    operational_metrics.increment(
        "swinglens_technical_artifact_cache_total",
        result="hit" if usage == "active" else "shadow_candidate",
        reason="certified" if usage == "active" else "candidate",
    )
    return artifact


@core_writer_transaction
def record_local_artifact_shadow_validation(
    db: Session,
    key: LocalArtifactKey,
    *,
    matched: bool,
    fresh_fingerprint: str,
    cached_fingerprint: str | None,
    run_id: int,
    error: str | None = None,
    differences: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> TechnicalFeatureArtifact:
    _fence_cache_attempt(db)
    if matched and (
        not fresh_fingerprint or fresh_fingerprint != cached_fingerprint or error is not None
    ):
        raise ValueError("TECHNICAL_CACHE_SHADOW_PROOF_MISMATCH")
    artifact = db.scalar(
        select(TechnicalFeatureArtifact)
        .where(
            TechnicalFeatureArtifact.ticker == key.ticker,
            TechnicalFeatureArtifact.timeframe == key.timeframe,
            TechnicalFeatureArtifact.artifact_kind == LOCAL_ARTIFACT_KIND,
            TechnicalFeatureArtifact.input_signature == key.input_signature,
        )
        .with_for_update()
    )
    if artifact is None:
        raise RuntimeError("shadow validation artifact was not persisted")
    if not _artifact_matches_key(artifact, key):
        raise ValueError("TECHNICAL_CACHE_AUTHORITY_MISMATCH")
    artifact.shadow_validation_count = (artifact.shadow_validation_count or 0) + 1
    artifact.last_shadow_validated_at = now or datetime.now(UTC)
    artifact.shadow_validation_status = SHADOW_MATCH if matched else SHADOW_MISMATCH
    publish_after_commit(
        db,
        "increment",
        "swinglens_technical_artifact_cache_shadow_validations_total",
        result="match" if matched else "mismatch",
    )
    if not matched:
        artifact.shadow_mismatch_count = (artifact.shadow_mismatch_count or 0) + 1
        artifact.last_shadow_mismatch_json = {
            "run_id": run_id,
            "ticker": key.ticker,
            "input_signature": key.input_signature,
            "fresh_fingerprint": fresh_fingerprint,
            "cached_fingerprint": cached_fingerprint,
            "error": redact_text(error) if error else None,
            "differences": redact_sensitive(differences or {}),
        }
        publish_after_commit(
            db, "increment", "swinglens_technical_artifact_cache_shadow_mismatches_total"
        )
    return artifact


@core_writer_transaction
def upsert_local_artifact(
    db: Session,
    key: LocalArtifactKey,
    *,
    artifact_json: dict[str, Any],
    warning_flags: list[str] | tuple[str, ...] = (),
    status: str = "READY",
) -> TechnicalFeatureArtifact:
    _fence_cache_attempt(db)
    expected = build_local_artifact_key(
        ticker=key.ticker,
        timeframe=key.timeframe,
        adjusted_series_version=key.input_versions["adjusted_series_version"],
        trades_series_version=key.input_versions["trades_series_version"],
        feature_config_hash=key.feature_config_hash,
        scoring_config_hash=key.scoring_config_hash,
        technical_engine_version=key.technical_engine_version,
        input_as_of_session=(
            date.fromisoformat(key.input_versions["input_as_of_session"])
            if key.input_versions["input_as_of_session"]
            else None
        ),
        calculation_cutoff_at=(
            datetime.fromisoformat(key.input_versions["calculation_cutoff_at"])
            if key.input_versions.get("calculation_cutoff_at")
            else None
        ),
        source_manifest_hash=key.input_versions.get("source_manifest_hash"),
        artifact_schema_version=key.artifact_schema_version,
    )
    if expected != key:
        raise ValueError("TECHNICAL_CACHE_KEY_FINGERPRINT_MISMATCH")
    artifact_json = dict(artifact_json)
    artifact_json.pop("_source_authority", None)
    artifact_json["_source_authority"] = {
        "input_signature": key.input_signature,
        "payload_fingerprint": CanonicalEvidenceSerializer.fingerprint(artifact_json),
    }
    artifact = db.scalar(
        select(TechnicalFeatureArtifact).where(
            TechnicalFeatureArtifact.ticker == key.ticker,
            TechnicalFeatureArtifact.timeframe == key.timeframe,
            TechnicalFeatureArtifact.artifact_kind == LOCAL_ARTIFACT_KIND,
            TechnicalFeatureArtifact.input_signature == key.input_signature,
        )
    )
    now = datetime.now(UTC)
    if artifact is None:
        artifact = TechnicalFeatureArtifact(
            ticker=key.ticker,
            timeframe=key.timeframe,
            artifact_kind=LOCAL_ARTIFACT_KIND,
            input_signature=key.input_signature,
            artifact_schema_version=key.artifact_schema_version,
            technical_engine_version=key.technical_engine_version,
            feature_config_hash=key.feature_config_hash,
            scoring_config_hash=key.scoring_config_hash,
            input_versions_json=key.input_versions,
            artifact_json=artifact_json,
            status=status,
            warning_flags_json=list(warning_flags),
            last_used_at=now,
        )
        db.add(artifact)
    else:
        if not _artifact_matches_key(artifact, key):
            raise ValueError("TECHNICAL_CACHE_AUTHORITY_MISMATCH")
        if artifact.artifact_json != artifact_json:
            # A shadow match certifies one payload, not every later value stored
            # under the same native key.
            artifact.shadow_validation_status = SHADOW_UNVALIDATED
        artifact.artifact_json = artifact_json
        artifact.status = status
        artifact.warning_flags_json = list(warning_flags)
        artifact.last_used_at = now
    return artifact


def _artifact_matches_key(artifact, key):
    payload = dict(artifact.artifact_json or {})
    authority = payload.pop("_source_authority", {})
    return (
        artifact.artifact_schema_version == key.artifact_schema_version
        and artifact.technical_engine_version == key.technical_engine_version
        and artifact.feature_config_hash == key.feature_config_hash
        and artifact.input_versions_json == key.input_versions
        and authority.get("input_signature") == key.input_signature
        and authority.get("payload_fingerprint") == CanonicalEvidenceSerializer.fingerprint(payload)
    )
