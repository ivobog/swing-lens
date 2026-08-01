from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEventRevision,
    CeriGuidanceEvent,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.services.ceri.config import CeriConfig, load_ceri_config


@dataclass(frozen=True)
class CeriExportResult:
    rows: list[dict[str, Any]]
    format: str

    def to_json(self) -> str:
        return json.dumps(self.rows, sort_keys=True, default=str)

    def to_csv(self) -> str:
        if not self.rows:
            return ""
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(self.rows[0]))
        writer.writeheader()
        writer.writerows(self.rows)
        return buffer.getvalue()


class CeriExportService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()

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
        rows = []
        for snapshot in snapshots if snapshots is not None else _load(db, CeriScoreSnapshot):
            if run_id is not None and snapshot.run_id != run_id:
                continue
            if tickers_set and snapshot.ticker.upper() not in tickers_set:
                continue
            rows.append(
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
                }
            )
        return CeriExportResult(rows=rows, format=output_format)

    def full_evidence(
        self,
        db: Session,
        *,
        company_id: int | None = None,
        as_of_session: date | None = None,
        output_format: str = "json",
        source_records: list[CeriSourceRecord] | None = None,
    ) -> CeriExportResult:
        rows = []
        for source in source_records if source_records is not None else _load(db, CeriSourceRecord):
            hint_company_id = (source.company_hint_json or {}).get("company_id")
            if company_id is not None and hint_company_id != company_id:
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
                "source_url": _restricted("source_url"),
                "raw_payload": _restricted("raw_payload"),
                "permitted_fields": source.restricted_normalized_json
                or _permitted_payload(source.raw_json),
            }
            rows.append(row)
        rows.extend(_revision_rows(db, company_id, as_of_session))
        rows.extend(_guidance_rows(db, company_id, as_of_session))
        rows.extend(_catalyst_rows(db, as_of_session))
        return CeriExportResult(rows=rows, format=output_format)


def _revision_rows(
    db: Session,
    company_id: int | None,
    as_of_session: date | None,
) -> list[dict[str, Any]]:
    rows = []
    for feature in _load(db, CeriRevisionFeature):
        if company_id is not None and feature.company_id != company_id:
            continue
        if as_of_session is not None and feature.as_of_session != as_of_session:
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
        if as_of_session is not None and guidance.effective_session != as_of_session:
            continue
        rows.append(
            {
                "record_type": "guidance",
                "company_id": guidance.company_id,
                "source_record_id": guidance.source_record_id,
                "action": guidance.action,
                "metric": guidance.metric,
                "effective_session": guidance.effective_session,
            }
        )
    return rows


def _catalyst_rows(db: Session, as_of_session: date | None) -> list[dict[str, Any]]:
    rows = []
    for revision in _load(db, CeriCatalystEventRevision):
        if as_of_session is not None and revision.effective_session != as_of_session:
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
            }
        )
    return rows


def _permitted_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        key: value
        for key, value in payload.items()
        if key not in {"raw_payload", "source_url", "provider_secret"}
    }


def _restricted(field: str) -> str:
    return f"<restricted:{field}>"


def _load(db: Session, model):
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)
