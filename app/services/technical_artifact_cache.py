from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import TechnicalFeatureArtifact
from app.services.operational_metrics import operational_metrics

LOCAL_ARTIFACT_KIND = "LOCAL"
ARTIFACT_SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class LocalArtifactKey:
    ticker: str
    timeframe: str
    input_signature: str
    technical_engine_version: str
    scoring_config_hash: str
    input_versions: dict[str, Any]


def canonical_json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)


def config_hash(config: Any) -> str:
    return hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()


def build_local_artifact_key(
    *,
    ticker: str,
    timeframe: str = "1 day",
    adjusted_series_version: int,
    trades_series_version: int,
    indicator_config_hash: str,
    scoring_config_hash: str,
    technical_engine_version: str,
    artifact_schema_version: str = ARTIFACT_SCHEMA_VERSION,
) -> LocalArtifactKey:
    input_versions = {
        "adjusted_series_version": adjusted_series_version,
        "trades_series_version": trades_series_version,
        "indicator_config_hash": indicator_config_hash,
        "scoring_config_hash": scoring_config_hash,
    }
    signature_payload = {
        "ticker": ticker.upper(),
        "timeframe": timeframe,
        **input_versions,
        "technical_engine_version": technical_engine_version,
        "artifact_schema_version": artifact_schema_version,
    }
    signature = hashlib.sha256(canonical_json(signature_payload).encode("utf-8")).hexdigest()
    return LocalArtifactKey(
        ticker=ticker.upper(),
        timeframe=timeframe,
        input_signature=signature,
        technical_engine_version=technical_engine_version,
        scoring_config_hash=scoring_config_hash,
        input_versions=input_versions,
    )


def get_local_artifact(
    db: Session,
    key: LocalArtifactKey,
) -> TechnicalFeatureArtifact | None:
    artifact = db.scalar(
        select(TechnicalFeatureArtifact).where(
            TechnicalFeatureArtifact.ticker == key.ticker,
            TechnicalFeatureArtifact.timeframe == key.timeframe,
            TechnicalFeatureArtifact.artifact_kind == LOCAL_ARTIFACT_KIND,
            TechnicalFeatureArtifact.input_signature == key.input_signature,
        )
    )
    if artifact is None or artifact.status != "READY":
        operational_metrics.increment(
            "swinglens_technical_artifact_cache_total",
            result="miss" if artifact is None else "invalid",
        )
        return None
    artifact.last_used_at = datetime.now(UTC)
    operational_metrics.increment(
        "swinglens_technical_artifact_cache_total",
        result="hit",
    )
    return artifact


def upsert_local_artifact(
    db: Session,
    key: LocalArtifactKey,
    *,
    artifact_json: dict[str, Any],
    warning_flags: list[str] | tuple[str, ...] = (),
    status: str = "READY",
) -> TechnicalFeatureArtifact:
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
            artifact_schema_version=ARTIFACT_SCHEMA_VERSION,
            technical_engine_version=key.technical_engine_version,
            scoring_config_hash=key.scoring_config_hash,
            input_versions_json=key.input_versions,
            artifact_json=artifact_json,
            status=status,
            warning_flags_json=list(warning_flags),
            last_used_at=now,
        )
        db.add(artifact)
    else:
        artifact.artifact_json = artifact_json
        artifact.status = status
        artifact.warning_flags_json = list(warning_flags)
        artifact.last_used_at = now
    return artifact
