from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from functools import cmp_to_key
from threading import Lock
from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select, true, tuple_
from sqlalchemy.orm import Session, aliased, load_only

from app.models.tables import (
    BackgroundJob,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalAlertRule,
    SignalChangeEvent,
)
from app.services.background_job_service import JobStatus
from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.enums import (
    Actionability,
    EventSeverity,
    LifecycleState,
    SetupFamily,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.us_market_calendar import next_us_trading_day


class SetupLifecycleQueryError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class SetupLifecycleViewScope(StrEnum):
    CURRENT_MARKET = "CURRENT_MARKET"
    HISTORICAL_RUN = "HISTORICAL_RUN"


_CHANGE_SUMMARY_CACHE: dict[tuple[Any, ...], tuple[dict[str, Any], int, int]] = {}
_CHANGE_SUMMARY_CACHE_LOCK = Lock()
_CHANGE_SUMMARY_CACHE_MAX_ENTRIES = 256


@dataclass(frozen=True)
class SetupLifecycleFilters:
    run_id: int | None = None
    ticker: str | None = None
    sector: str | None = None
    setup_family: str | None = None
    lifecycle_state: str | None = None
    transition: str | None = None
    actionability: str | None = None
    confidence_min: int | None = None
    confidence_max: int | None = None
    state_age_min: int | None = None
    state_age_max: int | None = None
    setup_score_min: float | None = None
    setup_score_max: float | None = None
    trigger_distance_min: float | None = None
    trigger_distance_max: float | None = None
    sector_rank_min: int | None = None
    sector_rank_max: int | None = None
    sector_rank_change_min: int | None = None
    sector_rank_change_max: int | None = None
    velocity_window: int = 3
    velocity_min: float | None = None
    velocity_max: float | None = None
    market_regime: str | None = None
    blocker: str | None = None
    warning_flag: str | None = None
    alert_status: str | None = None
    alert_severity: str | None = None
    as_of_date: date | None = None
    date_from: date | None = None
    date_to: date | None = None
    source_type: str | None = None
    alert_type: str | None = None


@dataclass(frozen=True)
class SetupLifecycleListQuery:
    filters: SetupLifecycleFilters
    sort: str = "latest_event_time"
    direction: str = "desc"
    limit: int = 50
    cursor: str | None = None
    view_scope: SetupLifecycleViewScope = SetupLifecycleViewScope.CURRENT_MARKET


class SetupLifecycleQueryService:
    def __init__(
        self,
        *,
        repository: SetupLifecycleRepository | None = None,
        config: SetupLifecycleConfig | None = None,
    ) -> None:
        self.repository = repository or SetupLifecycleRepository()
        self.config = config or load_setup_lifecycle_config()

    def changes(self, db: Session, query: SetupLifecycleListQuery) -> dict[str, Any]:
        query = _validate_query(query)
        if query.filters.transition == "NO_MATERIAL_CHANGE":
            return self._no_material_changes(db, query)
        lifecycle_statement = (
            select(SetupLifecycleEvent)
            .outerjoin(
                SetupSignalSnapshot,
                SetupLifecycleEvent.snapshot_id == SetupSignalSnapshot.id,
            )
            .where(
                SetupLifecycleEvent.event_type.in_(
                    ("EPISODE_OPENED", "STATE_TRANSITION", "PHASE_TRANSITION")
                )
            )
        )
        if query.view_scope == SetupLifecycleViewScope.CURRENT_MARKET:
            lifecycle_statement = lifecycle_statement.where(
                SetupLifecycleEvent.is_current_version.is_(True)
            )
        lifecycle_statement = _apply_event_filters(lifecycle_statement, query.filters)
        signal_statement = select(SignalChangeEvent).join(
            SetupSignalSnapshot,
            SignalChangeEvent.current_snapshot_id == SetupSignalSnapshot.id,
        )
        if query.view_scope == SetupLifecycleViewScope.CURRENT_MARKET:
            signal_statement = signal_statement.where(SetupSignalSnapshot.is_canonical.is_(True))
        signal_statement = _apply_signal_change_filters(signal_statement, query.filters)

        cursor_metadata = _decode_change_cursor(query.cursor, query=query)
        if cursor_metadata is not None and "summary" in cursor_metadata:
            summary = dict(cursor_metadata["summary"])
            lifecycle_total = int(cursor_metadata["lifecycle_total"])
            signal_total = int(cursor_metadata["signal_total"])
        else:
            summary, lifecycle_total, signal_total = _cached_changes_summary(
                db,
                lifecycle_statement,
                signal_statement,
                filters=query.filters,
                view_scope=query.view_scope,
            )
        selected_rows, last_page_key = _combined_change_page_rows(
            db,
            lifecycle_statement=lifecycle_statement,
            signal_statement=signal_statement,
            query=query,
        )
        lifecycle_by_id = _rows_by_id(
            db,
            SetupLifecycleEvent,
            {row_id for source_type, row_id in selected_rows if source_type == "LIFECYCLE_EVENT"},
        )
        signal_by_id = _rows_by_id(
            db,
            SignalChangeEvent,
            {
                row_id
                for source_type, row_id in selected_rows
                if source_type == "SIGNAL_CHANGE_EVENT"
            },
        )
        lifecycle_rows = list(lifecycle_by_id.values())
        signal_rows = list(signal_by_id.values())
        payload_context = _prime_market_change_payload_context(
            db,
            lifecycle_rows,
            signal_rows,
            view_scope=query.view_scope,
        )
        items = [
            *[
                market_change_payload(db, lifecycle_event=row, context=payload_context)
                for row in lifecycle_rows
            ],
            *[
                market_change_payload(db, signal_change_event=row, context=payload_context)
                for row in signal_rows
            ],
        ]
        items = _sort_market_change_items(items, query.sort, query.direction)
        payload = _page(
            items=items,
            total=lifecycle_total + signal_total,
            query=query,
            summary=summary,
        )
        if payload["next_cursor"] is not None and last_page_key is not None:
            payload["next_cursor"] = _encode_change_cursor(
                query,
                last_page_key,
                next_offset=_offset(query.cursor) + len(items),
                summary=summary,
                lifecycle_total=lifecycle_total,
                signal_total=signal_total,
            )
        return payload

    def _no_material_changes(self, db: Session, query: SetupLifecycleListQuery) -> dict[str, Any]:
        if query.filters.as_of_date is None:
            raise SetupLifecycleQueryError(
                "INVALID_DATE", "NO_MATERIAL_CHANGE requires an as-of date"
            )
        has_lifecycle_event = (
            select(SetupLifecycleEvent.id)
            .where(SetupLifecycleEvent.snapshot_id == SetupSignalSnapshot.id)
            .where(
                SetupLifecycleEvent.event_type.in_(
                    ("EPISODE_OPENED", "STATE_TRANSITION", "PHASE_TRANSITION")
                )
            )
            .exists()
        )
        if query.view_scope == SetupLifecycleViewScope.CURRENT_MARKET:
            has_lifecycle_event = has_lifecycle_event.where(
                SetupLifecycleEvent.is_current_version.is_(True)
            )
        has_signal_change = (
            select(SignalChangeEvent.id)
            .where(SignalChangeEvent.current_snapshot_id == SetupSignalSnapshot.id)
            .exists()
        )
        statement = select(SetupSignalSnapshot).where(
            SetupSignalSnapshot.data_as_of_date == query.filters.as_of_date,
            ~has_lifecycle_event,
            ~has_signal_change,
        )
        if query.view_scope == SetupLifecycleViewScope.CURRENT_MARKET:
            statement = statement.where(SetupSignalSnapshot.is_canonical.is_(True))
        statement = _apply_snapshot_filters(statement, query.filters)
        total = _count(db, statement)
        low_confidence = _count(db, statement.where(SetupSignalSnapshot.confidence_score < 70))
        rows = list(
            db.scalars(
                _sort_no_material_snapshots(statement, query)
                .options(load_only(*_SNAPSHOT_PAYLOAD_COLUMNS))
                .offset(_offset(query.cursor))
                .limit(query.limit)
            )
        )
        context = _prime_no_material_payload_context(db, rows)
        items = [no_material_change_payload(db, row, context=context) for row in rows]
        return _page(
            items=items,
            total=total,
            query=query,
            summary={
                "newly_discovered": 0,
                "tightening": 0,
                "newly_ready": 0,
                "newly_triggered": 0,
                "confirmed": 0,
                "extended": 0,
                "failed": 0,
                "material_changes": 0,
                "major_risk_changes": 0,
                "no_material_change": total,
                "low_confidence_count": low_confidence,
                "low_confidence_share": round(low_confidence / total, 6) if total else 0.0,
            },
        )

    def alerts(self, db: Session, query: SetupLifecycleListQuery) -> dict[str, Any]:
        query = _validate_query(query)
        statement = select(SignalAlertEvent).join(
            SignalAlertRule,
            SignalAlertEvent.alert_rule_id == SignalAlertRule.id,
        )
        statement = _apply_alert_filters(statement, query.filters)
        total = _count(db, statement)
        rows = list(
            db.scalars(
                _sort_alerts(statement, query.sort, query.direction)
                .offset(_offset(query.cursor))
                .limit(query.limit)
            )
        )
        # alert_payload projects evidence from the linked rule/event/change/snapshot.
        # Prime those rows in bounded set-based queries so a page does not issue up
        # to four lazy primary-key lookups per alert (especially costly for exports).
        payload_context = _prime_alert_payload_context(db, rows)
        return _page(
            items=[alert_payload(row, db=db, context=payload_context) for row in rows],
            total=total,
            query=query,
            summary=_alerts_summary(db, statement),
        )

    def ticker_timeline(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str = "1d",
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        normalized = self.repository.normalize_ticker(ticker)
        page_limit = max(1, min(limit, 500))
        cursor_key = _timeline_cursor_key(cursor)
        snapshots = list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(SetupSignalSnapshot.ticker == normalized)
                .where(SetupSignalSnapshot.timeframe == timeframe)
                .order_by(SetupSignalSnapshot.data_as_of_date.desc(), SetupSignalSnapshot.id.desc())
                .limit(2)
            )
        )
        episodes = list(
            db.scalars(
                select(SetupLifecycleEpisode)
                .where(SetupLifecycleEpisode.ticker == normalized)
                .where(SetupLifecycleEpisode.timeframe == timeframe)
                .order_by(SetupLifecycleEpisode.opened_on.desc(), SetupLifecycleEpisode.id.desc())
            )
        )
        lifecycle_statement = select(SetupLifecycleEvent).where(
            SetupLifecycleEvent.ticker == normalized,
            SetupLifecycleEvent.timeframe == timeframe,
        )
        lifecycle_statement = _timeline_after_cursor(
            lifecycle_statement,
            date_column=SetupLifecycleEvent.effective_date,
            id_column=SetupLifecycleEvent.id,
            kind_priority=3,
            cursor_key=cursor_key,
        )
        signal_statement = select(SignalChangeEvent).where(
            SignalChangeEvent.ticker == normalized,
            SignalChangeEvent.timeframe == timeframe,
        )
        signal_statement = _timeline_after_cursor(
            signal_statement,
            date_column=SignalChangeEvent.effective_date,
            id_column=SignalChangeEvent.id,
            kind_priority=2,
            cursor_key=cursor_key,
        )
        alert_statement = select(SignalAlertEvent).where(
            SignalAlertEvent.ticker == normalized,
            SignalAlertEvent.timeframe == timeframe,
        )
        alert_statement = _timeline_after_cursor(
            alert_statement,
            date_column=SignalAlertEvent.effective_date,
            id_column=SignalAlertEvent.id,
            kind_priority=1,
            cursor_key=cursor_key,
        )
        lifecycle_events = list(
            db.scalars(
                lifecycle_statement.order_by(
                    SetupLifecycleEvent.effective_date.desc(),
                    SetupLifecycleEvent.id.desc(),
                ).limit(page_limit + 1)
            )
        )
        signal_changes = list(
            db.scalars(
                signal_statement.order_by(
                    SignalChangeEvent.effective_date.desc(), SignalChangeEvent.id.desc()
                ).limit(page_limit + 1)
            )
        )
        alerts = list(
            db.scalars(
                alert_statement.order_by(
                    SignalAlertEvent.effective_date.desc(), SignalAlertEvent.id.desc()
                ).limit(page_limit + 1)
            )
        )
        combined = [
            *[
                (row.effective_date, 3, row.id, "lifecycle", lifecycle_event_payload(row))
                for row in lifecycle_events
            ],
            *[
                (row.effective_date, 2, row.id, "signal", signal_change_payload(row))
                for row in signal_changes
            ],
            *[
                (row.effective_date, 1, row.id, "alert", alert_payload(row, db=db))
                for row in alerts
            ],
        ]
        combined.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
        has_more = len(combined) > page_limit
        page = combined[:page_limit]
        page_lifecycle = [row[4] for row in page if row[3] == "lifecycle"]
        page_signals = [row[4] for row in page if row[3] == "signal"]
        page_alerts = [row[4] for row in page if row[3] == "alert"]
        next_cursor = (
            _encode_timeline_cursor(page[-1][0], page[-1][1], page[-1][2])
            if has_more and page
            else None
        )
        return {
            "ticker": normalized,
            "timeframe": timeframe,
            "snapshots": [snapshot_payload(row) for row in snapshots],
            "episodes": [episode_payload(row) for row in episodes],
            "lifecycle_events": page_lifecycle,
            "signal_changes": page_signals,
            "alerts": page_alerts,
            "limit": page_limit,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "source_links": _source_links(snapshots[:1]),
        }

    def episode_detail(self, db: Session, episode_id: int) -> dict[str, Any]:
        episode = db.get(SetupLifecycleEpisode, episode_id)
        if episode is None:
            raise SetupLifecycleQueryError(
                "EPISODE_NOT_FOUND",
                "Setup lifecycle episode was not found.",
                status_code=404,
            )
        events = list(
            db.scalars(
                select(SetupLifecycleEvent)
                .where(SetupLifecycleEvent.episode_id == episode_id)
                .order_by(SetupLifecycleEvent.effective_date.asc(), SetupLifecycleEvent.id.asc())
            )
        )
        changes = list(
            db.scalars(
                select(SignalChangeEvent)
                .where(SignalChangeEvent.episode_id == episode_id)
                .order_by(SignalChangeEvent.effective_date.asc(), SignalChangeEvent.id.asc())
            )
        )
        snapshot_ids = tuple(
            {
                item
                for item in (
                    episode.opening_snapshot_id,
                    episode.current_snapshot_id,
                    episode.closing_snapshot_id,
                )
                if item is not None
            }
        )
        snapshots = self.repository.get_snapshots_by_ids(db, snapshot_ids)
        return {
            "episode": episode_payload(episode),
            "snapshots": [snapshot_payload(row) for row in snapshots],
            "lifecycle_events": [lifecycle_event_payload(row) for row in events],
            "signal_changes": [signal_change_payload(row) for row in changes],
        }

    def operations(self, db: Session) -> dict[str, Any]:
        runs = list(
            db.scalars(
                select(SetupLifecycleEvaluationRun)
                .order_by(SetupLifecycleEvaluationRun.created_at.desc())
                .limit(25)
            )
        )
        return {
            "runs": [evaluation_run_payload(row) for row in runs],
            "summary": {
                "latest_status": runs[0].status if runs else None,
                "latest_evaluation_id": runs[0].id if runs else None,
                "active_episodes": self.repository.count_active_episodes(db),
            },
        }

    def evaluation_run(self, db: Session, evaluation_id: int) -> dict[str, Any]:
        run = db.get(SetupLifecycleEvaluationRun, evaluation_id)
        if run is None:
            raise SetupLifecycleQueryError(
                "EVALUATION_NOT_FOUND",
                "Evaluation was not found.",
                status_code=404,
            )
        return evaluation_run_payload(run)

    def diagnostics(self, db: Session) -> dict[str, Any]:
        latest_canonical_date = db.scalar(
            select(func.max(SetupSignalSnapshot.data_as_of_date)).where(
                SetupSignalSnapshot.is_canonical.is_(True)
            )
        )
        latest_success = db.scalar(
            select(SetupLifecycleEvaluationRun)
            .where(SetupLifecycleEvaluationRun.status.in_(("COMPLETED", "PARTIAL")))
            .order_by(
                SetupLifecycleEvaluationRun.completed_at.desc().nullslast(),
                SetupLifecycleEvaluationRun.id.desc(),
            )
            .limit(1)
        )
        active_episodes = self.repository.count_active_episodes(db)
        pending_jobs = _job_count(db, (JobStatus.QUEUED, JobStatus.RUNNING))
        stale_lease_count = _stale_lease_count(db)
        low_confidence_share = _low_confidence_share(db)
        stale_system_warning = latest_success is None or stale_lease_count > 0
        return {
            "latest_canonical_date": _date_or_none(latest_canonical_date),
            "latest_successful_evaluation": evaluation_run_payload(latest_success)
            if latest_success is not None
            else None,
            "active_episode_count": active_episodes,
            "pending_jobs": pending_jobs,
            "stale_lease_count": stale_lease_count,
            "low_confidence_share": low_confidence_share,
            "stale_system_warning": stale_system_warning,
        }

    def filter_options(self, db: Session) -> dict[str, Any]:
        return {
            "states": [state.value for state in LifecycleState],
            "families": [family.value for family in SetupFamily],
            "tickers": _distinct(db, SetupLifecycleEpisode.ticker),
            "sectors": _distinct(db, SetupSignalSnapshot.sector),
            "alert_types": [
                "NEW_READY",
                "NEW_TRIGGER",
                "NEW_CONFIRMATION",
                "NEW_FAILURE",
                "NEW_EXTENSION",
                "SCORE_ACCELERATION",
                "SECTOR_ACCELERATION",
                "GATE_BLOCKED",
                "DATA_DEGRADED",
            ],
            "alert_statuses": ["UNREAD", "ACKNOWLEDGED", "DISMISSED"],
            "alert_severities": ["INFO", "NOTABLE", "ACTIONABLE", "RISK"],
            "source_types": [
                "LIFECYCLE_EVENT",
                "SIGNAL_CHANGE_EVENT",
                "ACTIONABILITY_CHANGE",
                "DATA_QUALITY_CHANGE",
            ],
        }


