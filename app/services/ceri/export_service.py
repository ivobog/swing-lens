from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCatalystSource,
    CeriCompany,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.export_policy import CeriExportPolicyRegistry
from app.services.csv_export import write_csv

CERI_EXPORT_SCHEMA_ID = "swinglens.ceri.export.v1"
PURGE_INVALIDATION_FLAG = "provider_license_purge_invalidated"


@dataclass(frozen=True)
class CeriExportResult:
    rows: list[dict[str, Any]]
    format: str
    metadata: dict[str, Any] | None = None

    def to_json(self) -> str:
        return json.dumps(self.rows, sort_keys=True, default=str)

    def to_csv(self) -> str:
        metadata = self.metadata or {}
        if not self.rows:
            return write_csv(
                [],
                [],
                schema_id=CERI_EXPORT_SCHEMA_ID,
                metadata=metadata,
            )
        return write_csv(
            list(self.rows[0]),
            self.rows,
            schema_id=CERI_EXPORT_SCHEMA_ID,
            metadata=metadata,
        )


class CeriExportService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()
        self.policy = CeriExportPolicyRegistry(self.config)

    def current_view(
        self,
        db: Session,
        *,
        run_id: int | None = None,
        tickers: list[str] | None = None,
        output_format: str = "json",
        snapshots: list[CeriScoreSnapshot] | None = None,
    ) -> CeriExportResult:
        tickers_set = {ticker.upper() for ticker in tickers or []}
        candidates = snapshots if snapshots is not None else _load(db, CeriScoreSnapshot)
        if run_id is None:
            candidates = _latest_snapshot_rows(candidates)
        rows = []
        for snapshot in candidates:
            if run_id is not None and snapshot.run_id != run_id:
                continue
            if tickers_set and snapshot.ticker.upper() not in tickers_set:
                continue
            rows.append(
                self.policy.export_row(
                    {
                        "ticker": snapshot.ticker,
                        "run_id": snapshot.run_id,
                        "as_of_session": snapshot.as_of_session,
                        "cutoff_at": snapshot.cutoff_at,
                        "opportunity_score": snapshot.opportunity_score,
                        "event_risk_score": snapshot.event_risk_score,
                        "data_confidence": snapshot.data_confidence,
                        "posture": snapshot.posture,
                        "warnings": snapshot.warnings_json,
                        "evidence_hash": snapshot.evidence_hash,
                        "invalidated_by_purge": _is_invalidated(snapshot.warnings_json),
                        "purge_invalidation": (snapshot.alignment_flags_json or {}).get(
                            "purge_invalidation"
                        ),
                    }
                )
            )
        return CeriExportResult(
            rows=rows,
            format=output_format,
            metadata=_current_view_metadata(),
        )

    def full_evidence(
        self,
        db: Session,
        *,
        company_id: int | None = None,
        as_of_session: date | None = None,
        output_format: str = "json",
        source_records: list[CeriSourceRecord] | None = None,
    ) -> CeriExportResult:
        sources = source_records if source_records is not None else _load(db, CeriSourceRecord)
        source_company_ids = _source_company_ids(db)
        rows = []
        for source in sources:
            if company_id is not None and not _source_belongs_to_company(
                source, company_id, source_company_ids, db
            ):
                continue
            row = {
                "source_record_id": source.id,
                "ingestion_run_id": source.ingestion_run_id,
                "provider": source.provider,
                "dataset": source.dataset,
                "provider_record_id": source.provider_record_id,
                "published_at": source.published_at,
                "observed_at": source.observed_at,
                "ingested_at": source.ingested_at,
                "content_hash": source.content_hash,
                "export_policy": source.export_policy,
                "quarantine_reason": source.quarantine_reason,
                "purged": _is_purged_source(source),
                "purge_invalidation": (source.restricted_normalized_json or {}).get(
                    "purge_invalidation"
                )
                or source.restricted_normalized_json
                if _is_purged_source(source)
                else None,
                "source_url": self.policy.mask("source_url"),
                "raw_payload": self.policy.mask("raw_payload"),
                # A restricted provider payload is intentionally never passed
                # through as a generic export field.  The source hash and
                # provider identity are sufficient safe lineage for exports.
                "permitted_fields": None
                if _is_purged_source(source) or source.provider in {"eodhd", "sec"}
                else self.policy.permitted_payload(source.raw_json),
            }
            rows.append(self.policy.export_row(row))
        rows.extend(_revision_rows(db, company_id, as_of_session))
        rows.extend(_guidance_rows(db, company_id, as_of_session))
        rows.extend(_catalyst_rows(db, as_of_session))
        return CeriExportResult(
            rows=rows,
            format=output_format,
            metadata=_full_evidence_metadata(),
        )


def _revision_rows(
    db: Session,
    company_id: int | None,
    as_of_session: date | None,
) -> list[dict[str, Any]]:
    rows = []
    for feature in _load(db, CeriRevisionFeature):
        if company_id is not None and feature.company_id != company_id:
            continue
        if as_of_session is not None and feature.as_of_session > as_of_session:
            continue
        rows.append(
            {
                "record_type": "revision_feature",
                "company_id": feature.company_id,
                "metric": feature.metric,
                "as_of_session": feature.as_of_session,
                "window_days": feature.window_days,
                "baseline_snapshot_id": feature.baseline_snapshot_id,
                "current_snapshot_id": feature.current_snapshot_id,
                "source_observation_ids": feature.source_observation_ids_json,
                "evidence_hash": feature.evidence_hash,
                "invalidated_by_purge": _is_invalidated(feature.warnings_json),
                "unavailable_reason": feature.unavailable_reason,
            }
        )
    return rows


