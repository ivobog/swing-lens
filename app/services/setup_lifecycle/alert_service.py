from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.models.tables import (
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalAlertRule,
    SignalChangeEvent,
)
from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import AlertEvaluationResult
from app.services.setup_lifecycle.enums import Actionability, AlertStatus
from app.services.setup_lifecycle.episode_service import EpisodeEvaluationResult
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.us_market_calendar import next_us_trading_day


@dataclass(frozen=True)
class AlertServiceResult:
    created: int = 0
    suppressed: int = 0
    event_ids: tuple[int | None, ...] = ()
    warning_codes: tuple[str, ...] = ()

    def to_dto(self) -> AlertEvaluationResult:
        return AlertEvaluationResult(
            created=self.created,
            suppressed=self.suppressed,
            warning_codes=self.warning_codes,
        )


class SetupLifecycleAlertService:
    def __init__(
        self,
        *,
        repository: SetupLifecycleRepository | None = None,
        config: SetupLifecycleConfig | None = None,
    ) -> None:
        self.config = config or load_setup_lifecycle_config()
        self.repository = repository or SetupLifecycleRepository()

    def seed_builtin_rules(self, db) -> tuple[SignalAlertRule, ...]:
        if not self.config.alerts.built_in_rules_enabled:
            return ()
        rules: list[SignalAlertRule] = []
        for rule_id, rule in self.config.alerts.rules.items():
            rules.append(
                self.repository.upsert_alert_rule(
                    db,
                    rule_id=rule_id,
                    enabled=rule.enabled,
                    severity=rule.severity.value,
                    scope=rule.source,
                    setup_family=rule.filters.get("setup_family"),
                    cooldown_sessions=rule.cooldown_sessions,
                    minimum_confidence=rule.minimum_confidence,
                    config_version=self.config.engine.config_version,
                    condition=dict(rule.filters),
                    market_restrictions=rule.filters.get("market_restrictions"),
                )
            )
        return tuple(rules)

    def evaluate_episode_result(
        self,
        db,
        result: EpisodeEvaluationResult,
        *,
        evaluation_run_id: int | None = None,
    ) -> AlertServiceResult:
        created = 0
        suppressed = 0
        event_ids: list[int | None] = []
        warning_codes: list[str] = []

        if result.lifecycle_event is not None:
            lifecycle = self.evaluate_lifecycle_event(db, result.lifecycle_event)
            created += lifecycle.created
            suppressed += lifecycle.suppressed
            event_ids.extend(lifecycle.event_ids)
            warning_codes.extend(lifecycle.warning_codes)

        if _became_blocked(result):
            gate = self._create_gate_blocked_alert(
                db,
                result,
                evaluation_run_id=evaluation_run_id,
            )
            created += gate.created
            suppressed += gate.suppressed
            event_ids.extend(gate.event_ids)
            warning_codes.extend(gate.warning_codes)

        return AlertServiceResult(
            created=created,
            suppressed=suppressed,
            event_ids=tuple(event_ids),
            warning_codes=tuple(dict.fromkeys(warning_codes)),
        )

    def evaluate_lifecycle_event(
        self,
        db,
        event: SetupLifecycleEvent,
    ) -> AlertServiceResult:
        created = 0
        suppressed = 0
        event_ids: list[int | None] = []
        warning_codes: list[str] = []
        snapshot = (
            db.get(SetupSignalSnapshot, event.snapshot_id)
            if event.snapshot_id and hasattr(db, "get")
            else None
        )
        market_regime = _event_market_regime(event, snapshot)
        for rule in self._rules(db):
            if not _lifecycle_rule_matches(rule, event):
                continue
            outcome = self._persist_alert(
                db,
                rule=rule,
                ticker=event.ticker,
                timeframe=event.timeframe,
                effective_date=event.effective_date,
                source_event_key=event.source_event_key,
                evaluation_run_id=event.evaluation_run_id,
                lifecycle_event_id=event.id,
                episode_id=event.episode_id,
                source_confidence=event.confidence_score,
                semantic_key=_lifecycle_semantic_key(rule, event),
                reason_codes=(f"{rule.rule_id}_ALERT",),
                evidence={
                    "source": "lifecycle_event",
                    "to_state": event.to_state,
                    "to_phase": event.to_phase,
                    "actionability_after": event.actionability_after,
                    "market_regime": market_regime,
                    "setup_family": event.setup_family,
                    "source_evidence": dict(event.evidence_json or {}),
                    "semantic_key": _lifecycle_semantic_key(rule, event),
                },
            )
            created += outcome.created
            suppressed += outcome.suppressed
            event_ids.extend(outcome.event_ids)
            warning_codes.extend(outcome.warning_codes)
        return AlertServiceResult(
            created=created,
            suppressed=suppressed,
            event_ids=tuple(event_ids),
            warning_codes=tuple(dict.fromkeys(warning_codes)),
        )

    def evaluate_signal_change_events(
        self,
        db,
        events: tuple[SignalChangeEvent, ...] | list[SignalChangeEvent],
    ) -> AlertServiceResult:
        created = 0
        suppressed = 0
        event_ids: list[int | None] = []
        warning_codes: list[str] = []
        for event in events:
            for rule in self._rules(db):
                if not _signal_rule_matches(rule, event):
                    continue
                semantic_key = _signal_semantic_key(rule, event)
                outcome = self._persist_alert(
                    db,
                    rule=rule,
                    ticker=event.ticker,
                    timeframe=event.timeframe,
                    effective_date=event.effective_date,
                    source_event_key=event.source_event_key,
                    evaluation_run_id=event.evaluation_run_id,
                    signal_change_event_id=event.id,
                    episode_id=event.episode_id,
                    source_confidence=_source_confidence(event),
                    semantic_key=semantic_key,
                    reason_codes=(f"{rule.rule_id}_ALERT",),
                    evidence={
                        "source": "signal_change_event",
                        "signal_key": event.signal_key,
                        "threshold_direction": event.threshold_direction,
                        "direction": event.direction,
                        "market_regime": (event.evidence_json or {}).get("market_regime"),
                        "setup_family": (event.evidence_json or {}).get("setup_family"),
                        "old_value": dict(event.old_value_json or {}),
                        "new_value": dict(event.new_value_json or {}),
                        "normalized_delta": str(event.normalized_delta)
                        if event.normalized_delta is not None
                        else None,
                        "source_evidence": dict(event.evidence_json or {}),
                        "semantic_key": semantic_key,
                    },
                )
                created += outcome.created
                suppressed += outcome.suppressed
                event_ids.extend(outcome.event_ids)
                warning_codes.extend(outcome.warning_codes)
        return AlertServiceResult(
            created=created,
            suppressed=suppressed,
            event_ids=tuple(event_ids),
            warning_codes=tuple(dict.fromkeys(warning_codes)),
        )

    def acknowledge_alert(self, db, alert_id: int) -> SignalAlertEvent | None:
        return self.repository.acknowledge_alert_event(db, alert_id)

    def dismiss_alert(self, db, alert_id: int) -> SignalAlertEvent | None:
        return self.repository.dismiss_alert_event(db, alert_id)

    def _create_gate_blocked_alert(
        self,
        db,
        result: EpisodeEvaluationResult,
        *,
        evaluation_run_id: int | None,
    ) -> AlertServiceResult:
        episode = result.episode
        if episode is None:
            return AlertServiceResult()
        rule = next((item for item in self._rules(db) if item.rule_id == "GATE_BLOCKED"), None)
        if rule is None:
            return AlertServiceResult()
        effective_date = episode.current_as_of_date
        source_event_key = self.repository.stable_key(
            "gate_blocked",
            str(evaluation_run_id or ""),
            str(episode.id or ""),
            episode.ticker,
            episode.timeframe,
            effective_date.isoformat(),
            ",".join(result.actionability.blockers),
        )
        return self._persist_alert(
            db,
            rule=rule,
            ticker=episode.ticker,
            timeframe=episode.timeframe,
            effective_date=effective_date,
            source_event_key=source_event_key,
            evaluation_run_id=evaluation_run_id,
            episode_id=episode.id,
            source_confidence=result.decision.confidence_score,
            semantic_key=f"GATE_BLOCKED:{episode.id}",
            reason_codes=("GATE_BLOCKED_ALERT",),
            evidence={
                "source": "actionability_change",
                "semantic_key": f"GATE_BLOCKED:{episode.id}",
                "actionability_after": result.actionability.actionability.value,
                "blockers": list(result.actionability.blockers),
                "market_regime": (episode.metadata_json or {}).get("market_regime"),
                "setup_family": episode.setup_family,
            },
        )

    def _persist_alert(
        self,
        db,
        *,
        rule: SignalAlertRule,
        ticker: str,
        timeframe: str,
        effective_date: date,
        source_event_key: str,
        evaluation_run_id: int | None,
        source_confidence: int,
        semantic_key: str,
        reason_codes: tuple[str, ...],
        evidence: dict[str, Any],
        lifecycle_event_id: int | None = None,
        signal_change_event_id: int | None = None,
        episode_id: int | None = None,
    ) -> AlertServiceResult:
        if _reconstructed_source(evidence):
            return AlertServiceResult(suppressed=1, warning_codes=("RECONSTRUCTED_SUPPRESSED",))
        if not _market_restrictions_match(rule, evidence):
            return AlertServiceResult(suppressed=1, warning_codes=("MARKET_RESTRICTION",))
        if not rule.enabled or source_confidence < rule.minimum_confidence:
            return AlertServiceResult(suppressed=1)
        if self._cooldown_active(
            db,
            rule=rule,
            ticker=ticker,
            timeframe=timeframe,
            effective_date=effective_date,
            semantic_key=semantic_key,
        ):
            return AlertServiceResult(suppressed=1)

        event_key = self.repository.alert_event_key(
            rule_id=rule.rule_id,
            source_event_key=source_event_key,
            ticker=ticker,
            episode_id=episode_id,
            effective_date=effective_date,
            evaluation_run_id=evaluation_run_id,
        )
        alert = SignalAlertEvent(
            alert_rule_id=rule.id,
            lifecycle_event_id=lifecycle_event_id,
            signal_change_event_id=signal_change_event_id,
            evaluation_run_id=evaluation_run_id,
            ticker=self.repository.normalize_ticker(ticker),
            timeframe=timeframe,
            effective_date=effective_date,
            event_key=event_key,
            source_event_key=source_event_key,
            status=AlertStatus.UNREAD.value,
            severity=rule.severity,
            reason_codes_json=list(reason_codes),
            evidence_json={
                **evidence,
                "rule_id": rule.rule_id,
                "semantic_key": semantic_key,
                "source_confidence": source_confidence,
            },
        )
        persisted = self.repository.add_alert_event(db, alert)
        created = int(persisted is alert)
        return AlertServiceResult(
            created=created,
            suppressed=0 if created else 1,
            event_ids=(getattr(persisted, "id", None),),
        )

    def _rules(self, db) -> tuple[SignalAlertRule, ...]:
        return tuple(self.repository.alert_rules(db, enabled_only=True))

    def _cooldown_active(
        self,
        db,
        *,
        rule: SignalAlertRule,
        ticker: str,
        timeframe: str,
        effective_date: date,
        semantic_key: str,
    ) -> bool:
        if rule.cooldown_sessions <= 0:
            return False
        since = effective_date - timedelta(days=rule.cooldown_sessions * 4 + 7)
        recent = self.repository.recent_alert_events(
            db,
            alert_rule_id=rule.id,
            ticker=ticker,
            timeframe=timeframe,
            since_date=since,
            semantic_key=semantic_key,
        )
        return any(
            _trading_sessions_between(row.effective_date, effective_date)
            <= rule.cooldown_sessions
            for row in recent
        )


