from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.services.ceri.enums import HistoricalViewMode
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery

UTC = ZoneInfo("UTC")


def test_later_corrections_do_not_affect_historical_as_known_queries() -> None:
    original = _estimate(1, 101, date(2026, 8, 1), Decimal("10"))
    correction = _estimate(2, 102, date(2026, 9, 1), Decimal("15"))
    query = CeriPointInTimeQuery(
        snapshots=[original, correction],
        source_records={
            101: _source(101),
            102: _source(102, supersedes_id=101),
        },
    )
    cutoff = datetime(2026, 8, 15, 21, tzinfo=UTC)

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


def _estimate(
    snapshot_id: int,
    source_record_id: int,
    session: date,
    consensus: Decimal,
) -> CeriEstimateSnapshot:
    return CeriEstimateSnapshot(
        id=snapshot_id,
        source_record_id=source_record_id,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="ANNUAL",
        fiscal_period_end=date(2026, 12, 31),
        consensus=consensus,
        canonical_currency="USD",
        canonical_scale=Decimal("1"),
        effective_at=datetime.combine(session, datetime.min.time(), tzinfo=UTC),
        effective_session=session,
        canonical_observation_key=f"{snapshot_id}",
    )


def _source(source_record_id: int, supersedes_id: int | None = None) -> CeriSourceRecord:
    return CeriSourceRecord(
        id=source_record_id,
        provider="manual",
        dataset="estimates",
        provider_record_id=f"est-{source_record_id}",
        supersedes_id=supersedes_id,
        correction_type="CORRECTION" if supersedes_id else None,
        content_hash="hash",
        idempotency_key=f"key-{source_record_id}",
    )


class FakeDb:
    pass
