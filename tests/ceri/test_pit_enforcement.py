from datetime import UTC, date, datetime, timedelta

from app.models.ceri_tables import CeriEarningsActual, CeriSourceRecord
from app.models.tables import PriceBar
from app.services.ceri.capture_service import _eligible_source_backed_rows
from app.services.ceri.pit_eligibility import (
    price_bar_is_eligible,
    source_record_is_eligible,
    source_record_known_at,
)
from app.services.ceri.price_response_service import CeriPriceResponseService

CUTOFF = datetime(2026, 9, 9, 9, 30, 53, tzinfo=UTC)


def test_post_cutoff_source_with_old_effective_session_is_excluded() -> None:
    before = _source(1, CUTOFF - timedelta(seconds=1))
    after = _source(2, CUTOFF + timedelta(seconds=1))
    rows = [
        CeriEarningsActual(
            id=11,
            source_record_id=1,
            company_id=7,
            metric="EPS_DILUTED",
            period_type="CURRENT_QUARTER",
            fiscal_period_end=date(2026, 6, 30),
            report_session=date(2026, 7, 30),
        ),
        CeriEarningsActual(
            id=12,
            source_record_id=2,
            company_id=7,
            metric="EPS_DILUTED",
            period_type="CURRENT_QUARTER",
            fiscal_period_end=date(2026, 6, 30),
            report_session=date(2026, 7, 30),
        ),
    ]

    selected = _eligible_source_backed_rows(_SourceDb([before, after]), rows, CUTOFF)

    assert [row.id for row in selected] == [11]
    assert source_record_known_at(after) == CUTOFF + timedelta(seconds=1)


def test_source_known_at_uses_receipt_not_older_publication_time() -> None:
    source = _source(1, CUTOFF + timedelta(seconds=1))
    source.published_at = datetime(2025, 5, 1, tzinfo=UTC)

    assert source_record_known_at(source) == CUTOFF + timedelta(seconds=1)
    assert source_record_is_eligible(source, CUTOFF) is False


def test_old_session_late_known_bar_fails_knowledge_gate() -> None:
    bar = PriceBar(
        id=2263479,
        ticker="DRS",
        bar_date=date(2026, 9, 8),
        timeframe="1 day",
        close=10,
        source="IB",
        what_to_show="ADJUSTED_LAST",
        first_seen_at=CUTOFF + timedelta(seconds=11),
        last_seen_at=CUTOFF + timedelta(seconds=11),
    )

    assert bar.bar_date <= date(2026, 9, 8)
    assert not price_bar_is_eligible(
        bar,
        latest_completed_session=date(2026, 9, 8),
        cutoff_at=CUTOFF,
    )


def test_delayed_execution_still_uses_original_price_bar_cutoff() -> None:
    delayed_wall_clock = CUTOFF + timedelta(days=1)
    bar = PriceBar(
        id=3,
        ticker="DRS",
        bar_date=date(2026, 9, 8),
        timeframe="1 day",
        close=10,
        source="IB",
        what_to_show="TRADES",
        first_seen_at=CUTOFF + timedelta(minutes=1),
        last_seen_at=delayed_wall_clock,
    )

    assert bar.first_seen_at <= delayed_wall_clock
    assert not price_bar_is_eligible(
        bar,
        latest_completed_session=date(2026, 9, 8),
        cutoff_at=CUTOFF,
    )


def test_price_response_cache_identity_includes_cutoff() -> None:
    service = CeriPriceResponseService()
    kwargs = {
        "db": None,
        "company_id": 7,
        "ticker": "DRS",
        "event_type": "EARNINGS",
        "event_id": 9,
        "event_effective_at": datetime(2026, 9, 8, 12, tzinfo=UTC),
        "event_effective_session": date(2026, 9, 8),
        "stock_bars": [],
        "benchmark_bars": [],
        "feature_as_of_session": date(2026, 9, 8),
    }

    earlier = service.calculate(**kwargs, cutoff_at=CUTOFF)
    later = service.calculate(**kwargs, cutoff_at=CUTOFF + timedelta(minutes=5))

    assert earlier.event_key != later.event_key


def _source(source_id: int, retrieved_at: datetime) -> CeriSourceRecord:
    return CeriSourceRecord(
        id=source_id,
        provider="eodhd",
        dataset="earnings",
        provider_record_id=str(source_id),
        retrieved_at=retrieved_at,
        ingested_at=retrieved_at + timedelta(milliseconds=1),
        content_hash=f"hash-{source_id}",
        idempotency_key=f"key-{source_id}",
    )


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class _SourceDb:
    def __init__(self, sources):
        self.sources = sources

    def scalars(self, _statement):
        return _Rows(self.sources)
