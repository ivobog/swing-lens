from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import String, and_, cast, func, or_, select, text
from sqlalchemy.orm import Session, load_only

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriAlertRule,
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
from app.services.ceri.change_semantics import (
    ChangeGroup,
    ComparisonState,
    change_dimensions,
    change_group,
)
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.constants import (
    CERI_HIGH_OPPORTUNITY_THRESHOLD,
    CERI_LOW_RISK_THRESHOLD,
)
from app.services.ceri.enums import CeriDataset, HistoricalViewMode
from app.services.ceri.feature_flags import ceri_flags
from app.services.ceri.freshness_service import (
    FeedFreshness,
    evidence_observation_timestamp,
    freshness_age_days,
    global_feed_freshness_from_runs,
    ticker_feed_freshness_from_runs,
)
from app.services.ceri.guidance_normalizer import guidance_eligibility_reason
from app.services.ceri.provider_cost_ledger import ProviderCostLedger
from app.services.ceri.snapshot_service import CeriSnapshotService

PURGE_INVALIDATION_FLAG = "provider_license_purge_invalidated"
OPERATIONS_DETAIL_LIMIT = 200
OPERATIONS_REPRODUCTION_SAMPLE_LIMIT = 25

_SOURCE_RECORD_OPERATIONS_COLUMNS = (
    CeriSourceRecord.id,
    CeriSourceRecord.ingestion_run_id,
    CeriSourceRecord.provider,
    CeriSourceRecord.provider_terms_version,
    CeriSourceRecord.dataset,
    CeriSourceRecord.provider_record_id,
    CeriSourceRecord.company_hint_json,
    CeriSourceRecord.published_at,
    CeriSourceRecord.observed_at,
    CeriSourceRecord.ingested_at,
    CeriSourceRecord.content_hash,
    CeriSourceRecord.export_policy,
    CeriSourceRecord.provider_retention_deadline,
    CeriSourceRecord.supersedes_id,
    CeriSourceRecord.correction_type,
    CeriSourceRecord.quarantine_reason,
    CeriSourceRecord.restricted_normalized_json,
)