def lifecycle_event_payload(event: SetupLifecycleEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "episode_id": event.episode_id,
        "evaluation_run_id": event.evaluation_run_id,
        "snapshot_id": event.snapshot_id,
        "ticker": event.ticker,
        "timeframe": event.timeframe,
        "setup_family": event.setup_family,
        "effective_date": _date_or_none(event.effective_date),
        "event_type": event.event_type,
        "from_state": event.from_state,
        "to_state": event.to_state,
        "from_phase": event.from_phase,
        "to_phase": event.to_phase,
        "actionability_before": event.actionability_before,
        "actionability_after": event.actionability_after,
        "confidence_score": event.confidence_score,
        "confidence_label": event.confidence_label,
        "severity": event.severity,
        "source_event_key": event.source_event_key,
        "is_current_version": event.is_current_version,
        "record_status": ("CURRENT" if event.is_current_version is not False else "SUPERSEDED"),
        "superseded_by_event_id": event.superseded_by_event_id,
        "engine_version": event.engine_version,
        "config_version": event.config_version,
        "config_hash": event.config_hash,
        "reason_codes": list(event.reason_codes_json or []),
        "warning_flags": list(event.warning_flags_json or []),
        "evidence": dict(event.evidence_json or {}),
    }


def episode_payload(episode: SetupLifecycleEpisode) -> dict[str, Any]:
    return {
        "id": episode.id,
        "ticker": episode.ticker,
        "timeframe": episode.timeframe,
        "setup_family": episode.setup_family,
        "status": episode.status,
        "opened_on": _date_or_none(episode.opened_on),
        "current_as_of_date": _date_or_none(episode.current_as_of_date),
        "last_observed_on": _date_or_none(episode.last_observed_on),
        "closed_on": _date_or_none(episode.closed_on),
        "missing_observation_sessions": episode.missing_observation_sessions,
        "current_state": episode.current_state,
        "current_phase": episode.current_phase,
        "state_age_sessions": episode.state_age_sessions,
        "current_actionability": episode.current_actionability,
        "confidence_score": episode.confidence_score,
        "confidence_label": episode.confidence_label,
        "terminal_state": episode.terminal_state,
        "terminal_reason_code": episode.terminal_reason_code,
        "is_primary": episode.is_primary,
        "primary_rank": episode.primary_rank,
        "opening_snapshot_id": episode.opening_snapshot_id,
        "current_snapshot_id": episode.current_snapshot_id,
        "closing_snapshot_id": episode.closing_snapshot_id,
        "opening_evaluation_id": episode.opening_evaluation_id,
        "closing_evaluation_id": episode.closing_evaluation_id,
        "engine_version": episode.engine_version,
        "config_version": episode.config_version,
        "config_hash": episode.config_hash,
        "record_status": episode.status,
        "metadata": dict(episode.metadata_json or {}),
    }


def snapshot_payload(snapshot: SetupSignalSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "run_id": snapshot.run_id,
        "raw_row_id": snapshot.raw_row_id,
        "fundamental_score_id": snapshot.fundamental_score_id,
        "technical_score_id": snapshot.technical_score_id,
        "combined_result_id": snapshot.combined_result_id,
        "ranking_result_id": snapshot.ranking_result_id,
        "market_regime_snapshot_id": snapshot.market_regime_snapshot_id,
        "sector_rotation_snapshot_id": snapshot.sector_rotation_snapshot_id,
        "ticker": snapshot.ticker,
        "company_name": snapshot.company_name,
        "sector": snapshot.sector,
        "timeframe": snapshot.timeframe,
        "data_as_of_date": _date_or_none(snapshot.data_as_of_date),
        "calculated_at": _datetime_or_none(snapshot.calculated_at),
        "origin_type": snapshot.origin_type,
        "is_canonical": snapshot.is_canonical,
        "record_status": (
            "CURRENT_CANONICAL"
            if snapshot.is_canonical
            else "SUPERSEDED"
            if snapshot.superseded_by_snapshot_id is not None
            else "NONCANONICAL"
        ),
        "canonical_reason": snapshot.canonical_reason,
        "superseded_by_snapshot_id": snapshot.superseded_by_snapshot_id,
        "engine_version": snapshot.engine_version,
        "config_version": snapshot.config_version,
        "schema_version": snapshot.schema_version,
        "config_hash": snapshot.config_hash,
        "source_data_hash": snapshot.source_data_hash,
        "primary_setup_family": snapshot.primary_setup_family,
        "primary_phase": snapshot.primary_phase,
        "lifecycle_state_candidate": snapshot.lifecycle_state_candidate,
        "actionability_candidate": snapshot.actionability_candidate,
        "data_quality_label": snapshot.data_quality_label,
        "confidence_score": snapshot.confidence_score,
        "confidence_label": snapshot.confidence_label,
        "setup_score": _number_or_none(snapshot.setup_score),
        "technical_classification": snapshot.technical_classification,
        "distance_to_pivot_pct": _number_or_none(snapshot.distance_to_pivot_pct),
        "warning_flags": list(snapshot.warning_flags_json or []),
        "source_lineage": dict(snapshot.source_lineage_json or {}),
    }


def signal_change_payload(event: SignalChangeEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "episode_id": event.episode_id,
        "evaluation_run_id": event.evaluation_run_id,
        "previous_snapshot_id": event.previous_snapshot_id,
        "current_snapshot_id": event.current_snapshot_id,
        "ticker": event.ticker,
        "timeframe": event.timeframe,
        "effective_date": _date_or_none(event.effective_date),
        "category": event.category,
        "signal_key": event.signal_key,
        "old_value": dict(event.old_value_json or {}),
        "new_value": dict(event.new_value_json or {}),
        "delta_numeric": _number_or_none(event.delta_numeric),
        "percentage_delta": _number_or_none(event.percentage_delta),
        "rank_delta": event.rank_delta,
        "normalized_delta": _number_or_none(event.normalized_delta),
        "direction": event.direction,
        "threshold_name": event.threshold_name,
        "threshold_direction": event.threshold_direction,
        "severity": event.severity,
        "signal_definition_version": event.signal_definition_version,
        "config_hash": event.config_hash,
        "record_status": "CURRENT",
        "source_event_key": event.source_event_key,
        "reason_codes": list(event.reason_codes_json or []),
        "evidence": dict(event.evidence_json or {}),
    }


@dataclass(frozen=True)
class _MarketChangePayloadContext:
    current_snapshots: dict[int, SetupSignalSnapshot]
    explicit_previous_snapshots: dict[int, SetupSignalSnapshot]
    previous_by_current_snapshot: dict[int, SetupSignalSnapshot]
    lifecycle_by_snapshot: dict[int, SetupLifecycleEvent]
    episodes: dict[int, SetupLifecycleEpisode]


