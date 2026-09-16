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
from app.services.ceri.change_semantics import ComparisonState
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.ceri.enums import CeriChangeType
from app.services.ceri.evidence_eligibility import EXCLUDED, effective_disposition_by_snapshot
from app.services.ceri.feature_flags import ceri_flags
from app.services.configuration_delivery import anchored_decision_calculator


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
        from app.services.decision_effective_configuration import (
            resolve_ceri_decision_configuration,
        )

        self.effective_configuration = resolve_ceri_decision_configuration(
            self.config, enabled=self.alerts_enabled
        )
        self.config = self.effective_configuration.ceri_decision_config()
        self.alerts_enabled = self.effective_configuration.values["enabled"]
        self.sessions = CeriEffectiveSessionService(self.config.engine.timezone)

    @anchored_decision_calculator
    def rebuild_alerts(
        self,
        db: Session,
        *,
        changes: list[CeriChangeEvent],
        ticker_by_company: dict[int, str] | None = None,
    ) -> AlertRebuildResult:
        alerts = duplicates = skipped = 0
        self._ensure_rule_configuration(db)
        if not self.alerts_enabled:
            return AlertRebuildResult(alerts=0, duplicates=0, skipped=len(changes))
        ticker_by_company = ticker_by_company or {}
        referenced_snapshot_ids = {
            int(snapshot_id)
            for change in changes
            for snapshot_id in (change.from_snapshot_id, change.to_snapshot_id)
            if snapshot_id is not None
        }
        dispositions = effective_disposition_by_snapshot(db, referenced_snapshot_ids)
        for change in changes:
            if any(
                snapshot_id is not None and dispositions.get(int(snapshot_id)) == EXCLUDED
                for snapshot_id in (change.from_snapshot_id, change.to_snapshot_id)
            ):
                skipped += 1
                continue
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
        if change.comparison_state and change.comparison_state != ComparisonState.COMPARABLE.value:
            return False
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
                and coverage + 1e-9 >= self.config.revision.minimum_component_coverage_pct
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

    @anchored_decision_calculator
    def persist_alert_for_change(
        self,
        db: Session,
        *,
        change: CeriChangeEvent,
        ticker: str,
    ) -> CeriAlertEvent | None:
        self._ensure_rule_configuration(db)
        rule = self._rule_for_change(db, change)
        if rule is None:
            return None
        identity = alert_business_identity(rule.rule_id, change)
        event_key = _identity_hash(identity)
        existing = _maybe_scalar(
            db,
            select(CeriAlertEvent).where(CeriAlertEvent.event_key == event_key),
        )
        if existing is not None:
            return None
        if self._within_cooldown(db, rule, ticker, change):
            return None
        configuration_payload = self.effective_configuration.snapshot.as_dict()
        event = CeriAlertEvent(
            alert_rule_id=rule.id,
            source_change_event_id=change.id,
            source_catalyst_revision_id=change.catalyst_revision_id,
            event_key=event_key,
            ticker=ticker.upper(),
            severity=rule.severity,
            importance=change.importance or change.severity,
            signal_class=change.signal_class,
            validity_classification="VALID_CURRENT",
            status="UNREAD",
            evidence_json={
                "effective_configuration_at_creation": configuration_payload,
                "change_type": change.change_type,
                "dedup_key": change.dedup_key,
                "delta": change.delta_json,
                "alert_rule": rule.rule_id,
                "alert_rule_version": rule.config_version,
                "dedup_identity": identity,
                "dedup_identity_type": identity["identity_type"],
                "cooldown_scope": "rule_ticker",
                "cooldown_sessions": rule.cooldown_sessions,
            },
        )
        db.add(event)
        db.flush()
        return event

    def acknowledge(self, db: Session, alert: CeriAlertEvent) -> CeriAlertEvent:
        if alert.status == "INVALIDATED":
            return alert
        alert.status = "ACKNOWLEDGED"
        alert.acknowledged_at = datetime.now(UTC)
        db.flush()
        return alert

    def dismiss(self, db: Session, alert: CeriAlertEvent) -> CeriAlertEvent:
        if alert.status == "INVALIDATED":
            return alert
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
            from types import SimpleNamespace

            values = self._rule_values.get(change_type.value)
            # A newly appeared C2 row supplies an address only; the C1 native
            # default remains authority if this rule did not exist at resolution.
            if values is None:
                values = dict(
                    rule_id=change_type.value,
                    enabled=rule_config.enabled,
                    severity=rule_config.severity,
                    cooldown_sessions=rule_config.cooldown_sessions,
                    config_version=self.config.engine.config_version,
                )
            return SimpleNamespace(id=existing.id, **values) if values["enabled"] else None
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

    def _ensure_rule_configuration(self, db):
        if hasattr(self, "_rule_values"):
            return
        from app.services.configuration_delivery import current_delivery
        from app.services.decision_effective_configuration import (
            resolve_ceri_decision_configuration,
        )

        if current_delivery() is None:
            self.effective_configuration = resolve_ceri_decision_configuration(
                self.config, enabled=self.alerts_enabled, rules=tuple(_load(db, CeriAlertRule))
            )
        self._rule_values = {
            row["rule_id"]: row for row in self.effective_configuration.values["rules"]
        }

    def _within_cooldown(
        self,
        db: Session,
        rule: CeriAlertRule,
        ticker: str,
        change: CeriChangeEvent,
    ) -> bool:
        if not rule.cooldown_sessions:
            return False
        if change.catalyst_revision_id is not None or change.guidance_event_id is not None:
            # Material canonical event revisions carry their own deterministic
            # identity and must not be swallowed by a ticker-wide cooldown.
            return False
        alerts = _load(db, CeriAlertEvent)
        for alert in alerts:
            historical_rule = (alert.evidence_json or {}).get("alert_rule")
            same_rule = (
                historical_rule == rule.rule_id
                if historical_rule
                else alert.alert_rule_id == rule.id
            )
            if not same_rule or alert.ticker.upper() != ticker.upper():
                continue
            if alert.created_at is None or change.created_at is None:
                continue
            age_sessions = _trading_sessions_between(
                self.sessions.resolve(timestamp=alert.created_at).effective_session,
                self.sessions.resolve(timestamp=change.created_at).effective_session,
                self.sessions,
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


def alert_business_identity(rule_id: str, change: CeriChangeEvent) -> dict[str, object]:
    change_type = CeriChangeType(change.change_type)
    if change.catalyst_revision_id is not None:
        return {
            "identity_type": "CATALYST_REVISION",
            "rule_id": rule_id,
            "canonical_event_id": (change.delta_json or {}).get("canonical_event_id"),
            "event_revision_id": change.catalyst_revision_id,
        }
    if change.guidance_event_id is not None:
        return {
            "identity_type": "GUIDANCE_REVISION",
            "rule_id": rule_id,
            "guidance_event_id": change.guidance_event_id,
        }
    if change_type in {CeriChangeType.RISK_ESCALATED, CeriChangeType.RISK_DEESCALATED}:
        identity_type = "RISK_TRANSITION"
    elif change_type in {CeriChangeType.DATA_STALE, CeriChangeType.DATA_REFRESHED}:
        identity_type = "DATA_QUALITY_TRANSITION"
    else:
        identity_type = "OPPORTUNITY_TRANSITION"
    return {
        "identity_type": identity_type,
        "rule_id": rule_id,
        "company_id": change.company_id,
        "from_snapshot_id": change.from_snapshot_id,
        "to_snapshot_id": change.to_snapshot_id,
        "change_type": change.change_type,
    }


def _identity_hash(identity: dict[str, object]) -> str:
    encoded = "|".join(f"{key}={identity[key]}" for key in sorted(identity))
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