def _lifecycle_rule_matches(rule: SignalAlertRule, event: SetupLifecycleEvent) -> bool:
    condition = rule.condition_json or {}
    if rule.setup_family is not None and rule.setup_family != event.setup_family:
        return False
    if rule.scope == "lifecycle_transition":
        if event.event_type != "STATE_TRANSITION":
            return False
        if event.from_state == event.to_state:
            return False
        if condition.get("to_state") != event.to_state:
            return False
        if rule.rule_id == "NEW_READY" and event.actionability_after == "BLOCKED":
            return False
        if rule.rule_id == "NEW_EXTENSION" and event.from_state not in {
            "READY",
            "TRIGGERED",
            "CONFIRMED",
        }:
            return False
        return True
    if rule.scope == "actionability_change":
        return (
            event.event_type == "ACTIONABILITY_CHANGE"
            and condition.get("to_actionability") == event.actionability_after
            and event.actionability_before != event.actionability_after
        )
    return False


def _signal_rule_matches(rule: SignalAlertRule, event: SignalChangeEvent) -> bool:
    condition = rule.condition_json or {}
    if rule.scope != "signal_change":
        return False
    signal_keys = set(condition.get("signal_keys") or ())
    configured_signal = condition.get("signal_key")
    if signal_keys and event.signal_key not in signal_keys:
        return False
    if not signal_keys and configured_signal != event.signal_key:
        return False
    if rule.rule_id == "DATA_DEGRADED":
        return "DATA_QUALITY_DEGRADED" in set(event.reason_codes_json or ())
    if rule.rule_id == "SCORE_ACCELERATION":
        return _score_acceleration_matches(condition, event)
    if rule.rule_id == "SECTOR_ACCELERATION":
        return _sector_acceleration_matches(condition, event)
    return True


