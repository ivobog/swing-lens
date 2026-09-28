from __future__ import annotations

import io
import urllib.error

from scripts.ops import lifecycle_probe


def test_http_500_is_reachable_application_failure_with_bounded_body(monkeypatch) -> None:
    body = b'{"error":"readiness exploded","password":"hidden"}' + (b"x" * 3000)

    def fail(_url, timeout):
        assert timeout == 3
        raise urllib.error.HTTPError(
            "http://127.0.0.1:8000/ready/core",
            500,
            "Internal Server Error",
            {},
            io.BytesIO(body),
        )

    monkeypatch.setattr(lifecycle_probe.urllib.request, "urlopen", fail)

    report = lifecycle_probe._http_json("http://127.0.0.1:8000/ready/core")

    assert report["reachable"] is True
    assert report["statusCode"] == 500
    assert report["responseState"] == "HTTP_APPLICATION_FAILURE"
    assert len(report["bodySnippet"]) <= 2000
    assert "hidden" not in report["bodySnippet"]
    assert lifecycle_probe._core_diagnostic_boundary(report) == "CORE_HTTP_APPLICATION_FAILURE"


def test_connection_refused_is_unreachable_not_http_failure(monkeypatch) -> None:
    def refuse(_url, timeout):
        assert timeout == 3
        raise urllib.error.URLError(ConnectionRefusedError("connection refused"))

    monkeypatch.setattr(lifecycle_probe.urllib.request, "urlopen", refuse)

    report = lifecycle_probe._http_json("http://127.0.0.1:8000/ready/core")

    assert report["reachable"] is False
    assert report["statusCode"] == 0
    assert report["responseState"] == "UNREACHABLE"
    assert lifecycle_probe._core_diagnostic_boundary(report) == "CORE_ENDPOINT_UNREACHABLE"


def test_valid_http_503_readiness_payload_is_not_ready_not_application_error() -> None:
    report = lifecycle_probe._http_response_report(503, b'{"status":"failed"}')

    assert report["reachable"] is True
    assert report["responseState"] == "NOT_READY"
    assert lifecycle_probe._core_diagnostic_boundary(report) == "CORE_READINESS_FAILED"
