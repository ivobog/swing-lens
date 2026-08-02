from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriEarningsActual, CeriEstimateSnapshot
from app.services.ceri.surprise_feature_service import CeriSurpriseFeatureService

UTC = ZoneInfo("UTC")


def test_earnings_surprise_uses_consensus_immediately_before_report() -> None:
    stale = _estimate(1, date(2026, 7, 15), Decimal("9.50"))
    latest_before = _estimate(2, date(2026, 8, 1), Decimal("10.00"))
    after_report = _estimate(3, date(2026, 8, 3), Decimal("12.00"))
    earnings = _earnings(actual=Decimal("11.00"))

    feature = CeriSurpriseFeatureService().attach_consensus_snapshot(
        earnings,
        [stale, latest_before, after_report],
    )

    assert earnings.consensus_snapshot_id == 2
    assert earnings.consensus_selection_reason == "latest_consensus_before_report_at"
    assert feature.consensus_snapshot_id == 2
    assert earnings.surprise_absolute == Decimal("1.00")
    assert earnings.surprise_pct == Decimal("0.1")
    assert feature.direction == "positive"


def test_surprise_summary_uses_last_four_reported_periods() -> None:
    estimates = [_estimate(index, date(2026, 8, 1), Decimal("10")) for index in range(1, 7)]
    earnings = [
        _earnings(earnings_id=index, actual=Decimal("11"), report_day=10 + index)
        for index in range(1, 7)
    ]

    summary = CeriSurpriseFeatureService().summarize(earnings, estimates)

    assert len(summary.features) == 4
    assert summary.positive_count == 4
    assert summary.consistency == "consistently_positive"


def _estimate(
    snapshot_id: int,
    session: date,
    consensus: Decimal,
) -> CeriEstimateSnapshot:
    return CeriEstimateSnapshot(
        id=snapshot_id,
        source_record_id=100 + snapshot_id,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="ANNUAL",
        fiscal_period_end=date(2026, 12, 31),
        consensus=consensus,
        canonical_currency="USD",
        canonical_scale=Decimal("1"),
        effective_at=datetime.combine(session, datetime.min.time(), tzinfo=UTC),
        effective_session=session,
        canonical_observation_key=f"snapshot-{snapshot_id}",
    )


def _earnings(
    *,
    earnings_id: int = 7,
    actual: Decimal,
    report_day: int = 2,
) -> CeriEarningsActual:
    return CeriEarningsActual(
        id=earnings_id,
        source_record_id=200 + earnings_id,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="ANNUAL",
        fiscal_period_end=date(2026, 12, 31),
        report_at=datetime(2026, 8, report_day, 21, tzinfo=UTC),
        report_session=date(2026, 8, report_day),
        actual_value=actual,
    )
