from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import cmp_to_key
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

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
    velocity_min: float | None = None
    velocity_max: float | None = None
    market_regime: str | None = None
    warning_flag: str | None = None
    alert_status: str | None = None
    alert_severity: str | None = None
    as_of_date: date | None = None
    source_type: str | None = None
    alert_type: str | None = None


@dataclass(frozen=True)
class SetupLifecycleListQuery:
    filters: SetupLifecycleFilters
    sort: str = "latest_event_time"
    direction: str = "desc"
    limit: int = 50
    cursor: str | None = None


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
        lifecycle_statement = select(SetupLifecycleEvent).outerjoin(
            SetupSignalSnapshot,
            SetupLifecycleEvent.snapshot_id == SetupSignalSnapshot.id,
        ).where(
            SetupLifecycleEvent.is_current_version.is_(True),
            SetupLifecycleEvent.event_type.in_(
                ("EPISODE_OPENED", "STATE_TRANSITION", "PHASE_TRANSITION")
            ),
        )
        lifecycle_statement = _apply_event_filters(lifecycle_statement, query.filters)
        signal_statement = select(SignalChangeEvent).join(
            SetupSignalSnapshot,
            SignalChangeEvent.current_snapshot_id == SetupSignalSnapshot.id,
        ).where(SetupSignalSnapshot.is_canonical.is_(True))
        signal_statement = _apply_signal_change_filters(signal_statement, query.filters)

        lifecycle_total = _count(db, lifecycle_statement)
        signal_total = _count(db, signal_statement)
        window = _offset(query.cursor) + query.limit
        lifecycle_rows = list(
            db.scalars(
                _sort_events(lifecycle_statement, query.sort, query.direction).limit(window)
            )
        )
        signal_rows = list(
            db.scalars(
                _sort_signal_changes(signal_statement, query.sort, query.direction).limit(window)
            )
        )
        items = [
            *[market_change_payload(db, lifecycle_event=row) for row in lifecycle_rows],
            *[market_change_payload(db, signal_change_event=row) for row in signal_rows],
        ]
        items = _sort_market_change_items(items, query.sort, query.direction)
        start = _offset(query.cursor)
        return _page(
            items=items[start : start + query.limit],
            total=lifecycle_total + signal_total,
            query=query,
            summary=_changes_summary(db, lifecycle_statement, signal_statement),
        )

    def _no_material_changes(
        self, db: Session, query: SetupLifecycleListQuery
    ) -> dict[str, Any]:
        if query.filters.as_of_date is None:
            raise SetupLifecycleQueryError(
                "INVALID_DATE", "NO_MATERIAL_CHANGE requires an as-of date"
            )
        has_lifecycle_event = (
            select(SetupLifecycleEvent.id)
            .where(SetupLifecycleEvent.snapshot_id == SetupSignalSnapshot.id)
            .where(SetupLifecycleEvent.is_current_version.is_(True))
            .where(
                SetupLifecycleEvent.event_type.in_(
                    ("EPISODE_OPENED", "STATE_TRANSITION", "PHASE_TRANSITION")
                )
            )
            .exists()
        )
        has_signal_change = (
            select(SignalChangeEvent.id)
            .where(SignalChangeEvent.current_snapshot_id == SetupSignalSnapshot.id)
            .exists()
        )
        statement = select(SetupSignalSnapshot).where(
            SetupSignalSnapshot.is_canonical.is_(True),
            SetupSignalSnapshot.data_as_of_date == query.filters.as_of_date,
            ~has_lifecycle_event,
            ~has_signal_change,
        )
        statement = _apply_snapshot_filters(statement, query.filters)
        total = _count(db, statement)
        low_confidence = _count(
            db, statement.where(SetupSignalSnapshot.confidence_score < 70)
        )
        rows = list(db.scalars(statement.order_by(SetupSignalSnapshot.id.desc())))
        items = _sort_market_change_items(
            [no_material_change_payload(db, row) for row in rows],
            query.sort,
            query.direction,
        )
        start = _offset(query.cursor)
        return _page(
            items=items[start : start + query.limit],
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
        return _page(
            items=[alert_payload(row, db=db) for row in rows],
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
    ) -> dict[str, Any]:
        normalized = self.repository.normalize_ticker(ticker)
        snapshots = list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(SetupSignalSnapshot.ticker == normalized)
                .where(SetupSignalSnapshot.timeframe == timeframe)
                .order_by(SetupSignalSnapshot.data_as_of_date.desc(), SetupSignalSnapshot.id.desc())
                .limit(max(1, min(limit, 500)))
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
        lifecycle_events = list(
            db.scalars(
                select(SetupLifecycleEvent)
                .where(SetupLifecycleEvent.ticker == normalized)
                .where(SetupLifecycleEvent.timeframe == timeframe)
                .order_by(SetupLifecycleEvent.effective_date.desc(), SetupLifecycleEvent.id.desc())
                .limit(max(1, min(limit, 500)))
            )
        )
        signal_changes = list(
            db.scalars(
                select(SignalChangeEvent)
                .where(SignalChangeEvent.ticker == normalized)
                .where(SignalChangeEvent.timeframe == timeframe)
                .order_by(SignalChangeEvent.effective_date.desc(), SignalChangeEvent.id.desc())
                .limit(max(1, min(limit, 500)))
            )
        )
        alerts = list(
            db.scalars(
                select(SignalAlertEvent)
                .where(SignalAlertEvent.ticker == normalized)
                .where(SignalAlertEvent.timeframe == timeframe)
                .order_by(SignalAlertEvent.effective_date.desc(), SignalAlertEvent.id.desc())
                .limit(max(1, min(limit, 500)))
            )
        )
        return {
            "ticker": normalized,
            "timeframe": timeframe,
            "snapshots": [snapshot_payload(row) for row in snapshots],
            "episodes": [episode_payload(row) for row in episodes],
            "lifecycle_events": [lifecycle_event_payload(row) for row in lifecycle_events],
            "signal_changes": [signal_change_payload(row) for row in signal_changes],
            "alerts": [alert_payload(row) for row in alerts],
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
        "reason_codes": list(event.reason_codes_json or []),
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
        "reason_codes": list(event.reason_codes_json or []),
        "evidence": dict(event.evidence_json or {}),
    }


def market_change_payload(
    db: Session,
    *,
    lifecycle_event: SetupLifecycleEvent | None = None,
    signal_change_event: SignalChangeEvent | None = None,
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
        signal_change_event.previous_snapshot_id
        if signal_change_event is not None
        else None
    )
    current = db.get(SetupSignalSnapshot, current_snapshot_id) if current_snapshot_id else None
    previous = db.get(SetupSignalSnapshot, previous_snapshot_id) if previous_snapshot_id else None
    if previous is None and current is not None:
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
    if related_lifecycle is None and current_snapshot_id is not None:
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
        episode = db.get(SetupLifecycleEpisode, episode_id)
    evidence = dict(event.evidence_json or {})
    reason_codes = list(event.reason_codes_json or [])
    velocities = evidence.get("velocity") or {}
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
        "score_velocity_1d": _velocity_value(velocities, 1),
        "score_velocity_3d": _velocity_value(velocities, 3),
        "score_velocity_5d": _velocity_value(velocities, 5),
        "score_velocity_10d": _velocity_value(velocities, 10),
        "trigger_distance_pct": _number_or_none(
            getattr(current, "distance_to_pivot_pct", None)
        ),
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
        "signal_key": signal_key,
        "old_value": dict(signal_change_event.old_value_json or {})
        if signal_change_event is not None
        else None,
        "new_value": dict(signal_change_event.new_value_json or {})
        if signal_change_event is not None
        else None,
        "normalized_delta": _number_or_none(
            getattr(signal_change_event, "normalized_delta", None)
        ),
        "snapshot_id": current_snapshot_id,
        "previous_snapshot_id": getattr(previous, "id", None),
        "source_run_id": getattr(current, "run_id", None),
        "engine_version": getattr(current, "engine_version", None),
        "config_version": getattr(current, "config_version", None),
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


def alert_payload(alert: SignalAlertEvent, *, db: Session | None = None) -> dict[str, Any]:
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
    change_snapshot = (
        db.get(SetupSignalSnapshot, change.current_snapshot_id)
        if db is not None and change is not None and change.current_snapshot_id
        else None
    )
    evidence = dict(alert.evidence_json or {})
    source_type = _alert_source_type(rule, lifecycle, change, evidence)
    episode_id = getattr(lifecycle, "episode_id", None) or getattr(change, "episode_id", None)
    blockers = list(evidence.get("blockers") or ())
    confidence = getattr(lifecycle, "confidence_score", None)
    if confidence is None:
        confidence = getattr(change_snapshot, "confidence_score", None)
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
        or getattr(change_snapshot, "lifecycle_state_candidate", None),
        "actionability": getattr(lifecycle, "actionability_after", None)
        or getattr(change_snapshot, "actionability_candidate", None)
        or evidence.get("actionability_after"),
        "confidence": _int_or_none(confidence),
        "confidence_label": getattr(lifecycle, "confidence_label", None)
        or getattr(change_snapshot, "confidence_label", None)
        or evidence.get("confidence_label"),
        "blockers": blockers,
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


def no_material_change_payload(
    db: Session, snapshot: SetupSignalSnapshot
) -> dict[str, Any]:
    previous = db.scalar(
        select(SetupSignalSnapshot)
        .where(SetupSignalSnapshot.ticker == snapshot.ticker)
        .where(SetupSignalSnapshot.timeframe == snapshot.timeframe)
        .where(SetupSignalSnapshot.is_canonical.is_(True))
        .where(SetupSignalSnapshot.data_as_of_date < snapshot.data_as_of_date)
        .order_by(SetupSignalSnapshot.data_as_of_date.desc(), SetupSignalSnapshot.id.desc())
        .limit(1)
    )
    episode = db.scalar(
        select(SetupLifecycleEpisode)
        .where(SetupLifecycleEpisode.ticker == snapshot.ticker)
        .where(SetupLifecycleEpisode.timeframe == snapshot.timeframe)
        .where(SetupLifecycleEpisode.status == "ACTIVE")
        .order_by(SetupLifecycleEpisode.is_primary.desc(), SetupLifecycleEpisode.id.desc())
        .limit(1)
    )
    score = _number_or_none(snapshot.dual_score)
    previous_score = _number_or_none(getattr(previous, "dual_score", None))
    sector_rank = _snapshot_signal(snapshot, "sector_rank")
    previous_rank = _snapshot_signal(previous, "sector_rank")
    blockers = list((getattr(episode, "metadata_json", None) or {}).get("blockers") or ())
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
        "score_velocity_1d": None,
        "score_velocity_3d": None,
        "score_velocity_5d": None,
        "score_velocity_10d": None,
        "trigger_distance_pct": _number_or_none(snapshot.distance_to_pivot_pct),
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
        "engine_version": snapshot.engine_version,
        "config_version": snapshot.config_version,
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
        "output_evaluation_version": run.output_evaluation_version,
        "date_from": _date_or_none(run.date_from),
        "date_to": _date_or_none(run.date_to),
        "dry_run": run.dry_run,
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
    _validate_enum("setup_family", filters.setup_family, {item.value for item in SetupFamily})
    _validate_enum(
        "lifecycle_state", filters.lifecycle_state, {item.value for item in LifecycleState}
    )
    _validate_enum(
        "actionability", filters.actionability, {item.value for item in Actionability}
    )
    _validate_enum(
        "alert_status", filters.alert_status, {"UNREAD", "ACKNOWLEDGED", "DISMISSED"}
    )
    _validate_enum(
        "alert_severity", filters.alert_severity, {item.value for item in EventSeverity}
    )
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
        statement = statement.where(_event_float("velocity") >= filters.velocity_min)
    if filters.velocity_max is not None:
        statement = statement.where(_event_float("velocity") <= filters.velocity_max)
    if filters.market_regime:
        statement = statement.where(_signal_text("market_regime") == filters.market_regime)
    if filters.warning_flag:
        statement = statement.where(
            or_(
                SetupLifecycleEvent.warning_flags_json.contains([filters.warning_flag]),
                SetupSignalSnapshot.warning_flags_json.contains([filters.warning_flag]),
            )
        )
    if filters.as_of_date is not None:
        statement = statement.where(SetupLifecycleEvent.effective_date == filters.as_of_date)
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
    if filters.velocity_min is not None:
        statement = statement.where(_change_velocity(3) >= filters.velocity_min)
    if filters.velocity_max is not None:
        statement = statement.where(_change_velocity(3) <= filters.velocity_max)
    if filters.market_regime:
        statement = statement.where(_signal_text("market_regime") == filters.market_regime)
    if filters.warning_flag:
        statement = statement.where(
            SetupSignalSnapshot.warning_flags_json.contains([filters.warning_flag])
        )
    if filters.as_of_date is not None:
        statement = statement.where(SignalChangeEvent.effective_date == filters.as_of_date)
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
            .where(
                SetupSignalSnapshot.lifecycle_state_candidate == filters.lifecycle_state
            )
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
    return statement


def _apply_snapshot_filters(statement, filters: SetupLifecycleFilters):
    if filters.ticker:
        statement = statement.where(
            SetupSignalSnapshot.ticker == filters.ticker.strip().upper()
        )
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
        statement = statement.where(
            SetupSignalSnapshot.confidence_score >= filters.confidence_min
        )
    if filters.confidence_max is not None:
        statement = statement.where(
            SetupSignalSnapshot.confidence_score <= filters.confidence_max
        )
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
    if filters.market_regime:
        statement = statement.where(_signal_text("market_regime") == filters.market_regime)
    if filters.warning_flag:
        statement = statement.where(
            SetupSignalSnapshot.warning_flags_json.contains([filters.warning_flag])
        )
    if filters.velocity_min is not None or filters.velocity_max is not None:
        statement = statement.where(False)
    return statement


def _sort_events(statement, sort: str, direction: str):
    sort_map = {
        "transition_priority": case(
            (SetupLifecycleEvent.to_state == "FAILED", 9),
            (SetupLifecycleEvent.to_state == "EXTENDED", 8),
            (SetupLifecycleEvent.to_state == "CONFIRMED", 7),
            (SetupLifecycleEvent.to_state == "TRIGGERED", 6),
            (SetupLifecycleEvent.to_state == "READY", 5),
            (SetupLifecycleEvent.to_state == "TIGHTENING", 4),
            (SetupLifecycleEvent.to_state == "DEVELOPING", 3),
            (SetupLifecycleEvent.to_state == "DISCOVERED", 2),
            (SetupLifecycleEvent.to_state == "EXPIRED", 1),
            else_=0,
        ),
        "confidence": SetupLifecycleEvent.confidence_score,
        "score": SetupSignalSnapshot.dual_score,
        "setup_score": SetupSignalSnapshot.setup_score,
        "velocity": SetupLifecycleEvent.evidence_json["velocity"]["3"][
            "normalized_delta"
        ].as_float(),
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
        "selected_date": _date_or_none(query.filters.as_of_date),
        "summary": dict(summary or {}),
    }


def _offset(cursor: str | None) -> int:
    if cursor in {None, ""}:
        return 0
    try:
        value = int(cursor)
    except (TypeError, ValueError) as exc:
        raise SetupLifecycleQueryError("INVALID_CURSOR", "cursor must be an integer") from exc
    if value < 0:
        raise SetupLifecycleQueryError("INVALID_CURSOR", "cursor must be non-negative")
    return value


def _count(db: Session, statement) -> int:
    return int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def _signal_text(key: str):
    return SetupSignalSnapshot.signals_json[key]["value"].as_string()


def _signal_int(key: str):
    return SetupSignalSnapshot.signals_json[key]["value"].as_integer()


def _event_float(key: str):
    return SetupLifecycleEvent.evidence_json[key].as_float()


def _change_velocity(window: int):
    return SignalChangeEvent.evidence_json["velocity"][str(window)][
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
        db.scalar(select(func.count()).select_from(BackgroundJob).where(BackgroundJob.status.in_(statuses)))
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


def _changes_summary(db: Session, lifecycle_statement, signal_statement) -> dict[str, Any]:
    lifecycle = lifecycle_statement.subquery()
    signal = signal_statement.subquery()
    state_counts = {
        state: int(count)
        for state, count in db.execute(
            select(lifecycle.c.to_state, func.count())
            .group_by(lifecycle.c.to_state)
        ).all()
    }
    lifecycle_risk = int(
        db.scalar(
            select(func.count()).select_from(lifecycle).where(lifecycle.c.severity == "RISK")
        )
        or 0
    )
    signal_risk = int(
        db.scalar(select(func.count()).select_from(signal).where(signal.c.severity == "RISK"))
        or 0
    )
    material_changes = int(db.scalar(select(func.count()).select_from(signal)) or 0)
    lifecycle_total = _count(db, lifecycle_statement)
    signal_total = _count(db, signal_statement)
    low_confidence = _count(
        db, lifecycle_statement.where(SetupLifecycleEvent.confidence_score < 70)
    ) + _count(db, signal_statement.where(SetupSignalSnapshot.confidence_score < 70))
    total = lifecycle_total + signal_total
    return {
        "newly_discovered": state_counts.get("DISCOVERED", 0),
        "tightening": state_counts.get("TIGHTENING", 0),
        "newly_ready": state_counts.get("READY", 0),
        "newly_triggered": state_counts.get("TRIGGERED", 0),
        "confirmed": state_counts.get("CONFIRMED", 0),
        "extended": state_counts.get("EXTENDED", 0),
        "failed": state_counts.get("FAILED", 0),
        "material_changes": material_changes,
        "major_risk_changes": lifecycle_risk + signal_risk,
        "low_confidence_count": low_confidence,
        "low_confidence_share": round(low_confidence / total, 6) if total else 0.0,
    }


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
    return {"RISK": 4, "ACTIONABLE": 3, "NOTABLE": 2, "INFO": 1}.get(
        str(value or ""), 0
    )