def _guidance_rows(
    db: Session,
    company_id: int | None,
    as_of_session: date | None,
) -> list[dict[str, Any]]:
    rows = []
    for guidance in _load(db, CeriGuidanceEvent):
        if company_id is not None and guidance.company_id != company_id:
            continue
        if as_of_session is not None and (
            guidance.effective_session is None or guidance.effective_session > as_of_session
        ):
            continue
        rows.append(
            {
                "record_type": "guidance",
                "company_id": guidance.company_id,
                "source_record_id": guidance.source_record_id,
                "action": guidance.action,
                "metric": guidance.metric,
                "effective_session": guidance.effective_session,
                "invalidated_by_purge": _is_invalidated(guidance.quality_warnings_json),
            }
        )
    return rows


def _catalyst_rows(db: Session, as_of_session: date | None) -> list[dict[str, Any]]:
    rows = []
    for revision in _load(db, CeriCatalystEventRevision):
        effective_session = revision.effective_session or (
            revision.announced_at.date() if revision.announced_at else None
        )
        if as_of_session is not None and (
            effective_session is None or effective_session > as_of_session
        ):
            continue
        rows.append(
            {
                "record_type": "catalyst_revision",
                "catalyst_revision_id": revision.id,
                "catalyst_event_id": revision.catalyst_event_id,
                "source_record_id": revision.source_record_id,
                "status": revision.status,
                "direction": revision.direction,
                "effective_session": revision.effective_session,
                "conflict_flags": revision.conflict_flags_json,
                "invalidated_by_purge": _is_invalidated(revision.conflict_flags_json),
            }
        )
    return rows


def _is_invalidated(flags: list[str] | None) -> bool:
    return PURGE_INVALIDATION_FLAG in set(flags or [])


def _latest_snapshot_rows(snapshots: list[CeriScoreSnapshot]) -> list[CeriScoreSnapshot]:
    latest: dict[str, CeriScoreSnapshot] = {}
    for snapshot in snapshots:
        key = snapshot.ticker.upper()
        current = latest.get(key)
        if current is None or _snapshot_sort_key(snapshot) > _snapshot_sort_key(current):
            latest[key] = snapshot
    return sorted(latest.values(), key=lambda row: row.ticker.upper())


def _snapshot_sort_key(snapshot: CeriScoreSnapshot) -> tuple[Any, Any, int]:
    return (
        snapshot.as_of_session or date.min,
        snapshot.cutoff_at.isoformat() if snapshot.cutoff_at else "",
        snapshot.id or 0,
    )


def _source_company_ids(db: Session) -> dict[int, set[int]]:
    links: dict[int, set[int]] = {}
    for model in (CeriEstimateSnapshot, CeriEarningsActual, CeriGuidanceEvent):
        for row in _load(db, model):
            links.setdefault(row.source_record_id, set()).add(row.company_id)
    events_by_id = {event.id: event for event in _load(db, CeriCatalystEvent)}
    for row in _load(db, CeriCatalystSource):
        event = events_by_id.get(row.catalyst_event_id)
        if event is not None:
            links.setdefault(row.source_record_id, set()).add(event.company_id)
    return links


def _source_belongs_to_company(
    source: CeriSourceRecord,
    company_id: int,
    source_company_ids: dict[int, set[int]],
    db: Session,
) -> bool:
    if company_id in source_company_ids.get(source.id, set()):
        return True
    hint = source.company_hint_json or {}
    if hint.get("company_id") == company_id:
        return True
    company = _get_company(db, company_id)
    if company is None:
        return False
    ticker = str(hint.get("ticker") or "").upper()
    exchange = str(hint.get("exchange") or "").upper()
    company_exchange = str(company.exchange or "").upper()
    if ticker and ticker == str(company.ticker or "").upper() and (
        not exchange or not company_exchange or exchange == company_exchange
    ):
        return True
    provider_ids = company.current_provider_ids_json or {}
    return any(
        hint.get(key) is not None
        and str(hint.get(key)) == str(provider_ids.get(key))
        for key in ("provider_company_id", "cik")
    )


def _get_company(db: Session, company_id: int) -> CeriCompany | None:
    getter = getattr(db, "get", None)
    if callable(getter):
        return getter(CeriCompany, company_id)
    return next((row for row in _load(db, CeriCompany) if row.id == company_id), None)


def _current_view_metadata() -> dict[str, Any]:
    return {
        "guidance_type": "research_evidence",
        "execution_instruction": False,
        "evidence_mode": "latest_corrected",
        "source_cutoff": "row_level_cutoff_at",
        "freshness": "row_level_as_of_session",
        "correction_state": "row_level_warning_flags",
        "model_version": "ceri_policy",
    }


def _full_evidence_metadata() -> dict[str, Any]:
    return {
        "guidance_type": "source_evidence",
        "execution_instruction": False,
        "evidence_mode": "restricted_full_evidence",
        "source_cutoff": "row_level_observed_or_effective_at",
        "freshness": "row_level_dataset_policy",
        "correction_state": "revision_or_quarantine_state",
        "model_version": "not_applicable",
    }


def _is_purged_source(source: CeriSourceRecord) -> bool:
    return source.export_policy == "purged" or (source.quarantine_reason or "").startswith(
        "provider_license_purge"
    )


def _load(db: Session, model):
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)
