from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.models.tables import SetupLifecycleEvent, SignalAlertEvent
from app.services.setup_lifecycle.export_service import setup_lifecycle_point_in_time_features
from app.services.winner_probability.feature_extractor import _canonical_feature_json


def test_owpe_feature_export_uses_immutable_events_and_excludes_future() -> None:
    current = SetupLifecycleEvent(
        id=1,
        episode_id=12,
        ticker="MSFT",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_date=date(2026, 8, 1),
        event_type="STATE_TRANSITION",
        to_state="TRIGGERED",
        to_phase="BREAKOUT_TRIGGERED",
        state_age_before=2,
        actionability_after="ACTIONABLE",
        confidence_score=91,
        confidence_label="HIGH",
        severity="ACTIONABLE",
        source_event_key="event-key",
        is_current_version=True,
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        evidence_json={"velocity": 0.4},
    )
    future = SetupLifecycleEvent(
        id=2,
        episode_id=13,
        ticker="MSFT",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_date=date(2026, 8, 2),
        event_type="STATE_TRANSITION",
        to_state="FAILED",
        to_phase="FAILED",
        actionability_after="BLOCKED",
        confidence_score=50,
        confidence_label="LOW",
        severity="RISK",
        source_event_key="future-key",
        is_current_version=True,
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        evidence_json={},
    )
    alert = SignalAlertEvent(
        id=7,
        alert_rule_id=1,
        lifecycle_event_id=1,
        ticker="MSFT",
        timeframe="1d",
        effective_date=date(2026, 8, 1),
        event_key="alert-key",
        source_event_key="event-key",
        status="UNREAD",
        severity="ACTIONABLE",
    )

    payload = setup_lifecycle_point_in_time_features(
        FakeBridgeDb(events=[future, current], alerts=[alert]),
        ticker="MSFT",
        as_of_date=date(2026, 8, 1),
    )

    assert payload["setup_lifecycle_state"] == "TRIGGERED"
    assert payload["setup_lifecycle_event_id"] == 1
    assert payload["setup_lifecycle_alert_ids"] == [7]
    assert payload["setup_lifecycle_signal_velocity"] == 0.4


def test_winner_feature_json_accepts_setup_lifecycle_payload() -> None:
    features = _canonical_feature_json(
        config=SimpleNamespace(
            engine=SimpleNamespace(calculation_version="winner-v1"),
            config_hash="winner-hash",
            feature_schema=SimpleNamespace(version="v1"),
        ),
        ticker="MSFT",
        raw_row=SimpleNamespace(sector="Technology", sector_canonical="Technology"),
        fundamental=None,
        technical=SimpleNamespace(dual_score=8.1, action_bias="Constructive"),
        combined=SimpleNamespace(final_score=8.5, earnings_risk_level="LOW"),
        ranking=None,
        market=None,
        sector_row=None,
        run_context=SimpleNamespace(upload_run=SimpleNamespace(filename="run.csv", notes=None)),
        prediction_as_of=date(2026, 8, 1),
        planned_entry=date(2026, 8, 3),
        setup_lifecycle_features={
            "setup_lifecycle_state": "TRIGGERED",
            "setup_lifecycle_episode_id": 12,
            "ignored": "not copied",
        },
    )

    assert features["setup_lifecycle_state"] == "TRIGGERED"
    assert features["setup_lifecycle_episode_id"] == 12
    assert "ignored" not in features


class FakeBridgeDb:
    def __init__(self, *, events: list[SetupLifecycleEvent], alerts: list[SignalAlertEvent]):
        self.events = events
        self.alerts = alerts

    def scalar(self, _statement):
        eligible = [
            event
            for event in self.events
            if event.effective_date <= date(2026, 8, 1)
            and event.evidence_json.get("origin_type") != "RECONSTRUCTED"
        ]
        return sorted(eligible, key=lambda event: (event.effective_date, event.id), reverse=True)[0]

    def scalars(self, _statement):
        return self.alerts
