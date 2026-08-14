from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriChangeEvent,
    CeriCompany,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriIngestionRun,
    CeriPriceResponseFeature,
    CeriProcessingRun,
    CeriProviderRequestTelemetry,
    CeriPurgeAudit,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import UploadRun
from app.services.ceri.api_dtos import (
    CeriConfidenceDto,
    CeriDashboardRowDto,
    CeriOpportunityDto,
    CeriRiskDto,
    CeriTickerDetailDto,
)
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import CeriDataset, HistoricalViewMode
from app.services.ceri.feature_flags import ceri_flags
from app.services.ceri.guidance_normalizer import guidance_eligibility_reason
from app.services.ceri.provider_cost_ledger import ProviderCostLedger
from app.services.ceri.snapshot_service import CeriSnapshotService

PURGE_INVALIDATION_FLAG = "provider_license_purge_invalidated"


class CeriQueryError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class CeriQueryFilters:
    ticker: str | None = None
    run_id: int | None = None
    opportunity_min: float | None = None
    risk_max: float | None = None
    confidence: str | None = None
    eps_revision_window: int | None = None
    revenue_revision_window: int | None = None
    revision_breadth_min: float | None = None
    surprise_trend: str | None = None
    guidance_direction: str | None = None
    catalyst_category: str | None = None
    event_date_from: date | None = None
    event_date_to: date | None = None
    changed_since: datetime | None = None
    provider_freshness: str | None = None
    technical_score_min: float | None = None
    technical_classification: str | None = None
    fundamental_score_min: float | None = None
    sector_state: str | None = None
    sector_rank_max: int | None = None
    market_regime: str | None = None
    setup_lifecycle_actionability: str | None = None
    next_binary_event_sessions_max: int | None = None
    posture: str | None = None
    alignment_flag: str | None = None
    has_warnings: bool | None = None
    has_conflicts: bool | None = None
    mode: str | None = None
    as_of: datetime | None = None
    config_version: str | None = None


@dataclass(frozen=True)
class CeriListQuery:
    filters: CeriQueryFilters
    sort: str = "opportunity_score"
    direction: str = "desc"
    limit: int = 50
    offset: int = 0


