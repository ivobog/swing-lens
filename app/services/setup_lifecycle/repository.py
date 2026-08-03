from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, delete, func, select, tuple_, update
from sqlalchemy.orm import Session

from app.models.tables import (
    SetupLifecycleAdministrativeAuditEvent,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalAlertRule,
    SignalChangeEvent,
)
from app.services.setup_lifecycle.config import (
    SetupLifecycleConfig,
    load_setup_lifecycle_config,
)


@dataclass(frozen=True)
class SetupSignalSnapshotWrite:
    ticker: str
    timeframe: str
    data_as_of_date: date
    calculated_at: datetime
    origin_type: str
    engine_version: str
    config_version: str
    config_hash: str
    source_data_hash: str
    schema_version: str
    data_quality_label: str
    evaluation_run_id: int | None = None
    run_id: int | None = None
    source_run_id_text: str | None = None
    source_ids: dict[str, int | None] = field(default_factory=dict)
    promoted_fields: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    feature_flags: dict[str, Any] = field(default_factory=dict)
    warning_flags: list[str] = field(default_factory=list)
    missing_data: dict[str, Any] = field(default_factory=dict)
    source_lineage: dict[str, Any] = field(default_factory=dict)
    diagnostic_high_cross: dict[str, Any] = field(default_factory=dict)
    canonical_decision: dict[str, Any] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PurgeScope:
    before_date: date | None = None
    ticker: str | None = None
    evaluation_run_id: int | None = None


@dataclass(frozen=True)
class PurgePreview:
    scope: PurgeScope
    token: str
    counts: dict[str, int]