def _prime_market_change_payload_context(
    db: Session,
    lifecycle_events: list[SetupLifecycleEvent],
    signal_changes: list[SignalChangeEvent],
    *,
    view_scope: SetupLifecycleViewScope = SetupLifecycleViewScope.CURRENT_MARKET,
) -> _MarketChangePayloadContext:
    current_ids = {row.snapshot_id for row in lifecycle_events if row.snapshot_id} | {
        row.current_snapshot_id for row in signal_changes if row.current_snapshot_id
    }
    previous_ids = {row.previous_snapshot_id for row in signal_changes if row.previous_snapshot_id}
    current_snapshots = _snapshot_rows_by_id(db, current_ids)
    explicit_previous = _snapshot_rows_by_id(db, previous_ids)

    lifecycle_by_snapshot: dict[int, SetupLifecycleEvent] = {
        row.snapshot_id: row for row in lifecycle_events if row.snapshot_id
    }
    signal_snapshot_ids = {
        row.current_snapshot_id for row in signal_changes if row.current_snapshot_id
    }
    if signal_snapshot_ids:
        related_statement = (
            select(SetupLifecycleEvent)
            .where(SetupLifecycleEvent.snapshot_id.in_(signal_snapshot_ids))
            .order_by(SetupLifecycleEvent.id.desc())
        )
        if view_scope == SetupLifecycleViewScope.CURRENT_MARKET:
            related_statement = related_statement.where(
                SetupLifecycleEvent.is_current_version.is_(True)
            )
        related = db.scalars(related_statement).all()
        for row in related:
            if row.snapshot_id:
                lifecycle_by_snapshot.setdefault(row.snapshot_id, row)

    previous_by_current: dict[int, SetupSignalSnapshot] = {}
    explicit_by_current_id = {
        row.current_snapshot_id: explicit_previous.get(row.previous_snapshot_id)
        for row in signal_changes
        if row.current_snapshot_id and row.previous_snapshot_id
    }
    previous_by_current.update(
        {
            current_id: previous
            for current_id, previous in explicit_by_current_id.items()
            if previous is not None
        }
    )
    missing_previous = [
        row for row in current_snapshots.values() if row.id not in previous_by_current
    ]
    if missing_previous:
        current_alias = aliased(SetupSignalSnapshot)
        previous_lateral = (
            select(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == current_alias.ticker)
            .where(SetupSignalSnapshot.timeframe == current_alias.timeframe)
            .where(SetupSignalSnapshot.is_canonical.is_(True))
            .where(SetupSignalSnapshot.data_as_of_date < current_alias.data_as_of_date)
            .order_by(
                SetupSignalSnapshot.data_as_of_date.desc(),
                SetupSignalSnapshot.id.desc(),
            )
            .limit(1)
            .lateral()
        )
        previous_alias = aliased(SetupSignalSnapshot, previous_lateral)
        previous_rows = db.execute(
            select(current_alias.id, previous_alias)
            .options(
                load_only(
                    *(getattr(previous_alias, column.key) for column in _SNAPSHOT_PAYLOAD_COLUMNS)
                )
            )
            .join(previous_lateral, true())
            .where(current_alias.id.in_({row.id for row in missing_previous}))
        ).all()
        previous_by_current.update({current_id: previous for current_id, previous in previous_rows})

    episode_ids = (
        {row.episode_id for row in lifecycle_events if row.episode_id}
        | {row.episode_id for row in signal_changes if row.episode_id}
        | {row.episode_id for row in lifecycle_by_snapshot.values() if row.episode_id}
    )
    return _MarketChangePayloadContext(
        current_snapshots=current_snapshots,
        explicit_previous_snapshots=explicit_previous,
        previous_by_current_snapshot=previous_by_current,
        lifecycle_by_snapshot=lifecycle_by_snapshot,
        episodes=_rows_by_id(db, SetupLifecycleEpisode, episode_ids),
    )


def market_change_payload(
    db: Session,
    *,
    lifecycle_event: SetupLifecycleEvent | None = None,
    signal_change_event: SignalChangeEvent | None = None,
    context: _MarketChangePayloadContext | None = None,
) -> dict[str, Any]:
    if (lifecycle_event is None) == (signal_change_event is None):
        raise ValueError("exactly one source event is required")
    event = lifecycle_event or signal_change_event
    assert event is not None
    current_snapshot_id = (
        lifecycle_event.snapshot_id
        if lifecycle_event is not None
        else signal_change_event.current_snapshot_id
    )
    previous_snapshot_id = (
        signal_change_event.previous_snapshot_id if signal_change_event is not None else None
    )
    current = (
        context.current_snapshots.get(current_snapshot_id or 0)
        if context is not None
        else db.get(SetupSignalSnapshot, current_snapshot_id)
        if current_snapshot_id
        else None
    )
    previous = (
        context.explicit_previous_snapshots.get(previous_snapshot_id or 0)
        if context is not None
        else db.get(SetupSignalSnapshot, previous_snapshot_id)
        if previous_snapshot_id
        else None
    )
    if previous is None and current is not None and context is not None:
        previous = context.previous_by_current_snapshot.get(current.id)
    elif previous is None and current is not None:
        previous = db.scalar(
            select(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == current.ticker)
            .where(SetupSignalSnapshot.timeframe == current.timeframe)
            .where(SetupSignalSnapshot.is_canonical.is_(True))
            .where(SetupSignalSnapshot.data_as_of_date < current.data_as_of_date)
            .order_by(
                SetupSignalSnapshot.data_as_of_date.desc(),
                SetupSignalSnapshot.id.desc(),
            )
            .limit(1)
        )
    related_lifecycle = lifecycle_event
    if related_lifecycle is None and context is not None:
        related_lifecycle = context.lifecycle_by_snapshot.get(current_snapshot_id or 0)
    elif related_lifecycle is None and current_snapshot_id is not None:
        related_lifecycle = db.scalar(
            select(SetupLifecycleEvent)
            .where(SetupLifecycleEvent.snapshot_id == current_snapshot_id)
            .where(SetupLifecycleEvent.is_current_version.is_(True))
            .order_by(SetupLifecycleEvent.id.desc())
            .limit(1)
        )
    episode = None
    episode_id = getattr(related_lifecycle, "episode_id", None) or getattr(
        signal_change_event, "episode_id", None
    )
    if episode_id:
        episode = (
            context.episodes.get(episode_id)
            if context is not None
            else db.get(SetupLifecycleEpisode, episode_id)
        )
    evidence = dict(event.evidence_json or {})
    reason_codes = list(event.reason_codes_json or [])
    technical_velocities = _snapshot_velocity_map(current, "technical_score")
    setup_velocities = _snapshot_velocity_map(current, "setup_score")
    trigger_reference = dict(
        (getattr(current, "debug_json", None) or {}).get("trigger_reference") or {}
    )
    technical_score = _number_or_none(getattr(current, "dual_score", None))
    technical_score_previous = _number_or_none(getattr(previous, "dual_score", None))
    sector_rank = _snapshot_signal(current, "sector_rank")
    sector_rank_previous = _snapshot_signal(previous, "sector_rank")
    current_state = (
        related_lifecycle.to_state
        if related_lifecycle is not None
        else getattr(current, "lifecycle_state_candidate", None)
    )
    previous_state = (
        related_lifecycle.from_state
        if related_lifecycle is not None
        else getattr(previous, "lifecycle_state_candidate", None)
    )
    transition = None
    if related_lifecycle is not None and related_lifecycle.from_state != related_lifecycle.to_state:
        transition = (
            f"{related_lifecycle.from_state}_TO_{related_lifecycle.to_state}"
            if related_lifecycle.from_state
            else f"INITIAL_TO_{related_lifecycle.to_state}"
        )
    blockers = list(evidence.get("blockers") or ())
    if not blockers and episode is not None:
        blockers = list((episode.metadata_json or {}).get("blockers") or ())
    source_type = "LIFECYCLE_EVENT" if lifecycle_event is not None else "SIGNAL_CHANGE_EVENT"
    signal_key = signal_change_event.signal_key if signal_change_event is not None else None
    return {
        "id": event.id,
        "source_type": source_type,
        "lifecycle_event_id": lifecycle_event.id if lifecycle_event is not None else None,
        "signal_change_event_id": (
            signal_change_event.id if signal_change_event is not None else None
        ),
        "episode_id": episode_id,
        "evaluation_run_id": event.evaluation_run_id,
        "current_snapshot_id": current_snapshot_id,
        "ticker": event.ticker,
        "company": getattr(current, "company_name", None),
        "sector": getattr(current, "sector", None),
        "timeframe": event.timeframe,
        "data_as_of_date": _date_or_none(event.effective_date),
        "effective_date": _date_or_none(event.effective_date),
        "comparison_date": _date_or_none(getattr(previous, "data_as_of_date", None)),
        "missing_session_gap": _missing_session_gap(previous, current),
        "setup_family": getattr(related_lifecycle, "setup_family", None)
        or getattr(current, "primary_setup_family", None),
        "phase": getattr(related_lifecycle, "to_phase", None)
        or getattr(current, "primary_phase", None),
        "from_phase": getattr(related_lifecycle, "from_phase", None),
        "previous_state": previous_state,
        "current_state": current_state,
        "from_state": previous_state,
        "to_state": current_state,
        "transition": transition,
        "event_type": getattr(lifecycle_event, "event_type", None) or "MATERIAL_CHANGE",
        "state_age_sessions": getattr(episode, "state_age_sessions", None),
        "actionability": getattr(related_lifecycle, "actionability_after", None)
        or getattr(current, "actionability_candidate", None),
        "actionability_after": getattr(related_lifecycle, "actionability_after", None)
        or getattr(current, "actionability_candidate", None),
        "confidence": getattr(related_lifecycle, "confidence_score", None)
        if related_lifecycle is not None
        else getattr(current, "confidence_score", None),
        "confidence_score": getattr(related_lifecycle, "confidence_score", None)
        if related_lifecycle is not None
        else getattr(current, "confidence_score", None),
        "confidence_label": getattr(related_lifecycle, "confidence_label", None)
        if related_lifecycle is not None
        else getattr(current, "confidence_label", None),
        "technical_score": technical_score,
        "technical_score_previous": technical_score_previous,
        "technical_score_delta": _difference(technical_score, technical_score_previous),
        "setup_score": _number_or_none(getattr(current, "setup_score", None)),
        "setup_score_previous": _number_or_none(getattr(previous, "setup_score", None)),
        "score_velocity_1d": _velocity_value(technical_velocities, 1),
        "score_velocity_3d": _velocity_value(technical_velocities, 3),
        "score_velocity_5d": _velocity_value(technical_velocities, 5),
        "score_velocity_10d": _velocity_value(technical_velocities, 10),
        "technical_score_velocity": technical_velocities,
        "setup_score_velocity_1d": _velocity_value(setup_velocities, 1),
        "setup_score_velocity_3d": _velocity_value(setup_velocities, 3),
        "setup_score_velocity_5d": _velocity_value(setup_velocities, 5),
        "setup_score_velocity_10d": _velocity_value(setup_velocities, 10),
        "setup_score_velocity": setup_velocities,
        "trigger_distance_pct": _number_or_none(getattr(current, "distance_to_pivot_pct", None)),
        "trigger_reference_type": trigger_reference.get("reference_type"),
        "trigger_reference_price": _number_or_none(trigger_reference.get("reference_price")),
        "trigger_reference_source": trigger_reference.get("source_path"),
        "trigger_reference_source_id": trigger_reference.get("source_record_id"),
        "trigger_reference_session": trigger_reference.get("source_session"),
        "trigger_distance_missing_reason": trigger_reference.get("missing_reason"),
        "sector_rank": _int_or_none(sector_rank),
        "sector_rank_previous": _int_or_none(sector_rank_previous),
        "sector_rank_delta": _rank_improvement(sector_rank_previous, sector_rank),
        "market_regime": _snapshot_signal(current, "market_regime"),
        "market_gate": _snapshot_signal(current, "market_gate"),
        "earnings_risk": _snapshot_signal(current, "earnings_risk"),
        "liquidity_risk": _snapshot_signal(current, "liquidity"),
        "required_feature_coverage": _number_or_none(
            getattr(current, "required_feature_coverage", None)
        ),
        "freshness": getattr(current, "freshness_status", None),
        "data_quality_label": getattr(current, "data_quality_label", None),
        "blockers": blockers,
        "latest_reason": reason_codes[0] if reason_codes else None,
        "reason_codes": reason_codes,
        "warning_flags": list(getattr(current, "warning_flags_json", None) or ()),
        "warning_count": len(list(getattr(current, "warning_flags_json", None) or ())),
        "severity": event.severity,
        "signal_definition_version": getattr(
            signal_change_event, "signal_definition_version", None
        ),
        "signal_key": signal_key,
        "old_value": dict(signal_change_event.old_value_json or {})
        if signal_change_event is not None
        else None,
        "new_value": dict(signal_change_event.new_value_json or {})
        if signal_change_event is not None
        else None,
        "normalized_delta": _number_or_none(getattr(signal_change_event, "normalized_delta", None)),
        "snapshot_id": current_snapshot_id,
        "previous_snapshot_id": getattr(previous, "id", None),
        "source_run_id": getattr(current, "run_id", None),
        "origin_type": getattr(current, "origin_type", None),
        "is_canonical": getattr(current, "is_canonical", None),
        "record_status": (
            "CURRENT_CANONICAL"
            if getattr(current, "is_canonical", False)
            else "SUPERSEDED"
            if getattr(current, "superseded_by_snapshot_id", None) is not None
            else "NONCANONICAL"
        ),
        "superseded_by_snapshot_id": getattr(current, "superseded_by_snapshot_id", None),
        "engine_version": getattr(current, "engine_version", None),
        "config_version": getattr(current, "config_version", None),
        "schema_version": getattr(current, "schema_version", None),
        "config_hash": getattr(current, "config_hash", None),
        "source_data_hash": getattr(current, "source_data_hash", None),
        "source_event_key": event.source_event_key,
        "timeline_url": f"/setup-lifecycle/ticker/{event.ticker}",
        "evidence": evidence,
        "source_url": (
            f"/setup-lifecycle/episodes/{episode_id}"
            if lifecycle_event is not None and episode_id
            else f"/setup-lifecycle/ticker/{event.ticker}#signal-change-{signal_change_event.id}"
            if signal_change_event is not None
            else None
        ),
    }


