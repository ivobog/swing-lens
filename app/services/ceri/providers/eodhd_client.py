from __future__ import annotations

import json
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from app.services.redaction import redact_text


class EodhdProviderError(RuntimeError):
    """Safe, provider-facing failure which never contains the API token."""


class EodhdAuthenticationError(EodhdProviderError):
    pass


class EodhdQuotaExceeded(EodhdProviderError):
    pass


class EodhdTransientError(EodhdProviderError, ConnectionError):
    """A bounded HTTP attempt budget exhausted; the durable job may retry it."""


@dataclass(frozen=True)
class EodhdClientConfig:
    base_url: str = "https://eodhd.com"
    api_key: str | None = None
    timeout_seconds: int = 30
    max_attempts: int = 4
    requests_per_minute: int = 300
    daily_call_budget: int = 80_000
    user_agent: str = "SwingLens/0.1.0"


@dataclass(frozen=True)
class EodhdRequestStats:
    requests: int
    successful_requests: int
    failed_requests: int
    retries: int
    calls_used_today: int
    last_success_at: datetime | None
    last_error: str | None


class EodhdHttpClient:
    """Small, injectable HTTP boundary for EODHD.

    The default transport uses urllib so the application does not acquire a
    second production HTTP framework dependency. Tests can provide a transport
    returning a decoded JSON object or an object with ``status``/``headers``/
    ``read`` attributes.
    """

    def __init__(
        self,
        config: EodhdClientConfig,
        *,
        transport: Callable[[str, int], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._transport = transport or self._urllib_transport
        self._sleep = sleep
        self._clock = clock
        self._minute_requests: deque[float] = deque()
        self._day = datetime.now(UTC).date()
        self._calls_today = 0
        self._requests = 0
        self._successful = 0
        self._failed = 0
        self._retries = 0
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.config.api_key)

    def stats(self) -> EodhdRequestStats:
        return EodhdRequestStats(
            requests=self._requests,
            successful_requests=self._successful,
            failed_requests=self._failed,
            retries=self._retries,
            calls_used_today=self._calls_used_today(),
            last_success_at=self._last_success_at,
            last_error=self._last_error,
        )

    def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        call_cost: int = 1,
    ) -> Any:
        if not self.configured:
            raise EodhdAuthenticationError("EODHD_API_KEY is not configured")
        query = dict(params or {})
        query.update({"api_token": self.config.api_key, "fmt": "json"})
        url = urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))
        url = f"{url}?{urlencode(query, doseq=True)}"
        attempts = max(1, self.config.max_attempts)
        for attempt in range(1, attempts + 1):
            self._reserve(call_cost)
            self._requests += 1
            retry_after = None
            try:
                response = self._transport(url, self.config.timeout_seconds)
                payload, status, headers = self._decode_response(response)
                if status in {401, 403}:
                    raise EodhdAuthenticationError("EODHD rejected credentials or entitlement")
                if status == 429 or status >= 500:
                    raise _RetryableProviderError(status, _retry_after(headers))
                if status >= 400:
                    raise EodhdProviderError(f"EODHD request failed with HTTP {status}")
                self._successful += 1
                self._last_success_at = datetime.now(UTC)
                self._last_error = None
                return payload
            except EodhdAuthenticationError as exc:
                self._failed += 1
                self._last_error = redact_text(str(exc))
                raise
            except _RetryableProviderError as exc:
                self._last_error = str(exc)
                retry_after = exc.retry_after
            except HTTPError as exc:
                try:
                    self._last_error = f"HTTP {exc.code}"
                    if exc.code in {401, 403}:
                        self._failed += 1
                        raise EodhdAuthenticationError(
                            "EODHD rejected credentials or entitlement"
                        ) from None
                    if exc.code != 429 and exc.code < 500:
                        self._failed += 1
                        raise EodhdProviderError(
                            f"EODHD request failed with HTTP {exc.code}"
                        ) from None
                    retry_after = _retry_after(
                        {str(k).lower(): str(v) for k, v in dict(exc.headers or {}).items()}
                    )
                finally:
                    exc.close()
            except (URLError, OSError, HTTPException, json.JSONDecodeError, UnicodeError) as exc:
                self._last_error = _safe_error(exc, self.config.api_key)
            except EodhdProviderError as exc:
                self._failed += 1
                self._last_error = _safe_error(exc, self.config.api_key)
                raise
            if attempt >= attempts:
                self._failed += 1
                raise EodhdTransientError(
                    f"EODHD {path.split('?', 1)[0]} failed after {attempts} attempts "
                    f"(timeout={self.config.timeout_seconds}s): {self._last_error}"
                ) from None
            self._retries += 1
            self._sleep(min(30.0, retry_after if retry_after is not None else 2 ** (attempt - 1)))
        raise AssertionError("unreachable")

    def safe_metadata(self) -> dict[str, Any]:
        stats = self.stats()
        return {
            "base_url": self.config.base_url,
            "configured": self.configured,
            "timeout_seconds": self.config.timeout_seconds,
            "max_attempts": self.config.max_attempts,
            "requests_per_minute": self.config.requests_per_minute,
            "daily_call_budget": self.config.daily_call_budget,
            "stats": stats.__dict__,
        }

    def _reserve(self, call_cost: int) -> None:
        if call_cost < 1:
            raise ValueError("call_cost must be positive")
        now = self._clock()
        while self._minute_requests and now - self._minute_requests[0] >= 60:
            self._minute_requests.popleft()
        if len(self._minute_requests) >= self.config.requests_per_minute:
            raise EodhdQuotaExceeded("EODHD application request-per-minute budget reached")
        if self._calls_used_today() + call_cost > self.config.daily_call_budget:
            raise EodhdQuotaExceeded("EODHD application daily call budget reached")
        self._minute_requests.append(now)
        self._calls_today += call_cost

    @property
    def _calls_today(self) -> int:
        return self._calls_used_today_value

    @_calls_today.setter
    def _calls_today(self, value: int) -> None:
        self._calls_used_today_value = value

    def _calls_used_today(self) -> int:
        today = datetime.now(UTC).date()
        if today != self._day:
            self._day = today
            self._calls_used_today_value = 0
        return self._calls_today

    def _urllib_transport(self, url: str, timeout: int) -> Any:
        request = Request(url, headers={"User-Agent": self.config.user_agent})
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed provider base URL
            return _BufferedResponse(
                status=int(getattr(response, "status", getattr(response, "code", 200))),
                headers=dict(getattr(response, "headers", {})),
                body=response.read(),
            )

    @staticmethod
    def _decode_response(response: Any) -> tuple[Any, int, dict[str, str]]:
        if isinstance(response, (dict, list)):
            return response, 200, {}
        status = int(getattr(response, "status", getattr(response, "code", 200)))
        headers = {
            str(key).lower(): str(value)
            for key, value in dict(getattr(response, "headers", {})).items()
        }
        # HTTP error bodies are often HTML. Preserve the status even when the
        # response cannot be decoded as JSON.
        if status >= 400:
            return None, status, headers
        body = response.read()
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        return json.loads(body), status, headers


class _RetryableProviderError(RuntimeError):
    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"HTTP {status}")
        self.retry_after = retry_after


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
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _safe_error(exc: Exception, secret: str | None = None) -> str:
    text = redact_text(f"{exc.__class__.__name__}: {exc}")
    if secret:
        text = text.replace(secret, "[redacted]")
    text = text.replace("\n", " ").strip()
    return text[:300] or exc.__class__.__name__
