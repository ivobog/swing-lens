from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SecEdgarError(RuntimeError):
    pass


class SecEdgarNotFoundError(SecEdgarError):
    pass


class SecFairAccessError(SecEdgarError):
    pass


@dataclass(frozen=True)
class SecClientConfig:
    base_url: str = "https://data.sec.gov"
    archive_url: str = "https://www.sec.gov/Archives/edgar/data"
    company_tickers_url: str = "https://www.sec.gov/files/company_tickers.json"
    user_agent: str = "SwingLens/0.1.0 operator@example.invalid"
    requests_per_second: float = 2.0
    timeout_seconds: int = 30
    max_attempts: int = 3


@dataclass(frozen=True)
class SecClientStats:
    requests: int
    failures: int
    retries: int
    timeouts: int
    http_2xx: int
    http_403: int
    http_429: int
    http_5xx: int
    company_ticker_requests: int
    submissions_requests: int
    filing_document_requests: int
    other_requests: int
    bytes_downloaded: int
    pacing_sleep_ms: float
    retry_sleep_ms: float
    http_wait_ms: float


class SecEdgarClient:
    """Fair-access SEC JSON client with an injectable transport and redacted errors."""

    def __init__(
        self,
        config: SecClientConfig | None = None,
        *,
        transport: Callable[[str, int, str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or SecClientConfig()
        self._transport = transport or self._urllib_transport
        self._sleep = sleep
        self._last_request = 0.0
        self.requests = 0
        self.failures = 0
        self.last_success_at: datetime | None = None
        self._cache: dict[str, Any] = {}
        self.retries = 0
        self.timeouts = 0
        self.http_2xx = 0
        self.http_403 = 0
        self.http_429 = 0
        self.http_5xx = 0
        self.company_ticker_requests = 0
        self.submissions_requests = 0
        self.filing_document_requests = 0
        self.other_requests = 0
        self.bytes_downloaded = 0
        self.pacing_sleep_ms = 0.0
        self.retry_sleep_ms = 0.0
        self.http_wait_ms = 0.0

    def stats(self) -> SecClientStats:
        return SecClientStats(
            requests=self.requests,
            failures=self.failures,
            retries=self.retries,
            timeouts=self.timeouts,
            http_2xx=self.http_2xx,
            http_403=self.http_403,
            http_429=self.http_429,
            http_5xx=self.http_5xx,
            company_ticker_requests=self.company_ticker_requests,
            submissions_requests=self.submissions_requests,
            filing_document_requests=self.filing_document_requests,
            other_requests=self.other_requests,
            bytes_downloaded=self.bytes_downloaded,
            pacing_sleep_ms=self.pacing_sleep_ms,
            retry_sleep_ms=self.retry_sleep_ms,
            http_wait_ms=self.http_wait_ms,
        )

    def submissions(self, cik: str) -> dict[str, Any]:
        normalized = str(cik).zfill(10)
        return self.get_json(f"/submissions/CIK{normalized}.json")

    def company_tickers(self) -> dict[str, Any]:
        return self.get_json_absolute(self.config.company_tickers_url)

    def archive_document(self, cik: str, accession: str, document: str) -> str:
        normalized_cik = str(cik).lstrip("0") or "0"
        normalized_accession = accession.replace("-", "")
        url = (
            f"{self.config.archive_url.rstrip('/')}/{normalized_cik}/"
            f"{normalized_accession}/{document}"
        )
        try:
            return self.get_text(url, absolute=True)
        except SecEdgarNotFoundError:
            # Historical submissions metadata can expose the placeholder
            # primaryDocument name ``0001.txt`` even though EDGAR stores only
            # the accession-named full submission text in that directory.
            # This is an availability fallback for the same filing identity;
            # it does not alter parser/extractor behavior for documents that
            # were already retrievable.
            if document.lower() != "0001.txt":
                raise
            fallback_url = (
                f"{self.config.archive_url.rstrip('/')}/{normalized_cik}/"
                f"{normalized_accession}/{accession}.txt"
            )
            return self.get_text(fallback_url, absolute=True)

    def get_json(self, path: str) -> dict[str, Any]:
        value = self._request(path)
        if not isinstance(value, dict):
            raise SecEdgarError("SEC returned a non-object JSON response")
        return value

    def get_json_absolute(self, url: str) -> dict[str, Any]:
        value = self._request(url, absolute=True)
        if not isinstance(value, dict):
            raise SecEdgarError("SEC returned a non-object JSON response")
        return value

    def get_text(self, path: str, *, absolute: bool = False) -> str:
        value = self._request(path, absolute=absolute)
        if not isinstance(value, str):
            raise SecEdgarError("SEC returned an unexpected document response")
        return value

    def _request(self, path: str, *, absolute: bool = False) -> Any:
        url = path if absolute else self.config.base_url.rstrip("/") + "/" + path.lstrip("/")
        if url in self._cache:
            return self._cache[url]
        attempts = max(1, self.config.max_attempts)
        for attempt in range(1, attempts + 1):
            self._pace()
            self.requests += 1
            self._count_request_type(url)
            request_started = time.perf_counter()
            wait_recorded = False
            try:
                response = self._transport(url, self.config.timeout_seconds, self.config.user_agent)
                self.http_wait_ms += (time.perf_counter() - request_started) * 1000
                wait_recorded = True
                if isinstance(response, (dict, list, str)):
                    payload = response
                    status = 200
                    headers: dict[str, str] = {}
                else:
                    status = int(getattr(response, "status", getattr(response, "code", 200)))
                    headers = {
                        str(key).lower(): str(value)
                        for key, value in dict(getattr(response, "headers", {})).items()
                    }
                    if status == 403:
                        self.http_403 += 1
                        raise SecFairAccessError("SEC fair-access response HTTP 403")
                    if status == 429 or status >= 500:
                        self.http_429 += int(status == 429)
                        self.http_5xx += int(status >= 500)
                        if attempt >= attempts:
                            raise SecEdgarError(
                                f"SEC transient request failure after {attempts} attempts"
                            )
                        self._retry_sleep(_retry_after(headers) or min(30.0, 2 ** (attempt - 1)))
                        continue
                    if status >= 400:
                        if status == 404:
                            raise SecEdgarNotFoundError("SEC archive object was not found")
                        raise SecEdgarError(f"SEC request failed with HTTP {status}")
                    body = response.read()
                    if isinstance(body, bytes):
                        self.bytes_downloaded += len(body)
                        body = body.decode("utf-8")
                    content_type = str(getattr(response, "headers", {}).get("Content-Type", ""))
                    payload = (
                        body
                        if "json" not in content_type and not body.lstrip().startswith(("{", "["))
                        else json.loads(body)
                    )
                if isinstance(response, (dict, list, str)):
                    self.bytes_downloaded += _payload_bytes(payload)
                self.http_2xx += 1
                self._cache[url] = payload
                self.last_success_at = datetime.now(UTC)
                return payload
            except SecFairAccessError:
                self.failures += 1
                raise
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                if not wait_recorded:
                    self.http_wait_ms += max(0.0, (time.perf_counter() - request_started) * 1000)
                status = int(getattr(exc, "code", 0) or 0)
                self.http_403 += int(status == 403)
                self.http_429 += int(status == 429)
                self.http_5xx += int(status >= 500)
                self.timeouts += int(isinstance(exc, TimeoutError))
                if status == 403:
                    self.failures += 1
                    raise SecFairAccessError("SEC fair-access response HTTP 403") from exc
                if status == 404:
                    self.failures += 1
                    raise SecEdgarNotFoundError("SEC archive object was not found") from exc
                if (status == 429 or status >= 500 or status == 0) and attempt < attempts:
                    self._retry_sleep(min(30.0, 2 ** (attempt - 1)))
                    continue
                self.failures += 1
                raise SecEdgarError("SEC network or response failure") from exc
            except SecEdgarError:
                self.failures += 1
                raise
        self.failures += 1
        raise SecEdgarError("SEC request exhausted retry budget")

    def _pace(self) -> None:
        now = time.monotonic()
        minimum_gap = 1.0 / max(self.config.requests_per_second, 0.1)
        if self._last_request and now - self._last_request < minimum_gap:
            delay = minimum_gap - (now - self._last_request)
            self.pacing_sleep_ms += delay * 1000
            self._sleep(delay)
        self._last_request = time.monotonic()

    def _retry_sleep(self, seconds: float) -> None:
        self.retries += 1
        self.retry_sleep_ms += seconds * 1000
        self._sleep(seconds)

    def _count_request_type(self, url: str) -> None:
        if "company_tickers.json" in url:
            self.company_ticker_requests += 1
        elif "/submissions/CIK" in url:
            self.submissions_requests += 1
        elif "/Archives/edgar/data/" in url:
            self.filing_document_requests += 1
        else:
            self.other_requests += 1

    @staticmethod
    def _urllib_transport(url: str, timeout: int, user_agent: str) -> Any:
        request = Request(url, headers={"User-Agent": user_agent})
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - provider URL is configured
            return _BufferedResponse(
                status=int(getattr(response, "status", getattr(response, "code", 200))),
                headers=dict(getattr(response, "headers", {})),
                body=response.read(),
            )


@dataclass(frozen=True)
class _BufferedResponse:
    """Response data copied while urllib's context manager is still open."""

    status: int
    headers: dict[str, str]
    body: bytes

    def read(self) -> bytes:
        return self.body


def _retry_after(headers: dict[str, str]) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _payload_bytes(payload: Any) -> int:
    if isinstance(payload, str):
        return len(payload.encode("utf-8"))
    try:
        return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return 0