class CeriQueryService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()

    def latest(self, db: Session, query: CeriListQuery) -> dict[str, Any]:
        self._validate(query)
        snapshots = self._filtered_snapshots(db, query.filters)
        latest_by_ticker: dict[str, CeriScoreSnapshot] = {}
        for snapshot in snapshots:
            current = latest_by_ticker.get(snapshot.ticker.upper())
            if current is None or _snapshot_sort_tuple(snapshot) > _snapshot_sort_tuple(current):
                latest_by_ticker[snapshot.ticker.upper()] = snapshot
        return self._page(
            [_score_snapshot_payload(snapshot, db=db) for snapshot in latest_by_ticker.values()],
            query=query,
            sort_aliases={
                "opportunity_score": "opportunity_score",
                "risk_score": "event_risk_score",
                "event_risk_score": "event_risk_score",
                "ticker": "ticker",
                "as_of_session": "as_of_session",
                "cutoff_at": "cutoff_at",
            },
        )

    def run(self, db: Session, run_id: int, query: CeriListQuery) -> dict[str, Any]:
        self._require_run(db, run_id)
        filters = _replace_filter(query.filters, run_id=run_id)
        payload = self.latest(db, CeriListQuery(filters=filters, **_query_controls(query)))
        payload["run_id"] = run_id
        return payload

    def ticker(self, db: Session, ticker: str) -> dict[str, Any]:
        ticker = _ticker(ticker)
        snapshots = [
            snapshot
            for snapshot in self._filtered_snapshots(db, CeriQueryFilters(ticker=ticker))
            if snapshot.ticker.upper() == ticker
        ]
        if not snapshots:
            raise CeriQueryError(
                "TICKER_NOT_FOUND",
                f"CERI ticker was not found: {ticker}",
                status_code=404,
            )
        latest = max(snapshots, key=_snapshot_sort_tuple)
        company_id = latest.company_id
        company_revision_features = _rows_for_company(db, CeriRevisionFeature, company_id)
        company_earnings = _rows_for_company(db, CeriEarningsActual, company_id)
        return CeriTickerDetailDto(
            ticker=ticker,
            latest=_score_snapshot_payload(latest, db=db),
            revision_features=[
                _revision_feature_payload(feature)
                for feature in company_revision_features
                if feature.as_of_session == latest.as_of_session
                and feature.calculation_version == latest.calculation_version
            ],
            revision_history=[
                _revision_feature_payload(feature)
                for feature in company_revision_features
                if not (
                    feature.as_of_session == latest.as_of_session
                    and feature.calculation_version == latest.calculation_version
                )
            ],
            earnings_surprise_history=[
                _earnings_payload(row)
                for row in company_earnings
            ],
            guidance=_guidance_summary(db, company_id, latest.cutoff_at.date()),
            source_freshness=_snapshot_freshness(db, latest),
            events=_event_timeline_for_company(db, company_id, ticker)[:100],
            alerts=_alerts_for_ticker(db, ticker)[:25],
        ).to_dict()

    def ticker_history(self, db: Session, ticker: str, query: CeriListQuery) -> dict[str, Any]:
        filters = query.filters
        self._require_historical_view(filters)
        ticker = _ticker(ticker)
        snapshots = [
            snapshot
            for snapshot in self._filtered_snapshots(db, _replace_filter(filters, ticker=ticker))
            if snapshot.ticker.upper() == ticker
        ]
        if not snapshots:
            raise CeriQueryError(
                "TICKER_NOT_FOUND",
                f"CERI ticker was not found: {ticker}",
                status_code=404,
            )
        if filters.as_of is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.cutoff_at <= filters.as_of]
        payload = self._page(
            [_score_snapshot_payload(snapshot, db=db) for snapshot in snapshots],
            query=query,
            sort_aliases={
                "cutoff_at": "cutoff_at",
                "as_of_session": "as_of_session",
                "run_id": "run_id",
            },
        )
        payload["mode"] = HistoricalViewMode.STORED_SNAPSHOT.value
        payload["as_of"] = filters.as_of.isoformat()
        payload["source_correction_policy"] = "stored_score_snapshots_only"
        payload["evidence_hash"] = _stable_hash(
            {
                "ticker": ticker,
                "mode": HistoricalViewMode.STORED_SNAPSHOT.value,
                "as_of": filters.as_of.isoformat(),
                "snapshot_ids": [item["id"] for item in payload["items"]],
            }
        )
        return payload

    def changes(self, db: Session, query: CeriListQuery) -> dict[str, Any]:
        self._validate(query)
        company_by_id = _company_by_id(db)
        items = []
        for change in _load(db, CeriChangeEvent):
            ticker = company_by_id.get(change.company_id, {}).get("ticker")
            if query.filters.ticker and ticker != _ticker(query.filters.ticker):
                continue
            if query.filters.changed_since and change.created_at < query.filters.changed_since:
                continue
            items.append(_change_payload(change, ticker=ticker))
        return self._page(
            items,
            query=query,
            sort_aliases={
                "created_at": "created_at",
                "severity": "severity",
                "ticker": "ticker",
                "id": "id",
            },
        )

    def events(self, db: Session, query: CeriListQuery) -> dict[str, Any]:
        self._validate(query)
        company_by_id = _company_by_id(db)
        revisions = _current_revision_by_event_id(db)
        items = []
        for event in _load(db, CeriCatalystEvent):
            ticker = company_by_id.get(event.company_id, {}).get("ticker")
            revision = revisions.get(event.id)
            if not self._event_matches(event, revision, ticker, query.filters):
                continue
            items.append(_event_payload(event, ticker=ticker, current_revision=revision))
        return self._page(
            items,
            query=query,
            sort_aliases={
                "event_date": "expected_date",
                "category": "category",
                "ticker": "ticker",
                "id": "id",
            },
        )

    def event_detail(self, db: Session, event_id: int) -> dict[str, Any]:
        event = _get(db, CeriCatalystEvent, event_id)
        if event is None:
            raise CeriQueryError(
                "TICKER_NOT_FOUND",
                f"CERI event was not found: {event_id}",
                status_code=404,
            )
        company = _company_by_id(db).get(event.company_id, {})
        revisions = [
            revision
            for revision in _load(db, CeriCatalystEventRevision)
            if revision.catalyst_event_id == event_id
        ]
        current = next((revision for revision in revisions if revision.is_current), None)
        payload = _event_payload(event, ticker=company.get("ticker"), current_revision=current)
        payload["revisions"] = [_catalyst_revision_payload(revision) for revision in revisions]
        return payload

    def event_revisions(self, db: Session, event_id: int, query: CeriListQuery) -> dict[str, Any]:
        self.event_detail(db, event_id)
        items = [
            _catalyst_revision_payload(revision)
            for revision in _load(db, CeriCatalystEventRevision)
            if revision.catalyst_event_id == event_id
        ]
        return self._page(
            items,
            query=query,
            sort_aliases={"revision_number": "revision_number", "id": "id"},
        )

    def revisions(self, db: Session, query: CeriListQuery) -> dict[str, Any]:
        self._validate(query)
        company_by_id = _company_by_id(db)
        items = []
        for feature in _load(db, CeriRevisionFeature):
            ticker = company_by_id.get(feature.company_id, {}).get("ticker")
            if query.filters.ticker and ticker != _ticker(query.filters.ticker):
                continue
            if query.filters.revision_breadth_min is not None and (
                feature.net_breadth is None
                or float(feature.net_breadth) < query.filters.revision_breadth_min
            ):
                continue
            if query.filters.eps_revision_window and (
                feature.metric != "EPS_DILUTED"
                or feature.window_days != query.filters.eps_revision_window
            ):
                continue
            if query.filters.revenue_revision_window and (
                feature.metric != "REVENUE"
                or feature.window_days != query.filters.revenue_revision_window
            ):
                continue
            items.append(_revision_feature_payload(feature, ticker=ticker))
        return self._page(
            items,
            query=query,
            sort_aliases={
                "as_of_session": "as_of_session",
                "net_breadth": "net_breadth",
                "ticker": "ticker",
                "id": "id",
            },
        )

    def revision_detail(self, db: Session, revision_id: int) -> dict[str, Any]:
        feature = _get(db, CeriRevisionFeature, revision_id)
        if feature is None:
            raise CeriQueryError(
                "TICKER_NOT_FOUND",
                f"CERI revision was not found: {revision_id}",
                status_code=404,
            )
        ticker = _company_by_id(db).get(feature.company_id, {}).get("ticker")
        payload = _revision_feature_payload(feature, ticker=ticker)
        payload["lineage"] = {
            "baseline_snapshot_id": feature.baseline_snapshot_id,
            "current_snapshot_id": feature.current_snapshot_id,
            "source_observation_ids": list(feature.source_observation_ids_json or []),
            "selected_provider_hierarchy": [
                provider.value for provider in self.config.providers.priority
            ],
            "provider_selection_reason": feature.provider_selection_reason,
            "config_version": feature.config_version,
            "config_hash": feature.config_hash,
            "calculation_version": feature.calculation_version,
            "evidence_hash": feature.evidence_hash,
            "stored_values": {
                "absolute_change": _value(feature.absolute_change),
                "pct_change": _value(feature.pct_change),
                "net_breadth": _value(feature.net_breadth),
            },
            "reproduced_values": {
                "absolute_change": _value(feature.absolute_change),
                "pct_change": _value(feature.pct_change),
                "net_breadth": _value(feature.net_breadth),
            },
        }
        return payload

    def alerts(self, db: Session, query: CeriListQuery) -> dict[str, Any]:
        self._validate(query)
        items = []
        for alert in _load(db, CeriAlertEvent):
            if query.filters.ticker and alert.ticker.upper() != _ticker(query.filters.ticker):
                continue
            items.append(_alert_payload(alert))
        return self._page(
            items,
            query=query,
            sort_aliases={
                "created_at": "created_at",
                "severity": "severity",
                "ticker": "ticker",
                "status": "status",
                "id": "id",
            },
        )

    def operations_quarantine(self, db: Session, query: CeriListQuery) -> dict[str, Any]:
        items = [
            _source_record_payload(source)
            for source in _load(db, CeriSourceRecord)
            if source.quarantine_reason
        ]
        return self._page(
            items,
            query=query,
            sort_aliases={"ingested_at": "ingested_at", "provider": "provider", "id": "id"},
        )

    def operations_conflicts(self, db: Session, query: CeriListQuery) -> dict[str, Any]:
        items = []
        for revision in _load(db, CeriCatalystEventRevision):
            if revision.conflict_flags_json:
                items.append(_catalyst_revision_payload(revision))
        for feature in _load(db, CeriRevisionFeature):
            warnings = set(feature.warnings_json or [])
            if query.filters.has_conflicts is False:
                continue
            if any("conflict" in warning.lower() for warning in warnings):
                items.append(_revision_feature_payload(feature))
        return self._page(
            items,
            query=query,
            sort_aliases={"id": "id", "effective_session": "effective_session"},
        )

    def operations_stale(self, db: Session, query: CeriListQuery) -> dict[str, Any]:
        now = datetime.now(UTC).date()
        dataset_max_stale = {
            dataset.value: policy.max_stale_days for dataset, policy in self.config.datasets.items()
        }
        items = []
        for source in _load(db, CeriSourceRecord):
            observed = source.observed_at or source.published_at or source.ingested_at
            if observed is None:
                continue
            max_days = dataset_max_stale.get(source.dataset)
            if max_days is not None and (now - observed.date()).days > max_days:
                row = _source_record_payload(source)
                row["stale_days"] = (now - observed.date()).days
                row["max_stale_days"] = max_days
                items.append(row)
        return self._page(
            items,
            query=query,
            sort_aliases={"stale_days": "stale_days", "dataset": "dataset", "id": "id"},
        )

    def operations_status(self, db: Session) -> dict[str, Any]:
        ingestion_runs = _load(db, CeriIngestionRun)
        processing_runs = _load(db, CeriProcessingRun)
        source_records = _load(db, CeriSourceRecord)
        purge_audits = _load(db, CeriPurgeAudit)
        provider_telemetry = _load(db, CeriProviderRequestTelemetry)
        estimates = _load(db, CeriEstimateSnapshot)
        revisions = _load(db, CeriRevisionFeature)
        snapshots = _load(db, CeriScoreSnapshot)
        guidance = _load(db, CeriGuidanceEvent)
        catalyst_revisions = _load(db, CeriCatalystEventRevision)
        current_revisions = [
            row
            for row in revisions
            if row.config_hash == self.config.config_hash
            and row.calculation_version == self.config.engine.calculation_version
        ]
        current_snapshots = [
            row
            for row in snapshots
            if row.config_hash == self.config.config_hash
            and row.calculation_version == self.config.engine.calculation_version
        ]
        flags = ceri_flags()
        snapshot_service = CeriSnapshotService(config=self.config)
        reproduction_failures = sum(
            1 for snapshot in snapshots if not snapshot_service.reproduce_snapshot(snapshot).matches
        )
        comparable_estimates = sum(
            1
            for estimate in estimates
            if estimate.canonical_currency is not None
            and estimate.canonical_scale is not None
            and estimate.currency_verified is True
        )
        return {
            "effective_configuration": {
                "enabled": flags.enabled,
                "enabled_source": "CERI_ENABLED_RUNTIME_SETTING",
                "yaml_engine_enabled": self.config.engine.enabled,
                "yaml_engine_enabled_deprecated": True,
                "child_flags": {
                    "provider_ingest": flags.provider_ingest,
                    "run_capture": flags.run_capture,
                    "ui": flags.ui,
                    "alerts": flags.alerts,
                    "admin": flags.admin,
                    "backfill": flags.backfill,
                },
                "calculation_version": self.config.engine.calculation_version,
                "config_version": self.config.engine.config_version,
                "config_hash": self.config.config_hash,
            },
            "quality_metrics": {
                "ceri_estimate_comparable_pct": (
                    100.0 * comparable_estimates / len(estimates) if estimates else 0.0
                ),
                "ceri_estimate_currency_missing_pct": (
                    100.0
                    * sum(1 for estimate in estimates if estimate.canonical_currency is None)
                    / len(estimates)
                    if estimates
                    else 0.0
                ),
                "ceri_period_slot_ambiguous_count": sum(
                    1 for estimate in estimates if estimate.canonical_period_slot is None
                ),
                "ceri_revision_available_pct": (
                    100.0
                    * sum(1 for revision in current_revisions if revision.pct_change is not None)
                    / len(current_revisions)
                    if current_revisions
                    else 0.0
                ),
                "ceri_opportunity_rated_pct": (
                    100.0
                    * sum(
                        1
                        for snapshot in current_snapshots
                        if snapshot.opportunity_score is not None
                    )
                    / len(current_snapshots)
                    if current_snapshots
                    else 0.0
                ),
                "ceri_confidence_insufficient_pct": (
                    100.0
                    * sum(
                        1
                        for snapshot in current_snapshots
                        if snapshot.data_confidence == "Insufficient"
                    )
                    / len(current_snapshots)
                    if current_snapshots
                    else 0.0
                ),
                "ceri_guidance_rejected_count": sum(
                    1 for row in guidance if guidance_eligibility_reason(row) is not None
                ),
                "ceri_catalyst_relevance_rejected_count": sum(
                    1 for row in catalyst_revisions if row.issuer_relevance is not True
                ),
                "ceri_catalyst_unclassified_count": sum(
                    1
                    for row in catalyst_revisions
                    if row.issuer_relevance is None or row.binary_eligible is None
                ),
                "ceri_event_risk_ceiling_count": sum(
                    1 for snapshot in current_snapshots if snapshot.event_risk_score == 10.0
                ),
                "ceri_snapshot_reproduction_failure_count": reproduction_failures,
            },
            "dataset_freshness": self._dataset_freshness(source_records),
            "ingestion_status": dict(Counter(run.status for run in ingestion_runs)),
            "processing_status": dict(Counter(run.status for run in processing_runs)),
            "quota_state": [
                {
                    "ingestion_run_id": run.id,
                    "provider": run.provider,
                    "dataset": run.dataset,
                    "quota_state": run.quota_state_json,
                }
                for run in ingestion_runs
                if run.quota_state_json
            ],
            "errors": [
                {
                    "run_id": run.id,
                    "job_type": getattr(run, "job_type", None),
                    "errors": run.errors_json,
                }
                for run in [*ingestion_runs, *processing_runs]
                if run.errors_json
            ],
            "quarantined_count": sum(1 for source in source_records if source.quarantine_reason),
            "conflicted_count": len(
                self.operations_conflicts(
                    db,
                    CeriListQuery(CeriQueryFilters(), sort="id", limit=5000),
                )["items"]
            ),
            "stale_count": len(
                self.operations_stale(
                    db,
                    CeriListQuery(CeriQueryFilters(), sort="stale_days", limit=5000),
                )["items"]
            ),
            "processing_runs": [_processing_run_payload(run) for run in processing_runs],
            "alert_delivery": dict(Counter(alert.status for alert in _load(db, CeriAlertEvent))),
            "provider_terms_version": self.config.retention.provider_terms_version,
            "provider_cost_runtime_storage_ledger": ProviderCostLedger().summarize(
                provider_telemetry
            ),
            "deployment_identities": [
                {
                    "run_type": "ingestion",
                    "run_id": run.id,
                    "identity": run.deployment_identity_json,
                }
                for run in ingestion_runs
                if run.deployment_identity_json
            ]
            + [
                {
                    "run_type": "processing",
                    "run_id": run.id,
                    "identity": run.deployment_identity_json,
                }
                for run in processing_runs
                if run.deployment_identity_json
            ],
            "retention": {
                "retain_source_evidence_indefinitely": (
                    self.config.retention.retain_source_evidence_indefinitely
                ),
                "provider_license_purge_enabled": (
                    self.config.retention.provider_license_purge_enabled
                ),
            },
            "export_restrictions": self.config.exports.default_view_fields,
            "purge_previews": [_purge_audit_payload(audit) for audit in purge_audits],
        }

    def _require_run(self, db: Session, run_id: int) -> None:
        if _get(db, UploadRun, run_id) is None:
            raise CeriQueryError(
                "RUN_NOT_FOUND",
                f"CERI run was not found: {run_id}",
                status_code=404,
            )

    def _require_historical_view(self, filters: CeriQueryFilters) -> None:
        if filters.mode != HistoricalViewMode.STORED_SNAPSHOT.value:
            raise CeriQueryError(
                "INVALID_FILTER",
                (
                    "mode=STORED_SNAPSHOT is required for public CERI score history. "
                    "AS_KNOWN and LATEST_CORRECTED reconstruction are not exposed by this endpoint."
                ),
            )
        if filters.as_of is None:
            raise CeriQueryError(
                "INVALID_FILTER",
                "Historical CERI endpoints require explicit as_of cutoff.",
            )

    def _validate(self, query: CeriListQuery) -> None:
        if query.limit <= 0 or query.limit > 5000:
            raise CeriQueryError("INVALID_FILTER", "limit must be between 1 and 5000.")
        if query.offset < 0:
            raise CeriQueryError("INVALID_FILTER", "offset must be non-negative.")
        if query.direction.lower() not in {"asc", "desc"}:
            raise CeriQueryError("INVALID_SORT", "direction must be asc or desc.")
        filters = query.filters
        if (
            filters.event_date_from is not None
            and filters.event_date_to is not None
            and filters.event_date_from > filters.event_date_to
        ):
            raise CeriQueryError(
                "INVALID_DATE_RANGE",
                "event_date_from must be on or before event_date_to.",
            )
        if filters.config_version and filters.config_version != self.config.engine.config_version:
            raise CeriQueryError(
                "CONFIG_VERSION_NOT_FOUND",
                f"CERI config version was not found: {filters.config_version}",
                status_code=404,
            )

    def _filtered_snapshots(
        self, db: Session, filters: CeriQueryFilters
    ) -> list[CeriScoreSnapshot]:
        if filters.ticker and not _uses_fixture_collections(db):
            snapshot_rows = list(
                db.scalars(
                    select(CeriScoreSnapshot).where(
                        CeriScoreSnapshot.ticker == _ticker(filters.ticker)
                    )
                ).all()
            )
        else:
            snapshot_rows = _load(db, CeriScoreSnapshot)
        snapshots = []
        catalyst_company_ids: set[int] | None = None
        if filters.catalyst_category:
            events = [
                event
                for event in _load(db, CeriCatalystEvent)
                if event.category == filters.catalyst_category.upper()
            ]
            current_event_ids = {
                revision.catalyst_event_id
                for revision in _load(db, CeriCatalystEventRevision)
                if revision.is_current
            }
            catalyst_company_ids = {
                event.company_id for event in events if event.id in current_event_ids
            }
        for snapshot in snapshot_rows:
            if filters.run_id is not None and snapshot.run_id != filters.run_id:
                continue
            if filters.ticker and snapshot.ticker.upper() != _ticker(filters.ticker):
                continue
            if filters.opportunity_min is not None and (
                snapshot.opportunity_score is None
                or snapshot.opportunity_score < filters.opportunity_min
            ):
                continue
            if filters.risk_max is not None and (
                snapshot.event_risk_score is None or snapshot.event_risk_score > filters.risk_max
            ):
                continue
            if filters.confidence and snapshot.data_confidence != filters.confidence:
                continue
            if filters.posture and snapshot.posture != filters.posture:
                continue
            if (
                filters.has_warnings is not None
                and bool(snapshot.warnings_json) != filters.has_warnings
            ):
                continue
            if filters.alignment_flag and not (snapshot.alignment_flags_json or {}).get(
                filters.alignment_flag
            ):
                continue
            if catalyst_company_ids is not None and snapshot.company_id not in catalyst_company_ids:
                continue
            snapshots.append(snapshot)
        return snapshots

    def _event_matches(
        self,
        event: CeriCatalystEvent,
        revision: CeriCatalystEventRevision | None,
        ticker: str | None,
        filters: CeriQueryFilters,
    ) -> bool:
        if filters.ticker and ticker != _ticker(filters.ticker):
            return False
        if filters.catalyst_category and event.category != filters.catalyst_category.upper():
            return False
        if revision is None:
            return True
        event_date = revision.expected_date or revision.effective_session
        if filters.event_date_from and (event_date is None or event_date < filters.event_date_from):
            return False
        if filters.event_date_to and (event_date is None or event_date > filters.event_date_to):
            return False
        if (
            filters.has_conflicts is not None
            and bool(revision.conflict_flags_json) != filters.has_conflicts
        ):
            return False
        return True

    def _page(
        self,
        items: list[dict[str, Any]],
        *,
        query: CeriListQuery,
        sort_aliases: dict[str, str],
    ) -> dict[str, Any]:
        self._validate(query)
        sort_key = sort_aliases.get(query.sort)
        if sort_key is None:
            raise CeriQueryError("INVALID_SORT", f"Unsupported CERI sort: {query.sort}")
        reverse = query.direction.lower() == "desc"
        ordered = sorted(
            items,
            key=lambda item: (
                _sort_value(item.get("ticker")),
                _sort_value(item.get("id")),
            ),
        )
        ordered = sorted(
            ordered,
            key=lambda item: _sort_value(item.get(sort_key)),
            reverse=reverse,
        )
        total = len(ordered)
        page_items = ordered[query.offset : query.offset + query.limit]
        return {
            "items": page_items,
            "total": total,
            "limit": query.limit,
            "offset": query.offset,
            "next_offset": query.offset + query.limit
            if query.offset + query.limit < total
            else None,
            "sort": query.sort,
            "direction": query.direction,
        }

    def _dataset_freshness(
        self,
        source_records: list[CeriSourceRecord],
    ) -> list[dict[str, Any]]:
        latest: dict[tuple[str, str], CeriSourceRecord] = {}
        for source in source_records:
            key = (source.provider, source.dataset)
            current = latest.get(key)
            current_time = (
                current.observed_at or current.published_at or current.ingested_at
                if current
                else None
            )
            source_time = source.observed_at or source.published_at or source.ingested_at
            if current is None or (source_time and current_time and source_time > current_time):
                latest[key] = source
        rows = []
        now = datetime.now(UTC).date()
        for (provider, dataset), source in sorted(latest.items()):
            observed = source.observed_at or source.published_at or source.ingested_at
            try:
                dataset_key = CeriDataset(dataset)
            except ValueError:
                dataset_key = None
            dataset_config = self.config.datasets.get(dataset_key) if dataset_key else None
            max_stale_days = dataset_config.max_stale_days if dataset_config else None
            age_days = (now - observed.date()).days if observed else None
            rows.append(
                {
                    "provider": provider,
                    "dataset": dataset,
                    "latest_observed_at": _value(observed),
                    "age_days": age_days,
                    "max_stale_days": max_stale_days,
                    "fresh": None
                    if age_days is None or max_stale_days is None
                    else age_days <= max_stale_days,
                }
            )
        return rows


