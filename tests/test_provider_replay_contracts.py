"""Offline A15 contract tripwires; synthetic mutations are never LIVE evidence."""

from __future__ import annotations

import ast
import copy
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ceri.dtos import EstimateRequest, GuidanceRequest
from app.services.ceri.enums import CeriMetric, CeriPeriodType
from app.services.ceri.providers.eodhd_client import (
    EodhdAuthenticationError,
    EodhdClientConfig,
    EodhdHttpClient,
    EodhdQuotaExceeded,
)
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider
from app.services.ceri.sec.client import (
    SecClientConfig,
    SecEdgarClient,
    SecEdgarError,
    SecEdgarNotFoundError,
    SecFairAccessError,
    _BufferedResponse,
)
from app.services.ceri.sec.provider import SecCeriProvider
from app.services.ib_data_fetcher import fetch_daily_bars
from app.services.ib_gateway_health_service import IBGatewayHealthState, check_status
from app.services.ib_market_intelligence.request_budget import WeightedWindowBudget
from app.settings import Settings


@pytest.mark.parametrize("feed", ["TRADES", "ADJUSTED_LAST"])
def test_ib_actual_golden_replay_reaches_daily_adapter(monkeypatch, tmp_path, feed):
    from single_run_certification.golden_oracle import artifact

    # The server module installs application-wide hooks at import time. Execute
    # only its unchanged adapter class and bar factory, not those startup hooks.
    path = Path(__file__).parent / "e2e/single_run_certification/certification_server.py"
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, (ast.ClassDef, ast.FunctionDef))
             and node.name in {"DeterministicReadOnlyIB", "_bars_for_ticker"}]
    assert len(nodes) == 2
    namespace = {"os": os, "Path": Path, "SimpleNamespace": SimpleNamespace,
                 "json": json, "date": date, "datetime": datetime, "UTC": UTC}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    monkeypatch.setenv("CERTIFICATION_IB_LOG", str(tmp_path / "ib.jsonl"))
    monkeypatch.setenv("CERTIFICATION_GOLDEN_VERSION", "a05-v3")
    ib = namespace["DeterministicReadOnlyIB"]()
    ib.connect("unused", 0, readonly=True)
    try:
        expected = artifact("market-data.json")["ib_replay"]["ACMR"]
        bars = fetch_daily_bars(ib, SimpleNamespace(symbol="ACMR"),
                                settings=Settings(_env_file=None), what_to_show=feed)
        assert len(bars) == len(expected) > 0
        assert [(bar.ticker, bar.bar_date, bar.close, bar.volume, bar.what_to_show)
                for bar in bars] == [
            ("ACMR", date.fromisoformat(row["date"]), float(row["close"]),
             float(row["volume"]), feed) for row in expected
        ]
        with pytest.raises(AssertionError, match="order API is forbidden"):
            ib.placeOrder(None, None)
    finally:
        ib.disconnect()
    assert not ib.isConnected()


@pytest.mark.parametrize("mutation,exception", [
    ("required_field_removed", AttributeError), ("timestamp_incompatible", ValueError),
])
def test_ib_bar_contract_drift_is_detected(mutation, exception):
    bar = {"date": "20260807", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}
    if mutation == "required_field_removed":
        del bar["close"]
    else:
        bar["date"] = "provider-new-time-format"
    ib = SimpleNamespace(reqHistoricalData=lambda *_a, **_k: [SimpleNamespace(**bar)])
    with pytest.raises(exception):
        fetch_daily_bars(ib, SimpleNamespace(symbol="AAPL"), "TRADES",
                         settings=Settings(_env_file=None))


def test_sec_type_and_unsupported_pagination_drift_are_detected():
    client = SecEdgarClient(SecClientConfig(max_attempts=1), transport=lambda *_a: [])
    with pytest.raises(SecEdgarError, match="non-object JSON"):
        client.submissions("2488")
    # Current discovery supports recent parallel arrays only. A cursor-only
    # replacement cannot satisfy the independent expected accession witness.
    recent = {"form": ["8-K"], "accessionNumber": ["0000002488-26-000121"],
              "primaryDocument": ["q22026991.htm"], "filingDate": ["2026-08-04"]}
    payload = {"filings": {"recent": recent}}
    urls = []
    provider = SecCeriProvider(client=SecEdgarClient(
        SecClientConfig(max_attempts=1),
        transport=lambda url, *_a: urls.append(url) or copy.deepcopy(payload)))
    request = GuidanceRequest(None, "AMD", date(2026, 8, 1), date(2026, 8, 8))
    assert [d.accession_number for d in provider.discover_guidance_documents(
        request, cik="2488")] == ["0000002488-26-000121"]
    payload = {"filings": {"files": [{"name": "new-page.json"}], "next": "new-page.json"}}
    provider.client._cache.clear()
    assert provider.discover_guidance_documents(request, cik="2488") == ()
    assert len(urls) == 2 and all(url.endswith("CIK0000002488.json") for url in urls)


