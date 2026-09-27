from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriCatalystEventRevision,
    CeriCatalystSource,
    CeriChangeEvent,
    CeriDerivedFeature,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriPriceResponseFeature,
    CeriPurgeAudit,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.services.background_job_service import enqueue_job
from app.services.ceri.export_policy import redact_sensitive
from app.services.ceri.observability import ceri_log_event, ceri_metrics
from app.services.domain_mutation import MutationDomain, MutationSemanticMode
from app.services.historical_authority_retention import (
    PurgeDisposition,
    RetainedAuthority,
    RetentionClass,
    assess_authority_purge,
)
from app.services.source_mutation_authority import source_mutation_writer, source_writer_member

CERI_REBUILD_FEATURES_JOB_TYPE = "CERI_REBUILD_FEATURES"
PURGED_SOURCE_EXPORT_POLICY = "purged"
PURGE_INVALIDATION_FLAG = "provider_license_purge_invalidated"
PURGE_QUARANTINE_PREFIX = "provider_license_purge"
LICENSE_PURGE_BATCH_SIZE = 200


@dataclass(frozen=True)
class PurgeSourceIdentity:
    id: int
    provider_record_id: str
    content_hash: str
    normalized_hash: str | None


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
        calculated_hash = manifest["preview_manifest_hash"]
        if request.preview_manifest_hash and request.preview_manifest_hash != calculated_hash:
            raise CeriPurgeError("Supplied purge manifest hash does not match the current preview.")
        preview_hash = calculated_hash
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
            session=db,
            provider=request.provider,
            license_scope=request.license_scope,
        )
        ceri_metrics.increment(
            "ceri_purge_affected_records_total",
            float(manifest["affected_counts"]["source_records"]),
            session=db,
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

    @source_mutation_writer(
        MutationDomain.CERI_SOURCE, "provider_source", mode=MutationSemanticMode.MAINTENANCE
    )
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
            _record_blocked(db, request, "preview_missing", job_id, processing_run_id)
            raise CeriPurgeError("Provider-license purge execution requires a prior preview.")
        if audit.provider != request.provider or audit.license_scope != request.license_scope:
            _record_blocked(db, request, "preview_scope_mismatch", job_id, processing_run_id)
            raise CeriPurgeError(
                "Provider-license purge confirmation scope does not match preview."
            )
        if not audit.confirmation_token_hash:
            _record_blocked(db, request, "confirmation_unavailable", job_id, processing_run_id)
            raise CeriPurgeError("Provider-license purge preview is missing a confirmation token.")
        if audit.confirmation_token_hash != _confirmation_token_hash(request.confirmation_token):
            _record_blocked(db, request, "confirmation_mismatch", job_id, processing_run_id)
            raise CeriPurgeError("Provider-license purge confirmation token is invalid.")

        manifest = self._lifecycle_manifest(db, request.provider, request.license_scope)
        current_hash = _manifest_hash(
            _manifest_hash_input(manifest, request.provider, request.license_scope)
        )
        if current_hash != request.preview_manifest_hash:
            _record_blocked(db, request, "preview_manifest_changed", job_id, processing_run_id)
            raise CeriPurgeError(
                "Provider-license purge preview no longer matches the eligible evidence set."
            )
        certified_evidence_ids = manifest["certified_decision_evidence_ids"]
        retention = assess_authority_purge(
            (
                RetainedAuthority(
                    authority_type="CERI_SOURCE_EVIDENCE",
                    semantic_id=f"ceri-source:{source.id}",
                    content_fingerprint=source.content_hash,
                    material_to_reconstruction=True,
                    retention_class=RetentionClass.IMMUTABLE_AUTHORITY,
                    pinned_by=tuple(
                        f"core-calculation-evidence:{evidence_id}"
                        for evidence_id in certified_evidence_ids
                    ),
                )
                for source in manifest["sources"]
            ),
            # Unpinned licensed material may be purged only because the
            # lifecycle below marks every dependent derivative unavailable.
            explicit_reconstruction_downgrade=True,
        )
        if retention.disposition is PurgeDisposition.BLOCK_MATERIAL_AUTHORITY:
            _record_blocked(
                db,
                request,
                "immutable_decision_evidence_referenced",
                job_id,
                processing_run_id,
            )
            raise CeriPurgeError(
                "Provider-license purge is forbidden while source records are referenced "
                f"by immutable CERI decision evidence ids={certified_evidence_ids}."
            )
        lifecycle = _apply_purge_lifecycle(
            db,
            manifest,
            preview_manifest_hash=request.preview_manifest_hash,
            audit_id=audit.id,
        )
        lifecycle["invalidated_derivatives"]["historical_authority_retention"] = {
            "disposition": retention.disposition.value,
            "resulting_availability": retention.resulting_availability.value,
            "downgraded_authority_ids": list(retention.downgraded_authority_ids),
        }
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
            session=db,
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
            _manifest_hash_input(manifest, provider, license_scope)
        )
        return {
            "preview_manifest_hash": preview_manifest_hash,
            "affected_counts": redact_sensitive(manifest["affected_counts"]),
            "invalidated_derivatives": redact_sensitive(manifest["invalidated_derivatives"]),
        }

    def _lifecycle_manifest(self, db: Session, provider: str, license_scope: str) -> dict[str, Any]:
        if isinstance(db, Session):
            return _sql_lifecycle_manifest(db, provider=provider, license_scope=license_scope)
        sources = [
            source
            for source in _fixture_rows(db, CeriSourceRecord)
            if source.provider == provider and _source_matches_scope(source, license_scope)
        ]
        source_ids = {source.id for source in sources if source.id is not None}
        from app.services.ceri.decision_evidence import referenced_ceri_evidence_ids

        certified_decision_evidence_ids = referenced_ceri_evidence_ids(db, source_ids)
        estimates = _rows_with_source_ids(db, CeriEstimateSnapshot, source_ids)
        earnings = _rows_with_source_ids(db, CeriEarningsActual, source_ids)
        guidance = _rows_with_source_ids(db, CeriGuidanceEvent, source_ids)
        catalyst_revisions = _rows_with_source_ids(db, CeriCatalystEventRevision, source_ids)
        catalyst_sources = _rows_with_source_ids(db, CeriCatalystSource, source_ids)
        revision_features = [
            feature
            for feature in _fixture_rows(db, CeriRevisionFeature)
            if source_ids.intersection(set(feature.source_observation_ids_json or []))
        ]
        derived_features = [
            feature
            for feature in _fixture_rows(db, CeriDerivedFeature)
            if source_ids.intersection(set(feature.source_ids_json or []))
        ]
        normalized_ids = {
            row.id for row in [*estimates, *earnings, *guidance, *catalyst_revisions] if row.id
        }
        price_response_features = [
            feature
            for feature in _fixture_rows(db, CeriPriceResponseFeature)
            if feature.event_id in normalized_ids
        ]
        affected_source_ids = source_ids
        score_snapshots = [
            snapshot
            for snapshot in _fixture_rows(db, CeriScoreSnapshot)
            if affected_source_ids.intersection(_snapshot_source_ids(snapshot))
        ]
        score_snapshot_ids = {
            snapshot.id for snapshot in score_snapshots if snapshot.id is not None
        }
        catalyst_revision_ids = {
            revision.id for revision in catalyst_revisions if revision.id is not None
        }
        change_events = [
            change
            for change in _fixture_rows(db, CeriChangeEvent)
            if change.from_snapshot_id in score_snapshot_ids
            or change.to_snapshot_id in score_snapshot_ids
            or change.catalyst_revision_id in catalyst_revision_ids
        ]
        change_event_ids = {change.id for change in change_events if change.id is not None}
        alert_events = [
            alert
            for alert in _fixture_rows(db, CeriAlertEvent)
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
            "derived_features": len(derived_features),
            "price_response_features": len(price_response_features),
        }
        invalidated_derivatives = {
            "revision_features": len(revision_features),
            "derived_features": len(derived_features),
            "price_response_features": len(price_response_features),
            "score_snapshots": len(score_snapshots),
            "change_events": len(change_events),
            "alert_events": len(alert_events),
            "requires_rebuild": bool(
                revision_features or score_snapshots or change_events or alert_events
            ),
            "immutable_decision_evidence_ids": certified_decision_evidence_ids,
            "purge_blocked_by_immutable_decision_evidence": bool(certified_decision_evidence_ids),
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
            "derived_features": derived_features,
            "price_response_features": price_response_features,
            "score_snapshots": score_snapshots,
            "change_events": change_events,
            "alert_events": alert_events,
            "certified_decision_evidence_ids": certified_decision_evidence_ids,
            "affected_counts": affected_counts,
            "invalidated_derivatives": invalidated_derivatives,
        }


