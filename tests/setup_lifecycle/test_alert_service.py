from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvent,
    SignalAlertEvent,
    SignalAlertRule,
    SignalChangeEvent,
)
from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
from app.services.setup_lifecycle.dtos import ActionabilityDecision, LifecycleDecision
from app.services.setup_lifecycle.enums import (
    Actionability,
    ConfidenceLabel,
    LifecycleState,
    SetupFamily,
)
from app.services.setup_lifecycle.episode_service import EpisodeEvaluationResult
from app.services.setup_lifecycle.repository import SetupLifecycleRepository


def test_seed_builtin_rules_persists_all_configured_alert_rules() -> None:
    repository = FakeAlertRepository()
    service = SetupLifecycleAlertService(repository=repository)

    rules = service.seed_builtin_rules(db=object())

    assert {rule.rule_id for rule in rules} == {
        "NEW_READY",
        "NEW_TRIGGER",
        "NEW_CONFIRMATION",
        "NEW_FAILURE",
        "NEW_EXTENSION",
        "SCORE_ACCELERATION",
        "SECTOR_ACCELERATION",
        "GATE_BLOCKED",
        "DATA_DEGRADED",
    }


def test_new_ready_creates_one_actionable_alert_and_retry_dedupes() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)
    event = _lifecycle_event(
        event_id=10,
        episode_id=501,
        to_state=LifecycleState.READY,
        severity="ACTIONABLE",
        confidence_score=80,
        source_event_key="ready-source",
    )

    first = service.evaluate_lifecycle_event(db=object(), event=event)
    retry = service.evaluate_lifecycle_event(db=object(), event=event)

    assert first.created == 1
    assert retry.created == 0
    assert retry.suppressed == 1
    assert len(repository.alerts) == 1
    assert repository.alerts[0].severity == "ACTIONABLE"


def test_new_ready_requires_real_transition_and_nonblocked_actionability() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)
    blocked = _lifecycle_event(
        event_id=13,
        episode_id=501,
        to_state=LifecycleState.READY,
        source_event_key="ready-blocked",
    )
    blocked.actionability_after = Actionability.BLOCKED.value
    unchanged = _lifecycle_event(
        event_id=14,
        episode_id=501,
        to_state=LifecycleState.READY,
        source_event_key="ready-unchanged",
    )
    unchanged.from_state = LifecycleState.READY.value
    opened = _lifecycle_event(
        event_id=15,
        episode_id=501,
        to_state=LifecycleState.READY,
        source_event_key="ready-opened",
    )
    opened.event_type = "EPISODE_OPENED"
    opened.from_state = None

    assert service.evaluate_lifecycle_event(object(), blocked).created == 0
    assert service.evaluate_lifecycle_event(object(), unchanged).created == 0
    assert service.evaluate_lifecycle_event(object(), opened).created == 0


def test_ready_cooldown_suppresses_same_episode_but_allows_new_episode() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)

    service.evaluate_lifecycle_event(
        db=object(),
        event=_lifecycle_event(
            event_id=10,
            episode_id=501,
            to_state=LifecycleState.READY,
            effective_date=date(2026, 8, 3),
            source_event_key="ready-1",
        ),
    )
    repeated = service.evaluate_lifecycle_event(
        db=object(),
        event=_lifecycle_event(
            event_id=11,
            episode_id=501,
            to_state=LifecycleState.READY,
            effective_date=date(2026, 8, 4),
            source_event_key="ready-2",
        ),
    )
    new_episode = service.evaluate_lifecycle_event(
        db=object(),
        event=_lifecycle_event(
            event_id=12,
            episode_id=777,
            to_state=LifecycleState.READY,
            effective_date=date(2026, 8, 4),
            source_event_key="ready-3",
        ),
    )

    assert repeated.created == 0
    assert repeated.suppressed == 1
    assert new_episode.created == 1
    assert len(repository.alerts) == 2


def test_new_failure_creates_risk_alert() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)

    result = service.evaluate_lifecycle_event(
        db=object(),
        event=_lifecycle_event(
            event_id=20,
            episode_id=501,
            to_state=LifecycleState.FAILED,
            severity="RISK",
            confidence_score=10,
            source_event_key="failed-source",
        ),
    )

    assert result.created == 1
    assert repository.alerts[0].severity == "RISK"
    assert repository.alerts[0].reason_codes_json == ["NEW_FAILURE_ALERT"]


