from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.ceri_tables import CeriIngestionRun, CeriSourceRecord
from app.services.ceri.dtos import EarningsRequest
from app.services.ceri.freshness_service import (
    FreshnessTimestampError,
    evidence_observation_timestamp,
    global_feed_freshness_from_runs,
    ticker_feed_coverage_from_runs,
    ticker_feed_freshness_from_runs,
)
from app.services.ceri.providers.eodhd_client import EodhdClientConfig, EodhdHttpClient
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider

NOW = datetime(2026, 8, 25, 18, tzinfo=UTC)


def test_unchanged_estimate_after_successful_refresh_keeps_feed_fresh() -> None:
    immutable_source = CeriSourceRecord(
        provider="eodhd",
        dataset="estimates",
        provider_record_id="AAPL.US:0q:2026-09-30:EPS_DILUTED",
        content_hash="unchanged",
        idempotency_key="unchanged",
        retrieved_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
    )
    completed_check = _run(
        ticker="AAPL",
        dataset="estimates",
        completed_at=NOW,
        deduplicated_count=48,
    )

    freshness = ticker_feed_freshness_from_runs(
        [completed_check],
        ticker="AAPL",
        cutoff_at=NOW,
        max_stale_days={"estimates": 7},
    )["estimates"]

    assert immutable_source.retrieved_at.date().isoformat() == "2026-08-14"
    assert freshness.last_successful_check_at == NOW
    assert freshness.age_days == 0
    assert freshness.status == "FRESH"


def test_provider_not_checked_beyond_threshold_is_stale() -> None:
    freshness = ticker_feed_freshness_from_runs(
        [
            _run(
                ticker="AAPL",
                dataset="estimates",
                completed_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
            )
        ],
        ticker="AAPL",
        cutoff_at=NOW,
        max_stale_days={"estimates": 7},
    )["estimates"]

    assert freshness.age_days == 11
    assert freshness.status == "STALE"


def test_future_earnings_event_is_not_a_publication_or_freshness_timestamp() -> None:
    payload = {
        "earnings": [
            {
                "code": "AAPL.US",
                "report_date": "2026-11-03",
                "date": "2026-09-30",
                "estimate": 2.0,
            }
        ]
    }
    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret"),
        transport=lambda _url, _timeout: payload,
    )
    records = list(
        EodhdCeriProvider(client=client, clock=lambda: NOW).fetch_earnings_actuals(
            EarningsRequest(None, "AAPL")
        )
    )

    assert len(records) == 1
    assert records[0].payload["event_kind"] == "UPCOMING"
    assert records[0].published_at is None
    assert records[0].retrieved_at == NOW
    assert records[0].payload["source_timestamp_semantics"] == "EVENT_DATE_NOT_PUBLICATION_V1"


def test_global_feed_fresh_and_ticker_missing_are_both_represented() -> None:
    runs = [_run(ticker="AAPL", dataset="estimates", completed_at=NOW)]

    global_state = global_feed_freshness_from_runs(
        runs,
        cutoff_at=NOW,
        max_stale_days={"estimates": 7},
    )[("eodhd", "estimates")]
    coverage = ticker_feed_coverage_from_runs(
        runs,
        tickers={"AAPL", "MSFT"},
        provider="eodhd",
        dataset="estimates",
        cutoff_at=NOW,
        max_stale_days=7,
    )

    assert global_state.status == "FRESH"
    assert global_state.age_days == 0
    assert coverage == {"total": 2, "fresh": 1, "stale": 0, "missing": 1}


@pytest.mark.parametrize("dataset", ["estimates", "earnings", "catalysts", "guidance"])
def test_timestamp_fallback_rejects_future_business_date(dataset: str) -> None:
    source = CeriSourceRecord(
        provider="eodhd" if dataset != "guidance" else "sec",
        dataset=dataset,
        provider_record_id=f"fixture:{dataset}",
        content_hash=f"hash:{dataset}",
        idempotency_key=f"key:{dataset}",
        published_at=datetime(2026, 11, 3, tzinfo=UTC),
        retrieved_at=NOW,
    )

    selected = evidence_observation_timestamp(source, reference_at=NOW)

    assert selected.value == NOW
    assert selected.field_name == "retrieved_at"
    assert selected.quality == "RETRIEVAL_ONLY"


def test_feed_freshness_age_can_never_be_negative() -> None:
    future_run = _run(
        ticker="AAPL",
        dataset="estimates",
        completed_at=datetime(2026, 8, 26, 12, tzinfo=UTC),
    )

    with pytest.raises(FreshnessTimestampError):
        ticker_feed_freshness_from_runs(
            [future_run],
            ticker="AAPL",
            cutoff_at=NOW,
            max_stale_days={"estimates": 7},
        )


def test_ops_and_scoring_share_provider_feed_age_semantics() -> None:
    runs = [
        _run(
            ticker="AAPL",
            dataset="estimates",
            completed_at=datetime(2026, 8, 24, 22, tzinfo=UTC),
        )
    ]

    ops = global_feed_freshness_from_runs(
        runs,
        cutoff_at=NOW,
        max_stale_days={"estimates": 7},
    )[("eodhd", "estimates")]
    scoring = ticker_feed_freshness_from_runs(
        runs,
        ticker="AAPL",
        cutoff_at=NOW,
        max_stale_days={"estimates": 7},
    )["estimates"]

    assert ops.age_days == scoring.age_days == 1
    assert ops.status == scoring.status == "FRESH"


def _run(
    *,
    ticker: str,
    dataset: str,
    completed_at: datetime,
    deduplicated_count: int = 0,
) -> CeriIngestionRun:
    return CeriIngestionRun(
        provider="eodhd",
        dataset=dataset,
        scope_json={"ticker": ticker},
        status="COMPLETED",
        request_key=f"{ticker}:{dataset}:{completed_at.isoformat()}",
        requested_count=48,
        fetched_count=48,
        inserted_count=0,
        deduplicated_count=deduplicated_count,
        completed_at=completed_at,
    )
