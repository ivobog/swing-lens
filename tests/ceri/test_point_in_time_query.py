from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.services.ceri.enums import HistoricalViewMode
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery, canonical_estimate_key

UTC = ZoneInfo("UTC")


def test_as_known_returns_only_evidence_effective_at_or_before_cutoff() -> None:
    old = _estimate(1, 101, date(2026, 8, 1), Decimal("10"))
    future = _estimate(2, 102, date(2026, 8, 4), Decimal("12"))
    query = CeriPointInTimeQuery(snapshots=[old, future])

    rows = query.eligible_estimates(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 3, 21, tzinfo=UTC),
        mode=HistoricalViewMode.AS_KNOWN,
    )

    assert rows == [old]


def test_latest_corrected_applies_later_corrections_without_leaking_into_as_known() -> None:
    original = _estimate(1, 101, date(2026, 8, 1), Decimal("10"))
    correction = _estimate(2, 102, date(2026, 8, 10), Decimal("11"))
    sources = {
        101: _source(101),
        102: _source(102, supersedes_id=101, correction_type="CORRECTION"),
    }
    query = CeriPointInTimeQuery(snapshots=[original, correction], source_records=sources)
    cutoff = datetime(2026, 8, 3, 21, tzinfo=UTC)

    as_known = query.current_snapshot(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=cutoff,
        mode=HistoricalViewMode.AS_KNOWN,
    )
    latest_corrected = query.current_snapshot(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=cutoff,
        mode=HistoricalViewMode.LATEST_CORRECTED,
    )

    assert as_known is original
    assert latest_corrected is correction


def test_baseline_selection_uses_tolerance_and_records_elapsed_days() -> None:
    baseline = _estimate(1, 101, date(2026, 7, 31), Decimal("10"))
    current = _estimate(2, 102, date(2026, 8, 31), Decimal("12"))
    query = CeriPointInTimeQuery(snapshots=[baseline, current])

    selection = query.select_baseline(
        FakeDb(),
        current=current,
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 31, 21, tzinfo=UTC),
        window_days=30,
    )

    assert selection.baseline is baseline
    assert selection.target_baseline_date == date(2026, 8, 1)
    assert selection.actual_elapsed_days == 31


def test_canonical_estimate_key_rejects_cross_currency_comparison() -> None:
    usd = _estimate(1, 101, date(2026, 8, 1), Decimal("10"), currency="USD")
    eur = _estimate(2, 102, date(2026, 8, 1), Decimal("10"), currency="EUR")

    assert canonical_estimate_key(usd) != canonical_estimate_key(eur)


def _estimate(
    snapshot_id: int,
    source_record_id: int,
    session: date,
    consensus: Decimal,
    *,
    currency: str = "USD",
) -> CeriEstimateSnapshot:
    return CeriEstimateSnapshot(
        id=snapshot_id,
        source_record_id=source_record_id,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="ANNUAL",
        fiscal_period_end=date(2026, 12, 31),
        consensus=consensus,
        canonical_currency=currency,
        canonical_scale=Decimal("1"),
        effective_at=datetime.combine(session, datetime.min.time(), tzinfo=UTC),
        effective_session=session,
        canonical_observation_key=f"{snapshot_id}",
    )


def _source(
    source_record_id: int,
    *,
    supersedes_id: int | None = None,
    correction_type: str | None = None,
) -> CeriSourceRecord:
    return CeriSourceRecord(
        id=source_record_id,
        provider="manual",
        dataset="estimates",
        provider_record_id=f"est-{source_record_id}",
        supersedes_id=supersedes_id,
        correction_type=correction_type,
        content_hash="hash",
        idempotency_key=f"key-{source_record_id}",
    )


class FakeDb:
    pass
