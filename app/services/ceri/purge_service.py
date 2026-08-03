from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriCatalystEventRevision,
    CeriCatalystSource,
    CeriChangeEvent,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriPurgeAudit,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.services.background_job_service import enqueue_job
from app.services.ceri.export_policy import redact_sensitive
from app.services.ceri.observability import ceri_log_event, ceri_metrics

CERI_REBUILD_FEATURES_JOB_TYPE = "CERI_REBUILD_FEATURES"
PURGED_SOURCE_EXPORT_POLICY = "purged"
PURGE_INVALIDATION_FLAG = "provider_license_purge_invalidated"
PURGE_QUARANTINE_PREFIX = "provider_license_purge"


class CeriPurgeError(ValueError):
    pass


@dataclass(frozen=True)
class CeriPurgePreviewRequest:
    provider: str
    license_scope: str
    actor: str
    reason: str
    preview_manifest_hash: str | None = None


@dataclass(frozen=True)
class CeriPurgeExecuteRequest:
    provider: str
    license_scope: str
    actor: str
    reason: str
    confirmation_token: str
    preview_manifest_hash: str


class CeriPurgeService:
    def preview(
        self,
        db: Session,
        request: CeriPurgePreviewRequest,
        *,
        job_id: int | None = None,
        processing_run_id: int | None = None,
    ) -> CeriPurgeAudit:
        _validate_required_request(request)
        manifest = self.preview_manifest(db, request.provider, request.license_scope)
        preview_hash = request.preview_manifest_hash or manifest["preview_manifest_hash"]
        existing = _find_audit(db, preview_hash)
        if existing is not None:
            return existing

        audit = CeriPurgeAudit(
            provider=request.provider,
            license_scope=request.license_scope,
            preview_manifest_hash=preview_hash,
            actor=request.actor,
            reason=request.reason,
            confirmation_token_hash=_confirmation_token_hash(
                confirmation_token_for_preview(preview_hash)
            ),
            affected_counts_json=manifest["affected_counts"],
            invalidated_derivatives_json=manifest["invalidated_derivatives"],
            status="PREVIEWED",
        )
        db.add(audit)
        db.flush()
        ceri_metrics.increment(
            "ceri_purge_previews_total",
            provider=request.provider,
            license_scope=request.license_scope,
        )
        ceri_metrics.increment(
            "ceri_purge_affected_records_total",
            float(manifest["affected_counts"]["source_records"]),
            provider=request.provider,
            license_scope=request.license_scope,
        )
        ceri_log_event(
            "purge_preview",
            job_id=job_id,
            processing_run_id=processing_run_id,
            provider=request.provider,
            affected_counts=manifest["affected_counts"],
            license_scope=request.license_scope,
        )
        return audit

    def execute(
        self,
        db: Session,
        request: CeriPurgeExecuteRequest,
        *,
        job_id: int | None = None,
        processing_run_id: int | None = None,
    ) -> CeriPurgeAudit:
        _validate_required_request(request)
        audit = _find_audit(db, request.preview_manifest_hash)
        if audit is None:
            _record_blocked(request, "preview_missing", job_id, processing_run_id)
            raise CeriPurgeError("Provider-license purge execution requires a prior preview.")
        if audit.provider != request.provider or audit.license_scope != request.license_scope:
            _record_blocked(request, "preview_scope_mismatch", job_id, processing_run_id)
            raise CeriPurgeError(
                "Provider-license purge confirmation scope does not match preview."
            )
        if not audit.confirmation_token_hash:
            _record_blocked(request, "confirmation_unavailable", job_id, processing_run_id)
            raise CeriPurgeError("Provider-license purge preview is missing a confirmation token.")
        if audit.confirmation_token_hash != _confirmation_token_hash(request.confirmation_token):
            _record_blocked(request, "confirmation_mismatch", job_id, processing_run_id)
            raise CeriPurgeError("Provider-license purge confirmation token is invalid.")

        manifest = self._lifecycle_manifest(db, request.provider, request.license_scope)
        lifecycle = _apply_purge_lifecycle(
            manifest,
            preview_manifest_hash=request.preview_manifest_hash,
            audit_id=audit.id,
        )
        rebuild_job_ids = _enqueue_rebuild_jobs(
            db,
            request=request,
            audit_id=audit.id,
            lifecycle=lifecycle,
        )
        audit.status = "EXECUTED"
        audit.executed_at = datetime.now(UTC)
        audit.actor = request.actor
        audit.reason = request.reason
        audit.affected_counts_json = lifecycle["affected_counts"]
        audit.invalidated_derivatives_json = {
            **lifecycle["invalidated_derivatives"],
            "rebuild_job_ids": rebuild_job_ids,
        }
        db.flush()
        ceri_metrics.increment(
            "ceri_purge_executions_total",
            provider=request.provider,
            license_scope=request.license_scope,
        )
        ceri_log_event(
            "purge_executed",
            job_id=job_id,
            processing_run_id=processing_run_id,
            provider=request.provider,
            affected_counts=audit.affected_counts_json or {},
            license_scope=request.license_scope,
        )
        return audit

    def preview_manifest(self, db: Session, provider: str, license_scope: str) -> dict[str, Any]:
        manifest = self._lifecycle_manifest(db, provider, license_scope)
        preview_manifest_hash = _manifest_hash(
            {
                "provider": provider,
                "license_scope": license_scope,
                "affected_counts": manifest["affected_counts"],
                "invalidated_derivatives": manifest["invalidated_derivatives"],
                "source_ids": sorted(manifest["source_ids"]),
            }
        )
        return {
            "preview_manifest_hash": preview_manifest_hash,
            "affected_counts": redact_sensitive(manifest["affected_counts"]),
            "invalidated_derivatives": redact_sensitive(manifest["invalidated_derivatives"]),
        }

    def _lifecycle_manifest(self, db: Session, provider: str, license_scope: str) -> dict[str, Any]:
        sources = [
            source
            for source in _load(db, CeriSourceRecord)
            if source.provider == provider and _source_matches_scope(source, license_scope)
        ]
        source_ids = {source.id for source in sources if source.id is not None}
        estimates = _rows_with_source_ids(db, CeriEstimateSnapshot, source_ids)
        earnings = _rows_with_source_ids(db, CeriEarningsActual, source_ids)
        guidance = _rows_with_source_ids(db, CeriGuidanceEvent, source_ids)
        catalyst_revisions = _rows_with_source_ids(db, CeriCatalystEventRevision, source_ids)
        catalyst_sources = _rows_with_source_ids(db, CeriCatalystSource, source_ids)
        revision_features = [
            feature
            for feature in _load(db, CeriRevisionFeature)
            if source_ids.intersection(set(feature.source_observation_ids_json or []))
        ]
        company_ids = {
            row.company_id
            for row in [*estimates, *earnings, *guidance, *revision_features]
            if getattr(row, "company_id", None) is not None
        }
        score_snapshots = [
            snapshot
            for snapshot in _load(db, CeriScoreSnapshot)
            if snapshot.company_id in company_ids
        ]
        score_snapshot_ids = {
            snapshot.id for snapshot in score_snapshots if snapshot.id is not None
        }
        catalyst_revision_ids = {
            revision.id for revision in catalyst_revisions if revision.id is not None
        }
        change_events = [
            change
            for change in _load(db, CeriChangeEvent)
            if change.company_id in company_ids
            or change.from_snapshot_id in score_snapshot_ids
            or change.to_snapshot_id in score_snapshot_ids
        ]
        change_event_ids = {change.id for change in change_events if change.id is not None}
        alert_events = [
            alert
            for alert in _load(db, CeriAlertEvent)
            if alert.source_change_event_id in change_event_ids
            or alert.source_catalyst_revision_id in catalyst_revision_ids
        ]
        affected_counts = {
            "source_records": len(sources),
            "estimate_snapshots": len(estimates),
            "earnings_actuals": len(earnings),
            "guidance_events": len(guidance),
            "catalyst_revisions": len(catalyst_revisions),
            "catalyst_sources": len(catalyst_sources),
        }
        invalidated_derivatives = {
            "revision_features": len(revision_features),
            "score_snapshots": len(score_snapshots),
            "change_events": len(change_events),
            "alert_events": len(alert_events),
            "requires_rebuild": bool(
                revision_features or score_snapshots or change_events or alert_events
            ),
        }
        return {
            "source_ids": source_ids,
            "sources": sources,
            "estimates": estimates,
            "earnings": earnings,
            "guidance": guidance,
            "catalyst_revisions": catalyst_revisions,
            "catalyst_sources": catalyst_sources,
            "revision_features": revision_features,
            "score_snapshots": score_snapshots,
            "change_events": change_events,
            "alert_events": alert_events,
            "affected_counts": affected_counts,
            "invalidated_derivatives": invalidated_derivatives,
        }