def _score_acceleration_matches(
    condition: dict[str, Any],
    event: SignalChangeEvent,
) -> bool:
    if not _is_favorable_acceleration(event):
        return False
    window = str(condition.get("velocity_window", 3))
    velocity = ((event.evidence_json or {}).get("velocity") or {}).get(window) or {}
    improvement = _decimal_or_none(velocity.get("normalized_delta"))
    minimum = _decimal_or_none(condition.get("minimum_normalized_delta"))
    if improvement is None or minimum is None or improvement < minimum:
        return False
    thresholds = condition.get("tracking_thresholds") or {}
    threshold = _decimal_or_none(thresholds.get(event.signal_key))
    old_value = _decimal_or_none(velocity.get("old_value"))
    new_value = _decimal_or_none(velocity.get("new_value"))
    return (
        threshold is not None
        and old_value is not None
        and new_value is not None
        and old_value < threshold <= new_value
    )


def _sector_acceleration_matches(
    condition: dict[str, Any],
    event: SignalChangeEvent,
) -> bool:
    improvement = _decimal_or_none(event.normalized_delta)
    minimum = _decimal_or_none(condition.get("minimum_rank_improvement"))
    if improvement is None or minimum is None or improvement < minimum:
        return False
    confidence_order = {"LOW": 0, "NORMAL": 1, "HIGH": 2}
    actual = str((event.evidence_json or {}).get("sector_confidence") or "").upper()
    required = str(condition.get("minimum_sector_confidence") or "NORMAL").upper()
    return confidence_order.get(actual, -1) >= confidence_order.get(required, 1)


