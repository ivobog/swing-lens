from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriScoreSnapshot
from app.services.ceri.outcome_feature_export import CeriOutcomeFeatureExportService

UTC = ZoneInfo("UTC")


def test_outcome_feature_export_is_point_in_time_safe() -> None:
    eligible = _snapshot(1, datetime(2026, 8, 1, 21, tzinfo=UTC))
    future = _snapshot(2, datetime(2026, 8, 2, 21, tzinfo=UTC))

    result = CeriOutcomeFeatureExportService().export_snapshots(
        snapshots=[eligible, future],
        cutoff_at=datetime(2026, 8, 1, 23, tzinfo=UTC),
    )

    assert len(result.rows) == 1
    assert result.rows[0]["ticker"] == "MSFT"
    assert result.rows[0]["source_ids"] == [101, 102]


def _snapshot(snapshot_id: int, cutoff_at: datetime) -> CeriScoreSnapshot:
    return CeriScoreSnapshot(
        id=snapshot_id,
        company_id=42,
        ticker="MSFT",
        as_of_session=date(2026, 8, snapshot_id),
        cutoff_at=cutoff_at,
        opportunity_score=7.0,
        event_risk_score=2.0,
        data_confidence="High",
        coverage_pct=100.0,
        posture="Positive",
        component_json={"source_ids": [101, 102]},
        alignment_flags_json={"fundamentals": True},
        config_version="2026-07-31",
        config_hash="hash",
        calculation_version="ceri-1.0.0",
        evidence_hash="evidence",
    )
