from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.models.tables import PriceBar
from app.services.ceri.dtos import EstimateRequest
from app.services.ceri.enums import CeriMetric, CeriPeriodType
from app.services.ceri.estimate_normalizer import CeriEstimateNormalizer
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery
from app.services.ceri.price_response_service import CeriPriceResponseService
from app.services.ceri.providers.eodhd_client import EodhdClientConfig, EodhdHttpClient
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider
from app.services.ceri.source_record_service import CeriSourceRecordService


def test_eodhd_trend_baselines_are_relative_to_provider_observation_time() -> None:
    observed_at = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
    payload = [
        {
            "period": "0q",
            "date": "2026-12-31",
            "observedAt": observed_at.isoformat(),
            "currency": "USD",
            "earningsEstimateAvg": 2.0,
            "epsTrend7daysAgo": 1.9,
            "epsTrend30daysAgo": 1.8,
            "epsTrend90daysAgo": 1.7,
        }
    ]
    client = EodhdHttpClient(
        EodhdClientConfig(api_key="fixture"),
        transport=lambda _url, _timeout: payload,
    )
    records = list(
        EodhdCeriProvider(
            client=client,
            clock=lambda: datetime(2026, 8, 5, 16, 0, tzinfo=UTC),
        ).fetch_estimate_snapshots(
            EstimateRequest(
                None,
                "MSFT",
                (CeriMetric.EPS_DILUTED,),
                (CeriPeriodType.CURRENT_QUARTER,),
            )
        )
    )
    snapshots = [
        CeriEstimateNormalizer().normalize(
            CeriSourceRecord(
                id=index + 1,
                provider="eodhd",
                dataset="estimates",
                provider_record_id=record.provider_record_id,
                raw_json=record.payload,
                published_at=record.published_at,
                observed_at=record.observed_at,
                source_timestamp=record.source_timestamp,
                retrieved_at=record.retrieved_at,
                content_hash=str(index),
                idempotency_key=str(index),
            ),
            company_id=42,
        )
        for index, record in enumerate(records)
        if record.payload["metric"] == "EPS_DILUTED"
    ]
    current = next(row for row in snapshots if row.trend_baseline_window_days is None)
    baselines = {
        row.trend_baseline_window_days: row
        for row in snapshots
        if row.trend_baseline_window_days is not None
    }

    assert current.fiscal_period_end == date(2026, 12, 31)
    assert current.provider_observed_at == observed_at
    assert current.effective_session == date(2026, 8, 5)
    assert baselines[7].effective_session == date(2026, 7, 29)
    assert baselines[30].effective_session == date(2026, 7, 6)
    assert baselines[90].effective_session == date(2026, 5, 7)
    assert all(row.fiscal_period_end != row.effective_session for row in baselines.values())

    selection = CeriPointInTimeQuery(snapshots=snapshots).select_baseline(
        FakeDb({CeriEstimateSnapshot: snapshots}),
        current=current,
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=observed_at,
        window_days=30,
    )
    assert selection.baseline is baselines[30]
    assert selection.actual_elapsed_days == 30


def test_eodhd_missing_observation_time_does_not_get_fiscal_period_timestamp() -> None:
    client = EodhdHttpClient(
        EodhdClientConfig(api_key="fixture"),
        transport=lambda _url, _timeout: [
            {
                "period": "0q",
                "date": "2027-03-31",
                "currency": "USD",
                "earningsEstimateAvg": 1.0,
                "epsTrend7daysAgo": 0.9,
            }
        ],
    )
    record = next(
        iter(
            EodhdCeriProvider(client=client).fetch_estimate_snapshots(
                EstimateRequest(
                    None,
                    "MSFT",
                    (CeriMetric.EPS_DILUTED,),
                    (CeriPeriodType.CURRENT_QUARTER,),
                )
            )
        )
    )
    normalized = CeriEstimateNormalizer().normalize(
        CeriSourceRecord(
            id=1,
            provider="eodhd",
            dataset="estimates",
            provider_record_id=record.provider_record_id,
            raw_json=record.payload,
            content_hash="h",
            idempotency_key="i",
        ),
        company_id=42,
    )
    assert normalized.effective_session is None
    assert normalized.effective_at is None
    assert "missing_observation_timestamp" in normalized.quality_flags_json


def test_eodhd_source_storage_keeps_only_permitted_projection() -> None:
    db = FakeDb({})
    result = CeriSourceRecordService().store_source_record(
        db,
        ingestion_run_id=None,
        record=_raw_eodhd_record(),
        raw_payload_allowed=True,
    )

    source = result.source_record
    assert source.raw_json is None
    assert source.restricted_normalized_json == {"ticker": "MSFT", "consensus": 2.0}
    assert source.source_url is None
    assert source.license_scope == "personal"
    assert source.payload_remediation_version == "wave4-evidence-projection-v1"


def test_ibkr_price_response_is_relative_to_benchmark_and_does_not_use_other_sources() -> None:
    rows = []
    for index, day in enumerate((date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5))):
        rows.extend(
            [
                PriceBar(
                    id=index + 1,
                    ticker="MSFT",
                    bar_date=day,
                    timeframe="1 day",
                    open=Decimal("100"),
                    high=Decimal("106"),
                    low=Decimal("99"),
                    close=Decimal(str(100 + index * 3)),
                    volume=Decimal("100"),
                    source="IB",
                    what_to_show="TRADES",
                ),
                PriceBar(
                    id=index + 10,
                    ticker="SPY",
                    bar_date=day,
                    timeframe="1 day",
                    open=Decimal("100"),
                    high=Decimal("102"),
                    low=Decimal("99"),
                    close=Decimal(str(100 + index)),
                    volume=Decimal("100"),
                    source="IB",
                    what_to_show="TRADES",
                ),
            ]
        )
    rows.append(
        PriceBar(
            ticker="MSFT",
            bar_date=date(2026, 8, 5),
            timeframe="1d",
            close=Decimal("1000"),
            source="other_provider",
            what_to_show="TRADES",
        )
    )
    result = CeriPriceResponseService().calculate(
        FakeDb({PriceBar: rows}),
        company_id=42,
        ticker="MSFT",
        event_type="EARNINGS",
        event_id=7,
        event_effective_session=date(2026, 8, 4),
    )
    assert result.quality is not None
    assert result.metrics["benchmark"] == "SPY"
    assert result.metrics["relative_return_1d"] > 0
    assert all(row_id != 0 for row_id in result.price_bar_ids)


def _raw_eodhd_record():
    from app.services.ceri.dtos import RawProviderRecord
    from app.services.ceri.enums import CeriDataset, ExportPolicy

    return RawProviderRecord(
        provider="eodhd",
        dataset=CeriDataset.ESTIMATES,
        provider_record_id="msft:current",
        payload={
            "ticker": "MSFT",
            "consensus": 2.0,
            "source_url": "https://restricted.example",
            "original_document": "do not persist",
        },
        published_at=None,
        observed_at=None,
        export_policy=ExportPolicy.RESTRICTED.value,
    )


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.next_id = 100

    def scalar(self, _statement):
        return None

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        return FakeResult(self.rows.get(model, []))

    def add(self, row):
        self.added.append(row)
        self.rows.setdefault(type(row), []).append(row)

    def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1