@dataclass(frozen=True)
class _FreshnessRecord:
    provider: str
    dataset: str
    last_successful_check_at: datetime | None
    coverage: dict[str, int] | None = None


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
    from_run_id: int | None = None
    to_run_id: int | None = None
    change_group: str | None = None
    change_type: str | None = None
    importance: str | None = None
    signal_class: str | None = None
    min_delta: float | None = None
    alert_status: str | None = None
    history_scope: str | None = None
    include_non_comparable: bool = False
    include_ineligible: bool = False


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
        # Instance-scoped only: route handlers may safely reuse already-loaded rows,
        # while separate requests always receive a fresh service and cache.
        self._score_snapshots_by_id: dict[int, CeriScoreSnapshot] = {}

    def latest(self, db: Session, query: CeriListQuery) -> dict[str, Any]:
        self._validate(query)
        snapshots = self._filtered_snapshots(db, query.filters)
        latest_by_ticker: dict[str, CeriScoreSnapshot] = {}
        for snapshot in snapshots:
            current = latest_by_ticker.get(snapshot.ticker.upper())
            if current is None or _snapshot_sort_tuple(snapshot) > _snapshot_sort_tuple(current):
                latest_by_ticker[snapshot.ticker.upper()] = snapshot
        return self._snapshot_page(
            list(latest_by_ticker.values()),
            db=db,
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
            earnings_surprise_history=[_earnings_payload(row) for row in company_earnings],
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
        change_rows = _load(db, CeriChangeEvent)
        referenced_snapshot_ids = {
            snapshot_id
            for change in change_rows
            for snapshot_id in (change.from_snapshot_id, change.to_snapshot_id)
            if snapshot_id is not None
        }
        snapshots = self._snapshots_for_ids(db, referenced_snapshot_ids)
        revisions = {row.id: row for row in _load(db, CeriCatalystEventRevision)}
        revision_features = {row.id: row for row in _load(db, CeriRevisionFeature)}
        events = {row.id: row for row in _load(db, CeriCatalystEvent)}
        guidance = {row.id: row for row in _load(db, CeriGuidanceEvent)}
        latest_snapshot_ids = _latest_snapshot_ids_by_company(snapshots.values())
        items = []
        for change in change_rows:
            ticker = company_by_id.get(change.company_id, {}).get("ticker")
            if query.filters.ticker and ticker != _ticker(query.filters.ticker):
                continue
            if query.filters.changed_since and change.created_at < query.filters.changed_since:
                continue
            item = _change_payload(
                change,
                ticker=ticker,
                snapshots=snapshots,
                revisions=revisions,
                revision_features=revision_features,
                events=events,
                guidance=guidance,
                latest_snapshot_ids=latest_snapshot_ids,
                change_thresholds=self.config.change_thresholds,
            )
            if not _change_matches_filters(item, query.filters):
                continue
            items.append(item)
        payload = self._page(
            items,
            query=query,
            sort_aliases={
                "created_at": "created_at",
                "severity": "severity",
                "ticker": "ticker",
                "id": "id",
            },
        )
        payload["comparison_context"] = _comparison_context(
            items, snapshots.values(), filters=query.filters
        )
        return payload

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
        company_by_id = _company_by_id(db)
        snapshots = {row.id: row for row in _load(db, CeriScoreSnapshot)}
        revisions = {row.id: row for row in _load(db, CeriCatalystEventRevision)}
        revision_features = {row.id: row for row in _load(db, CeriRevisionFeature)}
        events = {row.id: row for row in _load(db, CeriCatalystEvent)}
        guidance = {row.id: row for row in _load(db, CeriGuidanceEvent)}
        changes = {row.id: row for row in _load(db, CeriChangeEvent)}
        rules = {row.id: row for row in _load(db, CeriAlertRule)}
        latest_snapshot_ids = _latest_snapshot_ids_by_company(snapshots.values())
        items = []
        for alert in _load(db, CeriAlertEvent):
            if query.filters.ticker and alert.ticker.upper() != _ticker(query.filters.ticker):
                continue
            if query.filters.alert_status and alert.status != query.filters.alert_status:
                continue
            change = changes.get(alert.source_change_event_id)
            change_payload = (
                _change_payload(
                    change,
                    ticker=company_by_id.get(change.company_id, {}).get("ticker"),
                    snapshots=snapshots,
                    revisions=revisions,
                    revision_features=revision_features,
                    events=events,
                    guidance=guidance,
                    latest_snapshot_ids=latest_snapshot_ids,
                    change_thresholds=self.config.change_thresholds,
                )
                if change is not None
                else None
            )
            item = _alert_payload(alert, change=change_payload, rule=rules.get(alert.alert_rule_id))
            if query.filters.importance and item["importance"] != query.filters.importance:
                continue
            if query.filters.signal_class and item["signal_class"] != query.filters.signal_class:
                continue
            if query.filters.history_scope and item["history_scope"] != query.filters.history_scope:
                continue
            items.append(item)
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

    def operations_quarantine(
        self,
        db: Session,
        query: CeriListQuery,
        *,
        known_total: int | None = None,
    ) -> dict[str, Any]:
        if not _uses_fixture_collections(db):
            return self._database_operations_quarantine(db, query, known_total=known_total)
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

    def operations_conflicts(
        self,
        db: Session,
        query: CeriListQuery,
        *,
        known_total: int | None = None,
    ) -> dict[str, Any]:
        if not _uses_fixture_collections(db):
            return self._database_operations_conflicts(db, query, known_total=known_total)
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

    def operations_stale(
        self,
        db: Session,
        query: CeriListQuery,
        *,
        known_total: int | None = None,
    ) -> dict[str, Any]:
        if not _uses_fixture_collections(db):
            return self._database_operations_stale(db, query, known_total=known_total)
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
        if not _uses_fixture_collections(db):
            return self._database_operations_status(db)
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
            "dataset_freshness": self._dataset_freshness(ingestion_runs),
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

    def _database_operations_quarantine(
        self,
        db: Session,
        query: CeriListQuery,
        *,
        known_total: int | None = None,
    ) -> dict[str, Any]:
        self._validate_operations_sort(
            query,
            {
                "ingested_at": CeriSourceRecord.ingested_at,
                "provider": CeriSourceRecord.provider,
                "id": CeriSourceRecord.id,
            },
        )
        predicate = CeriSourceRecord.quarantine_reason.is_not(None)
        total = (
            int(known_total)
            if known_total is not None
            else int(
                db.scalar(select(func.count()).select_from(CeriSourceRecord).where(predicate)) or 0
            )
        )
        statement = (
            select(CeriSourceRecord)
            .options(load_only(*_SOURCE_RECORD_OPERATIONS_COLUMNS))
            .where(predicate)
            .order_by(
                *_database_ordering(
                    query,
                    {
                        "ingested_at": CeriSourceRecord.ingested_at,
                        "provider": CeriSourceRecord.provider,
                        "id": CeriSourceRecord.id,
                    },
                )
            )
            .offset(query.offset)
            .limit(query.limit)
        )
        items = [_source_record_payload(row) for row in db.scalars(statement).all()]
        return _page_payload(items, total=total, query=query)

    def _database_operations_conflicts(
        self,
        db: Session,
        query: CeriListQuery,
        *,
        known_total: int | None = None,
    ) -> dict[str, Any]:
        self._validate_operations_sort(query, {"id": None, "effective_session": None})
        catalyst_predicate = _nonempty_json(CeriCatalystEventRevision.conflict_flags_json)
        feature_predicate = cast(CeriRevisionFeature.warnings_json, String).ilike("%conflict%")
        catalyst_count = 0
        feature_count = 0
        if known_total is None:
            catalyst_count = int(
                db.scalar(
                    select(func.count())
                    .select_from(CeriCatalystEventRevision)
                    .where(catalyst_predicate)
                )
                or 0
            )
        candidate_limit = query.offset + query.limit
        catalysts = list(
            db.scalars(
                select(CeriCatalystEventRevision)
                .where(catalyst_predicate)
                .order_by(CeriCatalystEventRevision.id.desc())
                .limit(candidate_limit)
            ).all()
        )
        items = [_catalyst_revision_payload(row) for row in catalysts]
        if query.filters.has_conflicts is not False:
            if known_total is None:
                feature_count = int(
                    db.scalar(
                        select(func.count())
                        .select_from(CeriRevisionFeature)
                        .where(feature_predicate)
                    )
                    or 0
                )
            features = db.scalars(
                select(CeriRevisionFeature)
                .where(feature_predicate)
                .order_by(CeriRevisionFeature.id.desc())
                .limit(candidate_limit)
            ).all()
            items.extend(_revision_feature_payload(row) for row in features)
        ordered = _ordered_nulls_last(
            items,
            value=lambda item: item.get(query.sort),
            tie=lambda item: (item.get("ticker") or "", item.get("id") or 0),
            descending=query.direction.lower() == "desc",
        )
        page_items = ordered[query.offset : query.offset + query.limit]
        return _page_payload(
            page_items,
            total=int(known_total) if known_total is not None else catalyst_count + feature_count,
            query=query,
        )

    def _database_operations_stale(
        self,
        db: Session,
        query: CeriListQuery,
        *,
        known_total: int | None = None,
    ) -> dict[str, Any]:
        self._validate_operations_sort(query, {"stale_days": None, "dataset": None, "id": None})
        candidate_limit = query.offset + query.limit
        total = int(known_total or 0)
        rows = []
        observed_at = _source_observed_at()
        for dataset, max_days in self._dataset_max_stale_days().items():
            predicate = _dataset_stale_predicate(dataset, max_days)
            if known_total is None:
                total += int(
                    db.scalar(select(func.count()).select_from(CeriSourceRecord).where(predicate))
                    or 0
                )
            if query.sort == "stale_days":
                primary = (
                    observed_at.asc() if query.direction.lower() == "desc" else observed_at.desc()
                )
            elif query.sort == "dataset":
                primary = (
                    CeriSourceRecord.dataset.desc()
                    if query.direction.lower() == "desc"
                    else CeriSourceRecord.dataset.asc()
                )
            else:
                primary = (
                    CeriSourceRecord.id.desc()
                    if query.direction.lower() == "desc"
                    else CeriSourceRecord.id.asc()
                )
            rows.extend(
                db.scalars(
                    select(CeriSourceRecord)
                    .options(load_only(*_SOURCE_RECORD_OPERATIONS_COLUMNS))
                    .where(predicate)
                    .order_by(primary, CeriSourceRecord.id.desc())
                    .limit(candidate_limit)
                ).all()
            )
        today = datetime.now(UTC).date()
        items = []
        for source in rows:
            observed = source.observed_at or source.published_at or source.ingested_at
            max_days = self._dataset_max_stale_days().get(source.dataset)
            row = _source_record_payload(source)
            row["stale_days"] = (today - observed.date()).days
            row["max_stale_days"] = max_days
            items.append(row)
        ordered = _ordered_nulls_last(
            items,
            value=lambda item: item.get(query.sort),
            tie=lambda item: (item.get("ticker") or "", item.get("id") or 0),
            descending=query.direction.lower() == "desc",
        )
        return _page_payload(
            ordered[query.offset : query.offset + query.limit],
            total=total,
            query=query,
        )

    def _database_operations_status(self, db: Session) -> dict[str, Any]:
        flags = ceri_flags()
        ingestion_status = _grouped_counts(db, CeriIngestionRun.status)
        processing_status = _grouped_counts(db, CeriProcessingRun.status)

        estimate_total, comparable_estimates, currency_missing, ambiguous_periods = db.execute(
            select(
                func.count(),
                func.count().filter(
                    CeriEstimateSnapshot.canonical_currency.is_not(None),
                    CeriEstimateSnapshot.canonical_scale.is_not(None),
                    CeriEstimateSnapshot.currency_verified.is_(True),
                ),
                func.count().filter(CeriEstimateSnapshot.canonical_currency.is_(None)),
                func.count().filter(CeriEstimateSnapshot.canonical_period_slot.is_(None)),
            )
        ).one()
        current_revision_predicate = and_(
            CeriRevisionFeature.config_hash == self.config.config_hash,
            CeriRevisionFeature.calculation_version == self.config.engine.calculation_version,
        )
        revision_total, revision_available = db.execute(
            select(
                func.count(),
                func.count().filter(CeriRevisionFeature.pct_change.is_not(None)),
            ).where(current_revision_predicate)
        ).one()
        current_snapshot_predicate = and_(
            CeriScoreSnapshot.config_hash == self.config.config_hash,
            CeriScoreSnapshot.calculation_version == self.config.engine.calculation_version,
        )
        snapshot_total, opportunity_rated, confidence_insufficient, risk_ceiling = db.execute(
            select(
                func.count(),
                func.count().filter(CeriScoreSnapshot.opportunity_score.is_not(None)),
                func.count().filter(CeriScoreSnapshot.data_confidence == "Insufficient"),
                func.count().filter(CeriScoreSnapshot.event_risk_score == 10.0),
            ).where(current_snapshot_predicate)
        ).one()
        guidance_rejected = int(
            db.scalar(
                select(func.count())
                .select_from(CeriGuidanceEvent)
                .where(
                    or_(
                        CeriGuidanceEvent.action == "UNKNOWN",
                        func.upper(CeriGuidanceEvent.confidence).not_in(("HIGH", "NORMAL")),
                        CeriGuidanceEvent.metric.is_(None),
                        CeriGuidanceEvent.period_type.is_(None),
                        and_(
                            CeriGuidanceEvent.metric == "EPS_DILUTED",
                            CeriGuidanceEvent.unit == "%",
                        ),
                        CeriGuidanceEvent.accepted_for_scoring.is_(False),
                    )
                )
            )
            or 0
        )
        catalyst_rejected, catalyst_unclassified = db.execute(
            select(
                func.count().filter(CeriCatalystEventRevision.issuer_relevance.is_not(True)),
                func.count().filter(
                    or_(
                        CeriCatalystEventRevision.issuer_relevance.is_(None),
                        CeriCatalystEventRevision.binary_eligible.is_(None),
                    )
                ),
            )
        ).one()
        reproduction_failures, reproduction_checked, reproduction_total = (
            _snapshot_reproduction_failure_count(db, self.config)
        )

        freshness_records = _database_freshness_records(db, self.config)

        quota_rows = db.execute(
            select(
                CeriIngestionRun.id,
                CeriIngestionRun.provider,
                CeriIngestionRun.dataset,
                CeriIngestionRun.quota_state_json,
            )
            .where(CeriIngestionRun.quota_state_json.is_not(None))
            .order_by(CeriIngestionRun.id.desc())
            .limit(OPERATIONS_DETAIL_LIMIT)
        ).all()
        processing_runs = db.scalars(
            select(CeriProcessingRun)
            .order_by(CeriProcessingRun.id.desc())
            .limit(OPERATIONS_DETAIL_LIMIT)
        ).all()
        purge_audits = db.scalars(
            select(CeriPurgeAudit).order_by(CeriPurgeAudit.id.desc()).limit(OPERATIONS_DETAIL_LIMIT)
        ).all()
        errors = _operations_errors(db)
        deployments = _operations_deployments(db)

        catalyst_conflicts = int(
            db.scalar(
                select(func.count())
                .select_from(CeriCatalystEventRevision)
                .where(_nonempty_json(CeriCatalystEventRevision.conflict_flags_json))
            )
            or 0
        )
        feature_conflicts = int(
            db.scalar(
                select(func.count())
                .select_from(CeriRevisionFeature)
                .where(cast(CeriRevisionFeature.warnings_json, String).ilike("%conflict%"))
            )
            or 0
        )
        quarantined_count = int(
            db.scalar(
                select(func.count())
                .select_from(CeriSourceRecord)
                .where(CeriSourceRecord.quarantine_reason.is_not(None))
            )
            or 0
        )
        stale_count = _database_stale_count(db, self._dataset_max_stale_days())

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
                "ceri_estimate_comparable_pct": _percentage(comparable_estimates, estimate_total),
                "ceri_estimate_currency_missing_pct": _percentage(currency_missing, estimate_total),
                "ceri_period_slot_ambiguous_count": int(ambiguous_periods or 0),
                "ceri_revision_available_pct": _percentage(revision_available, revision_total),
                "ceri_opportunity_rated_pct": _percentage(opportunity_rated, snapshot_total),
                "ceri_confidence_insufficient_pct": _percentage(
                    confidence_insufficient, snapshot_total
                ),
                "ceri_guidance_rejected_count": guidance_rejected,
                "ceri_catalyst_relevance_rejected_count": int(catalyst_rejected or 0),
                "ceri_catalyst_unclassified_count": int(catalyst_unclassified or 0),
                "ceri_event_risk_ceiling_count": int(risk_ceiling or 0),
                "ceri_snapshot_reproduction_failure_count": reproduction_failures,
                "ceri_snapshot_reproduction_checked_count": reproduction_checked,
                "ceri_snapshot_reproduction_total_count": reproduction_total,
                "ceri_snapshot_reproduction_truncated": (reproduction_checked < reproduction_total),
            },
            "dataset_freshness": self._dataset_freshness(freshness_records),
            "ingestion_status": ingestion_status,
            "processing_status": processing_status,
            "quota_state": [
                {
                    "ingestion_run_id": row.id,
                    "provider": row.provider,
                    "dataset": row.dataset,
                    "quota_state": row.quota_state_json,
                }
                for row in quota_rows
            ],
            "errors": errors,
            "quarantined_count": quarantined_count,
            "conflicted_count": catalyst_conflicts + feature_conflicts,
            "stale_count": stale_count,
            "processing_runs": [_processing_run_payload(run) for run in processing_runs],
            "processing_runs_total": sum(processing_status.values()),
            "operations_detail_limit": OPERATIONS_DETAIL_LIMIT,
            "alert_delivery": _grouped_counts(db, CeriAlertEvent.status),
            "provider_terms_version": self.config.retention.provider_terms_version,
            "provider_cost_runtime_storage_ledger": _provider_cost_summary(db),
            "deployment_identities": deployments,
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

    def _dataset_max_stale_days(self) -> dict[str, int]:
        return {
            dataset.value: policy.max_stale_days
            for dataset, policy in self.config.datasets.items()
            if policy.max_stale_days is not None
        }

    def _validate_operations_sort(self, query: CeriListQuery, aliases: dict[str, Any]) -> None:
        self._validate(query)
        if query.sort not in aliases:
            raise CeriQueryError("INVALID_SORT", f"Unsupported CERI sort: {query.sort}")

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
        if (filters.run_id is not None or filters.ticker) and not _uses_fixture_collections(db):
            predicates = []
            if filters.run_id is not None:
                predicates.append(CeriScoreSnapshot.run_id == filters.run_id)
            if filters.ticker:
                predicates.append(CeriScoreSnapshot.ticker == _ticker(filters.ticker))
            snapshot_rows = list(db.scalars(select(CeriScoreSnapshot).where(*predicates)).all())
        else:
            snapshot_rows = _load(db, CeriScoreSnapshot)
        self._remember_snapshots(snapshot_rows)
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

    def _remember_snapshots(self, snapshots: list[CeriScoreSnapshot]) -> None:
        for snapshot in snapshots:
            if snapshot.id is not None:
                self._score_snapshots_by_id[int(snapshot.id)] = snapshot

    def _snapshots_for_ids(
        self,
        db: Session,
        snapshot_ids: set[int],
    ) -> dict[int, CeriScoreSnapshot]:
        missing_ids = snapshot_ids.difference(self._score_snapshots_by_id)
        if missing_ids:
            if _uses_fixture_collections(db):
                rows = [row for row in _load(db, CeriScoreSnapshot) if row.id in missing_ids]
            else:
                rows = list(
                    db.scalars(
                        select(CeriScoreSnapshot).where(CeriScoreSnapshot.id.in_(missing_ids))
                    ).all()
                )
            self._remember_snapshots(rows)
        return {
            snapshot_id: self._score_snapshots_by_id[snapshot_id]
            for snapshot_id in snapshot_ids
            if snapshot_id in self._score_snapshots_by_id
        }

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
        ordered = _ordered_nulls_last(
            items,
            value=lambda item: item.get(sort_key),
            tie=lambda item: (item.get("ticker") or "", item.get("id") or 0),
            descending=query.direction.lower() == "desc",
        )
        total = len(ordered)
        page_items = ordered[query.offset : query.offset + query.limit]
        return _page_payload(page_items, total=total, query=query)

    def _snapshot_page(
        self,
        snapshots: list[CeriScoreSnapshot],
        *,
        db: Session,
        query: CeriListQuery,
        sort_aliases: dict[str, str],
    ) -> dict[str, Any]:
        self._validate(query)
        sort_key = sort_aliases.get(query.sort)
        if sort_key is None:
            raise CeriQueryError("INVALID_SORT", f"Unsupported CERI sort: {query.sort}")
        ordered = _ordered_nulls_last(
            snapshots,
            value=lambda snapshot: getattr(snapshot, sort_key),
            tie=lambda snapshot: (snapshot.ticker, snapshot.id or 0),
            descending=query.direction.lower() == "desc",
        )
        page_snapshots = ordered[query.offset : query.offset + query.limit]
        lineage_audits = _revision_lineage_audits(db, page_snapshots)
        page_items = [
            _score_snapshot_payload(
                snapshot,
                db=db,
                revision_lineage_audit=lineage_audits.get(snapshot.id),
            )
            for snapshot in page_snapshots
        ]
        # The list uses staged evidence diagnostics. The raw freshness mapping
        # remains available on ticker detail but is suppressed here so source
        # presence is never presented as evidence availability.
        for item in page_items:
            item["freshness"] = {}
        payload = _page_payload(
            page_items,
            total=len(ordered),
            query=query,
        )
        payload["summary"] = snapshot_population_summary(ordered)
        return payload

    def _dataset_freshness(
        self,
        records: list[CeriIngestionRun] | list[_FreshnessRecord],
    ) -> list[dict[str, Any]]:
        thresholds = {
            dataset.value: policy.max_stale_days for dataset, policy in self.config.datasets.items()
        }
        now = datetime.now(UTC)
        if records and isinstance(records[0], _FreshnessRecord):
            states = {
                (record.provider, record.dataset): FeedFreshness(
                    provider=record.provider,
                    dataset=record.dataset,
                    last_successful_check_at=record.last_successful_check_at,
                    age_days=(
                        freshness_age_days(
                            now,
                            record.last_successful_check_at,
                            timezone_name=self.config.engine.timezone,
                        )
                        if record.last_successful_check_at is not None
                        else None
                    ),
                    max_stale_days=thresholds[record.dataset],
                    status=(
                        "UNAVAILABLE"
                        if record.last_successful_check_at is None
                        else "FRESH"
                        if freshness_age_days(
                            now,
                            record.last_successful_check_at,
                            timezone_name=self.config.engine.timezone,
                        )
                        <= thresholds[record.dataset]
                        else "STALE"
                    ),
                    scope="PROVIDER_GLOBAL",
                )
                for record in records
                if record.dataset in thresholds
            }
            coverage = {
                (record.provider, record.dataset): record.coverage or {} for record in records
            }
        else:
            states = global_feed_freshness_from_runs(
                records,
                cutoff_at=now,
                max_stale_days=thresholds,
                timezone_name=self.config.engine.timezone,
            )
            coverage = {}
        return [
            {
                "provider": provider,
                "dataset": dataset,
                "scope": state.scope,
                "semantic": "PROVIDER_FEED_FRESHNESS",
                "latest_successful_check_at": _value(state.last_successful_check_at),
                "latest_observed_at": _value(state.last_successful_check_at),
                "age_days": state.age_days,
                "max_stale_days": state.max_stale_days,
                "status": state.status,
                "fresh": state.status == "FRESH",
                "ticker_coverage": coverage.get((provider, dataset), {}),
            }
            for (provider, dataset), state in sorted(states.items())
        ]


