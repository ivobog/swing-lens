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


@dataclass(frozen=True)
class SecClientConfig:
    base_url: str = "https://data.sec.gov"
    archive_url: str = "https://www.sec.gov/Archives/edgar/data"
    company_tickers_url: str = "https://www.sec.gov/files/company_tickers.json"
    user_agent: str = "SwingLens/0.1.0 operator@example.invalid"
    requests_per_second: float = 2.0
    timeout_seconds: int = 30


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
        now = time.monotonic()
        minimum_gap = 1.0 / self.config.requests_per_second
        if self._last_request and now - self._last_request < minimum_gap:
            self._sleep(minimum_gap - (now - self._last_request))
        self._last_request = time.monotonic()
        url = path if absolute else self.config.base_url.rstrip("/") + "/" + path.lstrip("/")
        self.requests += 1
        try:
            response = self._transport(url, self.config.timeout_seconds, self.config.user_agent)
            if isinstance(response, (dict, list, str)):
                payload = response
            else:
                status = int(getattr(response, "status", getattr(response, "code", 200)))
                if status in {403, 429}:
                    raise SecEdgarError(f"SEC fair-access response HTTP {status}")
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
            self.last_success_at = datetime.now(UTC)
            return payload
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            self.failures += 1
            raise SecEdgarError("SEC network or response failure") from exc

    @staticmethod
    def _urllib_transport(url: str, timeout: int, user_agent: str) -> Any:
        request = Request(
            url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - provider URL is configured
            return response