def confirmation_token_for_preview(preview_manifest_hash: str) -> str:
    return f"CONFIRM-{preview_manifest_hash[:12]}"


def _validate_required_request(request: Any) -> None:
    for field in ("provider", "license_scope", "actor", "reason"):
        if not str(getattr(request, field, "") or "").strip():
            raise CeriPurgeError(f"Provider-license purge requires {field}.")


def _record_blocked(
    request: CeriPurgeExecuteRequest,
    reason: str,
    job_id: int | None,
    processing_run_id: int | None,
) -> None:
    ceri_metrics.increment(
        "ceri_purge_blocked_total",
        provider=request.provider,
        license_scope=request.license_scope,
        reason=reason,
    )
    ceri_log_event(
        "purge_blocked",
        job_id=job_id,
        processing_run_id=processing_run_id,
        provider=request.provider,
        license_scope=request.license_scope,
        blocked_reason=reason,
    )


def _find_audit(db: Session, preview_manifest_hash: str) -> CeriPurgeAudit | None:
    scalar = getattr(db, "scalar", None)
    if not callable(scalar):
        return None
    return scalar(
        select(CeriPurgeAudit).where(CeriPurgeAudit.preview_manifest_hash == preview_manifest_hash)
    )


def _rows_with_source_ids(db: Session, model: type, source_ids: set[int]) -> list[Any]:
    return [
        row
        for row in _load(db, model)
        if getattr(row, "source_record_id", None) in source_ids
    ]


