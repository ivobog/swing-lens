from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalAlertRule,
    SignalChangeEvent,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.e2e
def test_populated_market_changes_and_alert_center_contract(
    page: Page,
    live_server_url: str,
    live_server_database_url: str,
) -> None:
    engine = create_engine(live_server_database_url)
    with Session(engine) as db:
        _seed_vertical_fixture(db)

    page.goto(f"{live_server_url}/setup-lifecycle?as_of=2026-08-10")
    expect(page.get_by_role("heading", name="Market Changes")).to_be_visible()
    expect(page.get_by_text("LIFECYCLE_EVENT", exact=True).last).to_be_visible()
    expect(page.get_by_text("SIGNAL_CHANGE_EVENT", exact=True).last).to_be_visible()
    expect(page.get_by_text("READY to TRIGGERED", exact=True).first).to_be_visible()
    expect(
        page.locator(".metric", has_text="Total Changes").get_by_text("2", exact=True)
    ).to_be_visible()
    page.screenshot(
        path=REPO_ROOT / "output" / "playwright" / "slse-market-changes-populated.png",
        full_page=True,
    )

    page.goto(f"{live_server_url}/setup-lifecycle/alerts")
    expect(page.get_by_role("heading", name="Alert Center")).to_be_visible()
    expect(page.get_by_text("NEW_TRIGGER", exact=True).last).to_be_visible()
    expect(page.get_by_text("SCORE_ACCELERATION", exact=True).last).to_be_visible()
    expect(page.get_by_text("NOTABLE", exact=True).last).to_be_visible()
    expect(page.get_by_text("SIGNAL_CHANGE_EVENT", exact=True).last).to_be_visible()
    expect(page.get_by_text("UNREAD", exact=True).last).to_be_visible()
    page.screenshot(
        path=REPO_ROOT / "output" / "playwright" / "slse-alert-center-populated.png",
        full_page=True,
    )

    trigger_row = page.locator("tr", has_text="NEW_TRIGGER")
    trigger_row.get_by_role("button", name="Acknowledge").click()
    expect(trigger_row.locator("[data-slse-alert-status]")).to_have_text("ACKNOWLEDGED")
    page.screenshot(
        path=REPO_ROOT / "output" / "playwright" / "slse-alert-acknowledged.png",
        full_page=True,
    )

    score_row = page.locator("tr", has_text="SCORE_ACCELERATION")
    score_row.get_by_role("button", name="Dismiss").click()
    expect(score_row.locator("[data-slse-alert-status]")).to_have_text("DISMISSED")

    with Session(engine) as db:
        statuses = dict(
            db.execute(select(SignalAlertEvent.event_key, SignalAlertEvent.status)).all()
        )
        assert statuses == {
            "score-alert": "DISMISSED",
            "trigger-alert": "ACKNOWLEDGED",
        }

    page.goto(
        f"{live_server_url}/setup-lifecycle/alerts?alert_type=SCORE_ACCELERATION"
    )
    expect(page.get_by_text("SCORE_ACCELERATION", exact=True).last).to_be_visible()
    expect(page.locator("tbody tr[data-slse-alert-row]")).to_have_count(1)
    assert page.request.get(
        f"{live_server_url}/api/setup-lifecycle/alerts/export.json"
        "?alert_type=SCORE_ACCELERATION"
    ).json()["items"][0]["severity"] == "NOTABLE"
    engine.dispose()


