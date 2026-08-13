from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery
from app.services.ceri.revision_feature_service import CeriRevisionFeatureService


def test_first_revenue_observation_has_no_fabricated_revision() -> None:
    current = _revenue(1, 101, date(2026, 8, 13), Decimal("100"))

    feature = _service([current]).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="REVENUE",
        cutoff_at=datetime(2026, 8, 13, 21, tzinfo=UTC),
        window_days=30,
    )

    assert feature.pct_change is None
    assert feature.baseline_snapshot_id is None
    assert feature.unavailable_reason == "UNAVAILABLE_BASELINE_NOT_ACCUMULATED"


def test_genuine_persisted_revenue_observation_becomes_historical_baseline() -> None:
    baseline = _revenue(1, 101, date(2026, 7, 14), Decimal("100"))
    current = _revenue(2, 102, date(2026, 8, 13), Decimal("110"))

    feature = _service([baseline, current]).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="REVENUE",
        cutoff_at=datetime(2026, 8, 13, 21, tzinfo=UTC),
        window_days=30,
    )

    assert feature.pct_change == Decimal("10.0")
    assert feature.comparison_mode == "HISTORICAL_OBSERVATION"
    assert feature.baseline_origin == "ACCUMULATED_IMMUTABLE_OBSERVATION"
    assert feature.reference_at == baseline.known_at


@pytest.mark.parametrize("mismatch", ["period", "currency", "scale"])
def test_revenue_history_requires_full_comparability(mismatch: str) -> None:
    baseline = _revenue(1, 101, date(2026, 7, 14), Decimal("100"))
    current = _revenue(2, 102, date(2026, 8, 13), Decimal("110"))
    if mismatch == "period":
        baseline.fiscal_period_end = date(2027, 12, 31)
    elif mismatch == "currency":
        baseline.canonical_currency = "EUR"
    else:
        baseline.canonical_scale = Decimal("1000")

    feature = _service([baseline, current]).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="REVENUE",
        cutoff_at=datetime(2026, 8, 13, 21, tzinfo=UTC),
        window_days=30,
    )

    assert feature.pct_change is None


def test_retrospectively_backdated_revenue_baseline_is_rejected() -> None:
    baseline = _revenue(1, 101, date(2026, 7, 14), Decimal("100"))
    baseline.baseline_origin = "PROVIDER_RETROSPECTIVE_WINDOW"
    baseline.trend_baseline_window_days = 30
    baseline.known_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    current = _revenue(2, 102, date(2026, 8, 13), Decimal("110"))

    feature = _service([baseline, current]).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="REVENUE",
        cutoff_at=datetime(2026, 8, 13, 21, tzinfo=UTC),
        window_days=30,
    )

    assert feature.pct_change is None
    assert feature.unavailable_reason == "UNAVAILABLE_BASELINE_NOT_ACCUMULATED"


def _revenue(snapshot_id: int, source_id: int, session: date, value: Decimal):
    known_at = datetime.combine(session, datetime.min.time(), tzinfo=UTC)
    return CeriEstimateSnapshot(
        id=snapshot_id,
        source_record_id=source_id,
        company_id=42,
        metric="REVENUE",
        period_type="CURRENT_FISCAL_YEAR",
        canonical_period_slot="CURRENT_FISCAL_YEAR",
        fiscal_period_end=date(2026, 12, 31),
        consensus=value,
        source_currency="USD",
        source_scale=Decimal("1"),
        canonical_currency="USD",
        canonical_scale=Decimal("1"),
        effective_at=known_at,
        effective_session=session,
        known_at=known_at,
        retrieved_at=known_at,
        source_provider="eodhd",
        canonical_observation_key=f"revenue-{snapshot_id}",
    )


def _service(snapshots):
    sources = {
        item.source_record_id: CeriSourceRecord(
            id=item.source_record_id,
            provider="eodhd",
            dataset="estimates",
            provider_record_id=f"revenue-{item.source_record_id}",
            content_hash=f"hash-{item.source_record_id}",
            idempotency_key=f"key-{item.source_record_id}",
        )
        for item in snapshots
    }
    return CeriRevisionFeatureService(
        query=CeriPointInTimeQuery(snapshots=snapshots, source_records=sources)
    )


class FakeDb:
    pass
