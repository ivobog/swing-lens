from __future__ import annotations

import io
from urllib.error import HTTPError

import pytest

from app.services.ceri.providers.eodhd_client import (
    EodhdAuthenticationError,
    EodhdClientConfig,
    EodhdHttpClient,
    EodhdProviderError,
)


def test_run38_timeout_retains_diagnosis_and_bounded_attempts() -> None:
    calls, sleeps = [], []

    def timeout(_url, seconds):
        calls.append(seconds)
        raise TimeoutError("The read operation timed out; api_token=secret")

    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret"), transport=timeout, sleep=sleeps.append
    )
    with pytest.raises(EodhdProviderError) as failure:
        client.get_json("/api/news", {"s": "AAPL.US"}, call_cost=5)

    assert calls == [30] * 4
    assert sleeps == [1, 2, 4]
    assert "/api/news" in str(failure.value)
    assert "TimeoutError" in str(failure.value)
    assert "read operation timed out" in str(failure.value)
    assert "secret" not in str(failure.value)
    assert isinstance(failure.value, ConnectionError)  # existing worker transient classification
    assert client.stats().calls_used_today == 20


def test_temporary_timeout_recovers_without_repeating_success() -> None:
    outcomes = iter([TimeoutError("read timed out"), {"news": []}])
    sleeps = []

    def transport(_url, _timeout):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret"), transport=transport, sleep=sleeps.append
    )
    assert client.get_json("/api/news") == {"news": []}
    assert client.stats().requests == 2
    assert client.stats().successful_requests == 1
    assert client.stats().failed_requests == 0
    assert sleeps == [1]


@pytest.mark.parametrize("status", [401, 403])
def test_http_status_checked_before_decoding_error_body(status) -> None:
    class Response:
        headers = {}

        def __init__(self):
            self.status = status

        def read(self):
            return b"<html>Access denied</html>"

    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret"),
        transport=lambda *_args: Response(),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(EodhdAuthenticationError):
        client.get_json("/api/news")
    assert client.stats().requests == 1


def test_urllib_http_error_is_closed_and_retry_after_is_bounded() -> None:
    body = io.BytesIO(b"rate limited")
    error = HTTPError(
        "https://eodhd.com/api/news?api_token=secret", 429, "busy", {"Retry-After": "86400"}, body
    )
    outcomes = iter([error, []])
    sleeps = []

    def transport(*_args):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret"), transport=transport, sleep=sleeps.append
    )
    assert client.get_json("/api/news") == []
    assert sleeps == [30]
    assert body.closed


def test_retry_after_zero_is_respected() -> None:
    error = HTTPError("https://eodhd.com/api/news", 503, "busy", {"Retry-After": "0"}, io.BytesIO())
    outcomes = iter([error, []])
    sleeps = []

    def transport(*_args):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret"), transport=transport, sleep=sleeps.append
    )
    assert client.get_json("/api/news") == []
    assert sleeps == [0]


@pytest.mark.parametrize("status", [429, 503])
def test_persistent_http_failure_retains_status_and_closes_every_response(status) -> None:
    bodies, sleeps = [], []

    def transport(*_args):
        body = io.BytesIO(b"<html>temporarily unavailable</html>")
        bodies.append(body)
        raise HTTPError("https://eodhd.com/api/news?api_token=secret", status, "busy", {}, body)

    client = EodhdHttpClient(
        EodhdClientConfig(api_key="secret", max_attempts=2),
        transport=transport,
        sleep=sleeps.append,
    )
    with pytest.raises(ConnectionError, match=f"HTTP {status}") as failure:
        client.get_json("/api/news")
    assert all(body.closed for body in bodies)
    assert len(bodies) == 2
    assert sleeps == [1]
    assert "secret" not in str(failure.value)
    assert client.stats().failed_requests == 1