@dataclass(frozen=True)
class _AlertPayloadContext:
    rules: dict[int, SignalAlertRule]
    lifecycle_events: dict[int, SetupLifecycleEvent]
    signal_changes: dict[int, SignalChangeEvent]
    snapshots: dict[int, SetupSignalSnapshot]


def _prime_alert_payload_context(
    db: Session, alerts: list[SignalAlertEvent]
) -> _AlertPayloadContext:
    rule_ids = {row.alert_rule_id for row in alerts}
    lifecycle_ids = {row.lifecycle_event_id for row in alerts if row.lifecycle_event_id}
    change_ids = {row.signal_change_event_id for row in alerts if row.signal_change_event_id}
    rules = _rows_by_id(db, SignalAlertRule, rule_ids)
    lifecycle_events = _rows_by_id(db, SetupLifecycleEvent, lifecycle_ids)
    signal_changes = _rows_by_id(db, SignalChangeEvent, change_ids)
    snapshot_ids = {row.snapshot_id for row in lifecycle_events.values() if row.snapshot_id} | {
        row.current_snapshot_id for row in signal_changes.values() if row.current_snapshot_id
    }
    return _AlertPayloadContext(
        rules=rules,
        lifecycle_events=lifecycle_events,
        signal_changes=signal_changes,
        snapshots=_snapshot_rows_by_id(db, snapshot_ids),
    )


def _rows_by_id(db: Session, model, row_ids: set[int]) -> dict[int, Any]:
    if not row_ids:
        return {}
    rows = db.scalars(select(model).where(model.id.in_(row_ids))).all()
    return {row.id: row for row in rows}


_SNAPSHOT_PAYLOAD_COLUMNS = (
    SetupSignalSnapshot.id,
    SetupSignalSnapshot.evaluation_run_id,
    SetupSignalSnapshot.run_id,
    SetupSignalSnapshot.ticker,
    SetupSignalSnapshot.company_name,
    SetupSignalSnapshot.sector,
    SetupSignalSnapshot.timeframe,
    SetupSignalSnapshot.data_as_of_date,
    SetupSignalSnapshot.origin_type,
    SetupSignalSnapshot.engine_version,
    SetupSignalSnapshot.config_version,
    SetupSignalSnapshot.config_hash,
    SetupSignalSnapshot.source_data_hash,
    SetupSignalSnapshot.schema_version,
    SetupSignalSnapshot.is_canonical,
    SetupSignalSnapshot.superseded_by_snapshot_id,
    SetupSignalSnapshot.primary_setup_family,
    SetupSignalSnapshot.primary_phase,
    SetupSignalSnapshot.lifecycle_state_candidate,
    SetupSignalSnapshot.actionability_candidate,
    SetupSignalSnapshot.data_quality_label,
    SetupSignalSnapshot.confidence_score,
    SetupSignalSnapshot.confidence_label,
    SetupSignalSnapshot.dual_score,
    SetupSignalSnapshot.setup_score,
    SetupSignalSnapshot.distance_to_pivot_pct,
    SetupSignalSnapshot.required_feature_coverage,
    SetupSignalSnapshot.freshness_status,
    SetupSignalSnapshot.signals_json,
    SetupSignalSnapshot.debug_json,
    SetupSignalSnapshot.warning_flags_json,
)


def _snapshot_rows_by_id(db: Session, row_ids: set[int]) -> dict[int, SetupSignalSnapshot]:
    if not row_ids:
        return {}
    rows = db.scalars(
        select(SetupSignalSnapshot)
        .options(load_only(*_SNAPSHOT_PAYLOAD_COLUMNS))
        .where(SetupSignalSnapshot.id.in_(row_ids))
    ).all()
    return {row.id: row for row in rows}


def alert_payload(
    alert: SignalAlertEvent,
    *,
    db: Session | None = None,
    context: _AlertPayloadContext | None = None,
) -> dict[str, Any]:
    if context is not None:
        rule = context.rules.get(alert.alert_rule_id)
        lifecycle = context.lifecycle_events.get(alert.lifecycle_event_id or 0)
        change = context.signal_changes.get(alert.signal_change_event_id or 0)
        snapshot_id = (
            change.current_snapshot_id
            if change is not None and change.current_snapshot_id
            else lifecycle.snapshot_id
            if lifecycle is not None
            else None
        )
        source_snapshot = context.snapshots.get(snapshot_id or 0)
    else:
        rule = getattr(alert, "alert_rule", None)
        if db is not None and (rule is None or getattr(rule, "id", None) is None):
            rule = db.get(SignalAlertRule, alert.alert_rule_id)
        lifecycle = (
            db.get(SetupLifecycleEvent, alert.lifecycle_event_id)
            if db is not None and alert.lifecycle_event_id
            else None
        )
        change = (
            db.get(SignalChangeEvent, alert.signal_change_event_id)
            if db is not None and alert.signal_change_event_id
            else None
        )
        source_snapshot = (
            db.get(SetupSignalSnapshot, change.current_snapshot_id)
            if db is not None and change is not None and change.current_snapshot_id
            else db.get(SetupSignalSnapshot, lifecycle.snapshot_id)
            if db is not None and lifecycle is not None and lifecycle.snapshot_id
            else None
        )
    evidence = dict(alert.evidence_json or {})
    source_type = _alert_source_type(rule, lifecycle, change, evidence)
    episode_id = getattr(lifecycle, "episode_id", None) or getattr(change, "episode_id", None)
    blockers = list(evidence.get("blockers") or ())
    confidence = getattr(lifecycle, "confidence_score", None)
    if confidence is None:
        confidence = getattr(source_snapshot, "confidence_score", None)
    if confidence is None:
        confidence = evidence.get("source_confidence")
    return {
        "id": alert.id,
        "alert_rule_id": alert.alert_rule_id,
        "lifecycle_event_id": alert.lifecycle_event_id,
        "signal_change_event_id": alert.signal_change_event_id,
        "evaluation_run_id": alert.evaluation_run_id,
        "ticker": alert.ticker,
        "timeframe": alert.timeframe,
        "effective_date": _date_or_none(alert.effective_date),
        "event_key": alert.event_key,
        "source_event_key": alert.source_event_key,
        "alert_type": getattr(rule, "rule_id", None) or evidence.get("rule_id"),
        "review_status": alert.status,
        "status": alert.status,
        "severity": alert.severity,
        "source_type": source_type,
        "episode_id": episode_id,
        "lifecycle_state": getattr(lifecycle, "to_state", None)
        or getattr(source_snapshot, "lifecycle_state_candidate", None),
        "actionability": getattr(lifecycle, "actionability_after", None)
        or getattr(source_snapshot, "actionability_candidate", None)
        or evidence.get("actionability_after"),
        "confidence": _int_or_none(confidence),
        "confidence_label": getattr(lifecycle, "confidence_label", None)
        or getattr(source_snapshot, "confidence_label", None)
        or evidence.get("confidence_label"),
        "blockers": blockers,
        "snapshot_id": getattr(lifecycle, "snapshot_id", None)
        or getattr(change, "current_snapshot_id", None),
        "previous_snapshot_id": getattr(change, "previous_snapshot_id", None),
        "origin_type": getattr(source_snapshot, "origin_type", None),
        "is_canonical": getattr(source_snapshot, "is_canonical", None),
        "record_status": (
            "CURRENT_CANONICAL"
            if getattr(source_snapshot, "is_canonical", False)
            else "SUPERSEDED"
            if getattr(source_snapshot, "superseded_by_snapshot_id", None) is not None
            else "CURRENT"
            if lifecycle is not None and lifecycle.is_current_version
            else "SUPERSEDED"
            if lifecycle is not None
            else "UNKNOWN"
        ),
        "engine_version": getattr(lifecycle, "engine_version", None)
        or getattr(source_snapshot, "engine_version", None),
        "config_version": getattr(lifecycle, "config_version", None)
        or getattr(source_snapshot, "config_version", None)
        or getattr(rule, "config_version", None),
        "schema_version": getattr(source_snapshot, "schema_version", None),
        "config_hash": getattr(lifecycle, "config_hash", None)
        or getattr(change, "config_hash", None)
        or getattr(source_snapshot, "config_hash", None),
        "source_data_hash": getattr(source_snapshot, "source_data_hash", None),
        "reason_codes": list(alert.reason_codes_json or []),
        "evidence": evidence,
        "source_url": (
            f"/setup-lifecycle/episodes/{episode_id}"
            if episode_id
            else (
                f"/setup-lifecycle/ticker/{alert.ticker}"
                f"#signal-change-{alert.signal_change_event_id}"
            )
            if alert.signal_change_event_id
            else None
        ),
    }


@dataclass(frozen=True)
class _NoMaterialPayloadContext:
    previous_by_current: dict[int, SetupSignalSnapshot]
    episode_by_ticker: dict[tuple[str, str], SetupLifecycleEpisode]


def _prime_no_material_payload_context(
    db: Session, snapshots: list[SetupSignalSnapshot]
) -> _NoMaterialPayloadContext:
    if not snapshots:
        return _NoMaterialPayloadContext({}, {})
    current_alias = aliased(SetupSignalSnapshot)
    previous_lateral = (
        select(SetupSignalSnapshot)
        .where(SetupSignalSnapshot.ticker == current_alias.ticker)
        .where(SetupSignalSnapshot.timeframe == current_alias.timeframe)
        .where(SetupSignalSnapshot.is_canonical.is_(True))
        .where(SetupSignalSnapshot.data_as_of_date < current_alias.data_as_of_date)
        .order_by(
            SetupSignalSnapshot.data_as_of_date.desc(),
            SetupSignalSnapshot.id.desc(),
        )
        .limit(1)
        .lateral()
    )
    previous_alias = aliased(SetupSignalSnapshot, previous_lateral)
    previous_rows = db.execute(
        select(current_alias.id, previous_alias)
        .options(
            load_only(
                *(getattr(previous_alias, column.key) for column in _SNAPSHOT_PAYLOAD_COLUMNS)
            )
        )
        .join(previous_lateral, true())
        .where(current_alias.id.in_({row.id for row in snapshots}))
    ).all()
    tickers = {row.ticker for row in snapshots}
    episodes = db.scalars(
        select(SetupLifecycleEpisode)
        .where(SetupLifecycleEpisode.ticker.in_(tickers))
        .where(SetupLifecycleEpisode.status == "ACTIVE")
        .order_by(
            SetupLifecycleEpisode.is_primary.desc(),
            SetupLifecycleEpisode.id.desc(),
        )
    ).all()
    episode_by_ticker: dict[tuple[str, str], SetupLifecycleEpisode] = {}
    for episode in episodes:
        episode_by_ticker.setdefault((episode.ticker, episode.timeframe), episode)
    return _NoMaterialPayloadContext(
        {current_id: previous for current_id, previous in previous_rows},
        episode_by_ticker,
    )