def _sql_lifecycle_manifest(
    db: Session,
    *,
    provider: str,
    license_scope: str,
) -> dict[str, Any]:
    """Build the legal purge set using SQL predicates and ID-only projections."""
    source_scope = (
        (CeriSourceRecord.provider == provider),
        (CeriSourceRecord.license_scope == license_scope.strip()),
        CeriSourceRecord.purge_eligible.is_(True),
    )
    source_rows = db.execute(
        select(
            CeriSourceRecord.id,
            CeriSourceRecord.provider_record_id,
            CeriSourceRecord.content_hash,
            CeriSourceRecord.normalized_hash,
        )
        .where(*source_scope)
        .order_by(CeriSourceRecord.id)
    ).all()
    sources = [
        PurgeSourceIdentity(
            id=int(row.id),
            provider_record_id=row.provider_record_id,
            content_hash=row.content_hash,
            normalized_hash=row.normalized_hash,
        )
        for row in source_rows
    ]
    source_ids = [source.id for source in sources]
    source_id_query = select(CeriSourceRecord.id).where(*source_scope)

    def source_fk_ids(model: type) -> list[int]:
        return _selected_ids(
            db,
            select(model.id)
            .where(model.source_record_id.in_(source_id_query))
            .order_by(model.id),
        )

    ids: dict[str, list[int]] = {
        "estimates": source_fk_ids(CeriEstimateSnapshot),
        "earnings": source_fk_ids(CeriEarningsActual),
        "guidance": source_fk_ids(CeriGuidanceEvent),
        "catalyst_revisions": source_fk_ids(CeriCatalystEventRevision),
        "catalyst_sources": source_fk_ids(CeriCatalystSource),
    }
    ids["revision_features"] = _selected_ids(
        db,
        select(CeriRevisionFeature.id)
        .where(
            _json_array_references_source(
                CeriRevisionFeature.source_observation_ids_json,
                source_id_query,
                "purge_revision_sources",
            )
        )
        .order_by(CeriRevisionFeature.id),
    )
    ids["derived_features"] = _selected_ids(
        db,
        select(CeriDerivedFeature.id)
        .where(
            _json_array_references_source(
                CeriDerivedFeature.source_ids_json,
                source_id_query,
                "purge_derived_sources",
            )
        )
        .order_by(CeriDerivedFeature.id),
    )
    normalized_ids = sorted(
        {
            *ids["estimates"],
            *ids["earnings"],
            *ids["guidance"],
            *ids["catalyst_revisions"],
        }
    )
    ids["price_response_features"] = _selected_ids_for_values(
        db,
        CeriPriceResponseFeature,
        CeriPriceResponseFeature.event_id,
        normalized_ids,
    )
    snapshot_predicates = [
        _json_array_references_source(
            CeriScoreSnapshot.component_json["source_ids"],
            source_id_query,
            "purge_snapshot_component_sources",
        )
    ]
    for index, key in enumerate(
        (
            "revision_source_ids",
            "earnings_source_ids",
            "guidance_source_ids",
            "catalyst_source_ids",
        )
    ):
        snapshot_predicates.append(
            _json_array_references_source(
                CeriScoreSnapshot.evidence_lineage_json[key],
                source_id_query,
                f"purge_snapshot_lineage_sources_{index}",
            )
        )
    ids["score_snapshots"] = _selected_ids(
        db,
        select(CeriScoreSnapshot.id)
        .where(or_(*snapshot_predicates))
        .order_by(CeriScoreSnapshot.id),
    )
    score_ids = ids["score_snapshots"]
    catalyst_revision_ids = ids["catalyst_revisions"]
    ids["change_events"] = _selected_ids_for_change_scope(
        db,
        score_ids=score_ids,
        catalyst_revision_ids=catalyst_revision_ids,
    )
    ids["alert_events"] = _selected_ids_for_alert_scope(
        db,
        change_ids=ids["change_events"],
        catalyst_revision_ids=catalyst_revision_ids,
    )

    from app.services.ceri.decision_evidence import referenced_ceri_evidence_ids

    certified_decision_evidence_ids = referenced_ceri_evidence_ids(db, source_ids)
    affected_counts = {
        "source_records": len(sources),
        "estimate_snapshots": len(ids["estimates"]),
        "earnings_actuals": len(ids["earnings"]),
        "guidance_events": len(ids["guidance"]),
        "catalyst_revisions": len(ids["catalyst_revisions"]),
        "catalyst_sources": len(ids["catalyst_sources"]),
        "derived_features": len(ids["derived_features"]),
        "price_response_features": len(ids["price_response_features"]),
    }
    invalidated_derivatives = {
        "revision_features": len(ids["revision_features"]),
        "derived_features": len(ids["derived_features"]),
        "price_response_features": len(ids["price_response_features"]),
        "score_snapshots": len(ids["score_snapshots"]),
        "change_events": len(ids["change_events"]),
        "alert_events": len(ids["alert_events"]),
        "requires_rebuild": bool(
            ids["revision_features"]
            or ids["score_snapshots"]
            or ids["change_events"]
            or ids["alert_events"]
        ),
        "immutable_decision_evidence_ids": certified_decision_evidence_ids,
        "purge_blocked_by_immutable_decision_evidence": bool(
            certified_decision_evidence_ids
        ),
    }
    return {
        "source_ids": source_ids,
        "sources": sources,
        **ids,
        "certified_decision_evidence_ids": certified_decision_evidence_ids,
        "affected_counts": affected_counts,
        "invalidated_derivatives": invalidated_derivatives,
    }


