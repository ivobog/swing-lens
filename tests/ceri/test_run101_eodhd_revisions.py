from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.services.ceri.dtos import EstimateRequest, RawProviderRecord
from app.services.ceri.enums import (
    CeriDataset,
    CeriMetric,
    CeriPeriodType,
    ExportPolicy,
    HistoricalViewMode,
)
from app.services.ceri.estimate_normalizer import CeriEstimateNormalizer
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery
from app.services.ceri.provider_registry import provider_storage_projection
from app.services.ceri.providers.eodhd_client import EodhdClientConfig, EodhdHttpClient
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider
from app.services.ceri.revision_feature_service import CeriRevisionFeatureService
from app.services.ceri.source_record_service import CeriSourceRecordService


def test_same_provider_eps_relative_revision_allows_unknown_currency() -> None:
    known_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    current, baseline, sources = _relative_pair(Decimal("2.20"), Decimal("2.00"), known_at)

    feature = _service(current, baseline, sources).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=known_at,
        window_days=30,
    )

    assert feature.pct_change == Decimal("10.0")
    assert feature.absolute_change is None
    assert feature.comparison_mode == "SAME_PROVIDER_RELATIVE"
    assert feature.known_at == known_at
    assert feature.reference_at == known_at - timedelta(days=30)
    assert "canonical_currency_unavailable_relative_only" in feature.warnings_json


def test_eodhd_raw_relative_eps_survives_to_component_ledger_without_currency() -> None:
    known_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    provider = EodhdCeriProvider(
        client=EodhdHttpClient(
            EodhdClientConfig(api_key="fixture"),
            transport=lambda _url, _timeout: [
                {
                    "code": "NVDA.US",
                    "period": "0q",
                    "date": "2026-10-31",
                    "earningsEstimateAvg": "2.3524",
                    "earningsEstimateNumberOfAnalysts": 39,
                    "epsTrend7daysAgo": "2.3524",
                    "epsTrend30daysAgo": "2.3466",
                    "epsTrend90daysAgo": "2.1772",
                    "epsRevisionsUpLast30days": 4,
                    "epsRevisionsDownLast30days": 0,
                }
            ],
        ),
        clock=lambda: known_at,
    )
    raw = list(
        provider.fetch_estimate_snapshots(
            EstimateRequest(
                None,
                "NVDA",
                (CeriMetric.EPS_DILUTED,),
                (CeriPeriodType.CURRENT_QUARTER,),
            )
        )
    )
    sources: dict[int, CeriSourceRecord] = {}
    snapshots: list[CeriEstimateSnapshot] = []
    for identifier, record in enumerate(raw, start=1):
        source = CeriSourceRecord(
            id=identifier,
            provider="eodhd",
            dataset="estimates",
            provider_record_id=record.provider_record_id,
            restricted_normalized_json=provider_storage_projection(
                "eodhd", "estimates", record.payload
            ),
            observed_at=record.observed_at,
            retrieved_at=known_at,
            content_hash=f"hash-{identifier}",
            idempotency_key=f"key-{identifier}",
        )
        snapshot = CeriEstimateNormalizer().normalize(source, company_id=42)
        snapshot.id = identifier
        sources[identifier] = source
        snapshots.append(snapshot)

    feature = CeriRevisionFeatureService(
        query=CeriPointInTimeQuery(snapshots=snapshots, source_records=sources)
    ).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=known_at,
        window_days=30,
        period_slot="CURRENT_QUARTER",
    )
    opportunity = CeriOpportunityScoreService().calculate(revision_features=[feature])
    magnitude = next(c for c in opportunity.components if c.name == "revision_magnitude")
    breadth = next(c for c in opportunity.components if c.name == "revision_breadth")

    assert feature.comparison_mode == "SAME_PROVIDER_RELATIVE"
    assert feature.pct_change == (Decimal("2.3524") - Decimal("2.3466")) / Decimal(
        "2.3466"
    ) * 100
    assert feature.net_breadth == Decimal("1")
    assert feature.known_at == known_at
    assert feature.reference_at == known_at - timedelta(days=30)
    assert magnitude.available is True
    assert breadth.available is True
    assert opportunity.score is None  # 60% gate remains unchanged.