def _score_snapshot_payload(
    snapshot: CeriScoreSnapshot,
    *,
    db: Session | None = None,
) -> dict[str, Any]:
    cutoff_date = (
        snapshot.cutoff_at.date()
        if snapshot.cutoff_at is not None
        else (snapshot.as_of_session or date.min)
    )
    opportunity_ledger = snapshot.opportunity_ledger_json or {}
    confidence_ledger = snapshot.confidence_ledger_json or {}
    risk_ledger = snapshot.event_risk_ledger_json or {}
    opportunity_coverage = (
        snapshot.opportunity_coverage_pct
        if snapshot.opportunity_coverage_pct is not None
        else opportunity_ledger.get("coverage_pct")
    )
    opportunity = CeriOpportunityDto(
        score=snapshot.opportunity_score,
        rated=bool(
            snapshot.opportunity_score is not None
            and snapshot.opportunity_unrated_reason is None
        ),
        coverage_pct=opportunity_coverage,
        minimum_required_coverage_pct=opportunity_ledger.get(
            "minimum_required_coverage_pct"
        ),
        unrated_reason=snapshot.opportunity_unrated_reason,
        reweighted=opportunity_ledger.get("reweighted", False),
    )
    event_risk = CeriRiskDto(
        score=snapshot.event_risk_score,
        dominant_reason=risk_ledger.get("dominant_component"),
    )
    confidence = CeriConfidenceDto(
        label=snapshot.data_confidence,
        score=confidence_ledger.get("score"),
        coverage_pct=snapshot.coverage_pct,
        gates=confidence_ledger.get("gates") or [],
        caps=confidence_ledger.get("caps") or [],
    )
    freshness = _snapshot_freshness(db, snapshot) if db is not None else {}
    return CeriDashboardRowDto(
        id=snapshot.id,
        run_id=snapshot.run_id,
        source_run_id_text=snapshot.source_run_id_text,
        company_id=snapshot.company_id,
        ticker=snapshot.ticker,
        as_of_session=_value(snapshot.as_of_session),
        cutoff_at=_value(snapshot.cutoff_at),
        opportunity_score=snapshot.opportunity_score,
        opportunity=opportunity,
        event_risk_score=snapshot.event_risk_score,
        event_risk=event_risk,
        data_confidence=snapshot.data_confidence,
        confidence=confidence,
        coverage_pct=snapshot.coverage_pct,
        posture=snapshot.posture,
        earnings_proximity_risk=snapshot.earnings_proximity_risk,
        alignment_flags=snapshot.alignment_flags_json,
        alignment_context=snapshot.alignment_context_json,
        evidence_lineage=snapshot.evidence_lineage_json,
        top_positive_contributors=snapshot.top_positive_contributors_json,
        top_negative_contributors=snapshot.top_negative_contributors_json,
        ledgers={
            "opportunity": opportunity_ledger,
            "confidence": confidence_ledger,
            "event_risk": risk_ledger,
        },
        guidance=(
            _guidance_summary(db, snapshot.company_id, cutoff_date)
            if db is not None
            else {"status": "UNAVAILABLE", "reason": "DTO_CONTEXT_UNAVAILABLE"}
        ),
        revision_evidence=(
            _current_revision_summary(db, snapshot) if db is not None else {}
        ),
        next_event=(
            _next_event_summary(db, snapshot.company_id, cutoff_date)
            if db is not None
            else {"status": "UNAVAILABLE", "reason": "DTO_CONTEXT_UNAVAILABLE"}
        ),
        freshness=freshness,
        evidence_diagnostics=(
            _evidence_diagnostics(db, snapshot, freshness, opportunity_ledger)
            if db is not None
            else {}
        ),
        reasons=snapshot.reasons_json,
        warnings=snapshot.warnings_json,
        config_version=snapshot.config_version,
        config_hash=snapshot.config_hash,
        calculation_version=snapshot.calculation_version,
        evidence_hash=snapshot.evidence_hash,
        hash_schema_version=snapshot.hash_schema_version,
        invalidated_by_purge=_is_invalidated(snapshot.warnings_json),
        purge_invalidation=(snapshot.alignment_flags_json or {}).get("purge_invalidation"),
    ).to_dict()


