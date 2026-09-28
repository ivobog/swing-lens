from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriFeatureSourceManifest
from app.models.tables import BackgroundJob, ExecutionConfigurationAnchor
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.source_mutation_authority import PrefetchedSourceBodies


def load_feature_source_manifest(
    db: Session, background_job_id: int
) -> CeriFeatureSourceManifest | None:
    return db.scalar(
        select(CeriFeatureSourceManifest).where(
            CeriFeatureSourceManifest.background_job_id == background_job_id
        )
    )


def freeze_or_verify_feature_source_manifest(
    db: Session,
    *,
    job: BackgroundJob,
    source_bodies: PrefetchedSourceBodies,
) -> tuple[CeriFeatureSourceManifest, bool]:
    """Create authority once, or verify a retry against the exact retained authority.

    Creation is flushed in the feature handler's current transaction. The handler
    commits that transaction before it starts the first ticker, so a crash leaves
    either no manifest and no feature output, or one durable immutable manifest.
    """

    if job.id is None:
        raise ValueError("CERI_SOURCE_MANIFEST_JOB_ID_REQUIRED")
    payload = dict(job.payload_json or {})
    authority = source_bodies.durable_manifest()
    anchor = dict(payload.get("effective_configuration_anchor") or {})
    retained_anchor = db.get(ExecutionConfigurationAnchor, anchor.get("anchor_id"))
    if retained_anchor is None or retained_anchor.fingerprint != anchor.get("fingerprint"):
        raise ValueError("CERI_SOURCE_MANIFEST_CONFIGURATION_MISMATCH")
    existing = db.scalar(
        select(CeriFeatureSourceManifest)
        .where(CeriFeatureSourceManifest.background_job_id == job.id)
        .with_for_update()
    )
    if existing is not None:
        _verify_identity(existing, payload, authority)
        source_bodies.verify_durable_manifest(existing.manifest_json)
        return existing, False

    required = {
        "run_id": payload.get("run_id") or job.related_run_id,
        "pipeline_run_id": payload.get("pipeline_run_id"),
        "calculation_context_id": payload.get("calculation_context_id"),
        "cutoff_at": payload.get("cutoff_at"),
        "as_of_session": payload.get("as_of_session"),
        "calendar_version": payload.get("calendar_version"),
        "configuration_anchor_id": anchor.get("anchor_id"),
        "configuration_fingerprint": anchor.get("fingerprint"),
    }
    missing = [key for key, value in required.items() if value in (None, "")]
    if missing:
        raise ValueError("CERI_SOURCE_MANIFEST_AUTHORITY_REQUIRED: " + ", ".join(missing))
    manifest = CeriFeatureSourceManifest(
        background_job_id=job.id,
        run_id=int(required["run_id"]),
        pipeline_run_id=int(required["pipeline_run_id"]),
        calculation_context_id=int(required["calculation_context_id"]),
        batch_index=int(payload.get("batch_index") or 0),
        cutoff_at=_datetime(required["cutoff_at"]),
        as_of_session=_date(required["as_of_session"]),
        calendar_version=str(required["calendar_version"]),
        configuration_anchor_id=str(required["configuration_anchor_id"]),
        configuration_fingerprint=str(required["configuration_fingerprint"]),
        scope_id=_optional_text(payload.get("scope_id") or job.scope_id),
        refresh_cycle_id=_optional_text(payload.get("refresh_cycle_id") or job.refresh_cycle_id),
        acquisition_plan_id=_optional_text(
            payload.get("acquisition_plan_id") or job.acquisition_plan_id
        ),
        bundle_fingerprint=str(authority["bundle_fingerprint"]),
        source_count=int(authority["source_count"]),
        manifest_version=str(authority["manifest_version"]),
        manifest_json=authority,
    )
    db.add(manifest)
    db.flush()
    return manifest, True


def _verify_identity(
    manifest: CeriFeatureSourceManifest,
    payload: dict[str, Any],
    authority: dict[str, Any],
) -> None:
    anchor = dict(payload.get("effective_configuration_anchor") or {})
    expected = {
        "run_id": int(payload.get("run_id") or manifest.run_id),
        "pipeline_run_id": int(payload.get("pipeline_run_id") or manifest.pipeline_run_id),
        "calculation_context_id": int(
            payload.get("calculation_context_id") or manifest.calculation_context_id
        ),
        "batch_index": int(payload.get("batch_index") or 0),
        "cutoff_at": _datetime(payload.get("cutoff_at") or manifest.cutoff_at),
        "as_of_session": _date(payload.get("as_of_session") or manifest.as_of_session),
        "calendar_version": str(payload.get("calendar_version") or manifest.calendar_version),
        "configuration_anchor_id": str(anchor.get("anchor_id") or manifest.configuration_anchor_id),
        "configuration_fingerprint": str(
            anchor.get("fingerprint") or manifest.configuration_fingerprint
        ),
        "bundle_fingerprint": str(authority["bundle_fingerprint"]),
        "source_count": int(authority["source_count"]),
        "manifest_version": str(authority["manifest_version"]),
    }
    observed = {key: getattr(manifest, key) for key in expected}
    if Canonical.fingerprint(observed) != Canonical.fingerprint(expected):
        raise ValueError("CERI_SOURCE_AUTHORITY_CHANGED_BEFORE_RETRY")


def _datetime(value: Any) -> datetime:
    result = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("CERI_SOURCE_MANIFEST_CUTOFF_MUST_BE_AWARE")
    return result


def _date(value: Any) -> date:
    return (
        value
        if isinstance(value, date) and not isinstance(value, datetime)
        else date.fromisoformat(str(value))
    )


def _optional_text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)