def test_provider_relative_revision_is_excluded_before_response_known_at() -> None:
    known_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    current, baseline, sources = _relative_pair(Decimal("2.20"), Decimal("2.00"), known_at)

    feature = _service(current, baseline, sources).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=known_at - timedelta(seconds=1),
        window_days=30,
        mode=HistoricalViewMode.AS_KNOWN,
    )

    assert feature.pct_change is None
    assert feature.unavailable_reason == "current_snapshot_unavailable"


def test_provider_period_slot_selects_latest_fiscal_end_even_after_period_end() -> None:
    known_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    relevant = _estimate(1, 101, Decimal("2.00"), known_at)
    relevant.fiscal_period_end = date(2026, 7, 31)
    stale_duplicate = _estimate(2, 102, Decimal("1.50"), known_at)
    stale_duplicate.fiscal_period_end = date(2026, 4, 30)
    query = CeriPointInTimeQuery(snapshots=[relevant, stale_duplicate])

    selected = query.current_snapshot(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
        period_slot="CURRENT_QUARTER",
    )

    assert selected is relevant


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        ("cross_provider", "CROSS_PROVIDER_CURRENCY_REQUIRED"),
        ("period_mismatch", "SAME_PROVIDER_PERIOD_MISMATCH"),
        ("scale_mismatch", "SAME_PROVIDER_SCALE_MISMATCH"),
    ],
)
def test_same_provider_relative_comparability_fails_closed(
    failure: str, expected_reason: str
) -> None:
    known_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    current, baseline, sources = _relative_pair(Decimal("2.20"), Decimal("2.00"), known_at)
    if failure == "cross_provider":
        sources[baseline.source_record_id].provider = "manual"
    elif failure == "period_mismatch":
        baseline.fiscal_period_end = date(2027, 3, 31)
    else:
        baseline.source_scale = Decimal("1000")
        baseline.canonical_scale = Decimal("1000")

    feature = _service(current, baseline, sources).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=known_at,
        window_days=30,
    )

    assert feature.pct_change is None
    assert feature.unavailable_reason == expected_reason


def test_absolute_missing_currency_comparison_remains_rejected() -> None:
    known_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    current, baseline, sources = _relative_pair(Decimal("2.20"), Decimal("2.00"), known_at)
    baseline.baseline_origin = None
    baseline.trend_baseline_window_days = None

    feature = _service(current, baseline, sources).calculate_feature(
        FakeDb(),
        company_id=42,
        metric="EPS_DILUTED",
        cutoff_at=known_at,
        window_days=30,
    )

    assert feature.pct_change is None
    assert feature.absolute_change is None
    assert feature.unavailable_reason == "ABSOLUTE_COMPARISON_CURRENCY_REQUIRED"


def test_missing_analyst_count_does_not_invalidate_relative_revision_magnitude() -> None:
    known_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    current, baseline, sources = _relative_pair(Decimal("2.20"), Decimal("2.00"), known_at)
    current.analyst_count = None

    feature = _service(current, baseline, sources).calculate_feature(
        FakeDb(), company_id=42, metric="EPS_DILUTED", cutoff_at=known_at, window_days=30
    )

    assert feature.pct_change == Decimal("10.0")
    assert "analyst_sample_unavailable" in feature.warnings_json