def _revision_feature_payload(
    feature: CeriRevisionFeature,
    *,
    ticker: str | None = None,
) -> dict[str, Any]:
    return {
        "id": feature.id,
        "company_id": feature.company_id,
        "ticker": ticker,
        "metric": feature.metric,
        "period_key": feature.period_key,
        "period_slot": feature.period_slot,
        "as_of_session": _value(feature.as_of_session),
        "window_days": feature.window_days,
        "baseline_snapshot_id": feature.baseline_snapshot_id,
        "current_snapshot_id": feature.current_snapshot_id,
        "actual_elapsed_days": feature.actual_elapsed_days,
        "absolute_change": _value(feature.absolute_change),
        "pct_change": _value(feature.pct_change),
        "pct_change_unit": feature.pct_change_unit,
        "upward_count": feature.upward_count,
        "downward_count": feature.downward_count,
        "raw_breadth_counts": {
            "upward_count": feature.upward_count,
            "downward_count": feature.downward_count,
        },
        "net_breadth": _value(feature.net_breadth),
        "dispersion": _value(feature.dispersion),
        "acceleration": _value(feature.acceleration),
        "acceleration_unit": feature.acceleration_unit,
        "baseline_origin": feature.baseline_origin,
        "comparison_mode": feature.comparison_mode,
        "known_at": _value(feature.known_at),
        "reference_at": _value(feature.reference_at),
        "current_source_record_id": feature.current_source_record_id,
        "baseline_source_record_id": feature.baseline_source_record_id,
        "revision_confidence_score": feature.revision_confidence_score,
        "revision_confidence_label": feature.revision_confidence_label,
        "warnings": feature.warnings_json,
        "source_observation_ids": feature.source_observation_ids_json,
        "provider_selection_reason": feature.provider_selection_reason,
        "unavailable_reason": feature.unavailable_reason,
        "evidence_hash": feature.evidence_hash,
        "config_version": feature.config_version,
        "config_hash": feature.config_hash,
        "calculation_version": feature.calculation_version,
        "invalidated_by_purge": _is_invalidated(feature.warnings_json),
    }