def test_new_extension_requires_prior_ready_triggered_or_confirmed_state() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)
    valid = _lifecycle_event(
        event_id=21,
        episode_id=501,
        to_state=LifecycleState.EXTENDED,
        source_event_key="extended-valid",
        severity="RISK",
    )
    valid.from_state = LifecycleState.TRIGGERED.value
    invalid = _lifecycle_event(
        event_id=22,
        episode_id=777,
        to_state=LifecycleState.EXTENDED,
        source_event_key="extended-invalid",
        severity="RISK",
    )
    invalid.from_state = LifecycleState.DEVELOPING.value

    assert service.evaluate_lifecycle_event(object(), valid).created == 1
    assert service.evaluate_lifecycle_event(object(), invalid).created == 0


def test_gate_blocked_alert_does_not_require_lifecycle_state_mutation() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)
    episode = _episode()
    result = EpisodeEvaluationResult(
        episode=episode,
        decision=_decision(LifecycleState.READY),
        actionability=ActionabilityDecision(
            actionability=Actionability.BLOCKED,
            reason_codes=("GATE_BLOCKED",),
            blockers=("IMMINENT_EARNINGS",),
        ),
        lifecycle_event=None,
        actionability_before=Actionability.ACTIONABLE.value,
    )

    alerts = service.evaluate_episode_result(db=object(), result=result, evaluation_run_id=88)

    assert alerts.created == 1
    assert repository.alerts[0].lifecycle_event_id is None
    assert repository.alerts[0].evidence_json["blockers"] == ["IMMINENT_EARNINGS"]


@pytest.mark.parametrize(
    ("before", "expected"),
    [
        (None, 0),
        (Actionability.BLOCKED.value, 0),
        (Actionability.LOW_CONFIDENCE.value, 0),
        (Actionability.ACTIONABLE.value, 1),
        (Actionability.WATCH_ONLY.value, 1),
    ],
)
def test_gate_blocked_truth_table_only_allows_srs_predecessors(before, expected) -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)
    result = EpisodeEvaluationResult(
        episode=_episode(),
        decision=_decision(LifecycleState.READY),
        actionability=ActionabilityDecision(
            actionability=Actionability.BLOCKED,
            reason_codes=("GATE_BLOCKED",),
            blockers=("MARKET_POLICY_BLOCK",),
        ),
        actionability_before=before,
    )

    outcome = service.evaluate_episode_result(db=object(), result=result, evaluation_run_id=88)

    assert outcome.created == expected


def test_cooldown_suppresses_repeated_score_acceleration() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)

    first = service.evaluate_signal_change_events(
        db=object(),
        events=[
            _signal_change(
                event_id=30,
                signal_key="technical_score",
                source_event_key="score-1",
                effective_date=date(2026, 8, 3),
                normalized_delta=Decimal("1"),
            )
        ],
    )
    repeated = service.evaluate_signal_change_events(
        db=object(),
        events=[
            _signal_change(
                event_id=31,
                signal_key="technical_score",
                source_event_key="score-2",
                effective_date=date(2026, 8, 4),
                normalized_delta=Decimal("1"),
            )
        ],
    )

    assert first.created == 1
    assert repeated.created == 0
    assert repeated.suppressed == 1


def test_score_acceleration_requires_window_amount_crossing_and_real_confidence() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)
    valid = _signal_change(
        event_id=60,
        signal_key="technical_score",
        source_event_key="score-valid",
    )
    missing_confidence = _signal_change(
        event_id=61,
        signal_key="technical_score",
        source_event_key="score-no-confidence",
    )
    missing_confidence.evidence_json.pop("confidence_score")
    too_small = _signal_change(
        event_id=62,
        signal_key="technical_score",
        source_event_key="score-small",
    )
    too_small.evidence_json["velocity"]["3"]["normalized_delta"] = "0.4"
    no_tracking_cross = _signal_change(
        event_id=63,
        signal_key="technical_score",
        source_event_key="score-no-cross",
    )
    no_tracking_cross.evidence_json["velocity"]["3"].update(
        {"old_value": 7.1, "new_value": 7.8}
    )

    assert service.evaluate_signal_change_events(object(), [valid]).created == 1
    assert service.evaluate_signal_change_events(object(), [missing_confidence]).created == 0
    assert service.evaluate_signal_change_events(object(), [too_small]).created == 0
    assert service.evaluate_signal_change_events(object(), [no_tracking_cross]).created == 0


def test_sector_acceleration_uses_improvement_and_sector_confidence() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)
    valid = _signal_change(
        event_id=70,
        signal_key="sector_rank",
        source_event_key="sector-valid",
        normalized_delta=Decimal("4"),
    )
    low_confidence = _signal_change(
        event_id=71,
        signal_key="sector_rank",
        source_event_key="sector-low-confidence",
        normalized_delta=Decimal("4"),
    )
    low_confidence.evidence_json["sector_confidence"] = "LOW"
    wrong_direction = _signal_change(
        event_id=72,
        signal_key="sector_rank",
        source_event_key="sector-worse",
        normalized_delta=Decimal("-4"),
    )

    assert service.evaluate_signal_change_events(object(), [valid]).created == 1
    assert service.evaluate_signal_change_events(object(), [low_confidence]).created == 0
    assert service.evaluate_signal_change_events(object(), [wrong_direction]).created == 0


