from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriAlertRule,
    CeriChangeEvent,
    CeriScoreSnapshot,
)
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.ceri.enums import CeriChangeType
from app.services.ceri.feature_flags import ceri_flags


@dataclass(frozen=True)
class AlertRebuildResult:
    alerts: int
    duplicates: int
    skipped: int

    def as_dict(self) -> dict[str, int]:
        return {"alerts": self.alerts, "duplicates": self.duplicates, "skipped": self.skipped}


class CeriAlertService:
    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        alerts_enabled: bool | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        requested = self.config.alerts.enabled if alerts_enabled is None else bool(alerts_enabled)
        self.alerts_enabled = ceri_flags().alerts and requested
        self.sessions = CeriEffectiveSessionService(self.config.engine.timezone)

    def rebuild_alerts(
        self,
        db: Session,
        *,
        changes: list[CeriChangeEvent],
        ticker_by_company: dict[int, str] | None = None,
    ) -> AlertRebuildResult:
        alerts = duplicates = skipped = 0
        if not self.alerts_enabled:
            return AlertRebuildResult(alerts=0, duplicates=0, skipped=len(changes))
        ticker_by_company = ticker_by_company or {}
        for change in changes:
            if not self._eligible_change(db, change):
                skipped += 1
                continue
            event = self.persist_alert_for_change(
                db,
                change=change,
                ticker=ticker_by_company.get(change.company_id, "UNKNOWN"),
            )
            if event is None:
                duplicates += 1
            else:
                alerts += 1
        return AlertRebuildResult(alerts=alerts, duplicates=duplicates, skipped=skipped)

    def _eligible_change(self, db: Session, change: CeriChangeEvent) -> bool:
        try:
            change_type = CeriChangeType(change.change_type)
        except ValueError:
            return False
        opportunity_types = {
            CeriChangeType.OPPORTUNITY_UPGRADED,
            CeriChangeType.OPPORTUNITY_DOWNGRADED,
        }
        risk_types = {
            CeriChangeType.RISK_ESCALATED,
            CeriChangeType.RISK_DEESCALATED,
        }
        if change_type not in opportunity_types | risk_types:
            return True
        if change.from_snapshot_id is None or change.to_snapshot_id is None:
            return False
        snapshot = _get_snapshot(db, change.to_snapshot_id)
        if snapshot is None:
            return False
        if change_type in opportunity_types:
            coverage = snapshot.opportunity_coverage_pct
            return bool(
                snapshot.opportunity_score is not None
                and coverage is not None
                and coverage + 1e-9
                >= self.config.revision.minimum_component_coverage_pct
                and snapshot.data_confidence != "Insufficient"
            )
        delta = change.delta_json or {}
        ledger = snapshot.event_risk_ledger_json or {}
        accepted = bool(
            delta.get("accepted_evidence") is True
            and (
                ledger.get("accepted_evidence") is True
                or ledger.get("accepted_evidence_ids")
                or ledger.get("selected_event_ids")
            )
        )
        return bool(delta.get("prior_comparable") is True and accepted)

    def persist_alert_for_change(
        self,
        db: Session,
        *,
        change: CeriChangeEvent,
        ticker: str,
    ) -> CeriAlertEvent | None:
        rule = self._rule_for_change(db, change)
        if rule is None:
            return None
        event_key = alert_event_key(
            rule_id=rule.rule_id,
            change_dedup_key=change.dedup_key,
            catalyst_revision_id=change.catalyst_revision_id,
        )
        existing = _maybe_scalar(
            db,
            select(CeriAlertEvent).where(CeriAlertEvent.event_key == event_key),
        )
        if existing is not None:
            return None
        if self._within_cooldown(db, rule, ticker, change):
            return None
        event = CeriAlertEvent(
            alert_rule_id=rule.id,
            source_change_event_id=change.id,
            source_catalyst_revision_id=change.catalyst_revision_id,
            event_key=event_key,
            ticker=ticker.upper(),
            severity=rule.severity,
            status="UNREAD",
            evidence_json={
                "change_type": change.change_type,
                "dedup_key": change.dedup_key,
                "delta": change.delta_json,
                "cooldown_scope": self.config.alerts.dedup_scope,
            },
        )
        db.add(event)
        db.flush()
        return event

    def acknowledge(self, db: Session, alert: CeriAlertEvent) -> CeriAlertEvent:
        alert.status = "ACKNOWLEDGED"
        alert.acknowledged_at = datetime.now(UTC)
        db.flush()
        return alert

    def dismiss(self, db: Session, alert: CeriAlertEvent) -> CeriAlertEvent:
        alert.status = "DISMISSED"
        alert.dismissed_at = datetime.now(UTC)
        db.flush()
        return alert

    def _rule_for_change(
        self,
        db: Session,
        change: CeriChangeEvent,
    ) -> CeriAlertRule | None:
        try:
            change_type = CeriChangeType(change.change_type)
        except ValueError:
            return None
        rule_config = self.config.alerts.rules.get(change_type)
        if rule_config is None or not rule_config.enabled:
            return None
        existing = _maybe_scalar(
            db,
            select(CeriAlertRule).where(CeriAlertRule.rule_id == change_type.value),
        )
        if existing is not None:
            return existing if existing.enabled else None
        rule = CeriAlertRule(
            rule_id=change_type.value,
            enabled=True,
            severity=rule_config.severity,
            thresholds_json={},
            scope_json={},
            cooldown_sessions=rule_config.cooldown_sessions,
            config_version=self.config.engine.config_version,
            source_event_types_json=[change_type.value],
        )
        db.add(rule)
        db.flush()
        return rule

    def _within_cooldown(
        self,
        db: Session,
        rule: CeriAlertRule,
        ticker: str,
        change: CeriChangeEvent,
    ) -> bool:
        if not rule.cooldown_sessions:
            return False
        alerts = _load(db, CeriAlertEvent)
        for alert in alerts:
            if alert.alert_rule_id != rule.id or alert.ticker.upper() != ticker.upper():
                continue
            if alert.created_at is None or change.created_at is None:
                continue
            age_sessions = _trading_sessions_between(
                alert.created_at.date(), change.created_at.date(), self.sessions
            )
            if 0 <= age_sessions < rule.cooldown_sessions:
                return True
        return False


def alert_event_key(
    *,
    rule_id: str,
    change_dedup_key: str,
    catalyst_revision_id: int | None,
) -> str:
    encoded = f"{rule_id}:{change_dedup_key}:{catalyst_revision_id or ''}"
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _maybe_scalar(db: Session, statement):
    scalar = getattr(db, "scalar", None)
    if callable(scalar):
        return scalar(statement)
    return None


def _get_snapshot(db: Session, snapshot_id: int) -> CeriScoreSnapshot | None:
    get = getattr(db, "get", None)
    if callable(get):
        return get(CeriScoreSnapshot, snapshot_id)
    return None


def _load(db: Session, model):
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)


def _trading_sessions_between(start, end, sessions: CeriEffectiveSessionService) -> int:
    if end <= start:
        return (end - start).days
    count = 0
    cursor = start
    while cursor < end:
        cursor = sessions.next_trading_session(cursor + timedelta(days=1))
        count += 1
    return count