def no_material_change_payload(
    db: Session,
    snapshot: SetupSignalSnapshot,
    *,
    context: _NoMaterialPayloadContext | None = None,
) -> dict[str, Any]:
    previous = (
        context.previous_by_current.get(snapshot.id)
        if context is not None
        else db.scalar(
            select(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == snapshot.ticker)
            .where(SetupSignalSnapshot.timeframe == snapshot.timeframe)
            .where(SetupSignalSnapshot.is_canonical.is_(True))
            .where(SetupSignalSnapshot.data_as_of_date < snapshot.data_as_of_date)
            .order_by(
                SetupSignalSnapshot.data_as_of_date.desc(),
                SetupSignalSnapshot.id.desc(),
            )
            .limit(1)
        )
    )
    episode = (
        context.episode_by_ticker.get((snapshot.ticker, snapshot.timeframe))
        if context is not None
        else db.scalar(
            select(SetupLifecycleEpisode)
            .where(SetupLifecycleEpisode.ticker == snapshot.ticker)
            .where(SetupLifecycleEpisode.timeframe == snapshot.timeframe)
            .where(SetupLifecycleEpisode.status == "ACTIVE")
            .order_by(
                SetupLifecycleEpisode.is_primary.desc(),
                SetupLifecycleEpisode.id.desc(),
            )
            .limit(1)
        )
    )
    score = _number_or_none(snapshot.dual_score)
    previous_score = _number_or_none(getattr(previous, "dual_score", None))
    sector_rank = _snapshot_signal(snapshot, "sector_rank")
    previous_rank = _snapshot_signal(previous, "sector_rank")
    blockers = list((getattr(episode, "metadata_json", None) or {}).get("blockers") or ())
    technical_velocities = _snapshot_velocity_map(snapshot, "technical_score")
    setup_velocities = _snapshot_velocity_map(snapshot, "setup_score")
    trigger_reference = dict((snapshot.debug_json or {}).get("trigger_reference") or {})
    return {
        "id": snapshot.id,
        "source_type": "SNAPSHOT_OBSERVATION",
        "lifecycle_event_id": None,
        "signal_change_event_id": None,
        "episode_id": getattr(episode, "id", None),
        "evaluation_run_id": snapshot.evaluation_run_id,
        "ticker": snapshot.ticker,
        "company": snapshot.company_name,
        "sector": snapshot.sector,
        "timeframe": snapshot.timeframe,
        "data_as_of_date": _date_or_none(snapshot.data_as_of_date),
        "effective_date": _date_or_none(snapshot.data_as_of_date),
        "comparison_date": _date_or_none(getattr(previous, "data_as_of_date", None)),
        "missing_session_gap": _missing_session_gap(previous, snapshot),
        "setup_family": snapshot.primary_setup_family,
        "phase": snapshot.primary_phase,
        "from_phase": getattr(previous, "primary_phase", None),
        "previous_state": getattr(previous, "lifecycle_state_candidate", None),
        "current_state": snapshot.lifecycle_state_candidate,
        "from_state": getattr(previous, "lifecycle_state_candidate", None),
        "to_state": snapshot.lifecycle_state_candidate,
        "transition": "NO_MATERIAL_CHANGE",
        "event_type": "NO_MATERIAL_CHANGE",
        "state_age_sessions": getattr(episode, "state_age_sessions", None),
        "actionability": snapshot.actionability_candidate,
        "actionability_after": snapshot.actionability_candidate,
        "confidence": snapshot.confidence_score,
        "confidence_score": snapshot.confidence_score,
        "confidence_label": snapshot.confidence_label,
        "technical_score": score,
        "technical_score_previous": previous_score,
        "technical_score_delta": _difference(score, previous_score),
        "setup_score": _number_or_none(snapshot.setup_score),
        "setup_score_previous": _number_or_none(getattr(previous, "setup_score", None)),
        "score_velocity_1d": _velocity_value(technical_velocities, 1),
        "score_velocity_3d": _velocity_value(technical_velocities, 3),
        "score_velocity_5d": _velocity_value(technical_velocities, 5),
        "score_velocity_10d": _velocity_value(technical_velocities, 10),
        "technical_score_velocity": technical_velocities,
        "setup_score_velocity_1d": _velocity_value(setup_velocities, 1),
        "setup_score_velocity_3d": _velocity_value(setup_velocities, 3),
        "setup_score_velocity_5d": _velocity_value(setup_velocities, 5),
        "setup_score_velocity_10d": _velocity_value(setup_velocities, 10),
        "setup_score_velocity": setup_velocities,
        "trigger_distance_pct": _number_or_none(snapshot.distance_to_pivot_pct),
        "trigger_reference_type": trigger_reference.get("reference_type"),
        "trigger_reference_price": _number_or_none(trigger_reference.get("reference_price")),
        "trigger_reference_source": trigger_reference.get("source_path"),
        "trigger_reference_source_id": trigger_reference.get("source_record_id"),
        "trigger_reference_session": trigger_reference.get("source_session"),
        "trigger_distance_missing_reason": trigger_reference.get("missing_reason"),
        "sector_rank": _int_or_none(sector_rank),
        "sector_rank_previous": _int_or_none(previous_rank),
        "sector_rank_delta": _rank_improvement(previous_rank, sector_rank),
        "market_regime": _snapshot_signal(snapshot, "market_regime"),
        "market_gate": _snapshot_signal(snapshot, "market_gate"),
        "earnings_risk": _snapshot_signal(snapshot, "earnings_risk"),
        "liquidity_risk": _snapshot_signal(snapshot, "liquidity"),
        "required_feature_coverage": _number_or_none(snapshot.required_feature_coverage),
        "freshness": snapshot.freshness_status,
        "data_quality_label": snapshot.data_quality_label,
        "blockers": blockers,
        "latest_reason": "NO_MATERIAL_CHANGE",
        "reason_codes": ["NO_MATERIAL_CHANGE"],
        "warning_flags": list(snapshot.warning_flags_json or ()),
        "warning_count": len(list(snapshot.warning_flags_json or ())),
        "severity": "INFO",
        "signal_key": None,
        "old_value": None,
        "new_value": None,
        "normalized_delta": None,
        "snapshot_id": snapshot.id,
        "previous_snapshot_id": getattr(previous, "id", None),
        "source_run_id": snapshot.run_id,
        "origin_type": snapshot.origin_type,
        "is_canonical": snapshot.is_canonical,
        "record_status": "CURRENT_CANONICAL",
        "superseded_by_snapshot_id": snapshot.superseded_by_snapshot_id,
        "engine_version": snapshot.engine_version,
        "config_version": snapshot.config_version,
        "schema_version": snapshot.schema_version,
        "config_hash": snapshot.config_hash,
        "source_data_hash": snapshot.source_data_hash,
        "source_event_key": None,
        "timeline_url": f"/setup-lifecycle/ticker/{snapshot.ticker}",
        "evidence": {"snapshot_id": snapshot.id},
        "source_url": f"/setup-lifecycle/ticker/{snapshot.ticker}",
    }


def evaluation_run_payload(run: SetupLifecycleEvaluationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "source_run_id": run.source_run_id,
        "mode": run.mode,
        "status": run.status,
        "current_phase": run.current_phase,
        "engine_version": run.engine_version,
        "config_version": run.config_version,
        "config_hash": run.config_hash,
        "output_evaluation_version": run.output_evaluation_version,
        "date_from": _date_or_none(run.date_from),
        "date_to": _date_or_none(run.date_to),
        "dry_run": run.dry_run,
        "ticker_scope": list(run.ticker_scope_json or []),
        "requested_config": dict(run.requested_config_json or {}),
        "requester": run.requester,
        "audit": dict(run.audit_json or {}),
        "read_count": run.read_count,
        "captured_count": run.captured_count,
        "canonical_count": run.canonical_count,
        "changed_count": run.changed_count,
        "transitioned_count": run.transitioned_count,
        "alerted_count": run.alerted_count,
        "warning_count": run.warning_count,
        "failed_count": run.failed_count,
        "created_at": _datetime_or_none(run.created_at),
        "completed_at": _datetime_or_none(run.completed_at),
        "duration_ms": run.duration_ms,
        "errors": dict(run.error_summary_json or {}),
    }


def _validate_query(query: SetupLifecycleListQuery) -> SetupLifecycleListQuery:
    if query.limit < 1 or query.limit > 500:
        raise SetupLifecycleQueryError("INVALID_LIMIT", "limit must be between 1 and 500")
    if query.direction not in {"asc", "desc"}:
        raise SetupLifecycleQueryError("INVALID_SORT", "direction must be asc or desc")
    filters = query.filters
    if query.view_scope == SetupLifecycleViewScope.HISTORICAL_RUN and filters.run_id is None:
        raise SetupLifecycleQueryError(
            "INVALID_CONFIGURATION",
            "HISTORICAL_RUN scope requires run_id",
        )
    _validate_enum("setup_family", filters.setup_family, {item.value for item in SetupFamily})
    _validate_enum(
        "lifecycle_state", filters.lifecycle_state, {item.value for item in LifecycleState}
    )
    _validate_enum("actionability", filters.actionability, {item.value for item in Actionability})
    _validate_enum("alert_status", filters.alert_status, {"UNREAD", "ACKNOWLEDGED", "DISMISSED"})
    _validate_enum("alert_severity", filters.alert_severity, {item.value for item in EventSeverity})
    _validate_enum(
        "source_type",
        filters.source_type,
        {
            "LIFECYCLE_EVENT",
            "SIGNAL_CHANGE_EVENT",
            "ACTIONABILITY_CHANGE",
            "DATA_QUALITY_CHANGE",
        },
    )
    if filters.alert_type and filters.alert_type not in {
        "NEW_READY",
        "NEW_TRIGGER",
        "NEW_CONFIRMATION",
        "NEW_FAILURE",
        "NEW_EXTENSION",
        "SCORE_ACCELERATION",
        "SECTOR_ACCELERATION",
        "GATE_BLOCKED",
        "DATA_DEGRADED",
    }:
        raise SetupLifecycleQueryError("INVALID_CONFIGURATION", "unsupported alert_type")
    _validate_range("confidence", filters.confidence_min, filters.confidence_max, 0, 100)
    _validate_range("state_age", filters.state_age_min, filters.state_age_max, 0, None)
    _validate_range(
        "sector_rank_change",
        filters.sector_rank_change_min,
        filters.sector_rank_change_max,
        None,
        None,
    )
    if filters.velocity_window not in {1, 3, 5, 10}:
        raise SetupLifecycleQueryError(
            "INVALID_CONFIGURATION", "velocity_window must be one of 1, 3, 5, or 10"
        )
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise SetupLifecycleQueryError("INVALID_DATE", "date_from must not be after date_to")
    _offset(query.cursor)
    return query


def _validate_enum(name: str, value: str | None, allowed: set[str]) -> None:
    if value is not None and value not in allowed:
        raise SetupLifecycleQueryError("INVALID_CONFIGURATION", f"unsupported {name}: {value}")


def _validate_range(
    name: str,
    minimum: int | float | None,
    maximum: int | float | None,
    floor: int | float | None,
    ceiling: int | float | None,
) -> None:
    if minimum is not None and floor is not None and minimum < floor:
        raise SetupLifecycleQueryError("INVALID_THRESHOLD", f"{name}_min is below {floor}")
    if minimum is not None and ceiling is not None and minimum > ceiling:
        raise SetupLifecycleQueryError("INVALID_THRESHOLD", f"{name}_min exceeds {ceiling}")
    if maximum is not None and floor is not None and maximum < floor:
        raise SetupLifecycleQueryError("INVALID_THRESHOLD", f"{name}_max is below {floor}")
    if maximum is not None and ceiling is not None and maximum > ceiling:
        raise SetupLifecycleQueryError("INVALID_THRESHOLD", f"{name}_max exceeds {ceiling}")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise SetupLifecycleQueryError("INVALID_THRESHOLD", f"{name}_min exceeds {name}_max")


