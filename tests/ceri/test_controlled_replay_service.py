from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.ceri_tables import CeriControlledReplay, CeriRevisionFeature
from app.services.ceri.controlled_replay_service import (
    ControlledReplayCertificationError,
    _feature_change,
    _merge_opportunity,
    _ranking_impact,
    _validate_selected_revision_features,
)
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService


def test_controlled_replay_schema_is_additive_and_links_parallel_rows() -> None:
    migration = Path(
        "alembic/versions/20260814_0044_ceri_controlled_replays.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0044_ceri_controlled_replays"' in migration
    assert 'down_revision: str | None = "0043_ceri_run102_relative_evidence"' in migration
    assert "ceri_controlled_replays" in migration
    assert "controlled_replay_id" in migration
    assert "UPDATE ceri_" not in migration
    assert "DELETE FROM ceri_" not in migration
    assert CeriControlledReplay.__table__.c.source_run_id.nullable is False
    assert CeriControlledReplay.__table__.c.original_cutoff_at.nullable is False


def test_replay_merges_only_revision_components_and_preserves_policy_weights() -> None:
    old_components = [
        _component("revision_magnitude", 10.0, 0.25, [1]),
        _component("revision_breadth", 10.0, 0.15, [1]),
        _component("revision_acceleration", 10.0, 0.10, [1]),
        _component("surprise_trend", 8.0, 0.15, []),
        _component("guidance", None, 0.15, [], available=False),
        _component("catalysts", None, 0.15, [], available=False),
        _component("price_response", 7.5, 0.05, [9]),
    ]
    corrected = [
        CeriRevisionFeature(
            id=101,
            company_id=7,
            metric="EPS_DILUTED",
            period_key="EPS:CQ",
            period_slot="CURRENT_QUARTER",
            as_of_session=date(2026, 8, 14),
            window_days=30,
            actual_elapsed_days=30,
            pct_change=Decimal("2"),
            upward_count=1,
            downward_count=1,
            net_breadth=Decimal("0"),
            config_version="test",
            config_hash="hash",
            calculation_version="replay",
        )
    ]

    result = _merge_opportunity(
        old_components,
        corrected,
        CeriOpportunityScoreService(),
        conflict_penalty=0.0,
    )

    by_name = {component.name: component for component in result.components}
    assert by_name["revision_magnitude"].value == pytest.approx(2.0)
    assert by_name["revision_breadth"].value == pytest.approx(5.0)
    assert by_name["surprise_trend"].value == 8.0
    assert by_name["price_response"].evidence_ids == (9,)
    assert result.coverage_pct == pytest.approx(60.0)
    assert result.minimum_required_coverage_pct == 60.0


def test_selected_revision_certification_fails_on_one_nonreproducing_value() -> None:
    feature = CeriRevisionFeature(
        id=1,
        company_id=7,
        metric="EPS_DILUTED",
        period_key="EPS:CQ",
        period_slot="CURRENT_QUARTER",
        as_of_session=date(2026, 8, 14),
        window_days=30,
        current_snapshot_id=10,
        baseline_snapshot_id=11,
        pct_change=Decimal("50.909091"),
        config_version="test",
        config_hash="hash",
        calculation_version="replay",
    )

    with pytest.raises(ControlledReplayCertificationError, match="feature 1"):
        _validate_selected_revision_features(
            selected_ids={1},
            features={1: feature},
            estimates={
                10: _estimate(10, Decimal("-0.475")),
                11: _estimate(11, Decimal("-0.475")),
            },
            groups={(7, "EPS_DILUTED", "CURRENT_QUARTER"): [feature]},
        )


def test_feature_change_captures_old_and_corrected_lineage() -> None:
    old = CeriRevisionFeature(
        id=1,
        company_id=7,
        metric="EPS_DILUTED",
        period_key="EPS:CQ",
        period_slot="CURRENT_QUARTER",
        as_of_session=date(2026, 8, 14),
        window_days=30,
        current_snapshot_id=10,
        baseline_snapshot_id=11,
        pct_change=Decimal("50.909091"),
        comparison_mode="SAME_PROVIDER_RELATIVE",
        config_version="test",
        config_hash="hash",
        calculation_version="old",
    )
    replay = CeriRevisionFeature(
        id=2,
        company_id=7,
        metric="EPS_DILUTED",
        period_key="EPS:CQ",
        period_slot="CURRENT_QUARTER",
        as_of_session=date(2026, 8, 14),
        window_days=30,
        current_snapshot_id=20,
        baseline_snapshot_id=21,
        pct_change=Decimal("0"),
        comparison_mode="SAME_PROVIDER_RELATIVE",
        config_version="test",
        config_hash="hash",
        calculation_version="replay",
    )

    change = _feature_change(
        "MSGE",
        old,
        replay,
        estimates={
            20: _estimate(20, Decimal("-0.475")),
            21: _estimate(21, Decimal("-0.475")),
        },
    )

    assert change is not None
    assert change["old_pct_change"] == "50.909091"
    assert change["replay_pct_change"] == "0"
    assert change["old_selected_evidence_ids"] == [1]
    assert change["corrected_evidence_ids"] == [2]
    assert change["reason"] == "STALE_VALUE_LINEAGE_PAIRING"


def test_ranking_impact_reports_transitions_and_every_ticker_rank() -> None:
    rows = [
        {"ticker": "AAA", "original_score": 9.0, "replay_score": 6.0,
         "original_posture": "Positive", "replay_posture": "Improving",
         "original_high_low": True, "replay_high_low": False},
        {"ticker": "BBB", "original_score": 6.0, "replay_score": 9.0,
         "original_posture": "Improving", "replay_posture": "Positive",
         "original_high_low": False, "replay_high_low": True},
        {"ticker": "CCC", "original_score": None, "replay_score": None,
         "original_posture": "Unrated", "replay_posture": "Unrated",
         "original_high_low": False, "replay_high_low": False},
    ]

    impact = _ranking_impact(rows)

    assert impact["opportunity_changed_count"] == 2
    assert impact["posture_transition_count"] == 2
    assert impact["entering_positive"] == ["BBB"]
    assert impact["leaving_positive"] == ["AAA"]
    assert impact["entering_high_opportunity_low_risk"] == ["BBB"]
    assert impact["leaving_high_opportunity_low_risk"] == ["AAA"]
    assert {row["ticker"] for row in impact["rank_movements"]} == {"AAA", "BBB", "CCC"}


def _component(name, value, weight, evidence_ids, *, available=True):
    return {
        "name": name,
        "value": value,
        "weight": weight,
        "contribution": None if value is None else value * weight,
        "available": available,
        "unavailable_reason": None if available else "UNAVAILABLE",
        "evidence_ids": evidence_ids,
        "reasons": [],
        "warnings": [],
    }


def _estimate(row_id: int, consensus: Decimal):
    from app.models.ceri_tables import CeriEstimateSnapshot

    return CeriEstimateSnapshot(
        id=row_id,
        source_record_id=row_id,
        company_id=7,
        metric="EPS_DILUTED",
        fiscal_period_end=date(2026, 9, 30),
        period_type="CURRENT_QUARTER",
        canonical_period_slot="CURRENT_QUARTER",
        consensus=consensus,
        canonical_observation_key=str(row_id),
        effective_at=datetime(2026, 8, 14, tzinfo=UTC),
        known_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
