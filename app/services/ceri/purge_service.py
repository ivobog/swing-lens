from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEventRevision,
    CeriCatalystSource,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriPurgeAudit,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.services.ceri.export_policy import redact_sensitive
from app.services.ceri.observability import ceri_log_event, ceri_metrics


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

        audit.status = "EXECUTED"
        audit.executed_at = datetime.now(UTC)
        audit.actor = request.actor
        audit.reason = request.reason
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
            for row in [*estimates, *earnings, *guidance]
            if getattr(row, "company_id", None) is not None
        }
        score_snapshots = [
            snapshot
            for snapshot in _load(db, CeriScoreSnapshot)
            if snapshot.company_id in company_ids
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
            "requires_rebuild": bool(revision_features or score_snapshots),
        }
        preview_manifest_hash = _manifest_hash(
            {
                "provider": provider,
                "license_scope": license_scope,
                "affected_counts": affected_counts,
                "invalidated_derivatives": invalidated_derivatives,
                "source_ids": sorted(source_ids),
            }
        )
        return {
            "preview_manifest_hash": preview_manifest_hash,
            "affected_counts": redact_sensitive(affected_counts),
            "invalidated_derivatives": redact_sensitive(invalidated_derivatives),
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


def _confirmation_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load(db: Session, model: type) -> list[Any]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)