def _selected_ids(db: Session, statement) -> list[int]:
    return [int(value) for value in db.scalars(statement)]


def _json_array_references_source(column, source_id_query, alias_name: str):
    values = func.jsonb_array_elements_text(column).table_valued("value").alias(alias_name)
    return exists(
        select(1)
        .select_from(values)
        .where(cast(values.c.value, BigInteger).in_(source_id_query))
    )


def _selected_ids_for_values(
    db: Session,
    model: type,
    column,
    values: list[int],
) -> list[int]:
    selected: set[int] = set()
    for batch in _batches(values, LICENSE_PURGE_BATCH_SIZE):
        selected.update(
            _selected_ids(
                db,
                select(model.id).where(column.in_(batch)).order_by(model.id),
            )
        )
    return sorted(selected)


def _selected_ids_for_change_scope(
    db: Session,
    *,
    score_ids: list[int],
    catalyst_revision_ids: list[int],
) -> list[int]:
    selected: set[int] = set()
    for batch in _batches(score_ids, LICENSE_PURGE_BATCH_SIZE):
        selected.update(
            _selected_ids(
                db,
                select(CeriChangeEvent.id)
                .where(
                    or_(
                        CeriChangeEvent.from_snapshot_id.in_(batch),
                        CeriChangeEvent.to_snapshot_id.in_(batch),
                    )
                )
                .order_by(CeriChangeEvent.id),
            )
        )
    selected.update(
        _selected_ids_for_values(
            db,
            CeriChangeEvent,
            CeriChangeEvent.catalyst_revision_id,
            catalyst_revision_ids,
        )
    )
    return sorted(selected)