def _is_favorable_acceleration(event: SignalChangeEvent) -> bool:
    if event.threshold_direction == "EXIT":
        return False
    normalized = _decimal_or_none(event.normalized_delta)
    if normalized is not None:
        return normalized > 0
    return event.threshold_direction == "ENTER" or "VELOCITY_COMPUTED" in set(
        event.reason_codes_json or ()
    )


def _lifecycle_semantic_key(rule: SignalAlertRule, event: SetupLifecycleEvent) -> str:
    episode = str(event.episode_id or "")
    return f"{rule.rule_id}:{episode}:{event.to_state}:{event.actionability_after}"


def _signal_semantic_key(rule: SignalAlertRule, event: SignalChangeEvent) -> str:
    direction = event.threshold_direction or _signed_direction(event.normalized_delta)
    return f"{rule.rule_id}:{event.signal_key}:{direction}"


def _event_market_regime(
    event: SetupLifecycleEvent,
    snapshot: SetupSignalSnapshot | None,
) -> str | None:
    evidence_value = (event.evidence_json or {}).get("market_regime")
    if evidence_value is not None:
        return str(evidence_value)
    raw = (getattr(snapshot, "signals_json", None) or {}).get("market_regime")
    if isinstance(raw, dict):
        raw = raw.get("value")
    return str(raw) if raw is not None else None


def _market_restrictions_match(rule: SignalAlertRule, evidence: dict[str, Any]) -> bool:
    restrictions = dict(rule.market_restrictions_json or {})
    if not restrictions:
        return True
    market = str(evidence.get("market_regime") or "").strip().upper()
    allowed = {
        str(value).strip().upper()
        for value in restrictions.get("allowed_regimes", restrictions.get("include", ()))
    }
    blocked = {
        str(value).strip().upper()
        for value in restrictions.get("blocked_regimes", restrictions.get("exclude", ()))
    }
    if not market:
        return False
    if allowed and market not in allowed:
        return False
    return market not in blocked


def _signed_direction(value: Any) -> str:
    number = _decimal_or_none(value)
    if number is None:
        return "UNKNOWN"
    if number > 0:
        return "UP"
    if number < 0:
        return "DOWN"
    return "FLAT"


def _source_confidence(event: SignalChangeEvent) -> int:
    evidence = event.evidence_json or {}
    value = evidence.get("confidence_score", evidence.get("current_confidence_score"))
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def _reconstructed_source(evidence: dict[str, Any]) -> bool:
    origin = str(evidence.get("origin_type") or evidence.get("snapshot_origin") or "")
    return origin.upper() == "RECONSTRUCTED"


def _became_blocked(result: EpisodeEvaluationResult) -> bool:
    return (
        result.actionability.actionability is Actionability.BLOCKED
        and result.actionability_before
        in {Actionability.ACTIONABLE.value, Actionability.WATCH_ONLY.value}
        and "GATE_BLOCKED" in result.actionability.reason_codes
    )


def _trading_sessions_between(start_exclusive: date, end_inclusive: date) -> int:
    if end_inclusive <= start_exclusive:
        return 0
    count = 0
    current = start_exclusive
    while True:
        current = next_us_trading_day(current)
        if current > end_inclusive:
            return count
        count += 1


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None
