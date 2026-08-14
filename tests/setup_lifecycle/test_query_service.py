from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

import app.services.setup_lifecycle.query_service as query_module
from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalChangeEvent,
)
from app.services.setup_lifecycle.query_service import (
    SetupLifecycleFilters,
    SetupLifecycleListQuery,
    SetupLifecycleQueryError,
    SetupLifecycleQueryService,
    SetupLifecycleViewScope,
    _encode_timeline_cursor,
    _MarketChangePayloadContext,
    _timeline_cursor_key,
    alert_payload,
    episode_payload,
    lifecycle_event_payload,
    market_change_payload,
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
    event_payload = lifecycle_event_payload(event)
    serialized_snapshot = snapshot_payload(snapshot)
    assert event_payload["record_status"] == "CURRENT"
    assert event_payload["config_hash"] == "hash"
    assert serialized_snapshot["setup_score"] == 8.25
    assert serialized_snapshot["record_status"] == "CURRENT_CANONICAL"
    assert serialized_snapshot["source_data_hash"] == "source"


def test_change_summary_cache_reuses_same_revision_and_invalidates_on_advance(
    monkeypatch,
) -> None:
    query_module._CHANGE_SUMMARY_CACHE.clear()
    calls: list[int] = []

    class _Result:
        def __init__(self, revisions):
            self.revisions = revisions

        def one(self):
            return self.revisions

    class _Database:
        revisions = (10, 20)

        def execute(self, _statement):
            return _Result(self.revisions)

        def get_bind(self):
            return type("Bind", (), {"url": type("Url", (), {"database": "cache-test"})()})()

    def _summary(*_args):
        calls.append(1)
        return ({"material_changes": 2}, 1, 2)

    monkeypatch.setattr(query_module, "_changes_summary", _summary)
    db = _Database()
    filters = SetupLifecycleFilters()
    first = query_module._cached_changes_summary(db, object(), object(), filters=filters)
    second = query_module._cached_changes_summary(db, object(), object(), filters=filters)
    db.revisions = (11, 20)
    third = query_module._cached_changes_summary(db, object(), object(), filters=filters)

    assert first == second == third == ({"material_changes": 2}, 1, 2)
    assert len(calls) == 2


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


def test_historical_scope_requires_run_before_database_access() -> None:
    with pytest.raises(SetupLifecycleQueryError, match="requires run_id"):
        SetupLifecycleQueryService().changes(
            object(),  # type: ignore[arg-type]
            SetupLifecycleListQuery(
                filters=SetupLifecycleFilters(),
                view_scope=SetupLifecycleViewScope.HISTORICAL_RUN,
            ),
        )


def test_change_cursor_is_bound_to_scope_and_filter_set() -> None:
    current_query = SetupLifecycleListQuery(filters=SetupLifecycleFilters(ticker="FIX"))
    cursor = query_module._encode_change_cursor(
        current_query,
        (date(2026, 8, 10), date(2026, 8, 10), 4, 1, 99),
        next_offset=1,
        summary={"material_changes": 1},
        lifecycle_total=1,
        signal_total=0,
    )

    assert query_module._decode_change_cursor(cursor, query=current_query)["offset"] == 1
    with pytest.raises(SetupLifecycleQueryError, match="cursor"):
        query_module._decode_change_cursor(
            cursor,
            query=SetupLifecycleListQuery(filters=SetupLifecycleFilters(ticker="OTHER")),
        )
    with pytest.raises(SetupLifecycleQueryError, match="cursor"):
        query_module._decode_change_cursor(
            cursor,
            query=SetupLifecycleListQuery(
                filters=SetupLifecycleFilters(ticker="FIX", run_id=101),
                view_scope=SetupLifecycleViewScope.HISTORICAL_RUN,
            ),
        )


@pytest.mark.parametrize(
    "filters",
    [
        SetupLifecycleFilters(alert_severity="WARNING"),
        SetupLifecycleFilters(alert_status="REVIEWED"),
        SetupLifecycleFilters(source_type="EVENT"),
        SetupLifecycleFilters(confidence_min=101),
        SetupLifecycleFilters(confidence_min=80, confidence_max=70),
        SetupLifecycleFilters(state_age_min=-1),
        SetupLifecycleFilters(velocity_window=2),
        SetupLifecycleFilters(date_from=date(2026, 8, 2), date_to=date(2026, 8, 1)),
    ],
)
def test_invalid_semantic_filters_fail_before_database_access(
    filters: SetupLifecycleFilters,
) -> None:
    with pytest.raises(SetupLifecycleQueryError):
        SetupLifecycleQueryService().changes(
            object(),  # type: ignore[arg-type]
            SetupLifecycleListQuery(filters=filters),
        )


def test_timeline_cursor_is_opaque_deterministic_and_validated() -> None:
    cursor = _encode_timeline_cursor(date(2026, 8, 1), 2, 99)

    assert cursor != "2026-08-01:2:99"
    assert _timeline_cursor_key(cursor) == (date(2026, 8, 1), 2, 99)
    with pytest.raises(SetupLifecycleQueryError, match="timeline cursor"):
        _timeline_cursor_key("not-a-valid-cursor")


def test_market_change_projection_uses_snapshot_technical_velocity_for_every_row() -> None:
    current = SetupSignalSnapshot(
        id=30,
        run_id=7,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 13),
        calculated_at=datetime(2026, 8, 13, 21, tzinfo=UTC),
        origin_type="LIVE_RUN",
        engine_version="slse-test",
        config_version="test-v2",
        config_hash="config-hash",
        source_data_hash="source-current",
        schema_version="v2",
        is_canonical=True,
        primary_setup_family="BREAKOUT",
        data_quality_label="HIGH",
        dual_score=Decimal("8.1"),
        setup_score=Decimal("7.8"),
        close_price=Decimal("98"),
        trigger_price=Decimal("100"),
        distance_to_pivot_pct=Decimal("2.0"),
        signals_json={
            "technical_score": {
                "value": "8.1",
                "velocity": {
                    "1": {"target_date": "2026-08-12", "normalized_delta": "0.6"},
                    "3": {"target_date": "2026-08-10", "normalized_delta": "1.1"},
                },
            },
            "setup_score": {
                "value": "7.8",
                "velocity": {
                    "1": {"target_date": "2026-08-12", "normalized_delta": "0.2"},
                    "3": {"target_date": "2026-08-10", "normalized_delta": "0.4"},
                },
            },
        },
        debug_json={
            "trigger_reference": {
                "reference_type": "BREAKOUT_PIVOT",
                "reference_price": "100",
                "source_path": "technical_scores.v4_debug_json.box.box_high",
                "source_record_id": 301,
            }
        },
        warning_flags_json=[],
    )
    previous = SetupSignalSnapshot(
        id=29,
        run_id=6,
        ticker="MSFT",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 12),
        calculated_at=datetime(2026, 8, 12, 21, tzinfo=UTC),
        origin_type="LIVE_RUN",
        engine_version="slse-test",
        config_version="test-v2",
        config_hash="config-hash",
        source_data_hash="source-previous",
        schema_version="v2",
        is_canonical=True,
        data_quality_label="HIGH",
        dual_score=Decimal("7.5"),
        setup_score=Decimal("7.6"),
        signals_json={},
        warning_flags_json=[],
    )
    lifecycle = SetupLifecycleEvent(
        id=41,
        snapshot_id=current.id,
        ticker="MSFT",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_date=current.data_as_of_date,
        event_type="STATE_TRANSITION",
        from_state="TIGHTENING",
        to_state="READY",
        severity="ACTIONABLE",
        source_event_key="lifecycle",
        engine_version="slse-test",
        config_version="test-v2",
        config_hash="config-hash",
        reason_codes_json=["PIVOT_DISTANCE_READY"],
        evidence_json={},
    )
    signal = SignalChangeEvent(
        id=42,
        previous_snapshot_id=previous.id,
        current_snapshot_id=current.id,
        ticker="MSFT",
        timeframe="1d",
        effective_date=current.data_as_of_date,
        category="SCORE",
        signal_key="setup_score",
        value_type="float",
        old_value_json={"value": 7.6},
        new_value_json={"value": 7.8},
        direction="higher_is_better",
        severity="NOTABLE",
        signal_definition_version="test-v2",
        source_event_key="signal",
        config_hash="config-hash",
        reason_codes_json=["VALUE_CHANGED"],
        evidence_json={"velocity": {"3": {"normalized_delta": "99"}}},
    )
    context = _MarketChangePayloadContext(
        current_snapshots={current.id: current},
        explicit_previous_snapshots={previous.id: previous},
        previous_by_current_snapshot={current.id: previous},
        lifecycle_by_snapshot={current.id: lifecycle},
        episodes={},
    )

    lifecycle_payload = market_change_payload(
        object(),
        lifecycle_event=lifecycle,
        context=context,  # type: ignore[arg-type]
    )
    signal_payload = market_change_payload(
        object(),
        signal_change_event=signal,
        context=context,  # type: ignore[arg-type]
    )

    for payload in (lifecycle_payload, signal_payload):
        assert payload["score_velocity_1d"] == 0.6
        assert payload["score_velocity_3d"] == 1.1
        assert payload["setup_score_velocity_3d"] == 0.4
        assert payload["trigger_distance_pct"] == 2.0
        assert payload["trigger_reference_type"] == "BREAKOUT_PIVOT"
        assert payload["trigger_reference_price"] == 100.0