class SetupLifecycleRepository:
    def __init__(self, config: SetupLifecycleConfig | None = None) -> None:
        self.config = config or load_setup_lifecycle_config()

    def create_evaluation_run(
        self,
        db: Session,
        *,
        mode: str,
        status: str,
        engine_version: str,
        config_version: str,
        config_hash: str,
        source_run_id: int | None = None,
        source_run_id_text: str | None = None,
        output_evaluation_version: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        ticker_scope: list[str] | None = None,
        requested_config: dict[str, Any] | None = None,
        dry_run: bool = False,
        requester: str | None = None,
    ) -> SetupLifecycleEvaluationRun:
        evaluation_run = SetupLifecycleEvaluationRun(
            source_run_id=source_run_id,
            source_run_id_text=source_run_id_text,
            mode=mode,
            status=status,
            engine_version=engine_version,
            config_version=config_version,
            config_hash=config_hash,
            output_evaluation_version=output_evaluation_version,
            date_from=date_from,
            date_to=date_to,
            ticker_scope_json=list(ticker_scope or []),
            requested_config_json=dict(requested_config or {}),
            dry_run=dry_run,
            requester=requester,
            started_at=_utcnow(),
        )
        return self.add(db, evaluation_run)

    def complete_evaluation_run(
        self,
        db: Session,
        evaluation_run: SetupLifecycleEvaluationRun,
        *,
        status: str,
        current_phase: str | None = None,
        counts: dict[str, int] | None = None,
        errors: dict[str, Any] | None = None,
        source_snapshot_min_id: int | None = None,
        source_snapshot_max_id: int | None = None,
        completed_at: datetime | None = None,
    ) -> SetupLifecycleEvaluationRun:
        finished_at = completed_at or _utcnow()
        evaluation_run.status = status
        evaluation_run.current_phase = current_phase
        evaluation_run.completed_at = finished_at
        evaluation_run.heartbeat_at = finished_at
        evaluation_run.last_heartbeat_at = finished_at
        if evaluation_run.started_at is not None:
            evaluation_run.duration_ms = _duration_ms(evaluation_run.started_at, finished_at)
        if source_snapshot_min_id is not None:
            evaluation_run.source_snapshot_min_id = source_snapshot_min_id
        if source_snapshot_max_id is not None:
            evaluation_run.source_snapshot_max_id = source_snapshot_max_id
        if counts is not None:
            self.apply_evaluation_counts(evaluation_run, counts)
        if errors is not None:
            evaluation_run.error_summary_json = dict(errors)
        db.flush()
        return evaluation_run

    def heartbeat_evaluation_run(
        self,
        db: Session,
        evaluation_run_id: int,
        *,
        current_phase: str | None = None,
    ) -> None:
        now = _utcnow()
        values: dict[str, Any] = {"heartbeat_at": now, "last_heartbeat_at": now}
        if current_phase is not None:
            values["current_phase"] = current_phase
        db.execute(
            update(SetupLifecycleEvaluationRun)
            .where(SetupLifecycleEvaluationRun.id == evaluation_run_id)
            .values(**values)
        )
        db.flush()

    def apply_evaluation_counts(
        self,
        evaluation_run: SetupLifecycleEvaluationRun,
        counts: dict[str, int],
    ) -> None:
        normalized = {key: int(value) for key, value in counts.items()}
        evaluation_run.counts_json = normalized
        for key in (
            "read",
            "captured",
            "canonical",
            "changed",
            "transitioned",
            "alerted",
            "skipped",
            "warning",
            "failed",
        ):
            value = normalized.get(key)
            if value is not None:
                setattr(evaluation_run, f"{key}_count", value)

    def upsert_snapshot(
        self,
        db: Session,
        dto: SetupSignalSnapshotWrite,
    ) -> SetupSignalSnapshot:
        snapshot = self.find_snapshot_by_identity(
            db,
            run_id=dto.run_id,
            ticker=dto.ticker,
            timeframe=dto.timeframe,
            data_as_of_date=dto.data_as_of_date,
            engine_version=dto.engine_version,
            config_hash=dto.config_hash,
            source_data_hash=dto.source_data_hash,
        )
        if snapshot is None:
            snapshot = SetupSignalSnapshot(
                run_id=dto.run_id,
                ticker=self.normalize_ticker(dto.ticker),
                timeframe=dto.timeframe,
                data_as_of_date=dto.data_as_of_date,
                calculated_at=dto.calculated_at,
                origin_type=dto.origin_type,
                engine_version=dto.engine_version,
                config_version=dto.config_version,
                config_hash=dto.config_hash,
                source_data_hash=dto.source_data_hash,
                schema_version=dto.schema_version,
                data_quality_label=dto.data_quality_label,
            )
            db.add(snapshot)

        self._apply_snapshot_fields(snapshot, dto)
        db.flush()
        return snapshot

    def find_snapshot_by_identity(
        self,
        db: Session,
        *,
        run_id: int | None,
        ticker: str,
        timeframe: str,
        data_as_of_date: date,
        engine_version: str,
        config_hash: str,
        source_data_hash: str,
    ) -> SetupSignalSnapshot | None:
        statement = (
            select(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == self.normalize_ticker(ticker))
            .where(SetupSignalSnapshot.timeframe == timeframe)
            .where(SetupSignalSnapshot.data_as_of_date == data_as_of_date)
            .where(SetupSignalSnapshot.engine_version == engine_version)
            .where(SetupSignalSnapshot.config_hash == config_hash)
            .where(SetupSignalSnapshot.source_data_hash == source_data_hash)
        )
        if run_id is None:
            statement = statement.where(SetupSignalSnapshot.run_id.is_(None))
        else:
            statement = statement.where(SetupSignalSnapshot.run_id == run_id)
        return db.scalar(statement.limit(1))

    def promote_canonical_snapshot(
        self,
        db: Session,
        snapshot: SetupSignalSnapshot,
        *,
        reason: str,
        decision: dict[str, Any] | None = None,
    ) -> SetupSignalSnapshot:
        db.execute(
            update(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == snapshot.ticker)
            .where(SetupSignalSnapshot.timeframe == snapshot.timeframe)
            .where(SetupSignalSnapshot.data_as_of_date == snapshot.data_as_of_date)
            .where(SetupSignalSnapshot.id != snapshot.id)
            .where(SetupSignalSnapshot.is_canonical.is_(True))
            .values(
                is_canonical=False,
                superseded_by_snapshot_id=snapshot.id,
            )
        )
        snapshot.is_canonical = True
        snapshot.canonical_reason = reason
        snapshot.canonical_decision_json = dict(decision or {})
        snapshot.canonicalized_at = _utcnow()
        db.flush()
        return snapshot

    def latest_canonical_snapshot(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str = "1d",
        as_of_date: date | None = None,
    ) -> SetupSignalSnapshot | None:
        statement = (
            select(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == self.normalize_ticker(ticker))
            .where(SetupSignalSnapshot.timeframe == timeframe)
            .where(SetupSignalSnapshot.is_canonical.is_(True))
        )
        if as_of_date is not None:
            statement = statement.where(SetupSignalSnapshot.data_as_of_date <= as_of_date)
        return db.scalar(
            statement.order_by(
                SetupSignalSnapshot.data_as_of_date.desc(),
                SetupSignalSnapshot.id.desc(),
            ).limit(1)
        )

    def previous_canonical_snapshot(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        before_date: date,
    ) -> SetupSignalSnapshot | None:
        return db.scalar(
            select(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == self.normalize_ticker(ticker))
            .where(SetupSignalSnapshot.timeframe == timeframe)
            .where(SetupSignalSnapshot.data_as_of_date < before_date)
            .where(SetupSignalSnapshot.is_canonical.is_(True))
            .order_by(
                SetupSignalSnapshot.data_as_of_date.desc(),
                SetupSignalSnapshot.id.desc(),
            )
            .limit(1)
        )

    def canonical_snapshot_history(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        before_date: date,
        limit: int = 10,
    ) -> list[SetupSignalSnapshot]:
        safe_limit = max(1, min(int(limit), 50))
        return list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(SetupSignalSnapshot.ticker == self.normalize_ticker(ticker))
                .where(SetupSignalSnapshot.timeframe == timeframe)
                .where(SetupSignalSnapshot.data_as_of_date < before_date)
                .where(SetupSignalSnapshot.is_canonical.is_(True))
                .order_by(
                    SetupSignalSnapshot.data_as_of_date.desc(),
                    SetupSignalSnapshot.id.desc(),
                )
                .limit(safe_limit)
            )
        )

    def get_snapshots_by_ids(
        self,
        db: Session,
        snapshot_ids: tuple[int, ...] | list[int],
    ) -> list[SetupSignalSnapshot]:
        if not snapshot_ids:
            return []
        return list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(SetupSignalSnapshot.id.in_(snapshot_ids))
                .order_by(
                    SetupSignalSnapshot.ticker,
                    SetupSignalSnapshot.timeframe,
                    SetupSignalSnapshot.data_as_of_date,
                    SetupSignalSnapshot.id,
                )
            )
        )

    def load_snapshots_for_run(
        self,
        db: Session,
        *,
        run_id: int,
        config_hash: str | None = None,
    ) -> list[SetupSignalSnapshot]:
        statement = select(SetupSignalSnapshot).where(SetupSignalSnapshot.run_id == run_id)
        if config_hash is not None:
            statement = statement.where(SetupSignalSnapshot.config_hash == config_hash)
        return list(
            db.scalars(
                statement.order_by(
                    SetupSignalSnapshot.ticker,
                    SetupSignalSnapshot.timeframe,
                    SetupSignalSnapshot.data_as_of_date,
                    SetupSignalSnapshot.id,
                )
            )
        )

    def load_canonicalization_candidates(
        self,
        db: Session,
        affected_snapshots: tuple[SetupSignalSnapshot, ...] | list[SetupSignalSnapshot],
        *,
        lock: bool = False,
    ) -> list[SetupSignalSnapshot]:
        keys = {
            (
                snapshot.ticker,
                snapshot.timeframe,
                snapshot.data_as_of_date,
                snapshot.config_hash,
            )
            for snapshot in affected_snapshots
        }
        if not keys:
            return []
        statement = (
            select(SetupSignalSnapshot)
            .where(
                tuple_(
                    SetupSignalSnapshot.ticker,
                    SetupSignalSnapshot.timeframe,
                    SetupSignalSnapshot.data_as_of_date,
                    SetupSignalSnapshot.config_hash,
                ).in_(keys)
            )
            .order_by(
                SetupSignalSnapshot.ticker,
                SetupSignalSnapshot.timeframe,
                SetupSignalSnapshot.data_as_of_date,
                SetupSignalSnapshot.id,
            )
        )
        if lock:
            statement = statement.with_for_update()
        return list(db.scalars(statement))

    def previous_canonical_snapshots(
        self,
        db: Session,
        *,
        tickers: tuple[str, ...],
        timeframe: str,
        before_date: date,
    ) -> dict[str, SetupSignalSnapshot]:
        normalized = tuple(sorted({self.normalize_ticker(ticker) for ticker in tickers}))
        if not normalized:
            return {}
        rows = list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(SetupSignalSnapshot.ticker.in_(normalized))
                .where(SetupSignalSnapshot.timeframe == timeframe)
                .where(SetupSignalSnapshot.data_as_of_date < before_date)
                .where(SetupSignalSnapshot.is_canonical.is_(True))
                .order_by(
                    SetupSignalSnapshot.ticker,
                    SetupSignalSnapshot.data_as_of_date.desc(),
                    SetupSignalSnapshot.id.desc(),
                )
            )
        )
        latest: dict[str, SetupSignalSnapshot] = {}
        for row in rows:
            latest.setdefault(row.ticker, row)
        return latest

    def count_active_episodes(self, db: Session, *, config_hash: str | None = None) -> int:
        statement = select(func.count()).select_from(SetupLifecycleEpisode).where(
            SetupLifecycleEpisode.status == "ACTIVE"
        )
        if config_hash is not None:
            statement = statement.where(SetupLifecycleEpisode.config_hash == config_hash)
        return int(db.scalar(statement) or 0)

    def active_episode_for_update(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        setup_family: str,
        lock: bool = True,
    ) -> SetupLifecycleEpisode | None:
        statement = (
            select(SetupLifecycleEpisode)
            .where(SetupLifecycleEpisode.ticker == self.normalize_ticker(ticker))
            .where(SetupLifecycleEpisode.timeframe == timeframe)
            .where(SetupLifecycleEpisode.setup_family == setup_family)
            .where(SetupLifecycleEpisode.status == "ACTIVE")
            .order_by(SetupLifecycleEpisode.id.desc())
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        return db.scalar(statement)

    def latest_closed_episode(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        setup_family: str,
    ) -> SetupLifecycleEpisode | None:
        return db.scalar(
            select(SetupLifecycleEpisode)
            .where(SetupLifecycleEpisode.ticker == self.normalize_ticker(ticker))
            .where(SetupLifecycleEpisode.timeframe == timeframe)
            .where(SetupLifecycleEpisode.setup_family == setup_family)
            .where(SetupLifecycleEpisode.status == "CLOSED")
            .order_by(
                SetupLifecycleEpisode.closed_on.desc().nullslast(),
                SetupLifecycleEpisode.id.desc(),
            )
            .limit(1)
        )

    def active_episodes_for_ticker(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
    ) -> list[SetupLifecycleEpisode]:
        return list(
            db.scalars(
                select(SetupLifecycleEpisode)
                .where(SetupLifecycleEpisode.ticker == self.normalize_ticker(ticker))
                .where(SetupLifecycleEpisode.timeframe == timeframe)
                .where(SetupLifecycleEpisode.status == "ACTIVE")
                .order_by(SetupLifecycleEpisode.id)
            )
        )

    def supersede_prior_current_events(
        self,
        db: Session,
        event: SetupLifecycleEvent,
    ) -> None:
        if event.id is None:
            db.flush()
        db.execute(
            update(SetupLifecycleEvent)
            .where(SetupLifecycleEvent.id != event.id)
            .where(SetupLifecycleEvent.is_current_version.is_(True))
            .where(SetupLifecycleEvent.episode_id == event.episode_id)
            .where(SetupLifecycleEvent.effective_date == event.effective_date)
            .where(SetupLifecycleEvent.event_type == event.event_type)
            .values(
                is_current_version=False,
                superseded_by_event_id=event.id,
            )
        )
        db.flush()

    def latest_authoritative_evaluation_version(
        self,
        db: Session,
    ) -> str | None:
        row = db.scalar(
            select(SetupLifecycleEvaluationRun.output_evaluation_version)
            .where(SetupLifecycleEvaluationRun.status == "COMPLETED")
            .where(SetupLifecycleEvaluationRun.dry_run.is_(False))
            .where(SetupLifecycleEvaluationRun.output_evaluation_version.is_not(None))
            .order_by(
                SetupLifecycleEvaluationRun.completed_at.desc().nullslast(),
                SetupLifecycleEvaluationRun.id.desc(),
            )
            .limit(1)
        )
        return row

    def add_lifecycle_event(
        self,
        db: Session,
        event: SetupLifecycleEvent,
    ) -> SetupLifecycleEvent:
        existing = self.get_lifecycle_event(
            db,
            evaluation_run_id=event.evaluation_run_id,
            source_event_key=event.source_event_key,
        )
        if existing is not None:
            return existing
        return self.add(db, event)

    def get_lifecycle_event(
        self,
        db: Session,
        *,
        evaluation_run_id: int | None,
        source_event_key: str,
    ) -> SetupLifecycleEvent | None:
        statement = select(SetupLifecycleEvent).where(
            SetupLifecycleEvent.source_event_key == source_event_key
        )
        if evaluation_run_id is None:
            statement = statement.where(SetupLifecycleEvent.evaluation_run_id.is_(None))
        else:
            statement = statement.where(SetupLifecycleEvent.evaluation_run_id == evaluation_run_id)
        return db.scalar(statement.limit(1))

    def add_signal_change_event(
        self,
        db: Session,
        event: SignalChangeEvent,
    ) -> SignalChangeEvent:
        existing = self.get_signal_change_event(db, event.source_event_key)
        if existing is not None:
            return existing
        return self.add(db, event)

    def get_signal_change_event(
        self,
        db: Session,
        source_event_key: str,
    ) -> SignalChangeEvent | None:
        return db.scalar(
            select(SignalChangeEvent)
            .where(SignalChangeEvent.source_event_key == source_event_key)
            .limit(1)
        )

    def get_signal_change_events_by_ids(
        self,
        db: Session,
        event_ids: tuple[int, ...] | list[int],
    ) -> list[SignalChangeEvent]:
        if not event_ids:
            return []
        return list(
            db.scalars(
                select(SignalChangeEvent)
                .where(SignalChangeEvent.id.in_(event_ids))
                .order_by(SignalChangeEvent.effective_date, SignalChangeEvent.id)
            )
        )

    def upsert_alert_rule(
        self,
        db: Session,
        *,
        rule_id: str,
        severity: str,
        scope: str,
        config_version: str,
        enabled: bool = True,
        setup_family: str | None = None,
        cooldown_sessions: int = 0,
        minimum_confidence: int = 0,
        condition: dict[str, Any] | None = None,
        market_restrictions: dict[str, Any] | None = None,
    ) -> SignalAlertRule:
        rule = db.scalar(select(SignalAlertRule).where(SignalAlertRule.rule_id == rule_id).limit(1))
        if rule is None:
            rule = SignalAlertRule(rule_id=rule_id)
            db.add(rule)
        rule.enabled = enabled
        rule.severity = severity
        rule.scope = scope
        rule.setup_family = setup_family
        rule.cooldown_sessions = cooldown_sessions
        rule.minimum_confidence = minimum_confidence
        rule.config_version = config_version
        rule.condition_json = dict(condition or {})
        rule.market_restrictions_json = dict(market_restrictions or {})
        db.flush()
        return rule

    def alert_rules(
        self,
        db: Session,
        *,
        enabled_only: bool = True,
    ) -> list[SignalAlertRule]:
        statement = select(SignalAlertRule).order_by(SignalAlertRule.rule_id)
        if enabled_only:
            statement = statement.where(SignalAlertRule.enabled.is_(True))
        return list(db.scalars(statement))

    def add_alert_event(
        self,
        db: Session,
        event: SignalAlertEvent,
    ) -> SignalAlertEvent:
        existing = db.scalar(
            select(SignalAlertEvent).where(SignalAlertEvent.event_key == event.event_key).limit(1)
        )
        if existing is not None:
            return existing
        return self.add(db, event)

    def recent_alert_events(
        self,
        db: Session,
        *,
        alert_rule_id: int,
        ticker: str,
        timeframe: str,
        since_date: date,
        semantic_key: str | None = None,
    ) -> list[SignalAlertEvent]:
        statement = (
            select(SignalAlertEvent)
            .where(SignalAlertEvent.alert_rule_id == alert_rule_id)
            .where(SignalAlertEvent.ticker == self.normalize_ticker(ticker))
            .where(SignalAlertEvent.timeframe == timeframe)
            .where(SignalAlertEvent.effective_date >= since_date)
            .order_by(SignalAlertEvent.effective_date.desc(), SignalAlertEvent.id.desc())
        )
        rows = list(db.scalars(statement))
        if semantic_key is None:
            return rows
        return [
            row
            for row in rows
            if (row.evidence_json or {}).get("semantic_key") == semantic_key
        ]

    def get_alert_event(self, db: Session, alert_id: int) -> SignalAlertEvent | None:
        return db.get(SignalAlertEvent, alert_id)

    def acknowledge_alert_event(
        self,
        db: Session,
        alert_id: int,
        *,
        acknowledged_at: datetime | None = None,
    ) -> SignalAlertEvent | None:
        alert = self.get_alert_event(db, alert_id)
        if alert is None:
            return None
        alert.status = "ACKNOWLEDGED"
        alert.acknowledged_at = acknowledged_at or _utcnow()
        db.flush()
        return alert

    def dismiss_alert_event(
        self,
        db: Session,
        alert_id: int,
        *,
        dismissed_at: datetime | None = None,
    ) -> SignalAlertEvent | None:
        alert = self.get_alert_event(db, alert_id)
        if alert is None:
            return None
        alert.status = "DISMISSED"
        alert.dismissed_at = dismissed_at or _utcnow()
        db.flush()
        return alert

    def write_admin_audit_event(
        self,
        db: Session,
        *,
        event_type: str,
        requester: str,
        evaluation_run_id: int | None = None,
        reason: str | None = None,
        scope: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        affected_counts: dict[str, int] | None = None,
        preview_token: str | None = None,
    ) -> SetupLifecycleAdministrativeAuditEvent:
        audit_event = SetupLifecycleAdministrativeAuditEvent(
            event_type=event_type,
            requester=requester,
            evaluation_run_id=evaluation_run_id,
            reason=reason,
            scope_json=dict(scope or {}),
            before_json=dict(before or {}),
            after_json=dict(after or {}),
            affected_counts_json=dict(affected_counts or {}),
            preview_token_hash=self.hash_token(preview_token) if preview_token else None,
        )
        return self.add(db, audit_event)

    def preview_purge(self, db: Session, scope: PurgeScope) -> PurgePreview:
        counts = {
            "alert_events": self._count(db, self._scoped_alert_events(scope)),
            "signal_change_events": self._count(db, self._scoped_signal_change_events(scope)),
            "lifecycle_events": self._count(db, self._scoped_lifecycle_events(scope)),
            "episodes": self._count(db, self._scoped_episodes(scope)),
            "snapshots": self._count(db, self._scoped_snapshots(scope)),
            "evaluation_runs": self._count(db, self._scoped_evaluation_runs(scope)),
        }
        token = self.stable_hash({"scope": scope.__dict__, "counts": counts})
        return PurgePreview(scope=scope, token=token, counts=counts)

    def execute_purge(self, db: Session, preview: PurgePreview, token: str) -> dict[str, int]:
        if not self.config.retention.purge_enabled:
            raise ValueError("setup lifecycle purge is disabled by retention policy")
        return self._execute_purge_unchecked(db, preview, token)

    def _execute_purge_unchecked(
        self,
        db: Session,
        preview: PurgePreview,
        token: str,
    ) -> dict[str, int]:
        if token != preview.token:
            raise ValueError("purge preview token does not match")
        deleted: dict[str, int] = {}
        for name, statement in (
            ("alert_events", self._scoped_alert_events(preview.scope)),
            ("signal_change_events", self._scoped_signal_change_events(preview.scope)),
            ("lifecycle_events", self._scoped_lifecycle_events(preview.scope)),
            ("episodes", self._scoped_episodes(preview.scope)),
            ("snapshots", self._scoped_snapshots(preview.scope)),
            ("evaluation_runs", self._scoped_evaluation_runs(preview.scope)),
        ):
            deleted[name] = self._delete_selected(db, statement)
        db.flush()
        return deleted

    def add(self, db: Session, row: Any) -> Any:
        db.add(row)
        db.flush()
        return row

    @staticmethod
    def normalize_ticker(ticker: str) -> str:
        return ticker.strip().upper()

    @classmethod
    def snapshot_identity_key(
        cls,
        *,
        run_id: int | None,
        ticker: str,
        timeframe: str,
        data_as_of_date: date,
        engine_version: str,
        config_hash: str,
        source_data_hash: str,
    ) -> tuple[int | None, str, str, str, str, str, str]:
        return (
            run_id,
            cls.normalize_ticker(ticker),
            timeframe,
            data_as_of_date.isoformat(),
            engine_version,
            config_hash,
            source_data_hash,
        )

    @classmethod
    def lifecycle_event_key(
        cls,
        *,
        ticker: str,
        timeframe: str,
        setup_family: str,
        effective_date: date,
        from_state: str | None,
        to_state: str,
        engine_version: str,
        config_hash: str,
    ) -> str:
        return cls.stable_key(
            "lifecycle",
            cls.normalize_ticker(ticker),
            timeframe,
            setup_family,
            effective_date.isoformat(),
            from_state or "",
            to_state,
            engine_version,
            config_hash,
        )

    @classmethod
    def signal_change_key(
        cls,
        *,
        ticker: str,
        timeframe: str,
        signal_key: str,
        effective_date: date,
        old_value: Any,
        new_value: Any,
        config_hash: str,
    ) -> str:
        payload = {
            "ticker": cls.normalize_ticker(ticker),
            "timeframe": timeframe,
            "signal_key": signal_key,
            "effective_date": effective_date.isoformat(),
            "old": old_value,
            "new": new_value,
            "config_hash": config_hash,
        }
        return cls.stable_hash(payload)

    @classmethod
    def alert_event_key(
        cls,
        *,
        rule_id: str,
        source_event_key: str,
        ticker: str,
        episode_id: int | None = None,
        effective_date: date | None = None,
        evaluation_run_id: int | None = None,
    ) -> str:
        return cls.stable_key(
            "alert",
            rule_id,
            source_event_key,
            cls.normalize_ticker(ticker),
            str(episode_id or ""),
            effective_date.isoformat() if effective_date else "",
            str(evaluation_run_id or ""),
        )

    @staticmethod
    def stable_key(*parts: str) -> str:
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def stable_hash(payload: Any) -> str:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    @classmethod
    def hash_token(cls, token: str) -> str:
        return cls.stable_key("token", token)

    def _apply_snapshot_fields(
        self,
        snapshot: SetupSignalSnapshot,
        dto: SetupSignalSnapshotWrite,
    ) -> None:
        snapshot.evaluation_run_id = dto.evaluation_run_id
        snapshot.run_id = dto.run_id
        snapshot.source_run_id_text = dto.source_run_id_text
        snapshot.ticker = self.normalize_ticker(dto.ticker)
        snapshot.timeframe = dto.timeframe
        snapshot.data_as_of_date = dto.data_as_of_date
        snapshot.calculated_at = dto.calculated_at
        snapshot.origin_type = dto.origin_type
        snapshot.engine_version = dto.engine_version
        snapshot.config_version = dto.config_version
        snapshot.config_hash = dto.config_hash
        snapshot.source_data_hash = dto.source_data_hash
        snapshot.schema_version = dto.schema_version
        snapshot.data_quality_label = dto.data_quality_label
        for field_name, value in dto.source_ids.items():
            if hasattr(snapshot, field_name):
                setattr(snapshot, field_name, value)
        for field_name, value in dto.promoted_fields.items():
            if hasattr(snapshot, field_name):
                setattr(snapshot, field_name, self._coerce_promoted_value(value))
        snapshot.signals_json = dict(dto.signals)
        snapshot.feature_flags_json = dict(dto.feature_flags)
        snapshot.warning_flags_json = list(dto.warning_flags)
        snapshot.missing_data_json = dict(dto.missing_data)
        snapshot.source_lineage_json = dict(dto.source_lineage)
        snapshot.diagnostic_high_cross_json = dict(dto.diagnostic_high_cross)
        snapshot.canonical_decision_json = dict(dto.canonical_decision)
        snapshot.debug_json = dict(dto.debug)

    @staticmethod
    def _coerce_promoted_value(value: Any) -> Any:
        if isinstance(value, float):
            return Decimal(str(value))
        return value

    def _scoped_snapshots(self, scope: PurgeScope):
        statement = select(SetupSignalSnapshot)
        if scope.ticker is not None:
            statement = statement.where(
                SetupSignalSnapshot.ticker == self.normalize_ticker(scope.ticker)
            )
        if scope.before_date is not None:
            statement = statement.where(SetupSignalSnapshot.data_as_of_date < scope.before_date)
        if scope.evaluation_run_id is not None:
            statement = statement.where(
                SetupSignalSnapshot.evaluation_run_id == scope.evaluation_run_id
            )
        return statement

    def _scoped_episodes(self, scope: PurgeScope):
        statement = select(SetupLifecycleEpisode)
        if scope.ticker is not None:
            statement = statement.where(
                SetupLifecycleEpisode.ticker == self.normalize_ticker(scope.ticker)
            )
        if scope.before_date is not None:
            statement = statement.where(
                SetupLifecycleEpisode.current_as_of_date < scope.before_date
            )
        return statement

    def _scoped_lifecycle_events(self, scope: PurgeScope):
        statement = select(SetupLifecycleEvent)
        if scope.ticker is not None:
            statement = statement.where(
                SetupLifecycleEvent.ticker == self.normalize_ticker(scope.ticker)
            )
        if scope.before_date is not None:
            statement = statement.where(SetupLifecycleEvent.effective_date < scope.before_date)
        if scope.evaluation_run_id is not None:
            statement = statement.where(
                SetupLifecycleEvent.evaluation_run_id == scope.evaluation_run_id
            )
        return statement

    def _scoped_signal_change_events(self, scope: PurgeScope):
        statement = select(SignalChangeEvent)
        if scope.ticker is not None:
            statement = statement.where(
                SignalChangeEvent.ticker == self.normalize_ticker(scope.ticker)
            )
        if scope.before_date is not None:
            statement = statement.where(SignalChangeEvent.effective_date < scope.before_date)
        if scope.evaluation_run_id is not None:
            statement = statement.where(
                SignalChangeEvent.evaluation_run_id == scope.evaluation_run_id
            )
        return statement

    def _scoped_alert_events(self, scope: PurgeScope):
        statement = select(SignalAlertEvent)
        if scope.ticker is not None:
            statement = statement.where(
                SignalAlertEvent.ticker == self.normalize_ticker(scope.ticker)
            )
        if scope.before_date is not None:
            statement = statement.where(SignalAlertEvent.effective_date < scope.before_date)
        if scope.evaluation_run_id is not None:
            statement = statement.where(
                SignalAlertEvent.evaluation_run_id == scope.evaluation_run_id
            )
        return statement

    def _scoped_evaluation_runs(self, scope: PurgeScope):
        statement = select(SetupLifecycleEvaluationRun)
        if scope.before_date is not None:
            statement = statement.where(SetupLifecycleEvaluationRun.date_to < scope.before_date)
        if scope.evaluation_run_id is not None:
            statement = statement.where(SetupLifecycleEvaluationRun.id == scope.evaluation_run_id)
        return statement

    @staticmethod
    def _count(db: Session, statement: Select[tuple[Any]]) -> int:
        return int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)

    @staticmethod
    def _delete_selected(db: Session, statement: Select[tuple[Any]]) -> int:
        entity = statement.column_descriptions[0]["entity"]
        criteria = tuple(statement.whereclause.clauses) if statement.whereclause is not None else ()
        result = db.execute(delete(entity).where(*criteria))
        return int(result.rowcount or 0)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=UTC)
    return max(0, int((finished_at - started_at).total_seconds() * 1000))