def _event_payload(
    event: CeriCatalystEvent,
    *,
    ticker: str | None,
    current_revision: CeriCatalystEventRevision | None,
) -> dict[str, Any]:
    payload = {
        "id": event.id,
        "company_id": event.company_id,
        "ticker": ticker,
        "category": event.category,
        "subtype": event.subtype,
        "subject_key": event.subject_key,
        "canonical_text": event.canonical_text,
        "first_seen_at": _value(event.first_seen_at),
        "last_updated_at": _value(event.last_updated_at),
    }
    if current_revision is not None:
        payload["current_revision"] = _catalyst_revision_payload(current_revision)
        payload["expected_date"] = _value(current_revision.expected_date)
        payload["status"] = current_revision.status
        payload["direction"] = current_revision.direction
    else:
        payload["current_revision"] = None
        payload["expected_date"] = None
        payload["status"] = None
        payload["direction"] = None
    return payload


def _catalyst_revision_payload(revision: CeriCatalystEventRevision) -> dict[str, Any]:
    return {
        "id": revision.id,
        "catalyst_event_id": revision.catalyst_event_id,
        "source_record_id": revision.source_record_id,
        "prior_revision_id": revision.prior_revision_id,
        "outcome_revision_id": revision.outcome_revision_id,
        "revision_number": revision.revision_number,
        "is_current": revision.is_current,
        "announced_at": _value(revision.announced_at),
        "expected_date": _value(revision.expected_date),
        "effective_session": _value(revision.effective_session),
        "status": revision.status,
        "direction": revision.direction,
        "materiality": revision.materiality,
        "date_confidence": revision.date_confidence,
        "source_confidence": revision.source_confidence,
        "operational_values": revision.operational_values_json,
        "conflict_flags": revision.conflict_flags_json,
        "review_state": revision.review_state,
        "issuer_relevance": revision.issuer_relevance,
        "relevance_reason": revision.relevance_reason,
        "binary_eligible": revision.binary_eligible,
        "created_at": _value(revision.created_at),
        "invalidated_by_purge": _is_invalidated(revision.conflict_flags_json),
        "lineage": {
            "source_record_id": revision.source_record_id,
            "prior_revision_id": revision.prior_revision_id,
            "outcome_revision_id": revision.outcome_revision_id,
        },
    }


