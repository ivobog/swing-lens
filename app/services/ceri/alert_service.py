from __future__ import annotations

import hashlib
from contextlib import nullcontext
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
from app.services.core_mutation_authority import core_writer_member, core_writer_transaction


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
    @core_writer_transaction
    def rebuild_alerts(
        self,
        db: Session,
        *,
        changes: list[CeriChangeEvent],
        ticker_by_company: dict[int, str] | None = None,
        _source_bundle=None,
    ) -> AlertRebuildResult:
        alerts = duplicates = skipped = 0
        self._ensure_rule_configuration(db)
        if not self.alerts_enabled:
            return AlertRebuildResult(alerts=0, duplicates=0, skipped=len(changes))
        if isinstance(db, Session):
            self._materialize_rule_addresses(db, changes)
        ticker_by_company = ticker_by_company or {}
        source_scope = nullcontext()
        if _source_bundle is not None:
            from app.services.source_mutation_authority import prefetched_source_scope

            rule_ids = {rule.id for rule in getattr(self, "_rule_addresses", {}).values()}
            retained_rules = _source_bundle.load(
                CeriAlertRule,
                select(CeriAlertRule).where(CeriAlertRule.id.in_(rule_ids)),
            )
            if {rule.id for rule in retained_rules} != rule_ids:
                raise ValueError("MUTATION_CERI_ALERT_RULE_SOURCE_SET_MISMATCH")
            _source_bundle.seal()
            source_scope = prefetched_source_scope(db, _source_bundle)
        try:
            with source_scope:
                referenced_snapshot_ids = {
                    int(snapshot_id)
                    for change in changes
                    for snapshot_id in (change.from_snapshot_id, change.to_snapshot_id)
                    if snapshot_id is not None
                }
                dispositions = effective_disposition_by_snapshot(db, referenced_snapshot_ids)
                self._prefetched_dispositions = dispositions
                for change in changes:
                    if any(
                        snapshot_id is not None
                        and dispositions.get(int(snapshot_id)) == EXCLUDED
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
        finally:
            if hasattr(self, "_prefetched_dispositions"):
                del self._prefetched_dispositions
            if hasattr(self, "_rule_addresses"):
                del self._rule_addresses
        return AlertRebuildResult(alerts=alerts, duplicates=duplicates, skipped=skipped)

    @core_writer_member("app.services.ceri.alert_service:CeriAlertService.rebuild_alerts")
    def _materialize_rule_addresses(
        self,
        db: Session,
        changes: list[CeriChangeEvent],
    ) -> None:
        """Create the bounded FK address set before sealing alert sources."""
        self._ensure_rule_configuration(db)
        rule_types = sorted(
            {
                change.change_type
                for change in changes
                if change.change_type in {kind.value for kind in self.config.alerts.rules}
                and self.config.alerts.rules[CeriChangeType(change.change_type)].enabled
            }
        )
        if not rule_types:
            self._rule_addresses = {}
            return
        if set(getattr(self, "_rule_addresses", {})) == set(rule_types):
            return
        existing = list(
            db.scalars(select(CeriAlertRule).where(CeriAlertRule.rule_id.in_(rule_types)))
        )
        missing = set(rule_types) - {rule.rule_id for rule in existing}
        if missing:
            values = []
            for rule_id in sorted(missing):
                native = self.config.alerts.rules[CeriChangeType(rule_id)]
                values.append(
                    {
                        "rule_id": rule_id,
                        "enabled": True,
                        "severity": native.severity,
                        "thresholds_json": {},
                        "scope_json": {},
                        "cooldown_sessions": native.cooldown_sessions,
                        "config_version": self.config.engine.config_version,
                        "source_event_types_json": [rule_id],
                    }
                )
            if db.connection().dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert

                db.execute(
                    insert(CeriAlertRule)
                    .values(values)
                    .on_conflict_do_nothing(index_elements=[CeriAlertRule.rule_id])
                )
            else:
                db.add_all(CeriAlertRule(**value) for value in values)
            db.flush()
            existing = list(
                db.scalars(select(CeriAlertRule).where(CeriAlertRule.rule_id.in_(rule_types)))
            )
        self._rule_addresses = {rule.rule_id: rule for rule in existing}
        if set(self._rule_addresses) != set(rule_types):
            raise ValueError("MUTATION_CERI_ALERT_RULE_ADDRESS_SET_MISMATCH")

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
    @core_writer_transaction
    def persist_alert_for_change(
        self,
        db: Session,
        *,
        change: CeriChangeEvent,
        ticker: str,
    ) -> CeriAlertEvent | None:
        cutoff = None
        if isinstance(db, Session):
            from app.services.ceri.alert_authority import validate_alert_source
            from app.services.decision_mutation_authority import (
                lock_decision_scope,
                operational_decision_authority,
            )

            # Serializes both first-rule creation and the ticker cooldown check.
            lock_decision_scope(db, ("ceri-alert", change.change_type, ticker.upper()))
            self._ensure_rule_configuration(db)
            cutoff = validate_alert_source(db, self, change, ticker)
            from app.models.ceri_tables import CeriScoreSnapshot

            source = (
                db.get(CeriScoreSnapshot, change.to_snapshot_id) if change.to_snapshot_id else None
            )
            operational_decision_authority(
                db,
                writer="persist_ceri_alert",
                run_id=source.run_id if source else None,
                manifest={
                    "change_id": change.id,
                    "change_key": change.dedup_key,
                    "ticker": ticker.upper(),
                    "cutoff_at": cutoff.cutoff_at,
                    "session": cutoff.latest_completed_session,
                    "configuration": self.effective_configuration.snapshot.as_dict(),
                },
                job_types=(
                    "CERI_ALERT_REBUILD",
                    "CERI_CAPTURE_RUN",
                    "FULL_PIPELINE",
                    "REPAIR_TICKER",
                ),
            )
        self._ensure_rule_configuration(db)
        rule = self._rule_for_change(db, change)
        if rule is None:
            return None
        if isinstance(db, Session):
            from app.services.ceri.alert_authority import validate_rule

            validate_rule(db, self, rule, change)
        identity = alert_business_identity(rule.rule_id, change)
        event_key = _identity_hash(identity)
        existing = _maybe_scalar(
            db,
            select(CeriAlertEvent).where(CeriAlertEvent.event_key == event_key),
        )
        if existing is not None:
            if isinstance(db, Session):
                from app.services.ceri.alert_authority import validate_notification

                validate_notification(db, existing)
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
        if isinstance(db, Session):
            from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
            from app.services.ceri.alert_authority import alert_body

            event.evidence_json["native_alert_proof"] = Canonical.canonicalize(
                {
                    "artifact_role": "SUPPORTING_NOTIFICATION",
                    "classification": "SUPPORTED_DISTINCT_SAFE",
                    "change_key": change.dedup_key,
                    "body_fingerprint": Canonical.fingerprint(alert_body(event)),
                    "operation_time": {
                        "cutoff_at": cutoff.cutoff_at,
                        "session": cutoff.latest_completed_session,
                    },
                    "rule": {
                        "row_id": rule.id,
                        "rule_id": rule.rule_id,
                        "enabled": rule.enabled,
                        "severity": rule.severity,
                        "cooldown_sessions": rule.cooldown_sessions,
                        "config_version": rule.config_version,
                    },
                }
            )
        db.add(event)
        db.flush()
        return event

    @core_writer_transaction
    def acknowledge(self, db: Session, alert: CeriAlertEvent) -> CeriAlertEvent:
        self._validate_status(db, alert)
        if alert.status == "INVALIDATED":
            return alert
        alert.status = "ACKNOWLEDGED"
        alert.acknowledged_at = datetime.now(UTC)
        db.flush()
        return alert

    @core_writer_transaction
    def dismiss(self, db: Session, alert: CeriAlertEvent) -> CeriAlertEvent:
        self._validate_status(db, alert)
        if alert.status == "INVALIDATED":
            return alert
        alert.status = "DISMISSED"
        alert.dismissed_at = datetime.now(UTC)
        db.flush()
        return alert

    def _validate_status(self, db, alert):
        if isinstance(db, Session):
            from app.services.ceri.alert_authority import _normalized_source, validate_notification
            from app.services.decision_mutation_authority import operational_decision_authority

            _normalized_source(db, CeriAlertEvent, alert.id)
            validate_notification(db, alert, allow_legacy=True)
            operational_decision_authority(
                db,
                writer="ceri_alert_status",
                manifest={
                    "alert_id": alert.id,
                    "event_key": alert.event_key,
                    "change_id": alert.source_change_event_id,
                    "legacy_notification": not bool(
                        (alert.evidence_json or {}).get("native_alert_proof")
                    ),
                },
            )

    @core_writer_member("app.services.ceri.alert_service:CeriAlertService.persist_alert_for_change")
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
        existing = getattr(self, "_rule_addresses", {}).get(change_type.value)
        if existing is None:
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
            if isinstance(db, Session):
                from datetime import date

                from app.services.ceri.alert_authority import (
                    validate_change_source,
                    validate_notification,
                )

                validate_notification(db, alert)
                prior_time = (alert.evidence_json or {})["native_alert_proof"]["operation_time"]
                current_time = validate_change_source(db, change)
                age_sessions = _trading_sessions_between(
                    date.fromisoformat(prior_time["session"]),
                    current_time.latest_completed_session,
                    self.sessions,
                )
                if 0 <= age_sessions < rule.cooldown_sessions:
                    return True
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
