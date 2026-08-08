from __future__ import annotations

import json
from datetime import UTC, datetime

from app.services.ceri.dtos import EstimateRequest
from app.services.ceri.enums import CeriMetric, CeriPeriodType
from app.services.ceri.providers.eodhd_client import EodhdClientConfig, EodhdHttpClient
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
