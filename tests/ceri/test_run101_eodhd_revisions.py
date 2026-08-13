from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.services.ceri.dtos import RawProviderRecord
from app.services.ceri.enums import CeriDataset, ExportPolicy, HistoricalViewMode
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery
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


@pytest.mark.parametrize("failure", ["cross_provider", "period_mismatch", "scale_mismatch"])
def test_same_provider_relative_comparability_fails_closed(failure: str) -> None:
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