def test_alert_market_restrictions_are_enforced() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    rule = next(rule for rule in repository.rules if rule.rule_id == "SCORE_ACCELERATION")
    rule.market_restrictions_json = {"allowed_regimes": ["RISK_ON"]}
    service = SetupLifecycleAlertService(repository=repository)
    allowed = _signal_change(
        event_id=73, signal_key="technical_score", source_event_key="market-allowed"
    )
    blocked = _signal_change(
        event_id=74, signal_key="technical_score", source_event_key="market-blocked"
    )
    blocked.evidence_json["market_regime"] = "RISK_OFF"

    assert service.evaluate_signal_change_events(object(), [allowed]).created == 1
    outcome = service.evaluate_signal_change_events(object(), [blocked])
    assert outcome.created == 0
    assert outcome.warning_codes == ("MARKET_RESTRICTION",)


def test_data_degraded_creates_risk_alert() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)

    result = service.evaluate_signal_change_events(
        db=object(),
        events=[
            _signal_change(
                event_id=40,
                signal_key="data_quality",
                source_event_key="quality-1",
                severity="RISK",
                reason_codes=("DATA_QUALITY_DEGRADED",),
                normalized_delta=Decimal("-1"),
            )
        ],
    )

    assert result.created == 1
    assert repository.alerts[0].severity == "RISK"
    assert repository.alerts[0].reason_codes_json == ["DATA_DEGRADED_ALERT"]


def test_acknowledge_and_dismiss_only_update_alert_user_state() -> None:
    repository = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repository)
    source_event = _lifecycle_event(
        event_id=50,
        episode_id=501,
        to_state=LifecycleState.READY,
        source_event_key="ack-source",
    )
    service.evaluate_lifecycle_event(db=object(), event=source_event)
    alert_id = repository.alerts[0].id

    acknowledged = service.acknowledge_alert(db=object(), alert_id=alert_id)
    dismissed = service.dismiss_alert(db=object(), alert_id=alert_id)

    assert acknowledged is dismissed
    assert dismissed.status == "DISMISSED"
    assert dismissed.acknowledged_at is not None
    assert dismissed.dismissed_at is not None
    assert source_event.to_state == LifecycleState.READY.value
    assert source_event.source_event_key == "ack-source"


class FakeAlertRepository:
    normalize_ticker = staticmethod(SetupLifecycleRepository.normalize_ticker)
    stable_key = staticmethod(SetupLifecycleRepository.stable_key)
    alert_event_key = staticmethod(SetupLifecycleRepository.alert_event_key)

    def __init__(self) -> None:
        self.rules: list[SignalAlertRule] = []
        self.alerts: list[SignalAlertEvent] = []
        self.next_rule_id = 1
        self.next_alert_id = 100

    @classmethod
    def with_seeded_rules(cls) -> FakeAlertRepository:
        repository = cls()
        SetupLifecycleAlertService(repository=repository).seed_builtin_rules(db=object())
        return repository

    def upsert_alert_rule(self, _db, **kwargs):
        existing = next(
            (rule for rule in self.rules if rule.rule_id == kwargs["rule_id"]),
            None,
        )
        rule = existing or SignalAlertRule(id=self.next_rule_id, rule_id=kwargs["rule_id"])
        if existing is None:
            self.next_rule_id += 1
            self.rules.append(rule)
        rule.enabled = kwargs["enabled"]
        rule.severity = kwargs["severity"]
        rule.scope = kwargs["scope"]
        rule.setup_family = kwargs["setup_family"]
        rule.cooldown_sessions = kwargs["cooldown_sessions"]
        rule.minimum_confidence = kwargs["minimum_confidence"]
        rule.config_version = kwargs["config_version"]
        rule.condition_json = kwargs["condition"]
        rule.market_restrictions_json = dict(kwargs["market_restrictions"] or {})
        return rule

    def alert_rules(self, _db, *, enabled_only=True):
        if enabled_only:
            return [rule for rule in self.rules if rule.enabled]
        return list(self.rules)

    def add_alert_event(self, _db, event):
        existing = next((row for row in self.alerts if row.event_key == event.event_key), None)
        if existing is not None:
            return existing
        event.id = self.next_alert_id
        self.next_alert_id += 1
        self.alerts.append(event)
        return event

    def recent_alert_events(
        self,
        _db,
        *,
        alert_rule_id,
        ticker,
        timeframe,
        since_date,
        semantic_key=None,
    ):
        rows = [
            alert
            for alert in self.alerts
            if alert.alert_rule_id == alert_rule_id
            and alert.ticker == self.normalize_ticker(ticker)
            and alert.timeframe == timeframe
            and alert.effective_date >= since_date
        ]
        if semantic_key is not None:
            rows = [
                alert
                for alert in rows
                if (alert.evidence_json or {}).get("semantic_key") == semantic_key
            ]
        return rows

    def acknowledge_alert_event(self, _db, alert_id):
        alert = self._alert(alert_id)
        alert.status = "ACKNOWLEDGED"
        alert.acknowledged_at = object()
        return alert

    def dismiss_alert_event(self, _db, alert_id):
        alert = self._alert(alert_id)
        alert.status = "DISMISSED"
        alert.dismissed_at = object()
        return alert

    def _alert(self, alert_id):
        return next(alert for alert in self.alerts if alert.id == alert_id)