def test_run102_migration_rehydrates_only_same_provider_relative_eps() -> None:
    migration = Path(
        "alembic/versions/20260814_0043_ceri_run102_relative_evidence.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0042_ceri_run101_fail_closed"' in migration
    assert "current_observation_reference IS NOT NULL" in migration
    assert "metric = 'EPS_DILUTED'" in migration
    assert "canonical_currency IS NULL" in migration
    assert "original_fields_json ->> 'consensus'" in migration
    assert "accepted_for_scoring" not in migration
    assert "minimum_component_coverage_pct" not in migration


@pytest.mark.parametrize(
    ("current_value", "baseline_value", "expected"),
    [
        (Decimal("2.00"), Decimal("2.00"), Decimal("0")),
        (Decimal("1.80"), Decimal("2.00"), Decimal("-10.0")),
    ],
)
def test_relative_revision_preserves_zero_and_negative_direction(
    current_value: Decimal, baseline_value: Decimal, expected: Decimal
) -> None:
    known_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    current, baseline, sources = _relative_pair(current_value, baseline_value, known_at)

    feature = _service(current, baseline, sources).calculate_feature(
        FakeDb(), company_id=42, metric="EPS_DILUTED", cutoff_at=known_at, window_days=30
    )

    assert feature.pct_change == expected


def test_relative_revision_near_zero_baseline_is_unavailable() -> None:
    known_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    current, baseline, sources = _relative_pair(Decimal("1"), Decimal("0.001"), known_at)

    feature = _service(current, baseline, sources).calculate_feature(
        FakeDb(), company_id=42, metric="EPS_DILUTED", cutoff_at=known_at, window_days=30
    )

    assert feature.pct_change is None
    assert "pct_change_unavailable_near_zero_baseline" in feature.warnings_json


def test_retrieval_fallback_time_is_not_economic_content_identity() -> None:
    first = _raw_record("2026-08-13T12:00:00+00:00")
    second = _raw_record("2026-08-13T13:00:00+00:00")
    db = SourceDb()
    service = CeriSourceRecordService()

    initial = service.store_source_record(
        db, ingestion_run_id=None, record=first, raw_payload_allowed=False
    )
    repeated = service.store_source_record(
        db, ingestion_run_id=None, record=second, raw_payload_allowed=False
    )

    assert initial.inserted is True
    assert repeated.deduplicated is True
    assert repeated.corrected is False
    assert repeated.source_record is initial.source_record


def _relative_pair(current_value: Decimal, baseline_value: Decimal, known_at: datetime):
    current = _estimate(1, 101, current_value, known_at)
    baseline = _estimate(2, 102, baseline_value, known_at)
    baseline.baseline_origin = "PROVIDER_RETROSPECTIVE_WINDOW"
    baseline.trend_baseline_window_days = 30
    baseline.reference_at = known_at - timedelta(days=30)
    current.current_observation_reference = "TEST:0q:2026-12-31:EPS_DILUTED"
    baseline.current_observation_reference = current.current_observation_reference
    sources = {101: _source(101, "eodhd"), 102: _source(102, "eodhd")}
    return current, baseline, sources


def _estimate(snapshot_id: int, source_id: int, value: Decimal, known_at: datetime):
    return CeriEstimateSnapshot(
        id=snapshot_id,
        source_record_id=source_id,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        canonical_period_slot="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 12, 31),
        consensus=value,
        source_currency=None,
        source_scale=Decimal("1"),
        canonical_currency=None,
        canonical_scale=Decimal("1"),
        effective_at=known_at,
        effective_session=known_at.date(),
        known_at=known_at,
        retrieved_at=known_at,
        canonical_observation_key=f"estimate-{snapshot_id}",
        source_provider="eodhd",
    )


def _source(source_id: int, provider: str) -> CeriSourceRecord:
    return CeriSourceRecord(
        id=source_id,
        provider=provider,
        dataset="estimates",
        provider_record_id=f"record-{source_id}",
        content_hash=f"hash-{source_id}",
        idempotency_key=f"key-{source_id}",
    )


def _service(current, baseline, sources):
    return CeriRevisionFeatureService(
        query=CeriPointInTimeQuery(snapshots=[current, baseline], source_records=sources)
    )


def _raw_record(timestamp: str) -> RawProviderRecord:
    return RawProviderRecord(
        provider="eodhd",
        dataset=CeriDataset.ESTIMATES,
        provider_record_id="TEST:CURRENT_QUARTER:2026-12-31:EPS_DILUTED",
        payload={
            "ticker": "TEST",
            "metric": "EPS_DILUTED",
            "consensus": "2.20",
            "provider_observation_time_basis": "RETRIEVAL_FALLBACK",
            "provider_observed_at": timestamp,
            "observed_at": timestamp,
            "known_at": timestamp,
        },
        published_at=None,
        observed_at=datetime.fromisoformat(timestamp),
        retrieved_at=datetime.fromisoformat(timestamp),
        export_policy=ExportPolicy.RESTRICTED.value,
    )


class FakeDb:
    pass


class SourceDb:
    def __init__(self) -> None:
        self.rows: list[CeriSourceRecord] = []
        self.next_scalar = None

    def scalar(self, statement):
        params = statement.compile().params
        idempotency = params.get("idempotency_key_1")
        if idempotency:
            return next((row for row in self.rows if row.idempotency_key == idempotency), None)
        provider_record_id = params.get("provider_record_id_1")
        return next(
            (
                row
                for row in reversed(self.rows)
                if row.provider_record_id == provider_record_id
            ),
            None,
        )

    def add(self, row) -> None:
        self.rows.append(row)

    def flush(self) -> None:
        for index, row in enumerate(self.rows, start=1):
            if row.id is None:
                row.id = index
