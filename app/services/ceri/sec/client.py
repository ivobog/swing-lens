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
        return self.get_text(url, absolute=True)

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
            try:
                response = self._transport(url, self.config.timeout_seconds, self.config.user_agent)
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
                        raise SecFairAccessError("SEC fair-access response HTTP 403")
                    if status == 429 or status >= 500:
                        if attempt >= attempts:
                            raise SecEdgarError(
                                f"SEC transient request failure after {attempts} attempts"
                            )
                        self._sleep(_retry_after(headers) or min(30.0, 2 ** (attempt - 1)))
                        continue
                    if status >= 400:
                        raise SecEdgarError(f"SEC request failed with HTTP {status}")
                    body = response.read()
                    if isinstance(body, bytes):
                        body = body.decode("utf-8")
                    content_type = str(getattr(response, "headers", {}).get("Content-Type", ""))
                    payload = (
                        body
                        if "json" not in content_type and not body.lstrip().startswith(("{", "["))
                        else json.loads(body)
                    )
                self._cache[url] = payload
                self.last_success_at = datetime.now(UTC)
                return payload
            except SecFairAccessError:
                self.failures += 1
                raise
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                status = int(getattr(exc, "code", 0) or 0)
                if status == 403:
                    self.failures += 1
                    raise SecFairAccessError("SEC fair-access response HTTP 403") from exc
                if (status == 429 or status >= 500 or status == 0) and attempt < attempts:
                    self._sleep(min(30.0, 2 ** (attempt - 1)))
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
            self._sleep(minimum_gap - (now - self._last_request))
        self._last_request = time.monotonic()

    @staticmethod
    def _urllib_transport(url: str, timeout: int, user_agent: str) -> Any:
        request = Request(
            url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - provider URL is configured
            return response


def _retry_after(headers: dict[str, str]) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