def _lifecycle_event(
    *,
    event_id: int,
    episode_id: int,
    to_state: LifecycleState,
    source_event_key: str,
    effective_date: date = date(2026, 8, 3),
    severity: str = "ACTIONABLE",
    confidence_score: int = 80,
) -> SetupLifecycleEvent:
    return SetupLifecycleEvent(
        id=event_id,
        episode_id=episode_id,
        evaluation_run_id=7,
        snapshot_id=99,
        ticker="MSFT",
        timeframe="1d",
        setup_family=SetupFamily.BREAKOUT.value,
        effective_date=effective_date,
        event_type="STATE_TRANSITION",
        from_state=LifecycleState.DEVELOPING.value,
        to_state=to_state.value,
        from_phase="BASE_FORMING",
        to_phase="PIVOT_READY" if to_state is LifecycleState.READY else to_state.value,
        state_age_before=1,
        immediate_transition=True,
        actionability_before="WATCH_ONLY",
        actionability_after="ACTIONABLE" if severity == "ACTIONABLE" else "BLOCKED",
        confidence_score=confidence_score,
        confidence_label="NORMAL",
        severity=severity,
        source_event_key=source_event_key,
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        reason_codes_json=[f"{to_state.value}_TEST"],
        evidence_json={},
        warning_flags_json=[],
    )


def _signal_change(
    *,
    event_id: int,
    signal_key: str,
    source_event_key: str,
    effective_date: date = date(2026, 8, 3),
    severity: str = "NOTABLE",
    reason_codes: tuple[str, ...] = ("THRESHOLD_CROSSED",),
    normalized_delta: Decimal = Decimal("1"),
) -> SignalChangeEvent:
    return SignalChangeEvent(
        id=event_id,
        evaluation_run_id=7,
        episode_id=501,
        ticker="MSFT",
        timeframe="1d",
        effective_date=effective_date,
        category="SCORE",
        signal_key=signal_key,
        value_type="float",
        old_value_json={"value": 6.5},
        new_value_json={"value": 7.5},
        normalized_delta=normalized_delta,
        direction="higher_is_better",
        threshold_name="crossing_8",
        threshold_direction="ENTER" if normalized_delta > 0 else "EXIT",
        severity=severity,
        signal_definition_version="v1",
        source_event_key=source_event_key,
        config_hash="hash",
        reason_codes_json=list(reason_codes),
        evidence_json={
            "confidence_score": 90,
            "sector_confidence": "HIGH",
            "market_regime": "RISK_ON",
            "velocity": {
                "3": {
                    "old_value": 6.5,
                    "new_value": 7.5,
                    "normalized_delta": "1.0",
                }
            },
        },
    )


def _episode() -> SetupLifecycleEpisode:
    return SetupLifecycleEpisode(
        id=501,
        ticker="MSFT",
        timeframe="1d",
        setup_family=SetupFamily.BREAKOUT.value,
        status="ACTIVE",
        opened_on=date(2026, 8, 1),
        current_as_of_date=date(2026, 8, 3),
        last_observed_on=date(2026, 8, 3),
        current_state=LifecycleState.READY.value,
        current_phase="PIVOT_READY",
        state_entered_on=date(2026, 8, 3),
        state_age_sessions=0,
        current_actionability=Actionability.BLOCKED.value,
        confidence_score=80,
        confidence_label="NORMAL",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        metadata_json={"setup_score": 7.8},
    )


def _decision(state: LifecycleState) -> LifecycleDecision:
    return LifecycleDecision(
        setup_family=SetupFamily.BREAKOUT,
        phase_code="PIVOT_READY",
        previous_state=LifecycleState.DEVELOPING,
        proposed_state=state,
        actionability_candidate=Actionability.ACTIONABLE,
        confidence_score=80,
        confidence_label=ConfidenceLabel.NORMAL,
        reason_codes=("PIVOT_READY",),
        evidence={},
        immediate_transition=True,
    )