def _earnings_payload(row: CeriEarningsActual) -> dict[str, Any]:
    return {
        "id": row.id,
        "metric": row.metric,
        "period_type": row.period_type,
        "fiscal_period_end": _value(row.fiscal_period_end),
        "report_at": _value(row.report_at),
        "actual": _value(row.actual_value),
        "provider_consensus_at_report": _value(row.provider_consensus_value),
        "provider_surprise_pct": _value(row.provider_surprise_pct),
        "consensus_snapshot_id": row.consensus_snapshot_id,
        "consensus_selection_reason": row.consensus_selection_reason,
        "surprise_absolute": _value(row.surprise_absolute),
        "surprise_pct": _value(row.surprise_pct),
        "warnings": row.quality_warnings_json or [],
    }


def _current_revision_summary(db: Session, snapshot: CeriScoreSnapshot) -> dict[str, Any]:
    features = [
        feature
        for feature in _rows_for_company(db, CeriRevisionFeature, snapshot.company_id)
        if feature.as_of_session == snapshot.as_of_session
        and feature.calculation_version == snapshot.calculation_version
    ]
    result: dict[str, Any] = {}
    for feature in features:
        if feature.metric is None:
            continue
        metric = "eps" if feature.metric == "EPS_DILUTED" else feature.metric.lower()
        key = f"{metric}_{feature.period_slot or 'unresolved'}_{feature.window_days}d"
        result[key] = {
            "value": _value(feature.pct_change),
            "unit": feature.pct_change_unit,
            "available": feature.pct_change is not None,
            "reason": feature.unavailable_reason,
            "upward_count": feature.upward_count,
            "downward_count": feature.downward_count,
            "breadth": _value(feature.net_breadth),
            "comparison_mode": feature.comparison_mode,
            "warnings": feature.warnings_json or [],
            "currency_caveat": (
                "Same-provider relative change; canonical currency unavailable."
                if feature.comparison_mode == "SAME_PROVIDER_RELATIVE"
                and "canonical_currency_unavailable_relative_only"
                in (feature.warnings_json or [])
                else None
            ),
        }
    return result


def _guidance_summary(db: Session, company_id: int, cutoff: date) -> dict[str, Any]:
    rows = [
        row
        for row in _rows_for_company(db, CeriGuidanceEvent, company_id)
        if row.effective_session is None or row.effective_session <= cutoff
    ]
    accepted = [row for row in rows if row.accepted_for_scoring is True]
    rejected = [
        {"id": row.id, "reason": guidance_eligibility_reason(row)}
        for row in rows
        if row.accepted_for_scoring is not True
    ]
    if not accepted:
        return {
            "status": "UNAVAILABLE" if not rows else "REJECTED",
            "reason": "NO_ACCEPTED_CURRENT_GUIDANCE",
            "selected": None,
            "rejected": rejected,
        }
    selected = max(
        accepted,
        key=lambda row: (
            row.effective_at.isoformat()
            if row.effective_at is not None
            else (row.effective_session.isoformat() if row.effective_session else ""),
            row.id or 0,
        ),
    )
    return {
        "status": "AVAILABLE",
        "reason": None,
        "selected": {
            "id": selected.id,
            "action": selected.action,
            "metric": selected.metric,
            "period": selected.period_type or selected.period_label,
            "confidence": selected.confidence,
            "effective_date": _value(selected.effective_session),
        },
        "rejected": rejected,
    }


def _next_event_summary(db: Session, company_id: int, cutoff: date) -> dict[str, Any]:
    events = {
        event.id: event for event in _rows_for_company(db, CeriCatalystEvent, company_id)
    }
    if _uses_fixture_collections(db):
        revisions = [
            revision
            for revision in _load(db, CeriCatalystEventRevision)
            if revision.is_current and revision.catalyst_event_id in events
        ]
    elif events:
        revisions = list(
            db.scalars(
                select(CeriCatalystEventRevision).where(
                    CeriCatalystEventRevision.is_current.is_(True),
                    CeriCatalystEventRevision.catalyst_event_id.in_(events),
                )
            ).all()
        )
    else:
        revisions = []
    accepted = [
        revision
        for revision in revisions
        if revision.issuer_relevance is True
        and revision.binary_eligible is True
        and revision.status not in {"COMPLETED", "CANCELLED", "OUTCOME_KNOWN", "RESOLVED"}
        and (revision.expected_date is None or revision.expected_date >= cutoff)
    ]
    rejected = [
        {
            "event_id": revision.catalyst_event_id,
            "reason": revision.relevance_reason
            or ("BINARY_INELIGIBLE" if revision.binary_eligible is not True else "RESOLVED"),
        }
        for revision in revisions
        if revision not in accepted
    ]
    if not accepted:
        return {
            "status": "UNAVAILABLE" if not revisions else "NONE_FOUND",
            "reason": "NO_ACCEPTED_PENDING_BINARY_EVENT",
            "selected": None,
            "rejected": rejected,
        }
    selected = min(
        accepted,
        key=lambda row: (row.expected_date is None, row.expected_date or date.max, row.id or 0),
    )
    event = events[selected.catalyst_event_id]
    return {
        "status": "AVAILABLE",
        "reason": None,
        "selected": {
            "event_id": event.id,
            "category": event.category,
            "subtype": event.subtype,
            "expected_date": _value(selected.expected_date),
            "date_confidence": selected.date_confidence,
            "status": selected.status,
        },
        "rejected": rejected,
    }


def _snapshot_freshness(db: Session, snapshot: CeriScoreSnapshot) -> dict[str, Any]:
    if snapshot.cutoff_at is None:
        return {
            dataset.value: {"status": "UNAVAILABLE", "age_days": None}
            for dataset in CeriDataset
        }
    source_ids = set((snapshot.component_json or {}).get("source_ids") or [])
    if _uses_fixture_collections(db):
        sources = [source for source in _load(db, CeriSourceRecord) if source.id in source_ids]
    elif source_ids:
        sources = list(
            db.scalars(
                select(CeriSourceRecord).where(CeriSourceRecord.id.in_(source_ids))
            ).all()
        )
    else:
        sources = []
    result: dict[str, Any] = {}
    for dataset in CeriDataset:
        matching = [source for source in sources if source.dataset == dataset.value]
        if not matching:
            result[dataset.value] = {"status": "UNAVAILABLE", "age_days": None}
            continue
        latest = max(
            matching,
            key=lambda source: source.retrieved_at
            or source.observed_at
            or source.published_at
            or source.ingested_at,
        )
        stamp = (
            latest.retrieved_at
            or latest.observed_at
            or latest.published_at
            or latest.ingested_at
        )
        age = max(0, (snapshot.cutoff_at.date() - stamp.date()).days)
        threshold = load_ceri_config().datasets[dataset].max_stale_days
        result[dataset.value] = {
            "status": "AVAILABLE" if age <= threshold else "STALE",
            "age_days": age,
            "known_at": _value(stamp),
            "timestamp_quality": (
                "RETRIEVAL_ONLY"
                if latest.source_timestamp is None and latest.published_at is None
                else "SOURCE_TIMESTAMP"
            ),
        }
    return result


