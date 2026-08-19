from __future__ import annotations

import pytest

from app.services.ceri.sec.client import (
    SecClientConfig,
    SecEdgarClient,
    SecEdgarError,
    _BufferedResponse,
)


def test_429_honors_retry_after_and_is_counted() -> None:
    responses = iter(
        [
            _BufferedResponse(429, {"retry-after": "3"}, b""),
            _BufferedResponse(200, {"Content-Type": "application/json"}, b'{"ok":true}'),
        ]
    )
    sleeps: list[float] = []
    client = SecEdgarClient(
        SecClientConfig(requests_per_second=10, max_attempts=2),
        transport=lambda *_args: next(responses),
        sleep=sleeps.append,
    )

    assert client.get_json("/submissions/CIK0000123456.json") == {"ok": True}
    stats = client.stats()
    assert stats.requests == 2
    assert stats.retries == 1
    assert stats.http_429 == 1
    assert stats.http_2xx == 1
    assert stats.submissions_requests == 2
    assert stats.bytes_downloaded == len(b'{"ok":true}')
    assert 3.0 in sleeps


def test_5xx_and_timeout_remain_retryable_failures() -> None:
    responses = iter(
        [
            _BufferedResponse(503, {}, b""),
            _BufferedResponse(200, {"Content-Type": "application/json"}, b"{}"),
        ]
    )
    server_client = SecEdgarClient(
        SecClientConfig(requests_per_second=10, max_attempts=2),
        transport=lambda *_args: next(responses),
        sleep=lambda _seconds: None,
    )
    assert server_client.get_json("/submissions/CIK0000123456.json") == {}
    assert server_client.stats().http_5xx == 1
    assert server_client.stats().retries == 1

    timeout_client = SecEdgarClient(
        SecClientConfig(requests_per_second=10, max_attempts=2),
        transport=lambda *_args: (_ for _ in ()).throw(TimeoutError("timeout")),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(SecEdgarError):
        timeout_client.get_json("/submissions/CIK0000123456.json")
    assert timeout_client.stats().timeouts == 2
    assert timeout_client.stats().retries == 1


def test_legacy_0001_document_uses_accession_text_fallback_after_404() -> None:
    requested_urls: list[str] = []

    def transport(url: str, *_args):
        requested_urls.append(url)
        if url.endswith("/0001.txt"):
            return _BufferedResponse(404, {}, b"")
        return _BufferedResponse(200, {"Content-Type": "text/plain"}, b"<SEC-DOCUMENT>")

    client = SecEdgarClient(
        SecClientConfig(requests_per_second=10, max_attempts=1),
        transport=transport,
        sleep=lambda _seconds: None,
    )

    result = client.archive_document(
        "0000729580",
        "0000950110-01-000244",
        "0001.txt",
    )

    assert result == "<SEC-DOCUMENT>"
    assert requested_urls == [
        "https://www.sec.gov/Archives/edgar/data/729580/000095011001000244/0001.txt",
        "https://www.sec.gov/Archives/edgar/data/729580/000095011001000244/"
        "0000950110-01-000244.txt",
    ]
    assert client.stats().filing_document_requests == 2