def _source_matches_scope(source: CeriSourceRecord, license_scope: str) -> bool:
    scope = license_scope.strip()
    if scope in {"*", "all"}:
        return True
    return scope in {
        source.dataset,
        source.provider_terms_version,
        source.export_policy,
    }


def _manifest_hash(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(manifest, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _apply_purge_lifecycle(
    manifest: dict[str, Any],
    *,
    preview_manifest_hash: str,
    audit_id: int | None,
) -> dict[str, Any]:
    marker = {
        "purged": True,
        "policy": "tombstone_redact_invalidate",
        "preview_manifest_hash": preview_manifest_hash,
        "purge_audit_id": audit_id,
    }
    quarantine_reason = _purge_reason(preview_manifest_hash)
    for source in manifest["sources"]:
        source.raw_json = None
        source.restricted_normalized_json = {
            "purged": True,
            "preview_manifest_hash": preview_manifest_hash,
            "purge_audit_id": audit_id,
        }
        source.source_url = None
        source.source_reference = None
        source.export_policy = PURGED_SOURCE_EXPORT_POLICY
        source.quarantine_reason = _append_text_marker(
            source.quarantine_reason,
            quarantine_reason,
        )

    for estimate in manifest["estimates"]:
        estimate.quality_flags_json = _append_flag(
            estimate.quality_flags_json,
            PURGE_INVALIDATION_FLAG,
        )
        estimate.original_fields_json = _merge_json_object(estimate.original_fields_json, marker)
    for row in [*manifest["earnings"], *manifest["guidance"]]:
        row.quality_warnings_json = _append_flag(
            row.quality_warnings_json,
            PURGE_INVALIDATION_FLAG,
        )
    for revision in manifest["catalyst_revisions"]:
        revision.conflict_flags_json = _append_flag(
            revision.conflict_flags_json,
            PURGE_INVALIDATION_FLAG,
        )
        revision.review_state = "INVALIDATED_BY_PURGE"
    for catalyst_source in manifest["catalyst_sources"]:
        catalyst_source.source_fields_json = _merge_json_object(
            catalyst_source.source_fields_json,
            marker,
        )
    for feature in manifest["revision_features"]:
        feature.warnings_json = _append_flag(feature.warnings_json, PURGE_INVALIDATION_FLAG)
        feature.unavailable_reason = _append_text_marker(
            feature.unavailable_reason,
            quarantine_reason,
        )
        feature.provider_selection_reason = _append_text_marker(
            feature.provider_selection_reason,
            "invalidated_by_provider_license_purge",
        )
    for snapshot in manifest["score_snapshots"]:
        snapshot.warnings_json = _append_flag(snapshot.warnings_json, PURGE_INVALIDATION_FLAG)
        snapshot.data_confidence = "Invalidated"
        snapshot.posture = "Invalidated"
        snapshot.alignment_flags_json = _merge_json_object(snapshot.alignment_flags_json, marker)
    for change in manifest["change_events"]:
        change.severity = "INVALIDATED"
        change.delta_json = _merge_json_object(change.delta_json, marker)
    for alert in manifest["alert_events"]:
        alert.status = "INVALIDATED"
        alert.evidence_json = _merge_json_object(alert.evidence_json, marker)

    return {
        "affected_counts": manifest["affected_counts"],
        "invalidated_derivatives": {
            **manifest["invalidated_derivatives"],
            "policy": "tombstone_redact_invalidate",
            "preview_manifest_hash": preview_manifest_hash,
        },
    }


def _enqueue_rebuild_jobs(
    db: Session,
    *,
    request: CeriPurgeExecuteRequest,
    audit_id: int | None,
    lifecycle: dict[str, Any],
) -> list[int]:
    if not lifecycle["invalidated_derivatives"].get("requires_rebuild"):
        return []
    request_key = f"ceri:rebuild-after-purge:{request.preview_manifest_hash}"
    job = enqueue_job(
        db,
        CERI_REBUILD_FEATURES_JOB_TYPE,
        {
            "request_key": request_key,
            "actor": request.actor,
            "scope": {
                "provider": request.provider,
                "license_scope": request.license_scope,
                "purge_audit_id": audit_id,
                "preview_manifest_hash": request.preview_manifest_hash,
            },
            "reason": "rebuild after CERI provider-license purge",
        },
        request_key=request_key,
        priority=110,
    )
    return [job.id] if job.id is not None else []


def _append_flag(values: list[str] | None, flag: str) -> list[str]:
    current = list(values or [])
    if flag not in current:
        current.append(flag)
    return current


def _merge_json_object(value: dict[str, Any] | None, marker: dict[str, Any]) -> dict[str, Any]:
    return {**(value or {}), "purge_invalidation": marker}


def _append_text_marker(value: str | None, marker: str) -> str:
    if not value:
        return marker
    markers = {part.strip() for part in value.split(";") if part.strip()}
    if marker in markers:
        return value
    return f"{value};{marker}"


def _purge_reason(preview_manifest_hash: str) -> str:
    return f"{PURGE_QUARANTINE_PREFIX}:{preview_manifest_hash[:12]}"


def _confirmation_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load(db: Session, model: type) -> list[Any]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)