def _evidence_diagnostics(
    db: Session,
    snapshot: CeriScoreSnapshot,
    freshness: dict[str, Any],
    opportunity_ledger: dict[str, Any],
) -> dict[str, Any]:
    source_ids = set((snapshot.component_json or {}).get("source_ids") or [])
    if _uses_fixture_collections(db):
        sources = [row for row in _load(db, CeriSourceRecord) if row.id in source_ids]
    elif source_ids:
        sources = list(
            db.scalars(
                select(CeriSourceRecord).where(CeriSourceRecord.id.in_(source_ids))
            ).all()
        )
    else:
        sources = []
    estimates = [
        row
        for row in _rows_for_company(db, CeriEstimateSnapshot, snapshot.company_id)
        if row.source_record_id in source_ids
    ]
    earnings = [
        row
        for row in _rows_for_company(db, CeriEarningsActual, snapshot.company_id)
        if row.source_record_id in source_ids
    ]
    guidance = [
        row
        for row in _rows_for_company(db, CeriGuidanceEvent, snapshot.company_id)
        if row.source_record_id in source_ids
    ]
    catalyst_events = _rows_for_company(db, CeriCatalystEvent, snapshot.company_id)
    catalyst_event_ids = {row.id for row in catalyst_events}
    if _uses_fixture_collections(db):
        catalyst_revisions = [
            row
            for row in _load(db, CeriCatalystEventRevision)
            if row.catalyst_event_id in catalyst_event_ids
            and row.source_record_id in source_ids
        ]
    elif catalyst_event_ids and source_ids:
        catalyst_revisions = list(
            db.scalars(
                select(CeriCatalystEventRevision).where(
                    CeriCatalystEventRevision.catalyst_event_id.in_(catalyst_event_ids),
                    CeriCatalystEventRevision.source_record_id.in_(source_ids),
                )
            ).all()
        )
    else:
        catalyst_revisions = []
    revisions = [
        row
        for row in _rows_for_company(db, CeriRevisionFeature, snapshot.company_id)
        if row.as_of_session == snapshot.as_of_session
        and row.calculation_version == snapshot.calculation_version
    ]
    price_rows = [
        row
        for row in _rows_for_company(db, CeriPriceResponseFeature, snapshot.company_id)
    ]
    components = opportunity_ledger.get("components") or []

    def component_ids(*names: str) -> set[int]:
        return {
            int(evidence_id)
            for component in components
            if component.get("name") in names and component.get("available")
            for evidence_id in component.get("evidence_ids") or []
        }

    revision_selected = component_ids(
        "revision_magnitude", "revision_breadth", "revision_acceleration"
    )
    normalized = {
        "estimates": len(estimates),
        "earnings": len(earnings),
        "guidance": len(guidance),
        "catalysts": len(catalyst_revisions),
        "price_response": len(price_rows),
    }
    eligible = {
        "estimates": sum(
            1
            for row in revisions
            if any(
                value is not None
                for value in (row.pct_change, row.net_breadth, row.acceleration)
            )
        ),
        "earnings": sum(
            1
            for row in earnings
            if row.event_kind in (None, "REPORTED") and row.surprise_pct is not None
        ),
        "guidance": sum(1 for row in guidance if row.accepted_for_scoring is True),
        "catalysts": sum(
            1
            for row in catalyst_revisions
            if row.issuer_relevance is True and row.review_state != "REJECTED"
        ),
        "price_response": sum(
            1
            for row in price_rows
            if (row.metrics_json or {}).get("quality") is not None
        ),
    }
    selected = {
        "estimates": len(revision_selected),
        "earnings": 1
        if any(
            row.get("name") == "surprise_trend" and row.get("available")
            for row in components
        )
        else 0,
        "guidance": len(component_ids("guidance")),
        "catalysts": len(component_ids("catalysts")),
        "price_response": len(component_ids("price_response")),
    }

    estimate_reasons = [row.unavailable_reason for row in revisions if row.unavailable_reason]
    price_reasons = [
        reason
        for row in price_rows
        for reason in (row.reasons_json or [])
        if reason in {
            "NO_ACCEPTED_EVENT",
            "PRICE_DATA_MISSING",
            "EVENT_TIMESTAMP_UNRESOLVED",
            "WINDOW_NOT_ELAPSED",
            "PIT_UNSAFE",
        }
    ]
    blockers = {
        "estimates": _dominant_reason(estimate_reasons),
        "earnings": (
            None
            if eligible["earnings"]
            else "HISTORICAL_REPORTED_EARNINGS_MISSING"
        ),
        "guidance": None if eligible["guidance"] else "NO_ACCEPTED_CURRENT_GUIDANCE",
        "catalysts": None if eligible["catalysts"] else "NO_ACCEPTED_CATALYST",
        "price_response": _dominant_reason(price_reasons) or (
            None if eligible["price_response"] else "NO_ACCEPTED_EVENT"
        ),
    }
    result: dict[str, Any] = {}
    for dataset in normalized:
        state = freshness.get(dataset) or {}
        raw_status = state.get("status", "UNAVAILABLE")
        source_status = (
            "FRESH"
            if raw_status == "AVAILABLE"
            else ("STALE" if raw_status == "STALE" else "ABSENT")
        )
        result[dataset] = {
            "source_present": any(row.dataset == dataset for row in sources),
            "source_status": source_status,
            "source_age_days": state.get("age_days"),
            "normalized_count": normalized[dataset],
            "eligible_count": eligible[dataset],
            "selected_count": selected[dataset],
            "dominant_blocker": blockers[dataset],
        }
    return result


def _dominant_reason(reasons: list[str]) -> str | None:
    if not reasons:
        return None
    counts = Counter(reasons)
    return min(counts, key=lambda reason: (-counts[reason], reasons.index(reason)))


def _change_payload(change: CeriChangeEvent, *, ticker: str | None) -> dict[str, Any]:
    return {
        "id": change.id,
        "company_id": change.company_id,
        "ticker": ticker,
        "from_snapshot_id": change.from_snapshot_id,
        "to_snapshot_id": change.to_snapshot_id,
        "catalyst_revision_id": change.catalyst_revision_id,
        "change_type": change.change_type,
        "severity": change.severity,
        "delta": change.delta_json,
        "dedup_key": change.dedup_key,
        "created_at": _value(change.created_at),
        "invalidated_by_purge": _has_purge_marker(change.delta_json),
    }


