from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
)
from app.services.setup_lifecycle.query_service import (
    SetupLifecycleFilters,
    SetupLifecycleListQuery,
    SetupLifecycleQueryError,
    SetupLifecycleQueryService,
    alert_payload,
    episode_payload,
    lifecycle_event_payload,
    snapshot_payload,
)


def test_payload_helpers_serialize_dates_decimals_and_json_defaults() -> None:
    event = SetupLifecycleEvent(
        id=1,
        episode_id=2,
        ticker="MSFT",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_date=date(2026, 8, 1),
        event_type="STATE_TRANSITION",
        to_state="TRIGGERED",
        to_phase="BREAKOUT_TRIGGERED",
        actionability_after="ACTIONABLE",
        confidence_score=91,
        confidence_label="HIGH",
        severity="ACTIONABLE",
        source_event_key="event-key",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        reason_codes_json=["PRICE_TRIGGER_CONFIRMED"],
        evidence_json={"velocity": 0.5},
    )
    snapshot = SetupSignalSnapshot(
        id=3,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 1),
        calculated_at=datetime(2026, 8, 1, 21, tzinfo=UTC),
        origin_type="RUN_CAPTURE",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        source_data_hash="source",
        schema_version="v1",
        is_canonical=True,
        data_quality_label="HIGH",
        setup_score=Decimal("8.25"),
        warning_flags_json=["MISSING_SECTOR_ROTATION"],
        source_lineage_json={"source": "test"},
    )

    assert lifecycle_event_payload(event)["reason_codes"] == ["PRICE_TRIGGER_CONFIRMED"]
    assert snapshot_payload(snapshot)["setup_score"] == 8.25


def test_episode_and_alert_payloads_are_dashboard_ready() -> None:
    episode = SetupLifecycleEpisode(
        id=4,
        ticker="MSFT",
        timeframe="1d",
        setup_family="BREAKOUT",
        status="ACTIVE",
        opened_on=date(2026, 8, 1),
        current_as_of_date=date(2026, 8, 1),
        last_observed_on=date(2026, 8, 1),
        missing_observation_sessions=0,
        current_state="READY",
        current_phase="PIVOT_READY",
        state_entered_on=date(2026, 8, 1),
        state_age_sessions=0,
        current_actionability="WATCH_ONLY",
        confidence_score=82,
        confidence_label="NORMAL",
        engine_version="slse-1.0.0",
        config_version="v1",
        config_hash="hash",
        metadata_json={"setup_score": 7.9},
    )
    alert = SignalAlertEvent(
        id=5,
        alert_rule_id=10,
        ticker="MSFT",
        timeframe="1d",
        effective_date=date(2026, 8, 1),
        event_key="alert-key",
        source_event_key="event-key",
        status="UNREAD",
        severity="ACTIONABLE",
        reason_codes_json=["BECAME_ACTIONABLE"],
    )

    assert episode_payload(episode)["metadata"] == {"setup_score": 7.9}
    assert alert_payload(alert)["status"] == "UNREAD"


def test_invalid_cursor_fails_before_database_access() -> None:
    with pytest.raises(SetupLifecycleQueryError, match="cursor"):
        SetupLifecycleQueryService().changes(
            object(),  # type: ignore[arg-type]
            SetupLifecycleListQuery(
                filters=SetupLifecycleFilters(),
                cursor="not-an-offset",
            ),
        )
