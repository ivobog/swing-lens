from __future__ import annotations

import json
from datetime import UTC, datetime

from app.services.ceri.dtos import EarningsRequest, EstimateRequest
from app.services.ceri.enums import CeriMetric, CeriPeriodType
from app.services.ceri.providers.eodhd_client import EodhdClientConfig, EodhdHttpClient
from app.services.ceri.providers.eodhd_mapping import (
    canonical_ticker_from_eodhd_symbol,
    eodhd_symbol,
)
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider
from app.settings import Settings


def test_eodhd_provider_reads_api_key_from_application_settings(monkeypatch) -> None:
    settings = Settings(_env_file=None, eodhd_api_key="from-settings")
    monkeypatch.setattr(
        "app.services.ceri.providers.eodhd_provider.get_settings",
        lambda: settings,
    )

    provider = EodhdCeriProvider()

    assert provider.client.config.api_key == "from-settings"


def test_eodhd_symbol_maps_us_share_class_to_provider_notation() -> None:
    assert eodhd_symbol("MOG.A", "US") == "MOG-A.US"
    assert eodhd_symbol("BRK.B", "NYSE") == "BRK-B.US"
    assert eodhd_symbol("AAPL.US", "US") == "AAPL.US"
    assert canonical_ticker_from_eodhd_symbol("MOG-A.US") == "MOG.A"


def test_eodhd_default_transport_buffers_body_before_response_closes(monkeypatch) -> None:
    response = _ContextResponse({"earnings": [{"code": "AAPL.US"}]})
    monkeypatch.setattr(
        "app.services.ceri.providers.eodhd_client.urlopen",
        lambda _request, timeout: response,
    )

    payload = EodhdHttpClient(
        EodhdClientConfig(api_key="fixture", max_attempts=1)
    ).get_json("/api/calendar/earnings", {"symbols": "AAPL.US"})

    assert payload == {"earnings": [{"code": "AAPL.US"}]}
    assert response.closed is True


def test_eodhd_trends_maps_current_and_historical_eps_points_without_zero_fill() -> None:
    payload = [
        {
            "code": "AAPL.US",
            "period": "0q",
            "date": "2026-09-30",
            "earningsEstimateAvg": 2.0,
            "earningsEstimateHigh": 2.2,
            "earningsEstimateLow": 1.8,
            "earningsEstimateNumberOfAnalysts": 12,
            "epsTrend7daysAgo": 1.9,
            "epsTrend30daysAgo": None,
            "epsTrend90daysAgo": 1.7,
            "epsRevisionsUpLast30days": 4,
            "epsRevisionsDownLast30days": 1,
            "revenueEstimateAvg": 100,
            "revenueEstimateNumberOfAnalysts": 10,
        }
    ]
    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret"),
        transport=lambda _url, _timeout: payload,
    )
    records = list(
        EodhdCeriProvider(
            client=client,
            clock=lambda: datetime(2026, 8, 8, 12, tzinfo=UTC),
        ).fetch_estimate_snapshots(
            EstimateRequest(
                None,
                "AAPL",
                (CeriMetric.EPS_DILUTED, CeriMetric.REVENUE),
                (CeriPeriodType.CURRENT_QUARTER,),
            )
        )
    )

    eps = [record for record in records if record.payload["metric"] == "EPS_DILUTED"]
    assert {record.payload["consensus"] for record in eps} == {2.0, 1.9, 1.7}
    assert all(record.payload["observed_at"] is not None for record in records)
    assert all(record.export_policy == "restricted" for record in records)
    assert "secret" not in str(EodhdCeriProvider(client=client).safe_metadata())


def test_eodhd_trends_flattens_symbol_grouped_response() -> None:
    payload = {
        "type": "EarningsTrends",
        "symbols": "AAPL.US",
        "trends": [
            [
                {
                    "code": "AAPL.US",
                    "period": "0q",
                    "date": "2026-09-30",
                    "earningsEstimateAvg": "2.0",
                    "epsTrend7daysAgo": "1.9",
                }
            ]
        ],
    }
    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret"),
        transport=lambda _url, _timeout: payload,
    )

    records = list(
        EodhdCeriProvider(
            client=client,
            clock=lambda: datetime(2026, 8, 8, 12, tzinfo=UTC),
        ).fetch_estimate_snapshots(
            EstimateRequest(
                None,
                "AAPL",
                (CeriMetric.EPS_DILUTED,),
                (CeriPeriodType.CURRENT_QUARTER,),
            )
        )
    )

    assert [record.payload["consensus"] for record in records] == ["2.0", "1.9"]


def test_eodhd_share_class_records_retain_canonical_ticker() -> None:
    payload = [
        {
            "code": "MOG-A.US",
            "period": "0q",
            "date": "2026-09-30",
            "earningsEstimateAvg": 2.0,
        }
    ]
    requested_urls: list[str] = []
    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret"),
        transport=lambda url, _timeout: requested_urls.append(url) or payload,
    )

    records = list(
        EodhdCeriProvider(
            client=client,
            clock=lambda: datetime(2026, 8, 8, 12, tzinfo=UTC),
        ).fetch_estimate_snapshots(
            EstimateRequest(
                None,
                "MOG.A",
                (CeriMetric.EPS_DILUTED,),
                (CeriPeriodType.CURRENT_QUARTER,),
            )
        )
    )

    assert "symbols=MOG-A.US" in requested_urls[0]
    assert records[0].payload["ticker"] == "MOG.A"
    assert records[0].payload["provider_company_id"] == "MOG-A.US"


def test_eodhd_official_earnings_schema_maps_reported_result_and_zero_values() -> None:
    payload = {
        "earnings": [
            {
                "code": "AAPL.US",
                "report_date": "2026-07-30",
                "date": "2026-06-30",
                "before_after_market": "AfterMarket",
                "currency": "USD",
                "actual": 0,
                "estimate": 0,
                "difference": 0,
                "percent": 0,
            }
        ]
    }
    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret"),
        transport=lambda _url, _timeout: payload,
    )

    records = list(
        EodhdCeriProvider(
            client=client,
            clock=lambda: datetime(2026, 8, 14, 12, tzinfo=UTC),
        ).fetch_earnings_actuals(EarningsRequest(None, "AAPL"))
    )

    assert len(records) == 1
    record = records[0]
    assert record.payload["event_kind"] == "REPORTED"
    assert record.payload["report_at"].date().isoformat() == "2026-07-30"
    assert record.payload["fiscal_period_end"].isoformat() == "2026-06-30"
    assert record.payload["actual_value"] == 0
    assert record.payload["estimate"] == 0
    assert record.payload["surprise_percent"] == 0
    assert record.payload["provider_consensus_semantics"] == "REPORT_TIME_CONSENSUS"


class _ContextResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload) -> None:
        self.body = json.dumps(payload).encode("utf-8")
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.closed = True

    def read(self) -> bytes:
        if self.closed:
            return b""
        return self.body
