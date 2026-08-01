from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriEstimateSnapshot
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery
from app.services.ceri.revision_feature_service import CeriRevisionFeatureService

UTC = ZoneInfo("UTC")


def test_comparable_eps_snapshots_calculate_absolute_and_percentage_revision() -> None:
    baseline = _estimate(1, 101, date(2026, 7, 31), Decimal("10.00"))
    current = _estimate(
        2,
        102,
        date(2026, 8, 31),
        Decimal("12.00"),
        upward_count=6,
        downward_count=2,
        high=Decimal("13.00"),
        low=Decimal("11.00"),
        analyst_count=8,
    )
    service = _service([baseline, current])

    feature = service.calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 31, 21, tzinfo=UTC),
        window_days=30,
    )

    assert feature.absolute_change == Decimal("2.00")
    assert feature.pct_change == Decimal("0.2")
    assert feature.net_breadth == Decimal("0.5")
    assert feature.dispersion == Decimal("0.1666666666666666666666666667")
    assert feature.actual_elapsed_days == 31
    assert feature.source_observation_ids_json == [101, 102]
    assert feature.evidence_hash == service.reproduce_evidence_hash(feature)


def test_missing_baseline_returns_unavailable_reason_not_zero() -> None:
    current = _estimate(2, 102, date(2026, 8, 31), Decimal("12.00"))
    service = _service([current])

    feature = service.calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 31, 21, tzinfo=UTC),
        window_days=30,
    )

    assert feature.absolute_change is None
    assert feature.pct_change is None
    assert feature.unavailable_reason == "baseline_unavailable"
    assert "baseline_unavailable" in feature.warnings_json


def test_near_zero_and_sign_change_baselines_use_absolute_change_with_warning() -> None:
    near_zero = _service(
        [
            _estimate(1, 101, date(2026, 7, 31), Decimal("0.001")),
            _estimate(2, 102, date(2026, 8, 31), Decimal("1.00")),
        ]
    ).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 31, 21, tzinfo=UTC),
        window_days=30,
    )
    sign_change = _service(
        [
            _estimate(1, 101, date(2026, 7, 31), Decimal("-1.00")),
            _estimate(2, 102, date(2026, 8, 31), Decimal("1.00")),
        ]
    ).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 31, 21, tzinfo=UTC),
        window_days=30,
    )

    assert near_zero.absolute_change == Decimal("0.999")
    assert near_zero.pct_change is None
    assert "pct_change_unavailable_near_zero_baseline" in near_zero.warnings_json
    assert sign_change.absolute_change == Decimal("2.00")
    assert sign_change.pct_change is None
    assert "pct_change_unavailable_sign_change" in sign_change.warnings_json


def test_dispersion_unavailable_for_near_zero_consensus() -> None:
    feature = _service(
        [
            _estimate(1, 101, date(2026, 7, 31), Decimal("1.00")),
            _estimate(
                2,
                102,
                date(2026, 8, 31),
                Decimal("0.001"),
                high=Decimal("1"),
                low=Decimal("-1"),
            ),
        ]
    ).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 31, 21, tzinfo=UTC),
        window_days=30,
    )

    assert feature.dispersion is None
    assert "dispersion_unavailable_near_zero_consensus" in feature.warnings_json


def test_acceleration_uses_actual_elapsed_days() -> None:
    service = _service([])
    recent = _feature(
        absolute_change=Decimal("3"),
        actual_elapsed_days=10,
    )
    longer = _feature(
        absolute_change=Decimal("6"),
        actual_elapsed_days=30,
    )

    updated = service.with_acceleration(recent, longer)

    assert updated.acceleration == Decimal("0.1")


def test_revision_confidence_and_reproduction_are_deterministic() -> None:
    baseline = _estimate(1, 101, date(2026, 7, 31), Decimal("10.00"))
    current = _estimate(
        2,
        102,
        date(2026, 8, 31),
        Decimal("12.00"),
        analyst_count=1,
        high=Decimal("13"),
        low=Decimal("11"),
    )
    service = _service([baseline, current])

    first = service.calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 31, 21, tzinfo=UTC),
        window_days=30,
    )
    second = service.calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 31, 21, tzinfo=UTC),
        window_days=30,
    )

    assert first.revision_confidence_label == "Low"
    assert first.evidence_hash == second.evidence_hash
    assert service.reproduce_evidence_hash(first) == first.evidence_hash


def _service(snapshots: list[CeriEstimateSnapshot]) -> CeriRevisionFeatureService:
    query = CeriPointInTimeQuery(snapshots=snapshots)
    return CeriRevisionFeatureService(query=query)


def _estimate(
    snapshot_id: int,
    source_record_id: int,
    session: date,
    consensus: Decimal,
    *,
    upward_count: int | None = None,
    downward_count: int | None = None,
    high: Decimal | None = None,
    low: Decimal | None = None,
    analyst_count: int | None = 6,
) -> CeriEstimateSnapshot:
    return CeriEstimateSnapshot(
        id=snapshot_id,
        source_record_id=source_record_id,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="ANNUAL",
        fiscal_period_end=date(2026, 12, 31),
        consensus=consensus,
        high=high,
        low=low,
        analyst_count=analyst_count,
        upward_count=upward_count,
        downward_count=downward_count,
        canonical_currency="USD",
        canonical_scale=Decimal("1"),
        effective_at=datetime.combine(session, datetime.min.time(), tzinfo=UTC),
        effective_session=session,
        canonical_observation_key=f"{snapshot_id}",
    )


def _feature(
    *,
    absolute_change: Decimal,
    actual_elapsed_days: int,
):
    baseline = _estimate(1, 101, date(2026, 7, 31), Decimal("10"))
    current = _estimate(2, 102, date(2026, 8, 31), Decimal("13"))
    service = _service([baseline, current])
    feature = service.calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 31, 21, tzinfo=UTC),
        window_days=30,
    )
    feature.absolute_change = absolute_change
    feature.actual_elapsed_days = actual_elapsed_days
    return feature


class FakeDb:
    def __init__(self) -> None:
        self.added = []

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        pass