def _score_snapshot_payload(
    snapshot: CeriScoreSnapshot,
    *,
    db: Session | None = None,
    revision_lineage_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cutoff_date = (
        snapshot.cutoff_at.date()
        if snapshot.cutoff_at is not None
        else (snapshot.as_of_session or date.min)
    )
    opportunity_ledger = snapshot.opportunity_ledger_json or {}
    confidence_ledger = snapshot.confidence_ledger_json or {}
    risk_ledger = snapshot.event_risk_ledger_json or {}
    stored_opportunity_coverage = (
        snapshot.opportunity_coverage_pct
        if snapshot.opportunity_coverage_pct is not None
        else opportunity_ledger.get("coverage_pct")
    )
    components = opportunity_ledger.get("components") or []
    ledger_coverage = _component_coverage_pct(components)
    opportunity_coverage = ledger_coverage if components else stored_opportunity_coverage
    coverage_matches_ledger = (
        stored_opportunity_coverage is None
        or opportunity_coverage is None
        or abs(float(stored_opportunity_coverage) - float(opportunity_coverage)) < 1e-6
    )
    opportunity = CeriOpportunityDto(
        score=snapshot.opportunity_score,
        rated=bool(
            snapshot.opportunity_score is not None and snapshot.opportunity_unrated_reason is None
        ),
        coverage_pct=opportunity_coverage,
        minimum_required_coverage_pct=opportunity_ledger.get("minimum_required_coverage_pct"),
        unrated_reason=snapshot.opportunity_unrated_reason,
        reweighted=opportunity_ledger.get("reweighted", False),
        coverage_matches_ledger=coverage_matches_ledger,
    )
    risk_evidence_state = _risk_evidence_state(risk_ledger, snapshot.warnings_json or [])
    event_risk = CeriRiskDto(
        score=snapshot.event_risk_score,
        dominant_reason=risk_ledger.get("dominant_component"),
        evidence_state=risk_evidence_state,
        low_risk_eligible=risk_evidence_state == "SUFFICIENT",
    )
    confidence = CeriConfidenceDto(
        label=snapshot.data_confidence,
        score=confidence_ledger.get("score"),
        coverage_pct=snapshot.coverage_pct,
        gates=confidence_ledger.get("gates") or [],
        caps=confidence_ledger.get("caps") or [],
    )
    freshness = _snapshot_freshness(db, snapshot) if db is not None else {}
    revision_lineage_audit = revision_lineage_audit or {}
    display_warnings = list(snapshot.warnings_json or [])
    if revision_lineage_audit.get("revision_value_mismatches"):
        display_warnings.append("revision_feature_lineage_mismatch")
    lineage_reconciliation = _lineage_reconciliation(
        opportunity_ledger,
        snapshot.evidence_lineage_json or {},
    )
    lineage_reconciliation.update(revision_lineage_audit)
    lineage_reconciliation["valid"] = bool(
        lineage_reconciliation.get("valid", True)
        and not lineage_reconciliation.get("revision_value_mismatches")
    )
    lineage_reconciliation["requires_rebuild"] = bool(
        lineage_reconciliation.get("revision_value_mismatches")
    )
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
        revision_evidence=(_current_revision_summary(db, snapshot) if db is not None else {}),
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
        warnings=display_warnings or None,
        warning_summary=_warning_summary(display_warnings),
        lineage_reconciliation=lineage_reconciliation,
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
        "display_pct_change": _format_signed(feature.pct_change, suffix="%"),
        "pct_change_unit": feature.pct_change_unit,
        "upward_count": feature.upward_count,
        "downward_count": feature.downward_count,
        "raw_breadth_counts": {
            "upward_count": feature.upward_count,
            "downward_count": feature.downward_count,
        },
        "net_breadth": _value(feature.net_breadth),
        "display_net_breadth": _format_signed(feature.net_breadth),
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
            "display_value": _format_signed(feature.pct_change, suffix="%"),
            "unit": feature.pct_change_unit,
            "available": feature.pct_change is not None,
            "reason": feature.unavailable_reason,
            "upward_count": feature.upward_count,
            "downward_count": feature.downward_count,
            "breadth": _value(feature.net_breadth),
            "display_breadth": _format_signed(feature.net_breadth),
            "comparison_mode": feature.comparison_mode,
            "warnings": feature.warnings_json or [],
            "currency_caveat": (
                "Same-provider relative change; canonical currency unavailable."
                if feature.comparison_mode == "SAME_PROVIDER_RELATIVE"
                and "canonical_currency_unavailable_relative_only" in (feature.warnings_json or [])
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
    events = {event.id: event for event in _rows_for_company(db, CeriCatalystEvent, company_id)}
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
            dataset.value: {"status": "UNAVAILABLE", "age_days": None} for dataset in CeriDataset
        }
    source_ids = set((snapshot.component_json or {}).get("source_ids") or [])
    if _uses_fixture_collections(db):
        sources = [source for source in _load(db, CeriSourceRecord) if source.id in source_ids]
    elif source_ids:
        sources = list(
            db.scalars(select(CeriSourceRecord).where(CeriSourceRecord.id.in_(source_ids))).all()
        )
    else:
        sources = []
    if _uses_fixture_collections(db):
        provider_checks = [
            run
            for run in _load(db, CeriIngestionRun)
            if run.status == "COMPLETED"
            and run.completed_at is not None
            and run.completed_at <= snapshot.cutoff_at
            and str((run.scope_json or {}).get("ticker") or "").upper() == snapshot.ticker.upper()
        ]
    else:
        ticker_expression = func.upper(CeriIngestionRun.scope_json["ticker"].astext)
        provider_checks = list(
            db.scalars(
                select(CeriIngestionRun).where(
                    CeriIngestionRun.status == "COMPLETED",
                    CeriIngestionRun.completed_at.is_not(None),
                    CeriIngestionRun.completed_at <= snapshot.cutoff_at,
                    ticker_expression == snapshot.ticker.upper(),
                )
            ).all()
        )
    config = load_ceri_config()
    feed_states = ticker_feed_freshness_from_runs(
        provider_checks,
        ticker=snapshot.ticker,
        cutoff_at=snapshot.cutoff_at,
        max_stale_days={
            dataset.value: policy.max_stale_days for dataset, policy in config.datasets.items()
        },
        timezone_name=config.engine.timezone,
    )
    result: dict[str, Any] = {}
    for dataset in CeriDataset:
        matching = [source for source in sources if source.dataset == dataset.value]
        feed = feed_states[dataset.value]
        if not matching:
            result[dataset.value] = {
                "status": feed.status,
                "age_days": feed.age_days,
                "semantic": "PROVIDER_FEED_FRESHNESS",
                "provider_feed_status": feed.status,
                "provider_feed_age_days": feed.age_days,
                "provider_last_successful_check_at": _value(feed.last_successful_check_at),
                "evidence_status": "UNAVAILABLE",
                "evidence_retrieval_age_days": None,
                "evidence_observation_age_days": None,
                "timestamp_quality": None,
            }
            continue
        eligible = [
            source
            for source in matching
            if (source.retrieved_at or source.ingested_at) is not None
            and (source.retrieved_at or source.ingested_at) <= snapshot.cutoff_at
        ]
        if not eligible:
            result[dataset.value] = {
                "status": feed.status,
                "age_days": feed.age_days,
                "semantic": "PROVIDER_FEED_FRESHNESS",
                "provider_feed_status": feed.status,
                "provider_feed_age_days": feed.age_days,
                "provider_last_successful_check_at": _value(feed.last_successful_check_at),
                "evidence_status": "UNAVAILABLE",
                "evidence_retrieval_age_days": None,
                "evidence_observation_age_days": None,
                "timestamp_quality": None,
            }
            continue
        latest = max(eligible, key=lambda source: source.retrieved_at or source.ingested_at)
        retrieval_stamp = latest.retrieved_at or latest.ingested_at
        retrieval_age = freshness_age_days(
            snapshot.cutoff_at,
            retrieval_stamp,
            timezone_name=config.engine.timezone,
        )
        observation = evidence_observation_timestamp(
            latest,
            reference_at=snapshot.cutoff_at,
        )
        observation_age = freshness_age_days(
            snapshot.cutoff_at,
            observation.value,
            timezone_name=config.engine.timezone,
        )
        threshold = config.datasets[dataset].max_stale_days
        result[dataset.value] = {
            "status": feed.status,
            "age_days": feed.age_days,
            "semantic": "PROVIDER_FEED_FRESHNESS",
            "provider_feed_status": feed.status,
            "provider_feed_age_days": feed.age_days,
            "provider_last_successful_check_at": _value(feed.last_successful_check_at),
            "evidence_status": "AVAILABLE" if retrieval_age <= threshold else "STALE",
            "evidence_retrieval_age_days": retrieval_age,
            "evidence_last_retrieved_at": _value(retrieval_stamp),
            "evidence_observation_age_days": observation_age,
            "evidence_last_observed_at": _value(observation.value),
            "evidence_timestamp_field": observation.field_name,
            "ignored_future_timestamp_fields": list(observation.ignored_future_fields),
            "known_at": _value(observation.value),
            "timestamp_quality": observation.quality,
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
            db.scalars(select(CeriSourceRecord).where(CeriSourceRecord.id.in_(source_ids))).all()
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
            if row.catalyst_event_id in catalyst_event_ids and row.source_record_id in source_ids
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
        row for row in _rows_for_company(db, CeriPriceResponseFeature, snapshot.company_id)
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
                value is not None for value in (row.pct_change, row.net_breadth, row.acceleration)
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
            1 for row in price_rows if (row.metrics_json or {}).get("quality") is not None
        ),
    }
    selected = {
        "estimates": len(revision_selected),
        "earnings": 1
        if any(row.get("name") == "surprise_trend" and row.get("available") for row in components)
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
        if reason
        in {
            "NO_ACCEPTED_EVENT",
            "PRICE_DATA_MISSING",
            "EVENT_TIMESTAMP_UNRESOLVED",
            "WINDOW_NOT_ELAPSED",
            "PIT_UNSAFE",
        }
    ]
    blockers = {
        "estimates": _dominant_reason(estimate_reasons),
        "earnings": (None if eligible["earnings"] else "HISTORICAL_REPORTED_EARNINGS_MISSING"),
        "guidance": None if eligible["guidance"] else "NO_ACCEPTED_CURRENT_GUIDANCE",
        "catalysts": None if eligible["catalysts"] else "NO_ACCEPTED_CATALYST",
        "price_response": _dominant_reason(price_reasons)
        or (None if eligible["price_response"] else "NO_ACCEPTED_EVENT"),
    }
    result: dict[str, Any] = {}
    for dataset in normalized:
        state = freshness.get(dataset) or {}
        raw_status = state.get("evidence_status", state.get("status", "UNAVAILABLE"))
        source_status = (
            "FRESH"
            if raw_status == "AVAILABLE"
            else ("STALE" if raw_status == "STALE" else "ABSENT")
        )
        result[dataset] = {
            "source_present": any(row.dataset == dataset for row in sources),
            "source_status": source_status,
            "source_age_days": state.get("evidence_retrieval_age_days", state.get("age_days")),
            "normalized_count": normalized[dataset],
            "eligible_count": eligible[dataset],
            "selected_count": selected[dataset],
            "dominant_blocker": blockers[dataset],
            "evidence_state": _dataset_evidence_state(
                dataset,
                source_status=source_status,
                normalized_count=normalized[dataset],
                eligible_count=eligible[dataset],
                selected_count=selected[dataset],
            ),
        }
    return result


