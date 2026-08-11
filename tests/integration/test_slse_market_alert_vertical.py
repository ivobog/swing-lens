from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

from sqlalchemy import create_engine
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
from app.services.setup_lifecycle.export_service import export_alerts_csv, export_changes_csv
from app.services.setup_lifecycle.query_service import (
    SetupLifecycleFilters,
    SetupLifecycleListQuery,
    SetupLifecycleQueryService,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_combined_changes_alert_dtos_full_scope_counts_and_exports_use_postgres(
    disposable_postgres_database: str,
) -> None:
    env = {**os.environ, "DATABASE_URL": disposable_postgres_database}
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    engine = create_engine(disposable_postgres_database)

    with Session(engine) as db:
        evaluation = SetupLifecycleEvaluationRun(
            mode="LIVE",
            status="COMPLETED",
            engine_version="slse-test",
            config_version="test-v2",
            config_hash="config-hash",
        )
        db.add(evaluation)
        db.flush()
        previous = _snapshot(1, date(2026, 8, 7), Decimal("7.6"), 9, evaluation.id)
        current = _snapshot(2, date(2026, 8, 10), Decimal("8.1"), 5, evaluation.id)
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
                "velocity": {
                    "1": {"normalized_delta": 0.5},
                    "3": {"normalized_delta": 0.9},
                },
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
                _alert(
                    trigger_rule.id,
                    "trigger-alert",
                    "ACTIONABLE",
                    evaluation.id,
                    lifecycle_event_id=lifecycle.id,
                    evidence={"rule_id": "NEW_TRIGGER", "source_confidence": 86},
                ),
                _alert(
                    score_rule.id,
                    "score-alert",
                    "NOTABLE",
                    evaluation.id,
                    signal_change_event_id=change.id,
                    evidence={
                        "rule_id": "SCORE_ACCELERATION",
                        "source_confidence": 86,
                    },
                ),
            ]
        )
        quiet = _snapshot(3, date(2026, 8, 11), Decimal("8.1"), 5, evaluation.id)
        db.add(quiet)
        db.commit()

        service = SetupLifecycleQueryService()
        first = service.changes(
            db,
            SetupLifecycleListQuery(filters=SetupLifecycleFilters(), limit=1),
        )
        second = service.changes(
            db,
            SetupLifecycleListQuery(filters=SetupLifecycleFilters(), limit=1, cursor="1"),
        )

        assert first["total"] == 2
        assert first["page_item_count"] == 1
        assert first["summary"]["newly_triggered"] == 1
        assert first["summary"]["material_changes"] == 1
        assert {first["items"][0]["source_type"], second["items"][0]["source_type"]} == {
            "LIFECYCLE_EVENT",
            "SIGNAL_CHANGE_EVENT",
        }
        change_rows = [*first["items"], *second["items"]]
        signal_item = next(
            row for row in change_rows if row["source_type"] == "SIGNAL_CHANGE_EVENT"
        )
        assert signal_item["technical_score_previous"] == 7.6
        assert signal_item["technical_score"] == 8.1
        assert signal_item["technical_score_delta"] == 0.5
        assert signal_item["score_velocity_3d"] == 0.9
        assert signal_item["sector_rank_delta"] == 4
        assert signal_item["source_url"].endswith(
            f"#signal-change-{signal_item['signal_change_event_id']}"
        )

        alerts = service.alerts(
            db,
            SetupLifecycleListQuery(filters=SetupLifecycleFilters(), limit=1),
        )
        assert alerts["total"] == 2
        assert alerts["summary"]["actionable"] == 1
        assert alerts["summary"]["notable"] == 1
        assert alerts["items"][0]["alert_type"] == "NEW_TRIGGER"
        assert alerts["items"][0]["source_type"] == "LIFECYCLE_EVENT"
        assert alerts["items"][0]["episode_id"] == episode.id
        assert alerts["items"][0]["confidence"] == 86
        assert alerts["items"][0]["source_url"] == f"/setup-lifecycle/episodes/{episode.id}"

        change_csv = list(csv.DictReader(StringIO(export_changes_csv({"items": change_rows}))))
        alert_csv = list(
            csv.DictReader(
                StringIO(
                    export_alerts_csv(
                        service.alerts(
                            db,
                            SetupLifecycleListQuery(filters=SetupLifecycleFilters(), limit=50),
                        )
                    )
                )
            )
        )
        assert {row["source_type"] for row in change_csv} == {
            "LIFECYCLE_EVENT",
            "SIGNAL_CHANGE_EVENT",
        }
        assert {row["alert_type"] for row in alert_csv} == {
            "NEW_TRIGGER",
            "SCORE_ACCELERATION",
        }
        assert all(json.loads(row["reason_codes"]) for row in change_csv)

        no_change = service.changes(
            db,
            SetupLifecycleListQuery(
                filters=SetupLifecycleFilters(
                    as_of_date=date(2026, 8, 11), transition="NO_MATERIAL_CHANGE"
                )
            ),
        )
        assert no_change["total"] == 1
        assert no_change["summary"]["no_material_change"] == 1
        assert no_change["items"][0]["source_type"] == "SNAPSHOT_OBSERVATION"
        assert no_change["items"][0]["transition"] == "NO_MATERIAL_CHANGE"


def _snapshot(
    identity: int,
    as_of: date,
    score: Decimal,
    sector_rank: int,
    evaluation_id: int,
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
    *,
    lifecycle_event_id: int | None = None,
    signal_change_event_id: int | None = None,
    evidence: dict,
) -> SignalAlertEvent:
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
        reason_codes_json=[f"{evidence['rule_id']}_ALERT"],
        evidence_json=evidence,
    )