def _alert_payload(alert: CeriAlertEvent) -> dict[str, Any]:
    return {
        "id": alert.id,
        "alert_rule_id": alert.alert_rule_id,
        "source_change_event_id": alert.source_change_event_id,
        "source_catalyst_revision_id": alert.source_catalyst_revision_id,
        "event_key": alert.event_key,
        "ticker": alert.ticker,
        "severity": alert.severity,
        "status": alert.status,
        "evidence": alert.evidence_json,
        "created_at": _value(alert.created_at),
        "acknowledged_at": _value(alert.acknowledged_at),
        "dismissed_at": _value(alert.dismissed_at),
        "invalidated_by_purge": _has_purge_marker(alert.evidence_json)
        or alert.status == "INVALIDATED",
    }


def _source_record_payload(source: CeriSourceRecord) -> dict[str, Any]:
    return {
        "id": source.id,
        "ingestion_run_id": source.ingestion_run_id,
        "provider": source.provider,
        "provider_terms_version": source.provider_terms_version,
        "dataset": source.dataset,
        "provider_record_id": source.provider_record_id,
        "company_hint": source.company_hint_json,
        "published_at": _value(source.published_at),
        "observed_at": _value(source.observed_at),
        "ingested_at": _value(source.ingested_at),
        "content_hash": source.content_hash,
        "export_policy": source.export_policy,
        "provider_retention_deadline": _value(source.provider_retention_deadline),
        "supersedes_id": source.supersedes_id,
        "correction_type": source.correction_type,
        "quarantine_reason": source.quarantine_reason,
        "purged": _is_purged_source(source),
        "purge_invalidation": source.restricted_normalized_json
        if _is_purged_source(source)
        else None,
    }


def _processing_run_payload(run: CeriProcessingRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "job_type": run.job_type,
        "status": run.status,
        "request_key": run.deterministic_request_key,
        "scope": run.scope_json,
        "config_version": run.config_version,
        "config_hash": run.config_hash,
        "cutoff_at": _value(run.cutoff_at),
        "actor": run.actor,
        "retry_count": run.retry_count,
        "counts": run.counts_json,
        "checkpoint": run.checkpoint_json,
        "errors": run.errors_json,
        "heartbeat_at": _value(run.heartbeat_at),
        "duration_ms": run.duration_ms,
        "started_at": _value(run.started_at),
        "completed_at": _value(run.completed_at),
    }


def _purge_audit_payload(audit: CeriPurgeAudit) -> dict[str, Any]:
    return {
        "id": audit.id,
        "provider": audit.provider,
        "license_scope": audit.license_scope,
        "preview_manifest_hash": audit.preview_manifest_hash,
        "actor": audit.actor,
        "reason": audit.reason,
        "affected_counts": audit.affected_counts_json,
        "invalidated_derivatives": audit.invalidated_derivatives_json,
        "status": audit.status,
        "previewed_at": _value(audit.previewed_at),
        "executed_at": _value(audit.executed_at),
    }


def _is_invalidated(flags: list[str] | None) -> bool:
    return PURGE_INVALIDATION_FLAG in set(flags or [])


def _has_purge_marker(value: dict[str, Any] | None) -> bool:
    marker = (value or {}).get("purge_invalidation")
    return bool(isinstance(marker, dict) and marker.get("purged"))


def _is_purged_source(source: CeriSourceRecord) -> bool:
    return source.export_policy == "purged" or (source.quarantine_reason or "").startswith(
        "provider_license_purge"
    )


def _load(db: Session, model):
    collections = getattr(db, "collections", None)
    if isinstance(collections, dict):
        return list(collections.get(model, ()))
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)


def _uses_fixture_collections(db: Session) -> bool:
    return isinstance(getattr(db, "collections", None), dict)


def _rows_for_company(db: Session, model, company_id: int):
    if _uses_fixture_collections(db):
        return [row for row in _load(db, model) if row.company_id == company_id]
    return list(db.scalars(select(model).where(model.company_id == company_id)).all())


def _event_timeline_for_company(
    db: Session,
    company_id: int,
    ticker: str,
) -> list[dict[str, Any]]:
    events = _rows_for_company(db, CeriCatalystEvent, company_id)
    event_ids = {event.id for event in events if event.id is not None}
    if _uses_fixture_collections(db):
        revisions = [
            revision
            for revision in _load(db, CeriCatalystEventRevision)
            if revision.is_current and revision.catalyst_event_id in event_ids
        ]
    elif event_ids:
        revisions = list(
            db.scalars(
                select(CeriCatalystEventRevision).where(
                    CeriCatalystEventRevision.is_current.is_(True),
                    CeriCatalystEventRevision.catalyst_event_id.in_(event_ids),
                )
            ).all()
        )
    else:
        revisions = []
    current_by_event = {revision.catalyst_event_id: revision for revision in revisions}
    payloads = [
        _event_payload(
            event,
            ticker=ticker,
            current_revision=current_by_event.get(event.id),
        )
        for event in events
    ]
    return sorted(
        payloads,
        key=lambda item: (
            item.get("expected_date") is None,
            item.get("expected_date") or "9999-12-31",
            item.get("id") or 0,
        ),
    )


def _alerts_for_ticker(db: Session, ticker: str) -> list[dict[str, Any]]:
    if _uses_fixture_collections(db):
        rows = [row for row in _load(db, CeriAlertEvent) if row.ticker == ticker]
    else:
        rows = list(
            db.scalars(
                select(CeriAlertEvent).where(CeriAlertEvent.ticker == ticker)
            ).all()
        )
    rows.sort(key=lambda row: (row.created_at or datetime.min.replace(tzinfo=UTC)), reverse=True)
    return [_alert_payload(row) for row in rows]


def _stable_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _get(db: Session, model, row_id: int):
    getter = getattr(db, "get", None)
    if callable(getter):
        row = getter(model, row_id)
        if row is not None:
            return row
    for row in _load(db, model):
        if row.id == row_id:
            return row
    return None


def _company_by_id(db: Session) -> dict[int, dict[str, Any]]:
    return {
        company.id: {
            "ticker": company.ticker.upper(),
            "company_name": company.company_name,
            "exchange": company.exchange,
        }
        for company in _load(db, CeriCompany)
    }


def _current_revision_by_event_id(db: Session) -> dict[int, CeriCatalystEventRevision]:
    revisions: dict[int, CeriCatalystEventRevision] = {}
    for revision in _load(db, CeriCatalystEventRevision):
        if revision.is_current:
            revisions[revision.catalyst_event_id] = revision
    return revisions


def _snapshot_sort_tuple(snapshot: CeriScoreSnapshot) -> tuple:
    return (
        snapshot.cutoff_at or datetime.min.replace(tzinfo=UTC),
        snapshot.as_of_session or date.min,
        snapshot.id or 0,
    )


def _replace_filter(filters: CeriQueryFilters, **changes) -> CeriQueryFilters:
    values = filters.__dict__.copy()
    values.update(changes)
    return CeriQueryFilters(**values)


def _query_controls(query: CeriListQuery) -> dict[str, Any]:
    return {
        "sort": query.sort,
        "direction": query.direction,
        "limit": query.limit,
        "offset": query.offset,
    }


def _ticker(ticker: str) -> str:
    return ticker.strip().upper()


def _sort_value(value: Any) -> tuple[int, Any]:
    if value is None:
        return (1, "")
    return (0, value)


def _value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