def _dataset_evidence_state(
    dataset: str,
    *,
    source_status: str,
    normalized_count: int,
    eligible_count: int,
    selected_count: int,
) -> str:
    prefix = "CATALYST" if dataset == "catalysts" else dataset.upper()
    if source_status == "ABSENT":
        return f"{prefix}_SOURCE_UNAVAILABLE"
    if source_status == "STALE":
        return f"{prefix}_SOURCE_STALE"
    if normalized_count == 0:
        return f"{prefix}_NONE_ELIGIBLE"
    if eligible_count == 0 or selected_count == 0:
        return f"{prefix}_EVIDENCE_INELIGIBLE"
    return f"{prefix}_SELECTED"


def _dominant_reason(reasons: list[str]) -> str | None:
    if not reasons:
        return None
    counts = Counter(reasons)
    return min(counts, key=lambda reason: (-counts[reason], reasons.index(reason)))


def _change_payload(
    change: CeriChangeEvent,
    *,
    ticker: str | None,
    snapshots: dict[int, CeriScoreSnapshot] | None = None,
    revisions: dict[int, CeriCatalystEventRevision] | None = None,
    revision_features: dict[int, CeriRevisionFeature] | None = None,
    events: dict[int, CeriCatalystEvent] | None = None,
    guidance: dict[int, CeriGuidanceEvent] | None = None,
    latest_snapshot_ids: dict[int, int] | None = None,
    change_thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    snapshots = snapshots or {}
    revisions = revisions or {}
    revision_features = revision_features or {}
    events = events or {}
    guidance = guidance or {}
    prior = snapshots.get(change.from_snapshot_id)
    current = snapshots.get(change.to_snapshot_id)
    revision = revisions.get(change.catalyst_revision_id)
    event = events.get(revision.catalyst_event_id) if revision is not None else None
    guidance_event = guidance.get(change.guidance_event_id)
    importance, signal_class = change_dimensions(change.change_type, change.delta_json)
    semantic = _semantic_change_values(
        change,
        prior=prior,
        current=current,
        revision=revision,
        event=event,
        guidance=guidance_event,
        revision_features=revision_features,
        change_thresholds=change_thresholds or {},
    )
    is_current = bool(
        (current is not None and (latest_snapshot_ids or {}).get(change.company_id) == current.id)
        or (revision is not None and revision.is_current)
    )
    payload = {
        "id": change.id,
        "company_id": change.company_id,
        "ticker": ticker,
        "from_snapshot_id": change.from_snapshot_id,
        "to_snapshot_id": change.to_snapshot_id,
        "catalyst_revision_id": change.catalyst_revision_id,
        "change_type": change.change_type,
        "group": change_group(change.change_type).value,
        "importance": change.importance or importance.value,
        "signal_class": change.signal_class or signal_class.value,
        "severity": change.severity,
        "comparison_state": change.comparison_state or ComparisonState.COMPARABLE.value,
        "delta": change.delta_json,
        "dedup_key": change.dedup_key,
        "created_at": _value(change.created_at),
        "semantic": semantic,
        "title": semantic["title"],
        "summary": semantic["summary"],
        "previous": semantic["previous"],
        "current": semantic["current"],
        "event": semantic.get("event"),
        "history_scope": "CURRENT" if is_current else "HISTORICAL",
        "technical": {
            "change_event_id": change.id,
            "from_snapshot_id": change.from_snapshot_id,
            "to_snapshot_id": change.to_snapshot_id,
            "catalyst_revision_id": change.catalyst_revision_id,
            "guidance_event_id": change.guidance_event_id,
            "dedup_key": change.dedup_key,
        },
        "invalidated_by_purge": _has_purge_marker(change.delta_json),
    }
    return payload


def _alert_payload(
    alert: CeriAlertEvent,
    *,
    change: dict[str, Any] | None = None,
    rule: CeriAlertRule | None = None,
) -> dict[str, Any]:
    evidence = alert.evidence_json or {}
    importance = alert.importance or (change or {}).get("importance") or alert.severity
    signal_class = alert.signal_class or (change or {}).get("signal_class") or "NEUTRAL"
    return {
        "id": alert.id,
        "alert_rule_id": alert.alert_rule_id,
        "source_change_event_id": alert.source_change_event_id,
        "source_catalyst_revision_id": alert.source_catalyst_revision_id,
        "event_key": alert.event_key,
        "ticker": alert.ticker,
        "severity": alert.severity,
        "importance": importance,
        "signal_class": signal_class,
        "status": alert.status,
        "alert_type": (change or {}).get("change_type") or evidence.get("change_type"),
        "change": change,
        "change_summary": (change or {}).get("summary") or "Underlying change unavailable",
        "risk": ((change or {}).get("current") or {}).get("event_risk"),
        "confidence": ((change or {}).get("current") or {}).get("confidence"),
        "history_scope": (change or {}).get("history_scope") or "HISTORICAL",
        "evidence": evidence,
        "alert_rule": {
            "id": rule.rule_id if rule is not None else evidence.get("alert_rule"),
            "version": rule.config_version
            if rule is not None
            else evidence.get("alert_rule_version"),
        },
        "created_at": _value(alert.created_at),
        "acknowledged_at": _value(alert.acknowledged_at),
        "dismissed_at": _value(alert.dismissed_at),
        "validity_classification": alert.validity_classification,
        "invalidated_reason": alert.invalidated_reason,
        "invalidated_at": _value(alert.invalidated_at),
        "actionable": alert.status not in {"INVALIDATED", "DISMISSED"},
        "technical": {
            "alert_id": alert.id,
            "event_key": alert.event_key,
            "source_change_event_id": alert.source_change_event_id,
            "source_catalyst_revision_id": alert.source_catalyst_revision_id,
            "dedup_identity": evidence.get("dedup_identity"),
            "cooldown_scope": evidence.get("cooldown_scope"),
            "cooldown_sessions": evidence.get("cooldown_sessions"),
            "raw_evidence": evidence,
        },
        "invalidated_by_purge": _has_purge_marker(alert.evidence_json)
        or alert.status == "INVALIDATED",
    }


def _semantic_change_values(
    change: CeriChangeEvent,
    *,
    prior: CeriScoreSnapshot | None,
    current: CeriScoreSnapshot | None,
    revision: CeriCatalystEventRevision | None,
    event: CeriCatalystEvent | None,
    guidance: CeriGuidanceEvent | None,
    revision_features: dict[int, CeriRevisionFeature],
    change_thresholds: dict[str, float],
) -> dict[str, Any]:
    kind = change.change_type
    title = _humanize_change_type(kind)
    previous = _snapshot_business_values(prior)
    current_values = _snapshot_business_values(current)
    delta = change.delta_json or {}
    if kind in {
        "NEW_CATALYST",
        "CATALYST_UPDATED",
        "CATALYST_CONFIRMED",
        "NEW_BINARY_EVENT",
        "CATALYST_DELAYED",
        "CATALYST_CANCELLED",
        "CATALYST_RESOLVED",
        "EVENT_COMPLETED",
        "EVENT_CANCELLED",
        "EVENT_RESOLVED",
    }:
        event_payload = _event_business_values(event, revision, delta)
        return {
            "title": title,
            "summary": _event_summary(event_payload),
            "previous": None,
            "current": None,
            "event": event_payload,
            "display_previous_current": False,
        }
    if kind.startswith("GUIDANCE_"):
        guidance_payload = _guidance_business_values(guidance, delta)
        prior_action = delta.get("prior_action")
        summary = f"{_label(prior_action, 'No accepted guidance')} -> {guidance_payload['action']}"
        return {
            "title": title,
            "summary": summary,
            "previous": {"action": prior_action},
            "current": guidance_payload,
            "event": None,
            "display_previous_current": True,
        }
    if kind.startswith("RISK_"):
        old_risk = previous.get("event_risk") if previous else None
        new_risk = current_values.get("event_risk") if current_values else None
        return {
            "title": title,
            "summary": f"Event Risk {_number(old_risk)} -> {_number(new_risk)}",
            "previous": previous,
            "current": current_values,
            "event": None,
            "display_previous_current": True,
        }
    if kind.startswith("REVISION_"):
        component_name = "revision_acceleration" if "ACCELERAT" in kind else "revision_magnitude"
        old_value = _component_value_from_payload(prior, component_name)
        new_value = _component_value_from_payload(current, component_name)
        unit = "pp/day" if component_name == "revision_acceleration" else "pp"
        previous_windows = _revision_window_values(prior, revision_features)
        current_windows = _revision_window_values(current, revision_features)
        summary = _revision_summary(
            previous_windows,
            current_windows,
            fallback=(
                f"{_component_label(component_name)} {_signed(old_value)}"
                f" -> {_signed(new_value)} {unit}"
            ),
        )
        threshold = (
            {"acceleration_delta": change_thresholds.get("acceleration_delta", 0.01)}
            if component_name == "revision_acceleration"
            else {"revision_pct_points": change_thresholds.get("revision_pct_points", 2.0)}
        )
        return {
            "title": title,
            "summary": summary,
            "previous": {
                "value": old_value,
                "component": component_name,
                "revision_windows": previous_windows,
            },
            "current": {
                "value": new_value,
                "component": component_name,
                "revision_windows": current_windows,
            },
            "event": None,
            "display_previous_current": True,
            "threshold": threshold,
        }
    if kind in {
        "BECAME_RATED",
        "BECAME_UNRATED",
        "OPPORTUNITY_CHANGED",
        "OPPORTUNITY_UPGRADED",
        "OPPORTUNITY_DOWNGRADED",
        "POSTURE_CHANGED",
    }:
        old_label = _opportunity_label(previous)
        new_label = _opportunity_label(current_values)
        return {
            "title": title,
            "summary": f"{old_label} -> {new_label}",
            "previous": previous,
            "current": current_values,
            "event": None,
            "display_previous_current": True,
        }
    return {
        "title": title,
        "summary": _data_quality_summary(delta),
        "previous": previous,
        "current": current_values,
        "event": None,
        "display_previous_current": bool(previous or current_values),
    }


def _snapshot_business_values(snapshot: CeriScoreSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    risk_ledger = snapshot.event_risk_ledger_json or {}
    return {
        "opportunity_score": snapshot.opportunity_score,
        "posture": snapshot.posture,
        "coverage_pct": snapshot.opportunity_coverage_pct,
        "confidence": snapshot.data_confidence,
        "event_risk": snapshot.event_risk_score,
        "risk_sufficiency": "SUFFICIENT"
        if risk_ledger.get("accepted_evidence") is True
        else "INSUFFICIENT",
        "risk_driver": risk_ledger.get("dominant_component"),
        "run_id": snapshot.run_id,
        "as_of_session": _value(snapshot.as_of_session),
    }


def _event_business_values(
    event: CeriCatalystEvent | None,
    revision: CeriCatalystEventRevision | None,
    delta: dict[str, Any],
) -> dict[str, Any]:
    issuer_relevance = (
        revision.issuer_relevance if revision is not None else delta.get("issuer_relevance")
    )
    materiality = revision.materiality if revision is not None else delta.get("materiality")
    binary_eligible = (
        revision.binary_eligible if revision is not None else delta.get("binary_eligible")
    )
    return {
        "canonical_event_id": event.id if event is not None else delta.get("canonical_event_id"),
        "event_revision_id": revision.id
        if revision is not None
        else delta.get("event_revision_id"),
        "category": event.category if event is not None else delta.get("category"),
        "subtype": event.subtype if event is not None else delta.get("subtype"),
        "subject": event.canonical_text if event is not None else delta.get("subject"),
        "status": revision.status if revision is not None else delta.get("status"),
        "direction": revision.direction if revision is not None else delta.get("direction"),
        "materiality": materiality,
        "confidence": revision.source_confidence
        if revision is not None
        else delta.get("confidence"),
        "announced_at": _value(revision.announced_at)
        if revision is not None
        else delta.get("announced_at"),
        "effective_session": _value(revision.effective_session)
        if revision is not None
        else delta.get("effective_session"),
        "expected_date": _value(revision.expected_date)
        if revision is not None
        else delta.get("expected_date"),
        "eligibility": revision.relevance_reason
        if revision is not None
        else delta.get("eligibility_reason"),
        "binary_eligible": binary_eligible,
        "trader_eligible": issuer_relevance is True
        and (materiality is not None or binary_eligible is True),
    }


def _guidance_business_values(
    guidance: CeriGuidanceEvent | None,
    delta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "action": guidance.action if guidance is not None else delta.get("action"),
        "metric": guidance.metric if guidance is not None else delta.get("metric"),
        "period": guidance.period_type if guidance is not None else delta.get("period"),
        "low": _value(guidance.low_value) if guidance is not None else delta.get("low"),
        "high": _value(guidance.high_value) if guidance is not None else delta.get("high"),
        "point": _value(guidance.point_value) if guidance is not None else delta.get("point"),
        "confidence": guidance.confidence if guidance is not None else delta.get("confidence"),
        "accepted_for_scoring": guidance.accepted_for_scoring is True
        if guidance is not None
        else delta.get("accepted_for_scoring") is True,
    }


def _event_summary(event: dict[str, Any]) -> str:
    category = _label(event.get("category"), "Catalyst")
    subtype = _label(event.get("subtype"), "Event")
    subject = _label(event.get("subject"), "Accepted canonical event")
    return f"{category} · {subtype} · {subject}"


def _opportunity_label(values: dict[str, Any] | None) -> str:
    if not values or values.get("opportunity_score") is None:
        return "Unrated"
    return f"{float(values['opportunity_score']):.2f} {_label(values.get('posture'), '')}".strip()


def _humanize_change_type(value: str) -> str:
    return (
        value.replace("_", " ")
        .title()
        .replace("Became Rated", "Became rated")
        .replace("Became Unrated", "Became unrated")
    )


def _data_quality_summary(delta: dict[str, Any]) -> str:
    freshness = delta.get("freshness") or {}
    if freshness:
        status = str(freshness.get("status") or "UNKNOWN").title()
        dataset = str(freshness.get("dataset") or "data").title()
        age = freshness.get("age_days")
        threshold = freshness.get("max_stale_days")
        return (
            f"{dataset} provider feed {status.lower()} · age {_number(age)} days"
            f" · threshold {_number(threshold)} days"
        )
    if delta.get("warnings"):
        return ", ".join(str(value) for value in delta["warnings"])
    if delta.get("prior_warnings"):
        return "Resolved: " + ", ".join(str(value) for value in delta["prior_warnings"])
    return "Data-quality state changed"


def _component_value_from_payload(snapshot: CeriScoreSnapshot | None, name: str) -> float | None:
    if snapshot is None:
        return None
    for component in (snapshot.component_json or {}).get("components") or []:
        if component.get("name") == name and component.get("value") is not None:
            return float(component["value"])
    return None


def _revision_window_values(
    snapshot: CeriScoreSnapshot | None,
    features: dict[int, CeriRevisionFeature],
) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    evidence_ids: set[int] = set()
    for component in (snapshot.component_json or {}).get("components") or []:
        if str(component.get("name") or "").startswith("revision_"):
            evidence_ids.update(int(value) for value in component.get("evidence_ids") or [])
    rows = []
    for feature_id in sorted(evidence_ids):
        feature = features.get(feature_id)
        if feature is None:
            continue
        rows.append(
            {
                "feature_id": feature.id,
                "metric": feature.metric,
                "period_key": feature.period_key,
                "window_days": feature.window_days,
                "actual_elapsed_days": feature.actual_elapsed_days,
                "pct_change": _value(feature.pct_change),
                "pct_change_unit": feature.pct_change_unit,
                "net_breadth": _value(feature.net_breadth),
                "upward_count": feature.upward_count,
                "downward_count": feature.downward_count,
                "acceleration": _value(feature.acceleration),
                "acceleration_unit": feature.acceleration_unit,
                "confidence": feature.revision_confidence_label,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["metric"] or "",
            row["period_key"] or "",
            row["window_days"] or 0,
        ),
    )


def _revision_summary(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    fallback: str,
) -> str:
    previous_by_key = {
        (row["metric"], row["period_key"], row["window_days"]): row for row in previous
    }
    candidates = []
    for row in current:
        key = (row["metric"], row["period_key"], row["window_days"])
        old = previous_by_key.get(key)
        if old is not None and (
            old.get("pct_change") is not None or row.get("pct_change") is not None
        ):
            candidates.append((key, old, row))
    if not candidates:
        return fallback
    key, old, new = sorted(
        candidates,
        key=lambda item: (
            item[0][2] != 30,
            item[0][0] != "EPS_DILUTED",
            item[0][1] != "CURRENT_QUARTER",
            item[0],
        ),
    )[0]
    metric = "EPS" if key[0] == "EPS_DILUTED" else _label(key[0], "Revision")
    period = {
        "CURRENT_QUARTER": "CQ",
        "NEXT_QUARTER": "NQ",
        "CURRENT_FISCAL_YEAR": "CFY",
        "NEXT_FISCAL_YEAR": "NFY",
    }.get(key[1], _label(key[1], "Period"))
    return (
        f"{metric} {period} {key[2]}d "
        f"{_signed(old.get('pct_change'))}% -> {_signed(new.get('pct_change'))}%"
    )


def _component_label(name: str) -> str:
    return name.replace("_", " ").title()


def _number(value: Any) -> str:
    return "Unavailable" if value is None else f"{float(value):.2f}"


def _signed(value: Any) -> str:
    return "Unavailable" if value is None else f"{float(value):+.2f}"


def _label(value: Any, fallback: str) -> str:
    if value is None or str(value).strip() == "":
        return fallback
    return str(value).replace("_", " ").title()


def _latest_snapshot_ids_by_company(
    snapshots: Any,
) -> dict[int, int]:
    latest: dict[int, CeriScoreSnapshot] = {}
    for snapshot in snapshots:
        if snapshot.controlled_replay_id is not None:
            continue
        current = latest.get(snapshot.company_id)
        if current is None or _snapshot_sort_tuple(snapshot) > _snapshot_sort_tuple(current):
            latest[snapshot.company_id] = snapshot
    return {company_id: snapshot.id for company_id, snapshot in latest.items()}


def _change_matches_filters(item: dict[str, Any], filters: CeriQueryFilters) -> bool:
    if (
        not filters.include_non_comparable
        and item.get("comparison_state") != ComparisonState.COMPARABLE.value
    ):
        return False
    if (
        not filters.include_ineligible
        and item.get("group") == ChangeGroup.CATALYSTS.value
        and (item.get("event") or {}).get("trader_eligible") is not True
    ):
        return False
    if (
        not filters.include_ineligible
        and str(item.get("change_type") or "").startswith("GUIDANCE_")
        and (item.get("current") or {}).get("accepted_for_scoring") is not True
    ):
        return False
    if (
        filters.from_run_id is not None
        and (item.get("previous") or {}).get("run_id") != filters.from_run_id
    ):
        return False
    if (
        filters.to_run_id is not None
        and (item.get("current") or {}).get("run_id") != filters.to_run_id
    ):
        return False
    if filters.change_group and item.get("group") != filters.change_group:
        return False
    if filters.change_type and item.get("change_type") != filters.change_type:
        return False
    if filters.importance and item.get("importance") != filters.importance:
        return False
    if filters.signal_class and item.get("signal_class") != filters.signal_class:
        return False
    if (
        filters.catalyst_category
        and (item.get("event") or {}).get("category") != filters.catalyst_category
    ):
        return False
    if filters.history_scope and item.get("history_scope") != filters.history_scope:
        return False
    if filters.min_delta is not None:
        raw = (item.get("delta") or {}).get("delta")
        if raw is None or abs(float(raw)) < filters.min_delta:
            return False
    return True


def _comparison_context(
    items: list[dict[str, Any]],
    snapshots: Any,
    *,
    filters: CeriQueryFilters | None = None,
) -> dict[str, Any]:
    pairs = Counter(
        (
            (item.get("previous") or {}).get("run_id"),
            (item.get("current") or {}).get("run_id"),
        )
        for item in items
        if item.get("previous") and item.get("current")
    )
    requested_pair = (
        (filters.from_run_id, filters.to_run_id)
        if filters and (filters.from_run_id is not None or filters.to_run_id is not None)
        else None
    )
    pair = requested_pair or (pairs.most_common(1)[0][0] if pairs else (None, None))
    snapshot_rows = list(snapshots)
    excluded = sum(
        1
        for snapshot in snapshot_rows
        if snapshot.comparison_state
        and snapshot.comparison_state != ComparisonState.COMPARABLE.value
    )
    return {
        "from_run_id": pair[0],
        "to_run_id": pair[1],
        "label": f"Comparing Run {pair[0]} -> Run {pair[1]}"
        if pair[0] is not None and pair[1] is not None
        else "Mixed change history",
        "excluded_non_comparable": excluded,
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


def _source_observed_at():
    return func.coalesce(
        CeriSourceRecord.observed_at,
        CeriSourceRecord.published_at,
        CeriSourceRecord.ingested_at,
    )


def _dataset_stale_predicate(dataset: str, max_days: int):
    cutoff = datetime.combine(
        datetime.now(UTC).date() - timedelta(days=max_days),
        datetime.min.time(),
        tzinfo=UTC,
    )
    return and_(
        CeriSourceRecord.dataset == dataset,
        _source_observed_at() < cutoff,
    )


def _database_stale_count(db: Session, dataset_max_stale: dict[str, int]) -> int:
    return sum(
        int(
            db.scalar(
                select(func.count())
                .select_from(CeriSourceRecord)
                .where(_dataset_stale_predicate(dataset, max_days))
            )
            or 0
        )
        for dataset, max_days in dataset_max_stale.items()
    )


def _database_freshness_records(db: Session, config: CeriConfig) -> list[_FreshnessRecord]:
    pairs = db.execute(
        select(CeriIngestionRun.provider, CeriIngestionRun.dataset)
        .group_by(CeriIngestionRun.provider, CeriIngestionRun.dataset)
        .order_by(CeriIngestionRun.provider, CeriIngestionRun.dataset)
    ).all()
    records = []
    tracked_tickers = {
        str(ticker).upper() for ticker in db.scalars(select(CeriCompany.ticker)).all()
    }
    total_tickers = len(tracked_tickers)
    now = datetime.now(UTC)
    for provider, dataset in pairs:
        latest = db.scalar(
            select(CeriIngestionRun.completed_at)
            .where(
                CeriIngestionRun.provider == provider,
                CeriIngestionRun.dataset == dataset,
                CeriIngestionRun.status == "COMPLETED",
                CeriIngestionRun.completed_at.is_not(None),
            )
            .order_by(CeriIngestionRun.completed_at.desc())
            .limit(1)
        )
        threshold = config.datasets.get(CeriDataset(dataset))
        coverage_rows = db.execute(
            select(
                func.upper(CeriIngestionRun.scope_json["ticker"].astext),
                func.max(CeriIngestionRun.completed_at),
            )
            .where(
                CeriIngestionRun.provider == provider,
                CeriIngestionRun.dataset == dataset,
                CeriIngestionRun.status == "COMPLETED",
                CeriIngestionRun.completed_at.is_not(None),
                CeriIngestionRun.scope_json["ticker"].astext.is_not(None),
            )
            .group_by(text("1"))
        ).all()
        fresh = stale = 0
        for ticker_value, completed_at in coverage_rows:
            if str(ticker_value).upper() not in tracked_tickers:
                continue
            age = freshness_age_days(
                now,
                completed_at,
                timezone_name=config.engine.timezone,
            )
            if threshold is not None and age <= threshold.max_stale_days:
                fresh += 1
            else:
                stale += 1
        records.append(
            _FreshnessRecord(
                provider,
                dataset,
                latest,
                {
                    "total": total_tickers,
                    "fresh": fresh,
                    "stale": stale,
                    "missing": max(0, total_tickers - fresh - stale),
                },
            )
        )
    return records


def _database_ordering(query: CeriListQuery, aliases: dict[str, Any]) -> tuple[Any, ...]:
    column = aliases[query.sort]
    primary = column.desc() if query.direction.lower() == "desc" else column.asc()
    tie = (
        CeriSourceRecord.id.desc()
        if query.direction.lower() == "desc"
        else CeriSourceRecord.id.asc()
    )
    return primary.nulls_last(), tie


def _nonempty_json(column):
    return and_(column.is_not(None), cast(column, String).not_in(("[]", "{}", "null")))


def _grouped_counts(db: Session, column) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).group_by(column).order_by(column)).all()
    return {str(key): int(count) for key, count in rows}


def _percentage(numerator: int | None, denominator: int | None) -> float:
    return 100.0 * int(numerator or 0) / int(denominator or 0) if denominator else 0.0


def _operations_errors(db: Session) -> list[dict[str, Any]]:
    ingestion = db.execute(
        select(CeriIngestionRun.id, CeriIngestionRun.errors_json)
        .where(CeriIngestionRun.errors_json.is_not(None))
        .order_by(CeriIngestionRun.id.desc())
        .limit(OPERATIONS_DETAIL_LIMIT)
    ).all()
    processing = db.execute(
        select(CeriProcessingRun.id, CeriProcessingRun.job_type, CeriProcessingRun.errors_json)
        .where(CeriProcessingRun.errors_json.is_not(None))
        .order_by(CeriProcessingRun.id.desc())
        .limit(OPERATIONS_DETAIL_LIMIT)
    ).all()
    items = [{"run_id": row.id, "job_type": None, "errors": row.errors_json} for row in ingestion]
    items.extend(
        {"run_id": row.id, "job_type": row.job_type, "errors": row.errors_json}
        for row in processing
    )
    return items[:OPERATIONS_DETAIL_LIMIT]


def _operations_deployments(db: Session) -> list[dict[str, Any]]:
    ingestion = db.execute(
        select(CeriIngestionRun.id, CeriIngestionRun.deployment_identity_json)
        .where(CeriIngestionRun.deployment_identity_json.is_not(None))
        .order_by(CeriIngestionRun.id.desc())
        .limit(OPERATIONS_DETAIL_LIMIT)
    ).all()
    processing = db.execute(
        select(CeriProcessingRun.id, CeriProcessingRun.deployment_identity_json)
        .where(CeriProcessingRun.deployment_identity_json.is_not(None))
        .order_by(CeriProcessingRun.id.desc())
        .limit(OPERATIONS_DETAIL_LIMIT)
    ).all()
    items = [
        {"run_type": "ingestion", "run_id": row.id, "identity": row.deployment_identity_json}
        for row in ingestion
    ]
    items.extend(
        {"run_type": "processing", "run_id": row.id, "identity": row.deployment_identity_json}
        for row in processing
    )
    return items[:OPERATIONS_DETAIL_LIMIT]


def _provider_cost_summary(db: Session) -> dict[str, dict[str, int]]:
    rows = db.execute(
        select(
            CeriProviderRequestTelemetry.provider,
            func.count(),
            func.coalesce(func.sum(CeriProviderRequestTelemetry.call_cost), 0),
            func.coalesce(func.sum(CeriProviderRequestTelemetry.latency_ms), 0),
            func.coalesce(func.sum(CeriProviderRequestTelemetry.response_bytes), 0),
            func.coalesce(func.sum(CeriProviderRequestTelemetry.stored_bytes), 0),
        )
        .group_by(CeriProviderRequestTelemetry.provider)
        .order_by(CeriProviderRequestTelemetry.provider)
    ).all()
    return {
        str(provider): {
            "request_rows": int(request_rows),
            "call_cost_units": int(call_cost_units),
            "runtime_ms": int(runtime_ms),
            "response_bytes": int(response_bytes),
            "stored_bytes": int(stored_bytes),
        }
        for (
            provider,
            request_rows,
            call_cost_units,
            runtime_ms,
            response_bytes,
            stored_bytes,
        ) in rows
    }


def _snapshot_reproduction_failure_count(
    db: Session,
    config: CeriConfig,
) -> tuple[int, int, int]:
    service = CeriSnapshotService(config=config)
    total = int(db.scalar(select(func.count()).select_from(CeriScoreSnapshot)) or 0)
    statement = (
        select(CeriScoreSnapshot)
        .options(
            load_only(
                CeriScoreSnapshot.id,
                CeriScoreSnapshot.company_id,
                CeriScoreSnapshot.ticker,
                CeriScoreSnapshot.as_of_session,
                CeriScoreSnapshot.cutoff_at,
                CeriScoreSnapshot.opportunity_score,
                CeriScoreSnapshot.event_risk_score,
                CeriScoreSnapshot.data_confidence,
                CeriScoreSnapshot.coverage_pct,
                CeriScoreSnapshot.posture,
                CeriScoreSnapshot.alignment_context_json,
                CeriScoreSnapshot.evidence_lineage_json,
                CeriScoreSnapshot.component_json,
                CeriScoreSnapshot.opportunity_ledger_json,
                CeriScoreSnapshot.confidence_ledger_json,
                CeriScoreSnapshot.event_risk_ledger_json,
                CeriScoreSnapshot.config_hash,
                CeriScoreSnapshot.calculation_version,
                CeriScoreSnapshot.evidence_hash,
            )
        )
        .order_by(CeriScoreSnapshot.id.desc())
        .limit(OPERATIONS_REPRODUCTION_SAMPLE_LIMIT)
        .execution_options(yield_per=100)
    )
    failures = 0
    checked = 0
    for snapshot in db.scalars(statement):
        checked += 1
        failures += not service.reproduce_snapshot(snapshot).matches
    return failures, checked, total


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
        rows = list(db.scalars(select(CeriAlertEvent).where(CeriAlertEvent.ticker == ticker)).all())
    rows.sort(key=lambda row: row.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
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


def _ordered_nulls_last(
    items: list[Any],
    *,
    value,
    tie,
    descending: bool,
) -> list[Any]:
    """Return a deterministic ordering with missing values last in both directions."""
    non_null = sorted((item for item in items if value(item) is not None), key=tie)
    non_null = sorted(non_null, key=value, reverse=descending)
    nulls = sorted((item for item in items if value(item) is None), key=tie)
    return [*non_null, *nulls]


def _page_payload(
    items: list[dict[str, Any]],
    *,
    total: int,
    query: CeriListQuery,
) -> dict[str, Any]:
    total_pages = (total + query.limit - 1) // query.limit if total else 0
    page = query.offset // query.limit + 1
    has_previous = query.offset > 0
    has_next = query.offset + query.limit < total
    return {
        "items": items,
        "total": total,
        "total_items": total,
        "limit": query.limit,
        "page_size": query.limit,
        "offset": query.offset,
        "page": page,
        "total_pages": total_pages,
        "has_previous": has_previous,
        "has_next": has_next,
        "previous_offset": max(0, query.offset - query.limit) if has_previous else None,
        "next_offset": query.offset + query.limit if has_next else None,
        "start_item": query.offset + 1 if items else 0,
        "end_item": query.offset + len(items),
        "sort": query.sort,
        "direction": query.direction,
    }


def snapshot_population_summary(
    snapshots: list[CeriScoreSnapshot],
) -> dict[str, Any]:
    matches = [snapshot for snapshot in snapshots if _is_high_opportunity_low_risk(snapshot)]
    return {
        "population_count": len(snapshots),
        "high_opportunity_low_risk": len(matches),
        "high_opportunity_threshold": CERI_HIGH_OPPORTUNITY_THRESHOLD,
        "low_risk_threshold": CERI_LOW_RISK_THRESHOLD,
        "matching_tickers": sorted(snapshot.ticker for snapshot in matches),
        "predicate": (
            "opportunity_score >= 7.0 AND posture = Positive AND "
            "event_risk_score <= 3.0 AND risk_evidence_state = SUFFICIENT"
        ),
    }


def _is_high_opportunity_low_risk(snapshot: CeriScoreSnapshot) -> bool:
    risk_state = _risk_evidence_state(
        snapshot.event_risk_ledger_json or {},
        snapshot.warnings_json or [],
    )
    return bool(
        snapshot.opportunity_score is not None
        and snapshot.opportunity_score >= CERI_HIGH_OPPORTUNITY_THRESHOLD
        and snapshot.posture == "Positive"
        and snapshot.event_risk_score is not None
        and snapshot.event_risk_score <= CERI_LOW_RISK_THRESHOLD
        and risk_state == "SUFFICIENT"
    )


def _risk_evidence_state(risk_ledger: dict[str, Any], warnings: list[str]) -> str:
    if risk_ledger.get("accepted_evidence") is True:
        return "SUFFICIENT"
    if risk_ledger.get("rejected_event_ids"):
        return "PARTIAL"
    components = risk_ledger.get("components") or []
    earnings_unknown = any(
        component.get("reason") == "earnings_proximity:unknown" for component in components
    )
    if earnings_unknown and not risk_ledger.get("rejected_event_ids"):
        return "UNAVAILABLE"
    return "INSUFFICIENT"


def _component_coverage_pct(components: list[dict[str, Any]]) -> float | None:
    if not components:
        return None
    return 100.0 * sum(
        float(component.get("weight") or 0.0)
        for component in components
        if component.get("available") is True
    )


_WARNING_SEVERITY = {
    "revision_feature_lineage_mismatch": "BLOCKER",
    "opportunity_component_coverage_insufficient": "BLOCKER",
    "revision_magnitude_unavailable": "WARNING",
    "revision_acceleration_unavailable": "WARNING",
    "surprise_trend_unavailable": "WARNING",
    "guidance_unavailable": "WARNING",
    "catalysts_unavailable": "WARNING",
    "price_response_unavailable": "WARNING",
    "estimate_coverage_low": "INFO",
    "analyst_sample_sparse": "INFO",
}
_SEVERITY_RANK = {"NONE": 0, "INFO": 1, "WARNING": 2, "BLOCKER": 3}


def _warning_summary(warnings: list[str]) -> dict[str, Any]:
    if not warnings:
        return {"count": 0, "severity": "NONE", "dominant_warning": None}
    dominant = max(
        enumerate(warnings),
        key=lambda pair: (
            _SEVERITY_RANK.get(_WARNING_SEVERITY.get(pair[1], "WARNING"), 2),
            -pair[0],
        ),
    )[1]
    return {
        "count": len(warnings),
        "severity": _WARNING_SEVERITY.get(dominant, "WARNING"),
        "dominant_warning": dominant,
    }


def _lineage_reconciliation(
    opportunity_ledger: dict[str, Any],
    evidence_lineage: dict[str, Any],
) -> dict[str, Any]:
    ledger_selected = {
        int(row["evidence_id"])
        for row in evidence_lineage.get("evidence_states") or []
        if row.get("evidence_id") is not None
        and "SELECTED_FOR_COMPONENT" in (row.get("states") or [])
    }
    rows: list[dict[str, Any]] = []
    for component in opportunity_ledger.get("components") or []:
        if component.get("available") is not True:
            continue
        evidence_ids = sorted({int(value) for value in component.get("evidence_ids") or []})
        exemption = None
        if not evidence_ids and component.get("name") == "surprise_trend":
            exemption = "AGGREGATE_COMPONENT_NO_DIRECT_EVIDENCE_IDS"
        valid = bool(evidence_ids or exemption)
        rows.append(
            {
                "component": component.get("name"),
                "selected_lineage_ids": evidence_ids,
                "state_ledger_selected_ids": sorted(set(evidence_ids) & ledger_selected),
                "lineage_exemption_reason": exemption,
                "valid": valid,
            }
        )
    selected_evidence_ids = sorted(
        {evidence_id for row in rows for evidence_id in row["selected_lineage_ids"]}
    )
    return {
        "valid": all(row["valid"] for row in rows),
        "components": rows,
        "selected_evidence_count": len(selected_evidence_ids),
        "selected_evidence_ids": selected_evidence_ids,
    }


def _revision_lineage_audits(
    db: Session,
    snapshots: list[CeriScoreSnapshot],
) -> dict[int, dict[str, Any]]:
    revision_names = {
        "revision_magnitude",
        "revision_breadth",
        "revision_acceleration",
    }
    ids_by_snapshot = {
        snapshot.id: {
            int(evidence_id)
            for component in (snapshot.opportunity_ledger_json or {}).get("components") or []
            if component.get("name") in revision_names
            for evidence_id in component.get("evidence_ids") or []
        }
        for snapshot in snapshots
    }
    feature_ids = set().union(*ids_by_snapshot.values()) if ids_by_snapshot else set()
    if _uses_fixture_collections(db):
        features = [
            feature for feature in _load(db, CeriRevisionFeature) if feature.id in feature_ids
        ]
    elif feature_ids:
        features = list(
            db.scalars(
                select(CeriRevisionFeature).where(CeriRevisionFeature.id.in_(feature_ids))
            ).all()
        )
    else:
        features = []
    features_by_id = {feature.id: feature for feature in features}
    estimate_ids = {
        estimate_id
        for feature in features
        for estimate_id in (feature.current_snapshot_id, feature.baseline_snapshot_id)
        if estimate_id is not None
    }
    if _uses_fixture_collections(db):
        estimates = [
            estimate for estimate in _load(db, CeriEstimateSnapshot) if estimate.id in estimate_ids
        ]
    elif estimate_ids:
        estimates = list(
            db.scalars(
                select(CeriEstimateSnapshot).where(CeriEstimateSnapshot.id.in_(estimate_ids))
            ).all()
        )
    else:
        estimates = []
    estimates_by_id = {estimate.id: estimate for estimate in estimates}

    result: dict[int, dict[str, Any]] = {}
    for snapshot in snapshots:
        mismatches: list[int] = []
        source_field_exemptions: list[int] = []
        for feature_id in sorted(ids_by_snapshot.get(snapshot.id, set())):
            feature = features_by_id.get(feature_id)
            if feature is None or feature.pct_change is None:
                continue
            current = estimates_by_id.get(feature.current_snapshot_id)
            baseline = estimates_by_id.get(feature.baseline_snapshot_id)
            if current is None or current.consensus is None:
                mismatches.append(feature_id)
                continue
            baseline_value = baseline.consensus if baseline is not None else None
            baseline_rehydrated_from_source = False
            if baseline_value is None:
                baseline_value = _provider_baseline_value(current, feature.window_days)
                if baseline_value is not None:
                    baseline_rehydrated_from_source = True
            if baseline_value is None:
                continue
            threshold = Decimal(str(load_ceri_config().revision.near_zero_threshold))
            if abs(baseline_value) <= threshold:
                continue
            if (current.consensus > 0 > baseline_value) or (current.consensus < 0 < baseline_value):
                continue
            reproduced = (current.consensus - baseline_value) / abs(baseline_value) * Decimal("100")
            if abs(reproduced - feature.pct_change) > Decimal("0.0001"):
                mismatches.append(feature_id)
            elif baseline_rehydrated_from_source:
                source_field_exemptions.append(feature_id)
        result[snapshot.id] = {
            "revision_value_mismatches": mismatches,
            "lineage_exemptions": {
                "PROVIDER_RETROSPECTIVE_REHYDRATED_SOURCE_FIELD": source_field_exemptions
            },
        }
    return result


def _provider_baseline_value(
    current: CeriEstimateSnapshot,
    window_days: int,
) -> Decimal | None:
    raw = (current.original_fields_json or {}).get(f"eps_trend_{window_days}d")
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _format_signed(value: Any, *, suffix: str = "") -> str | None:
    if value is None:
        return None
    number = float(value)
    if abs(number) < 0.005:
        return f"0.00{suffix}"
    return f"{number:+.2f}{suffix}"


def _value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