def _seed_vertical_fixture(db: Session) -> None:
    evaluation = SetupLifecycleEvaluationRun(
        mode="LIVE",
        status="COMPLETED",
        engine_version="slse-test",
        config_version="test-v2",
        config_hash="config-hash",
    )
    db.add(evaluation)
    db.flush()
    previous = _snapshot(date(2026, 8, 7), Decimal("7.6"), 9, evaluation.id, 1)
    current = _snapshot(date(2026, 8, 10), Decimal("8.1"), 5, evaluation.id, 2)
    db.add_all([previous, current])
    db.flush()
    episode = SetupLifecycleEpisode(
        ticker="FIX",
        timeframe="1d",
        setup_family="BREAKOUT",
        status="ACTIVE",
        opened_on=date(2026, 8, 7),
        current_as_of_date=date(2026, 8, 10),
        last_observed_on=date(2026, 8, 10),
        current_state="TRIGGERED",
        current_phase="BREAKOUT",
        state_entered_on=date(2026, 8, 10),
        state_age_sessions=0,
        current_actionability="ACTIONABLE",
        confidence_score=86,
        confidence_label="HIGH",
        opening_snapshot_id=previous.id,
        current_snapshot_id=current.id,
        opening_evaluation_id=evaluation.id,
        engine_version="slse-test",
        config_version="test-v2",
        config_hash="config-hash",
        metadata_json={"blockers": []},
    )
    db.add(episode)
    db.flush()
    lifecycle = SetupLifecycleEvent(
        episode_id=episode.id,
        evaluation_run_id=evaluation.id,
        snapshot_id=current.id,
        ticker="FIX",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_date=date(2026, 8, 10),
        event_type="STATE_TRANSITION",
        from_state="READY",
        to_state="TRIGGERED",
        from_phase="PIVOT_READY",
        to_phase="BREAKOUT",
        state_age_before=2,
        actionability_before="ACTIONABLE",
        actionability_after="ACTIONABLE",
        confidence_score=86,
        confidence_label="HIGH",
        severity="ACTIONABLE",
        source_event_key="lifecycle-source",
        engine_version="slse-test",
        config_version="test-v2",
        config_hash="config-hash",
        reason_codes_json=["CLOSE_ABOVE_TRIGGER"],
        evidence_json={"blockers": []},
    )
    change = SignalChangeEvent(
        evaluation_run_id=evaluation.id,
        episode_id=episode.id,
        previous_snapshot_id=previous.id,
        current_snapshot_id=current.id,
        ticker="FIX",
        timeframe="1d",
        effective_date=date(2026, 8, 10),
        category="SCORE",
        signal_key="technical_score",
        value_type="float",
        old_value_json={"value": 7.6},
        new_value_json={"value": 8.1},
        delta_numeric=Decimal("0.5"),
        normalized_delta=Decimal("0.5"),
        direction="higher_is_better",
        threshold_name="crossing_8",
        threshold_direction="ENTER",
        severity="NOTABLE",
        signal_definition_version="test-v2",
        source_event_key="change-source",
        config_hash="config-hash",
        reason_codes_json=["THRESHOLD_CROSSED"],
        evidence_json={
            "confidence_score": 86,
            "velocity": {"1": {"normalized_delta": 0.5}, "3": {"normalized_delta": 0.9}},
        },
    )
    db.add_all([lifecycle, change])
    db.flush()
    trigger_rule = _rule("NEW_TRIGGER", "ACTIONABLE", "lifecycle_transition")
    score_rule = _rule("SCORE_ACCELERATION", "NOTABLE", "signal_change")
    db.add_all([trigger_rule, score_rule])
    db.flush()
    db.add_all(
        [
            _alert(trigger_rule.id, "trigger-alert", "ACTIONABLE", evaluation.id, lifecycle.id),
            _alert(
                score_rule.id,
                "score-alert",
                "NOTABLE",
                evaluation.id,
                signal_change_event_id=change.id,
            ),
        ]
    )
    db.commit()


def _snapshot(
    as_of: date, score: Decimal, sector_rank: int, evaluation_id: int, identity: int
) -> SetupSignalSnapshot:
    return SetupSignalSnapshot(
        evaluation_run_id=evaluation_id,
        source_run_id_text="fixture-run",
        ticker="FIX",
        company_name="Example Inc.",
        sector="Industrials",
        timeframe="1d",
        data_as_of_date=as_of,
        calculated_at=datetime(2026, 8, 10, 21, identity, tzinfo=UTC),
        origin_type="LIVE_RUN",
        engine_version="slse-test",
        config_version="test-v2",
        config_hash="config-hash",
        source_data_hash=f"source-{identity}",
        schema_version="v2",
        is_canonical=True,
        primary_setup_family="BREAKOUT",
        primary_phase="PIVOT_READY" if identity == 1 else "BREAKOUT",
        lifecycle_state_candidate="READY" if identity == 1 else "TRIGGERED",
        actionability_candidate="ACTIONABLE",
        data_quality_label="HIGH",
        confidence_score=86,
        confidence_label="HIGH",
        dual_score=score,
        setup_score=Decimal("7.8"),
        close_price=Decimal("101"),
        distance_to_pivot_pct=Decimal("-0.4"),
        required_feature_coverage=Decimal("1.0"),
        freshness_status="FRESH",
        signals_json={
            "sector_rank": {"value": sector_rank},
            "market_regime": {"value": "GREEN"},
            "market_gate": {"value": True},
            "earnings_risk": {"value": "LOW"},
            "liquidity": {"value": False},
        },
        warning_flags_json=[],
    )


def _rule(rule_id: str, severity: str, scope: str) -> SignalAlertRule:
    return SignalAlertRule(
        rule_id=rule_id,
        enabled=True,
        severity=severity,
        scope=scope,
        cooldown_sessions=5,
        minimum_confidence=70,
        config_version="test-v2",
    )


def _alert(
    rule_id: int,
    key: str,
    severity: str,
    evaluation_id: int,
    lifecycle_event_id: int | None = None,
    *,
    signal_change_event_id: int | None = None,
) -> SignalAlertEvent:
    alert_type = "NEW_TRIGGER" if lifecycle_event_id else "SCORE_ACCELERATION"
    return SignalAlertEvent(
        alert_rule_id=rule_id,
        lifecycle_event_id=lifecycle_event_id,
        signal_change_event_id=signal_change_event_id,
        evaluation_run_id=evaluation_id,
        ticker="FIX",
        timeframe="1d",
        effective_date=date(2026, 8, 10),
        event_key=key,
        source_event_key=f"source-{key}",
        status="UNREAD",
        severity=severity,
        reason_codes_json=[f"{alert_type}_ALERT"],
        evidence_json={"rule_id": alert_type, "source_confidence": 86, "blockers": []},
    )