def test_eodhd_unexpected_period_and_null_absent_values_remain_explicit():
    row = {"code": "AAPL.US", "period": "0q", "date": "2026-09-30"}
    provider = EodhdCeriProvider(client=EodhdHttpClient(
        EodhdClientConfig(api_key="fixture"), transport=lambda *_a: [row]),
        clock=lambda: datetime(2026, 8, 8, tzinfo=UTC))
    request = EstimateRequest(None, "AAPL", (CeriMetric.EPS_DILUTED,),
                              (CeriPeriodType.CURRENT_QUARTER,))
    for value in [None, 0, "2.0"]:
        row["earningsEstimateAvg"] = value
        records = list(provider.fetch_estimate_snapshots(request))
        assert len(records) == 1 and records[0].payload["consensus"] == value
        assert records[0].payload["provider_company_id"] == "AAPL.US"
    del row["earningsEstimateAvg"]
    assert list(provider.fetch_estimate_snapshots(request))[0].payload["consensus"] is None
    row["period"] = "NEW_UNSUPPORTED_ENUM"
    assert list(provider.fetch_estimate_snapshots(request)) == []


@pytest.mark.parametrize("status,error", [(403, SecFairAccessError), (404, SecEdgarNotFoundError)])
def test_sec_http_error_body_cannot_be_accepted_as_success(status, error):
    client = SecEdgarClient(SecClientConfig(max_attempts=3),
        transport=lambda *_a: _BufferedResponse(status, {}, b"<html>error</html>"))
    with pytest.raises(error):
        client.submissions("2488")
    assert client.stats().requests == 1 and client.stats().retries == 0


def test_eodhd_missing_auth_and_weighted_quota_prevent_transport():
    calls = []
    def transport(*_a):
        calls.append(1)
        return []
    missing = EodhdHttpClient(EodhdClientConfig(api_key=None), transport=transport)
    with pytest.raises(EodhdAuthenticationError):
        missing.get_json("/api/news", {}, call_cost=5)
    assert calls == []
    client = EodhdHttpClient(EodhdClientConfig(api_key="fixture", daily_call_budget=5),
                            transport=transport)
    assert client.get_json("/api/news", {}, call_cost=5) == []
    with pytest.raises(EodhdQuotaExceeded):
        client.get_json("/api/news", {}, call_cost=5)
    assert calls == [1] and client.stats().calls_used_today == 5


def test_request_budget_can_cancel_without_another_request_or_unbounded_wait():
    sleeps = []
    budget = WeightedWindowBudget(1, monotonic=lambda: 0, sleep=sleeps.append)
    budget.acquire()
    def cancelled():
        raise InterruptedError("operator cancelled")
    with pytest.raises(InterruptedError, match="operator cancelled"):
        budget.acquire(guard=cancelled)
    assert sleeps == []


@pytest.mark.parametrize("fail", [False, True])
def test_ib_health_live_allowlist_excludes_account_sync_and_always_disconnects(fail):
    calls = []
    class HealthOnly:
        RequestTimeout = 0
        def __init__(self):
            self.client = SimpleNamespace(connect=self.handshake, isReady=lambda: True,
                                          serverVersion=lambda: 180)
        def handshake(self, *_a, **_k):
            calls.append("handshake")
        def isConnected(self):
            return True
        def reqCurrentTime(self):
            calls.append("current_time")
            if fail:
                raise TimeoutError("fixture timeout")
            return datetime(2026, 8, 8, tzinfo=UTC)
        def disconnect(self):
            calls.append("disconnect")
        def __getattr__(self, name):
            raise AssertionError(f"outside health allowlist: {name}")
    status = check_status(Settings(_env_file=None, ib_health_timeout_seconds=1),
                          ib_factory=HealthOnly)
    assert calls == ["handshake", "current_time", "disconnect"]
    assert status.api_ready is (not fail)
    if not fail:
        assert status.status == IBGatewayHealthState.IB_API_READY


@pytest.fixture(autouse=True)
def isolated_historical_transport(monkeypatch):
    """Provider DTO/error/replay doubles do not require database coordination.

    Unpatched coordination is exercised by the PostgreSQL process suite.
    """
    from contextlib import nullcontext

    monkeypatch.setattr(
        "app.services.ib_data_fetcher.historical_request", lambda *_args, **_kwargs: nullcontext()
    )