def _apply_event_filters(statement, filters: SetupLifecycleFilters):
    if filters.ticker:
        statement = statement.where(SetupLifecycleEvent.ticker == filters.ticker.strip().upper())
    if filters.run_id is not None:
        statement = statement.where(SetupSignalSnapshot.run_id == filters.run_id)
    if filters.setup_family:
        statement = statement.where(SetupLifecycleEvent.setup_family == filters.setup_family)
    if filters.lifecycle_state:
        statement = statement.where(SetupLifecycleEvent.to_state == filters.lifecycle_state)
    if filters.transition:
        if filters.transition.startswith("TO_"):
            statement = statement.where(SetupLifecycleEvent.event_type == "STATE_TRANSITION")
            statement = statement.where(
                SetupLifecycleEvent.to_state == filters.transition.removeprefix("TO_")
            )
        elif "_TO_" in filters.transition:
            from_state, to_state = filters.transition.split("_TO_", 1)
            statement = statement.where(SetupLifecycleEvent.from_state == from_state)
            statement = statement.where(SetupLifecycleEvent.to_state == to_state)
        else:
            statement = statement.where(SetupLifecycleEvent.event_type == filters.transition)
    if filters.actionability:
        statement = statement.where(
            SetupLifecycleEvent.actionability_after == filters.actionability
        )
    if filters.confidence_min is not None:
        statement = statement.where(SetupLifecycleEvent.confidence_score >= filters.confidence_min)
    if filters.confidence_max is not None:
        statement = statement.where(SetupLifecycleEvent.confidence_score <= filters.confidence_max)
    if filters.state_age_min is not None:
        statement = statement.where(SetupLifecycleEvent.state_age_before >= filters.state_age_min)
    if filters.state_age_max is not None:
        statement = statement.where(SetupLifecycleEvent.state_age_before <= filters.state_age_max)
    if filters.sector:
        statement = statement.where(SetupSignalSnapshot.sector == filters.sector)
    if filters.setup_score_min is not None:
        statement = statement.where(SetupSignalSnapshot.setup_score >= filters.setup_score_min)
    if filters.setup_score_max is not None:
        statement = statement.where(SetupSignalSnapshot.setup_score <= filters.setup_score_max)
    if filters.trigger_distance_min is not None:
        statement = statement.where(
            SetupSignalSnapshot.distance_to_pivot_pct >= filters.trigger_distance_min
        )
    if filters.trigger_distance_max is not None:
        statement = statement.where(
            SetupSignalSnapshot.distance_to_pivot_pct <= filters.trigger_distance_max
        )
    if filters.sector_rank_min is not None:
        statement = statement.where(_signal_int("sector_rank") >= filters.sector_rank_min)
    if filters.sector_rank_max is not None:
        statement = statement.where(_signal_int("sector_rank") <= filters.sector_rank_max)
    if filters.velocity_min is not None:
        statement = statement.where(
            _event_velocity(filters.velocity_window) >= filters.velocity_min
        )
    if filters.velocity_max is not None:
        statement = statement.where(
            _event_velocity(filters.velocity_window) <= filters.velocity_max
        )
    if filters.market_regime:
        statement = statement.where(_signal_text("market_regime") == filters.market_regime)
    if filters.warning_flag:
        statement = statement.where(
            or_(
                SetupLifecycleEvent.warning_flags_json.contains([filters.warning_flag]),
                SetupSignalSnapshot.warning_flags_json.contains([filters.warning_flag]),
            )
        )
    if filters.blocker:
        statement = statement.where(
            SetupLifecycleEvent.evidence_json["blockers"].contains([filters.blocker])
        )
    if filters.as_of_date is not None:
        statement = statement.where(SetupLifecycleEvent.effective_date == filters.as_of_date)
    if filters.date_from is not None:
        statement = statement.where(SetupLifecycleEvent.effective_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(SetupLifecycleEvent.effective_date <= filters.date_to)
    if filters.source_type and filters.source_type != "LIFECYCLE_EVENT":
        statement = statement.where(False)
    return statement


def _apply_signal_change_filters(statement, filters: SetupLifecycleFilters):
    if filters.ticker:
        statement = statement.where(SignalChangeEvent.ticker == filters.ticker.strip().upper())
    if filters.run_id is not None:
        statement = statement.where(SetupSignalSnapshot.run_id == filters.run_id)
    if filters.sector:
        statement = statement.where(SetupSignalSnapshot.sector == filters.sector)
    if filters.setup_family:
        statement = statement.where(
            SetupSignalSnapshot.primary_setup_family == filters.setup_family
        )
    if filters.lifecycle_state:
        statement = statement.where(
            SetupSignalSnapshot.lifecycle_state_candidate == filters.lifecycle_state
        )
    if filters.transition:
        statement = statement.where(False)
    if filters.actionability:
        statement = statement.where(
            SetupSignalSnapshot.actionability_candidate == filters.actionability
        )
    if filters.confidence_min is not None:
        statement = statement.where(SetupSignalSnapshot.confidence_score >= filters.confidence_min)
    if filters.confidence_max is not None:
        statement = statement.where(SetupSignalSnapshot.confidence_score <= filters.confidence_max)
    if filters.setup_score_min is not None:
        statement = statement.where(SetupSignalSnapshot.setup_score >= filters.setup_score_min)
    if filters.setup_score_max is not None:
        statement = statement.where(SetupSignalSnapshot.setup_score <= filters.setup_score_max)
    if filters.trigger_distance_min is not None:
        statement = statement.where(
            SetupSignalSnapshot.distance_to_pivot_pct >= filters.trigger_distance_min
        )
    if filters.trigger_distance_max is not None:
        statement = statement.where(
            SetupSignalSnapshot.distance_to_pivot_pct <= filters.trigger_distance_max
        )
    if filters.sector_rank_min is not None:
        statement = statement.where(_signal_int("sector_rank") >= filters.sector_rank_min)
    if filters.sector_rank_max is not None:
        statement = statement.where(_signal_int("sector_rank") <= filters.sector_rank_max)
    if filters.sector_rank_change_min is not None:
        statement = statement.where(SignalChangeEvent.rank_delta >= filters.sector_rank_change_min)
    if filters.sector_rank_change_max is not None:
        statement = statement.where(SignalChangeEvent.rank_delta <= filters.sector_rank_change_max)
    if filters.velocity_min is not None:
        statement = statement.where(
            _change_velocity(filters.velocity_window) >= filters.velocity_min
        )
    if filters.velocity_max is not None:
        statement = statement.where(
            _change_velocity(filters.velocity_window) <= filters.velocity_max
        )
    if filters.market_regime:
        statement = statement.where(_signal_text("market_regime") == filters.market_regime)
    if filters.warning_flag:
        statement = statement.where(
            SetupSignalSnapshot.warning_flags_json.contains([filters.warning_flag])
        )
    if filters.blocker:
        blocker_episode = (
            select(SetupLifecycleEpisode.id)
            .where(SetupLifecycleEpisode.id == SignalChangeEvent.episode_id)
            .where(SetupLifecycleEpisode.metadata_json["blockers"].contains([filters.blocker]))
            .exists()
        )
        statement = statement.where(blocker_episode)
    if filters.as_of_date is not None:
        statement = statement.where(SignalChangeEvent.effective_date == filters.as_of_date)
    if filters.date_from is not None:
        statement = statement.where(SignalChangeEvent.effective_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(SignalChangeEvent.effective_date <= filters.date_to)
    if filters.source_type and filters.source_type != "SIGNAL_CHANGE_EVENT":
        statement = statement.where(False)
    return statement


def _apply_alert_filters(statement, filters: SetupLifecycleFilters):
    if filters.ticker:
        statement = statement.where(SignalAlertEvent.ticker == filters.ticker.strip().upper())
    if filters.alert_status:
        statement = statement.where(SignalAlertEvent.status == filters.alert_status)
    if filters.alert_severity:
        statement = statement.where(SignalAlertEvent.severity == filters.alert_severity)
    if filters.alert_type:
        statement = statement.where(SignalAlertRule.rule_id == filters.alert_type)
    if filters.as_of_date is not None:
        statement = statement.where(SignalAlertEvent.effective_date == filters.as_of_date)
    if filters.date_from is not None:
        statement = statement.where(SignalAlertEvent.effective_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(SignalAlertEvent.effective_date <= filters.date_to)
    if filters.lifecycle_state:
        lifecycle_match = (
            select(SetupLifecycleEvent.id)
            .where(SetupLifecycleEvent.id == SignalAlertEvent.lifecycle_event_id)
            .where(SetupLifecycleEvent.to_state == filters.lifecycle_state)
            .exists()
        )
        signal_match = (
            select(SignalChangeEvent.id)
            .join(
                SetupSignalSnapshot,
                SignalChangeEvent.current_snapshot_id == SetupSignalSnapshot.id,
            )
            .where(SignalChangeEvent.id == SignalAlertEvent.signal_change_event_id)
            .where(SetupSignalSnapshot.lifecycle_state_candidate == filters.lifecycle_state)
            .exists()
        )
        statement = statement.where(or_(lifecycle_match, signal_match))
    if filters.source_type:
        if filters.source_type == "LIFECYCLE_EVENT":
            statement = statement.where(SignalAlertEvent.lifecycle_event_id.is_not(None))
        elif filters.source_type == "SIGNAL_CHANGE_EVENT":
            statement = statement.where(SignalAlertEvent.signal_change_event_id.is_not(None))
        elif filters.source_type == "ACTIONABILITY_CHANGE":
            statement = statement.where(SignalAlertRule.scope == "actionability_change")
        elif filters.source_type == "DATA_QUALITY_CHANGE":
            statement = statement.where(SignalAlertRule.rule_id == "DATA_DEGRADED")
        else:
            statement = statement.where(False)
    if filters.actionability:
        lifecycle_match = (
            select(SetupLifecycleEvent.id)
            .where(SetupLifecycleEvent.id == SignalAlertEvent.lifecycle_event_id)
            .where(SetupLifecycleEvent.actionability_after == filters.actionability)
            .exists()
        )
        signal_match = (
            select(SignalChangeEvent.id)
            .join(
                SetupSignalSnapshot,
                SignalChangeEvent.current_snapshot_id == SetupSignalSnapshot.id,
            )
            .where(SignalChangeEvent.id == SignalAlertEvent.signal_change_event_id)
            .where(SetupSignalSnapshot.actionability_candidate == filters.actionability)
            .exists()
        )
        statement = statement.where(or_(lifecycle_match, signal_match))
    if filters.blocker:
        lifecycle_blocker = (
            select(SetupLifecycleEvent.id)
            .where(SetupLifecycleEvent.id == SignalAlertEvent.lifecycle_event_id)
            .where(SetupLifecycleEvent.evidence_json["blockers"].contains([filters.blocker]))
            .exists()
        )
        episode_blocker = (
            select(SetupLifecycleEpisode.id)
            .join(
                SetupLifecycleEvent,
                SetupLifecycleEvent.episode_id == SetupLifecycleEpisode.id,
            )
            .where(SetupLifecycleEvent.id == SignalAlertEvent.lifecycle_event_id)
            .where(SetupLifecycleEpisode.metadata_json["blockers"].contains([filters.blocker]))
            .exists()
        )
        statement = statement.where(
            or_(
                SignalAlertEvent.evidence_json["blockers"].contains([filters.blocker]),
                lifecycle_blocker,
                episode_blocker,
            )
        )
    return statement


def _apply_snapshot_filters(statement, filters: SetupLifecycleFilters):
    if filters.ticker:
        statement = statement.where(SetupSignalSnapshot.ticker == filters.ticker.strip().upper())
    if filters.run_id is not None:
        statement = statement.where(SetupSignalSnapshot.run_id == filters.run_id)
    if filters.sector:
        statement = statement.where(SetupSignalSnapshot.sector == filters.sector)
    if filters.setup_family:
        statement = statement.where(
            SetupSignalSnapshot.primary_setup_family == filters.setup_family
        )
    if filters.lifecycle_state:
        statement = statement.where(
            SetupSignalSnapshot.lifecycle_state_candidate == filters.lifecycle_state
        )
    if filters.actionability:
        statement = statement.where(
            SetupSignalSnapshot.actionability_candidate == filters.actionability
        )
    if filters.confidence_min is not None:
        statement = statement.where(SetupSignalSnapshot.confidence_score >= filters.confidence_min)
    if filters.confidence_max is not None:
        statement = statement.where(SetupSignalSnapshot.confidence_score <= filters.confidence_max)
    if filters.setup_score_min is not None:
        statement = statement.where(SetupSignalSnapshot.setup_score >= filters.setup_score_min)
    if filters.setup_score_max is not None:
        statement = statement.where(SetupSignalSnapshot.setup_score <= filters.setup_score_max)
    if filters.trigger_distance_min is not None:
        statement = statement.where(
            SetupSignalSnapshot.distance_to_pivot_pct >= filters.trigger_distance_min
        )
    if filters.trigger_distance_max is not None:
        statement = statement.where(
            SetupSignalSnapshot.distance_to_pivot_pct <= filters.trigger_distance_max
        )
    if filters.sector_rank_min is not None:
        statement = statement.where(_signal_int("sector_rank") >= filters.sector_rank_min)
    if filters.sector_rank_max is not None:
        statement = statement.where(_signal_int("sector_rank") <= filters.sector_rank_max)
    if filters.sector_rank_change_min is not None or filters.sector_rank_change_max is not None:
        statement = statement.where(False)
    if filters.market_regime:
        statement = statement.where(_signal_text("market_regime") == filters.market_regime)
    if filters.warning_flag:
        statement = statement.where(
            SetupSignalSnapshot.warning_flags_json.contains([filters.warning_flag])
        )
    if filters.blocker:
        blocker_episode = (
            select(SetupLifecycleEpisode.id)
            .where(SetupLifecycleEpisode.ticker == SetupSignalSnapshot.ticker)
            .where(SetupLifecycleEpisode.timeframe == SetupSignalSnapshot.timeframe)
            .where(SetupLifecycleEpisode.status == "ACTIVE")
            .where(SetupLifecycleEpisode.metadata_json["blockers"].contains([filters.blocker]))
            .exists()
        )
        statement = statement.where(blocker_episode)
    if filters.date_from is not None:
        statement = statement.where(SetupSignalSnapshot.data_as_of_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(SetupSignalSnapshot.data_as_of_date <= filters.date_to)
    if filters.velocity_min is not None:
        statement = statement.where(
            _snapshot_velocity(filters.velocity_window) >= filters.velocity_min
        )
    if filters.velocity_max is not None:
        statement = statement.where(
            _snapshot_velocity(filters.velocity_window) <= filters.velocity_max
        )
    return statement


def _sort_no_material_snapshots(statement, query: SetupLifecycleListQuery):
    sort_map = {
        "transition_priority": _state_priority(SetupSignalSnapshot.lifecycle_state_candidate),
        "confidence": SetupSignalSnapshot.confidence_score,
        "score": SetupSignalSnapshot.dual_score,
        "setup_score": SetupSignalSnapshot.setup_score,
        "velocity": _snapshot_velocity(3),
        "state_age": literal(None),
        "trigger_distance": SetupSignalSnapshot.distance_to_pivot_pct,
        "sector_rank": _signal_int("sector_rank"),
        "latest_event_time": SetupSignalSnapshot.data_as_of_date,
    }
    column = sort_map.get(query.sort)
    if column is None:
        raise SetupLifecycleQueryError("INVALID_SORT", f"unsupported sort: {query.sort}")
    order = column.asc() if query.direction == "asc" else column.desc()
    return statement.order_by(order.nullslast(), SetupSignalSnapshot.id.desc())


def _combined_change_page_rows(
    db: Session,
    *,
    lifecycle_statement,
    signal_statement,
    query: SetupLifecycleListQuery,
) -> tuple[list[tuple[str, int]], tuple[Any, date, int, int, int] | None]:
    lifecycle_primary, signal_primary = _combined_change_sort_columns(query.sort)
    cursor_key = _decode_change_cursor(query.cursor, query=query)
    lifecycle_severity = _severity_priority(SetupLifecycleEvent.severity)
    signal_severity = _severity_priority(SignalChangeEvent.severity)
    lifecycle_page = lifecycle_statement.with_only_columns(
        literal("LIFECYCLE_EVENT").label("source_type"),
        SetupLifecycleEvent.id.label("row_id"),
        lifecycle_primary.label("primary_sort"),
        SetupLifecycleEvent.effective_date.label("effective_date"),
        lifecycle_severity.label("severity_sort"),
        literal(1).label("source_sort"),
        maintain_column_froms=True,
    )
    signal_page = signal_statement.with_only_columns(
        literal("SIGNAL_CHANGE_EVENT").label("source_type"),
        SignalChangeEvent.id.label("row_id"),
        signal_primary.label("primary_sort"),
        SignalChangeEvent.effective_date.label("effective_date"),
        signal_severity.label("severity_sort"),
        literal(0).label("source_sort"),
        maintain_column_froms=True,
    )
    if cursor_key is not None:
        lifecycle_page = lifecycle_page.where(
            _combined_change_after_cursor(
                primary_column=lifecycle_primary,
                effective_date_column=SetupLifecycleEvent.effective_date,
                severity_column=lifecycle_severity,
                source_column=literal(1),
                id_column=SetupLifecycleEvent.id,
                cursor_key=cursor_key,
                direction=query.direction,
            )
        )
        signal_page = signal_page.where(
            _combined_change_after_cursor(
                primary_column=signal_primary,
                effective_date_column=SignalChangeEvent.effective_date,
                severity_column=signal_severity,
                source_column=literal(0),
                id_column=SignalChangeEvent.id,
                cursor_key=cursor_key,
                direction=query.direction,
            )
        )
    branch_limit = query.limit if cursor_key is not None else _offset(query.cursor) + query.limit
    lifecycle_order = (
        lifecycle_primary.asc() if query.direction == "asc" else lifecycle_primary.desc()
    )
    signal_order = signal_primary.asc() if query.direction == "asc" else signal_primary.desc()
    if query.sort != "latest_event_time":
        lifecycle_order = lifecycle_order.nullslast()
        signal_order = signal_order.nullslast()
    lifecycle_page = lifecycle_page.order_by(
        lifecycle_order,
        SetupLifecycleEvent.effective_date.desc(),
        lifecycle_severity.desc(),
        SetupLifecycleEvent.id.desc(),
    ).limit(branch_limit)
    signal_page = signal_page.order_by(
        signal_order,
        SignalChangeEvent.effective_date.desc(),
        signal_severity.desc(),
        SignalChangeEvent.id.desc(),
    ).limit(branch_limit)
    rows = [*db.execute(lifecycle_page).all(), *db.execute(signal_page).all()]
    rows.sort(key=cmp_to_key(lambda left, right: _compare_change_page_rows(left, right, query)))
    start = 0 if cursor_key is not None else _offset(query.cursor)
    rows = rows[start : start + query.limit]
    pairs = [(str(row.source_type), int(row.row_id)) for row in rows]
    last_key = (
        (
            rows[-1].primary_sort,
            rows[-1].effective_date,
            int(rows[-1].severity_sort),
            int(rows[-1].source_sort),
            int(rows[-1].row_id),
        )
        if rows
        else None
    )
    return pairs, last_key


def _compare_change_page_rows(left, right, query: SetupLifecycleListQuery) -> int:
    left_primary, right_primary = left.primary_sort, right.primary_sort
    if left_primary is None and right_primary is not None:
        return 1
    if right_primary is None and left_primary is not None:
        return -1
    if left_primary != right_primary:
        result = -1 if left_primary < right_primary else 1
        return result if query.direction == "asc" else -result
    left_tie = (
        left.effective_date,
        left.severity_sort,
        left.source_sort,
        left.row_id,
    )
    right_tie = (
        right.effective_date,
        right.severity_sort,
        right.source_sort,
        right.row_id,
    )
    return -1 if left_tie > right_tie else 1 if left_tie < right_tie else 0


def _combined_change_after_cursor(
    *,
    primary_column,
    effective_date_column,
    severity_column,
    source_column,
    id_column,
    cursor_key: dict[str, Any],
    direction: str,
):
    primary_value = cursor_key["primary"]
    tie_after = tuple_(
        effective_date_column,
        severity_column,
        source_column,
        id_column,
    ) < tuple_(
        cursor_key["effective_date"],
        cursor_key["severity"],
        cursor_key["source"],
        cursor_key["row_id"],
    )
    if primary_value is None:
        return and_(primary_column.is_(None), tie_after)
    primary_after = (
        primary_column > primary_value if direction == "asc" else primary_column < primary_value
    )
    return or_(
        primary_after,
        primary_column.is_(None),
        and_(primary_column == primary_value, tie_after),
    )


def _encode_change_cursor(
    query: SetupLifecycleListQuery,
    key: tuple[Any, date, int, int, int],
    *,
    next_offset: int,
    summary: dict[str, Any],
    lifecycle_total: int,
    signal_total: int,
) -> str:
    primary, effective_date, severity, source, row_id = key
    payload = {
        "v": 1,
        "offset": next_offset,
        "sort": query.sort,
        "direction": query.direction,
        "view_scope": query.view_scope.value,
        "filters_hash": _change_filters_hash(query.filters),
        "primary": str(primary) if primary is not None else None,
        "effective_date": effective_date.isoformat(),
        "severity": severity,
        "source": source,
        "row_id": row_id,
        "summary": summary,
        "lifecycle_total": lifecycle_total,
        "signal_total": signal_total,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "k1." + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _change_filters_hash(filters: SetupLifecycleFilters) -> str:
    return hashlib.sha256(repr(filters).encode("utf-8")).hexdigest()


def _decode_change_cursor(
    cursor: str | None, *, query: SetupLifecycleListQuery
) -> dict[str, Any] | None:
    if not cursor or not str(cursor).startswith("k1."):
        return None
    try:
        encoded = str(cursor)[3:]
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if (
            payload.get("v") != 1
            or payload.get("sort") != query.sort
            or payload.get("direction") != query.direction
            or payload.get("view_scope") != query.view_scope.value
            or payload.get("filters_hash") != _change_filters_hash(query.filters)
        ):
            raise ValueError
        primary = payload.get("primary")
        if primary is not None:
            if query.sort == "latest_event_time":
                primary = date.fromisoformat(str(primary))
            elif query.sort in {"score", "setup_score", "trigger_distance"}:
                primary = Decimal(str(primary))
            elif query.sort == "velocity":
                primary = float(primary)
            else:
                primary = int(primary)
        return {
            "offset": int(payload["offset"]),
            "primary": primary,
            "effective_date": date.fromisoformat(str(payload["effective_date"])),
            "severity": int(payload["severity"]),
            "source": int(payload["source"]),
            "row_id": int(payload["row_id"]),
            "summary": dict(payload["summary"]),
            "lifecycle_total": int(payload["lifecycle_total"]),
            "signal_total": int(payload["signal_total"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, binascii.Error) as exc:
        raise SetupLifecycleQueryError("INVALID_CURSOR", "changes cursor is invalid") from exc


def _combined_change_sort_columns(sort: str):
    lifecycle_state_priority = _state_priority(SetupLifecycleEvent.to_state)
    signal_state_priority = _state_priority(
        SetupSignalSnapshot.lifecycle_state_candidate,
        else_=_severity_priority(SignalChangeEvent.severity),
    )
    lifecycle_episode_age = (
        select(SetupLifecycleEpisode.state_age_sessions)
        .where(SetupLifecycleEpisode.id == SetupLifecycleEvent.episode_id)
        .scalar_subquery()
    )
    signal_episode_age = (
        select(SetupLifecycleEpisode.state_age_sessions)
        .where(SetupLifecycleEpisode.id == SignalChangeEvent.episode_id)
        .scalar_subquery()
    )
    columns = {
        "transition_priority": (lifecycle_state_priority, signal_state_priority),
        "confidence": (
            SetupLifecycleEvent.confidence_score,
            SetupSignalSnapshot.confidence_score,
        ),
        "score": (SetupSignalSnapshot.dual_score, SetupSignalSnapshot.dual_score),
        "setup_score": (SetupSignalSnapshot.setup_score, SetupSignalSnapshot.setup_score),
        "velocity": (
            _snapshot_velocity(3),
            _snapshot_velocity(3),
        ),
        "state_age": (lifecycle_episode_age, signal_episode_age),
        "trigger_distance": (
            SetupSignalSnapshot.distance_to_pivot_pct,
            SetupSignalSnapshot.distance_to_pivot_pct,
        ),
        "sector_rank": (_signal_int("sector_rank"), _signal_int("sector_rank")),
        "latest_event_time": (
            SetupLifecycleEvent.effective_date,
            SignalChangeEvent.effective_date,
        ),
    }
    selected = columns.get(sort)
    if selected is None:
        raise SetupLifecycleQueryError("INVALID_SORT", f"unsupported sort: {sort}")
    return selected


def _state_priority(column, *, else_=0):
    return case(
        (column == "FAILED", 9),
        (column == "EXTENDED", 8),
        (column == "CONFIRMED", 7),
        (column == "TRIGGERED", 6),
        (column == "READY", 5),
        (column == "TIGHTENING", 4),
        (column == "DEVELOPING", 3),
        (column == "DISCOVERED", 2),
        (column == "EXPIRED", 1),
        else_=else_,
    )


def _sort_events(statement, sort: str, direction: str):
    sort_map = {
        "transition_priority": _state_priority(SetupLifecycleEvent.to_state),
        "confidence": SetupLifecycleEvent.confidence_score,
        "score": SetupSignalSnapshot.dual_score,
        "setup_score": SetupSignalSnapshot.setup_score,
        "velocity": _snapshot_velocity(3),
        "state_age": SetupLifecycleEvent.state_age_before,
        "trigger_distance": SetupSignalSnapshot.distance_to_pivot_pct,
        "sector_rank": _signal_int("sector_rank"),
        "latest_event_time": SetupLifecycleEvent.effective_date,
    }
    column = sort_map.get(sort)
    if column is None:
        raise SetupLifecycleQueryError("INVALID_SORT", f"unsupported sort: {sort}")
    order = column.asc() if direction == "asc" else column.desc()
    return statement.order_by(order, SetupLifecycleEvent.id.desc())


def _sort_alerts(statement, sort: str, direction: str):
    sort_map = {
        "latest_event_time": SignalAlertEvent.effective_date,
        "severity": _severity_priority(),
        "alert_type": SignalAlertRule.rule_id,
    }
    column = sort_map.get(sort)
    if column is None:
        raise SetupLifecycleQueryError("INVALID_SORT", f"unsupported sort: {sort}")
    order = column.asc() if direction == "asc" else column.desc()
    return statement.order_by(
        order,
        _severity_priority().desc(),
        SignalAlertEvent.effective_date.desc(),
        _alert_type_priority().desc(),
        SignalAlertEvent.source_event_key.desc(),
        SignalAlertEvent.id.desc(),
    )


def _sort_signal_changes(statement, sort: str, direction: str):
    sort_map = {
        "transition_priority": _severity_priority(SignalChangeEvent.severity),
        "confidence": SetupSignalSnapshot.confidence_score,
        "score": SetupSignalSnapshot.dual_score,
        "setup_score": SetupSignalSnapshot.setup_score,
        "velocity": _change_velocity(3),
        "state_age": SignalChangeEvent.effective_date,
        "trigger_distance": SetupSignalSnapshot.distance_to_pivot_pct,
        "sector_rank": _signal_int("sector_rank"),
        "latest_event_time": SignalChangeEvent.effective_date,
    }
    column = sort_map.get(sort)
    if column is None:
        raise SetupLifecycleQueryError("INVALID_SORT", f"unsupported sort: {sort}")
    order = column.asc() if direction == "asc" else column.desc()
    return statement.order_by(order, SignalChangeEvent.id.desc())


def _page(
    *,
    items: list[dict[str, Any]],
    total: int,
    query: SetupLifecycleListQuery,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start = _offset(query.cursor)
    next_offset = start + len(items)
    return {
        "items": items,
        "total": total,
        "page_item_count": len(items),
        "limit": query.limit,
        "cursor": query.cursor,
        "next_cursor": str(next_offset) if next_offset < total else None,
        "sort": query.sort,
        "direction": query.direction,
        "view_scope": query.view_scope.value,
        "selected_date": _date_or_none(query.filters.as_of_date),
        "summary": dict(summary or {}),
    }


def _offset(cursor: str | None) -> int:
    if cursor in {None, ""}:
        return 0
    try:
        if str(cursor).startswith("k1."):
            encoded = str(cursor)[3:]
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            value = int(json.loads(raw.decode("utf-8"))["offset"])
        else:
            value = int(cursor)
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
    ) as exc:
        raise SetupLifecycleQueryError("INVALID_CURSOR", "cursor must be an integer") from exc
    if value < 0:
        raise SetupLifecycleQueryError("INVALID_CURSOR", "cursor must be non-negative")
    return value


def _encode_timeline_cursor(effective_date: date, kind_priority: int, row_id: int) -> str:
    raw = json.dumps(
        [effective_date.isoformat(), kind_priority, row_id],
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _timeline_cursor_key(cursor: str | None) -> tuple[date, int, int] | None:
    if cursor in {None, ""}:
        return None
    try:
        padded = str(cursor) + "=" * (-len(str(cursor)) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError
        cursor_date = date.fromisoformat(str(values[0]))
        kind_priority = int(values[1])
        row_id = int(values[2])
        if kind_priority not in {1, 2, 3} or row_id < 1:
            raise ValueError
        return cursor_date, kind_priority, row_id
    except (
        ValueError,
        TypeError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
    ) as exc:
        raise SetupLifecycleQueryError("INVALID_CURSOR", "timeline cursor is invalid") from exc


def _timeline_after_cursor(
    statement,
    *,
    date_column,
    id_column,
    kind_priority: int,
    cursor_key: tuple[date, int, int] | None,
):
    if cursor_key is None:
        return statement
    cursor_date, cursor_kind, cursor_id = cursor_key
    same_day = date_column == cursor_date
    if kind_priority < cursor_kind:
        same_day_after = same_day
    elif kind_priority == cursor_kind:
        same_day_after = same_day & (id_column < cursor_id)
    else:
        same_day_after = False
    return statement.where(or_(date_column < cursor_date, same_day_after))


def _count(db: Session, statement) -> int:
    return int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def _signal_text(key: str):
    return SetupSignalSnapshot.signals_json[key]["value"].as_string()


def _signal_int(key: str):
    return SetupSignalSnapshot.signals_json[key]["value"].as_integer()


def _event_float(key: str):
    return SetupLifecycleEvent.evidence_json[key].as_float()


def _event_velocity(window: int):
    return _snapshot_velocity(window)


def _change_velocity(window: int):
    return _snapshot_velocity(window)


def _snapshot_velocity(window: int, signal_key: str = "technical_score"):
    return SetupSignalSnapshot.signals_json[signal_key]["velocity"][str(window)][
        "normalized_delta"
    ].as_float()


def _severity_priority(column=None):
    column = column if column is not None else SignalAlertEvent.severity
    return case(
        (column == "RISK", 4),
        (column == "ACTIONABLE", 3),
        (column == "NOTABLE", 2),
        (column == "INFO", 1),
        else_=0,
    )


def _alert_type_priority():
    return case(
        (SignalAlertRule.rule_id == "NEW_FAILURE", 9),
        (SignalAlertRule.rule_id == "GATE_BLOCKED", 8),
        (SignalAlertRule.rule_id == "NEW_EXTENSION", 7),
        (SignalAlertRule.rule_id == "NEW_TRIGGER", 6),
        (SignalAlertRule.rule_id == "NEW_CONFIRMATION", 5),
        (SignalAlertRule.rule_id == "NEW_READY", 4),
        (SignalAlertRule.rule_id == "DATA_DEGRADED", 3),
        (SignalAlertRule.rule_id == "SCORE_ACCELERATION", 2),
        (SignalAlertRule.rule_id == "SECTOR_ACCELERATION", 1),
        else_=0,
    )


def _job_count(db: Session, statuses: tuple[str, ...]) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.status.in_(statuses))
        )
        or 0
    )


def _stale_lease_count(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.status == JobStatus.RUNNING)
            .where(BackgroundJob.lease_expires_at.is_not(None))
            .where(BackgroundJob.lease_expires_at < datetime.now(UTC))
        )
        or 0
    )


def _low_confidence_share(db: Session) -> float:
    total = int(db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) or 0)
    if total == 0:
        return 0.0
    low = int(
        db.scalar(
            select(func.count())
            .select_from(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.data_quality_label.in_(("LOW", "INSUFFICIENT")))
        )
        or 0
    )
    return round(low / total, 6)


def _distinct(db: Session, column) -> list[Any]:
    statement = select(column).where(column.is_not(None)).distinct().order_by(column)
    return [value for value in db.scalars(statement) if value is not None]


def _source_links(snapshots: list[SetupSignalSnapshot]) -> dict[str, str | None]:
    if not snapshots:
        return {}
    snapshot = snapshots[0]
    return {
        "source_run": f"/runs/{snapshot.run_id}" if snapshot.run_id else None,
        "technical_score_card": f"/runs/{snapshot.run_id}#ticker-{snapshot.ticker}"
        if snapshot.run_id
        else None,
        "market_regime": f"/runs/{snapshot.run_id}/market-regime"
        if snapshot.run_id and snapshot.market_regime_snapshot_id
        else "/market-regime"
        if snapshot.market_regime_snapshot_id
        else None,
        "sector_rotation": f"/runs/{snapshot.run_id}/sector-rotation"
        if snapshot.run_id and snapshot.sector_rotation_snapshot_id
        else None,
        "owpe": f"/winner-probability/tickers/{snapshot.ticker}",
    }


def _date_or_none(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _difference(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 8)


def _rank_improvement(previous: Any, current: Any) -> int | None:
    previous_rank = _int_or_none(previous)
    current_rank = _int_or_none(current)
    if previous_rank is None or current_rank is None:
        return None
    return previous_rank - current_rank


def _snapshot_signal(snapshot: SetupSignalSnapshot | None, key: str) -> Any:
    if snapshot is None:
        return None
    raw = (snapshot.signals_json or {}).get(key)
    if isinstance(raw, dict) and "value" in raw:
        return raw.get("value")
    return raw


def _velocity_value(velocities: dict[str, Any], window: int) -> float | None:
    row = velocities.get(str(window)) or {}
    return _number_or_none(row.get("normalized_delta"))


def _snapshot_velocity_map(
    snapshot: SetupSignalSnapshot | None,
    signal_key: str,
) -> dict[str, Any]:
    if snapshot is None:
        return {}
    signal = (snapshot.signals_json or {}).get(signal_key) or {}
    velocity = signal.get("velocity") if isinstance(signal, dict) else None
    return dict(velocity) if isinstance(velocity, dict) else {}


def _missing_session_gap(
    previous: SetupSignalSnapshot | None,
    current: SetupSignalSnapshot | None,
) -> int | None:
    if previous is None or current is None:
        return None
    sessions = 0
    cursor = previous.data_as_of_date
    while True:
        cursor = next_us_trading_day(cursor)
        if cursor >= current.data_as_of_date:
            break
        sessions += 1
    return sessions


def _alert_source_type(rule, lifecycle, change, evidence: dict[str, Any]) -> str:
    rule_id = getattr(rule, "rule_id", None) or evidence.get("rule_id")
    scope = getattr(rule, "scope", None) or evidence.get("source")
    if rule_id == "DATA_DEGRADED":
        return "DATA_QUALITY_CHANGE"
    if scope == "actionability_change" or rule_id == "GATE_BLOCKED":
        return "ACTIONABILITY_CHANGE"
    if lifecycle is not None:
        return "LIFECYCLE_EVENT"
    if change is not None:
        return "SIGNAL_CHANGE_EVENT"
    return str(scope or "UNKNOWN").upper()


def _cached_changes_summary(
    db: Session,
    lifecycle_statement,
    signal_statement,
    *,
    filters: SetupLifecycleFilters,
    view_scope: SetupLifecycleViewScope = SetupLifecycleViewScope.CURRENT_MARKET,
) -> tuple[dict[str, Any], int, int]:
    lifecycle_revision, signal_revision = db.execute(
        select(
            select(func.max(SetupLifecycleEvent.id)).scalar_subquery(),
            select(func.max(SignalChangeEvent.id)).scalar_subquery(),
        )
    ).one()
    bind = db.get_bind()
    database_identity = str(getattr(getattr(bind, "url", None), "database", "unknown"))
    cache_key = (
        database_identity,
        int(lifecycle_revision or 0),
        int(signal_revision or 0),
        filters,
        view_scope,
    )
    with _CHANGE_SUMMARY_CACHE_LOCK:
        cached = _CHANGE_SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        summary, lifecycle_total, signal_total = cached
        return dict(summary), lifecycle_total, signal_total
    result = _changes_summary(db, lifecycle_statement, signal_statement)
    summary, lifecycle_total, signal_total = result
    with _CHANGE_SUMMARY_CACHE_LOCK:
        if len(_CHANGE_SUMMARY_CACHE) >= _CHANGE_SUMMARY_CACHE_MAX_ENTRIES:
            _CHANGE_SUMMARY_CACHE.pop(next(iter(_CHANGE_SUMMARY_CACHE)))
        _CHANGE_SUMMARY_CACHE[cache_key] = (
            dict(summary),
            lifecycle_total,
            signal_total,
        )
    return result


def _changes_summary(
    db: Session, lifecycle_statement, signal_statement
) -> tuple[dict[str, Any], int, int]:
    lifecycle = lifecycle_statement.with_only_columns(
        SetupLifecycleEvent.to_state,
        SetupLifecycleEvent.severity,
        SetupLifecycleEvent.confidence_score,
    ).subquery()
    signal = signal_statement.with_only_columns(
        SignalChangeEvent.severity,
        SetupSignalSnapshot.confidence_score.label("snapshot_confidence_score"),
    ).subquery()
    lifecycle_metrics = db.execute(
        select(
            func.count().label("total"),
            *[
                func.count().filter(lifecycle.c.to_state == state).label(state.lower())
                for state in (
                    "DISCOVERED",
                    "TIGHTENING",
                    "READY",
                    "TRIGGERED",
                    "CONFIRMED",
                    "EXTENDED",
                    "FAILED",
                )
            ],
            func.count().filter(lifecycle.c.severity == "RISK").label("risk"),
            func.count().filter(lifecycle.c.confidence_score < 70).label("low_confidence"),
        ).select_from(lifecycle)
    ).one()
    signal_metrics = db.execute(
        select(
            func.count().label("total"),
            func.count().filter(signal.c.severity == "RISK").label("risk"),
            func.count().filter(signal.c.snapshot_confidence_score < 70).label("low_confidence"),
        ).select_from(signal)
    ).one()
    lifecycle_total = int(lifecycle_metrics.total or 0)
    signal_total = int(signal_metrics.total or 0)
    low_confidence = int(lifecycle_metrics.low_confidence or 0) + int(
        signal_metrics.low_confidence or 0
    )
    total = lifecycle_total + signal_total
    return (
        {
            "newly_discovered": int(lifecycle_metrics.discovered or 0),
            "tightening": int(lifecycle_metrics.tightening or 0),
            "newly_ready": int(lifecycle_metrics.ready or 0),
            "newly_triggered": int(lifecycle_metrics.triggered or 0),
            "confirmed": int(lifecycle_metrics.confirmed or 0),
            "extended": int(lifecycle_metrics.extended or 0),
            "failed": int(lifecycle_metrics.failed or 0),
            "material_changes": signal_total,
            "major_risk_changes": int(lifecycle_metrics.risk or 0) + int(signal_metrics.risk or 0),
            "low_confidence_count": low_confidence,
            "low_confidence_share": round(low_confidence / total, 6) if total else 0.0,
        },
        lifecycle_total,
        signal_total,
    )


def _alerts_summary(db: Session, statement) -> dict[str, int]:
    alerts = statement.subquery()
    status_counts = {
        status: int(count)
        for status, count in db.execute(
            select(alerts.c.status, func.count()).group_by(alerts.c.status)
        ).all()
    }
    severity_counts = {
        severity: int(count)
        for severity, count in db.execute(
            select(alerts.c.severity, func.count()).group_by(alerts.c.severity)
        ).all()
    }
    return {
        "unread": status_counts.get("UNREAD", 0),
        "acknowledged": status_counts.get("ACKNOWLEDGED", 0),
        "dismissed": status_counts.get("DISMISSED", 0),
        "info": severity_counts.get("INFO", 0),
        "notable": severity_counts.get("NOTABLE", 0),
        "actionable": severity_counts.get("ACTIONABLE", 0),
        "risk": severity_counts.get("RISK", 0),
    }


def _sort_market_change_items(
    items: list[dict[str, Any]],
    sort: str,
    direction: str,
) -> list[dict[str, Any]]:
    field_map = {
        "transition_priority": "transition_priority",
        "confidence": "confidence",
        "score": "technical_score",
        "setup_score": "setup_score",
        "velocity": "score_velocity_3d",
        "state_age": "state_age_sessions",
        "trigger_distance": "trigger_distance_pct",
        "sector_rank": "sector_rank",
        "latest_event_time": "effective_date",
    }
    field = field_map[sort]

    def primary(item: dict[str, Any]):
        if field == "transition_priority":
            return {
                "FAILED": 9,
                "EXTENDED": 8,
                "CONFIRMED": 7,
                "TRIGGERED": 6,
                "READY": 5,
                "TIGHTENING": 4,
                "DEVELOPING": 3,
                "DISCOVERED": 2,
                "EXPIRED": 1,
            }.get(item.get("current_state"), _severity_value(item.get("severity")))
        return item.get(field)

    def compare(left: dict[str, Any], right: dict[str, Any]) -> int:
        left_value, right_value = primary(left), primary(right)
        if left_value is None and right_value is not None:
            return 1
        if right_value is None and left_value is not None:
            return -1
        if left_value != right_value:
            result = -1 if left_value < right_value else 1
            return result if direction == "asc" else -result
        left_tie = (
            left.get("effective_date") or "",
            _severity_value(left.get("severity")),
            1 if left.get("source_type") == "LIFECYCLE_EVENT" else 0,
            left.get("id") or 0,
        )
        right_tie = (
            right.get("effective_date") or "",
            _severity_value(right.get("severity")),
            1 if right.get("source_type") == "LIFECYCLE_EVENT" else 0,
            right.get("id") or 0,
        )
        return -1 if left_tie > right_tie else 1 if left_tie < right_tie else 0

    return sorted(items, key=cmp_to_key(compare))


def _severity_value(value: Any) -> int:
    return {"RISK": 4, "ACTIONABLE": 3, "NOTABLE": 2, "INFO": 1}.get(str(value or ""), 0)