def _selected_ids_for_alert_scope(
    db: Session,
    *,
    change_ids: list[int],
    catalyst_revision_ids: list[int],
) -> list[int]:
    selected = set(
        _selected_ids_for_values(
            db,
            CeriAlertEvent,
            CeriAlertEvent.source_change_event_id,
            change_ids,
        )
    )
    selected.update(
        _selected_ids_for_values(
            db,
            CeriAlertEvent,
            CeriAlertEvent.source_catalyst_revision_id,
            catalyst_revision_ids,
        )
    )
    return sorted(selected)


def _batches(values: list[int], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def confirmation_token_for_preview(preview_manifest_hash: str) -> str:
    return f"CONFIRM-{preview_manifest_hash[:12]}"


def _validate_required_request(request: Any) -> None:
    for field in ("provider", "license_scope", "actor", "reason"):
        if not str(getattr(request, field, "") or "").strip():
            raise CeriPurgeError(f"Provider-license purge requires {field}.")
    provider = str(request.provider).strip().lower()
    scope = str(request.license_scope).strip().lower()
    if provider != "eodhd":
        raise CeriPurgeError("Provider-license purge is only valid for provider eodhd.")
    if not scope or scope in {"*", "all"}:
        raise CeriPurgeError("EODHD purge requires an explicit stored license scope.")


def _record_blocked(
    db: Session,
    request: CeriPurgeExecuteRequest,
    reason: str,
    job_id: int | None,
    processing_run_id: int | None,
) -> None:
    ceri_metrics.increment(
        "ceri_purge_blocked_total",
        session=db,
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
        for row in _fixture_rows(db, model)
        if getattr(row, "source_record_id", None) in source_ids
    ]


def _snapshot_source_ids(snapshot: CeriScoreSnapshot) -> set[int]:
    component_ids = set((snapshot.component_json or {}).get("source_ids") or [])
    lineage = snapshot.evidence_lineage_json or {}
    lineage_ids = set(lineage.get("revision_source_ids") or [])
    lineage_ids.update(lineage.get("earnings_source_ids") or [])
    lineage_ids.update(lineage.get("guidance_source_ids") or [])
    lineage_ids.update(lineage.get("catalyst_source_ids") or [])
    return component_ids | lineage_ids


def _source_matches_scope(source: CeriSourceRecord, license_scope: str) -> bool:
    scope = license_scope.strip()
    stored_scope = (source.license_scope or "").strip()
    return source.provider == "eodhd" and stored_scope == scope and source.purge_eligible


def _manifest_hash_input(
    manifest: dict[str, Any], provider: str, license_scope: str
) -> dict[str, Any]:
    return {
        "provider": provider,
        "license_scope": license_scope,
        "source_records": sorted(
            [
                {
                    "id": source.id,
                    "provider_record_id": source.provider_record_id,
                    "content_hash": source.content_hash,
                    "normalized_hash": source.normalized_hash,
                }
                for source in manifest["sources"]
            ],
            key=lambda row: (row["id"] or 0, row["provider_record_id"]),
        ),
        "normalized_ids": {
            key: sorted(
                int(row) if isinstance(row, int) else int(row.id)
                for row in manifest[key]
            )
            for key in (
                "estimates",
                "earnings",
                "guidance",
                "catalyst_revisions",
                "catalyst_sources",
                "price_response_features",
            )
        },
        "derived_ids": {
            key: sorted(
                int(row) if isinstance(row, int) else int(row.id)
                for row in manifest[key]
            )
            for key in (
                "revision_features",
                "derived_features",
                "price_response_features",
                "score_snapshots",
                "change_events",
                "alert_events",
            )
        },
        "certified_decision_evidence_ids": manifest["certified_decision_evidence_ids"],
    }


def _manifest_hash(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(manifest, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@source_writer_member("app.services.ceri.purge_service:CeriPurgeService.execute")
def _apply_purge_lifecycle(
    db: Session,
    manifest: dict[str, Any],
    *,
    preview_manifest_hash: str,
    audit_id: int | None,
) -> dict[str, Any]:
    if isinstance(db, Session):
        return _apply_purge_lifecycle_batched(
            db,
            manifest,
            preview_manifest_hash=preview_manifest_hash,
            audit_id=audit_id,
        )
    return _apply_purge_lifecycle_rows(
        manifest,
        preview_manifest_hash=preview_manifest_hash,
        audit_id=audit_id,
    )


def _apply_purge_lifecycle_batched(
    db: Session,
    manifest: dict[str, Any],
    *,
    preview_manifest_hash: str,
    audit_id: int | None,
) -> dict[str, Any]:
    model_by_key = {
        "sources": CeriSourceRecord,
        "estimates": CeriEstimateSnapshot,
        "earnings": CeriEarningsActual,
        "guidance": CeriGuidanceEvent,
        "catalyst_revisions": CeriCatalystEventRevision,
        "catalyst_sources": CeriCatalystSource,
        "revision_features": CeriRevisionFeature,
        "derived_features": CeriDerivedFeature,
        "price_response_features": CeriPriceResponseFeature,
        "score_snapshots": CeriScoreSnapshot,
        "change_events": CeriChangeEvent,
        "alert_events": CeriAlertEvent,
    }
    empty_manifest = {
        key: [] for key in model_by_key
    }
    empty_manifest.update(
        affected_counts=manifest["affected_counts"],
        invalidated_derivatives=manifest["invalidated_derivatives"],
    )
    for key, model in model_by_key.items():
        raw_ids = manifest["source_ids"] if key == "sources" else manifest[key]
        ids = [int(value.id) if hasattr(value, "id") else int(value) for value in raw_ids]
        for batch in _batches(ids, LICENSE_PURGE_BATCH_SIZE):
            rows = list(
                db.scalars(
                    select(model)
                    .where(model.id.in_(batch))
                    .order_by(model.id)
                    .with_for_update()
                )
            )
            if [int(row.id) for row in rows] != batch:
                raise CeriPurgeError(
                    f"Provider-license purge candidate set changed while locking {key}."
                )
            scoped = {**empty_manifest, key: rows}
            _apply_purge_lifecycle_rows(
                scoped,
                preview_manifest_hash=preview_manifest_hash,
                audit_id=audit_id,
            )
            db.flush()
            for row in rows:
                db.expunge(row)
    return {
        "affected_counts": manifest["affected_counts"],
        "invalidated_derivatives": {
            **manifest["invalidated_derivatives"],
            "policy": "tombstone_redact_invalidate",
            "preview_manifest_hash": preview_manifest_hash,
            "batch_size": LICENSE_PURGE_BATCH_SIZE,
        },
    }


def _apply_purge_lifecycle_rows(
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
        estimate.original_fields_json = marker
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
            None,
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
    for feature in manifest["derived_features"]:
        feature.value_json = _merge_json_object(
            feature.value_json,
            marker,
        )
        feature.source_ids_json = []
    for feature in manifest["price_response_features"]:
        feature.metrics_json = _merge_json_object(feature.metrics_json, marker)
        feature.warnings_json = _append_flag(
            feature.warnings_json,
            PURGE_INVALIDATION_FLAG,
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


@source_writer_member("app.services.ceri.purge_service:CeriPurgeService.execute")
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


def _fixture_rows(db: Session, model: type) -> list[Any]:
    if isinstance(db, Session):
        raise TypeError("fixture-only CERI row access cannot run against a production Session")
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)
