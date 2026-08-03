from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResourceLimitExceeded(ValueError):
    code: str
    message: str
    limit: int
    observed: int

    def __str__(self) -> str:
        return self.message


def enforce_row_limit(
    observed: int,
    limit: int,
    *,
    resource: str,
    code: str = "ROW_LIMIT_EXCEEDED",
) -> None:
    if observed > limit:
        raise ResourceLimitExceeded(
            code=code,
            message=f"{resource} has {observed} rows; the configured limit is {limit}.",
            limit=limit,
            observed=observed,
        )


def enforce_column_limit(
    observed: int,
    limit: int,
    *,
    resource: str,
) -> None:
    if observed > limit:
        raise ResourceLimitExceeded(
            code="COLUMN_LIMIT_EXCEEDED",
            message=f"{resource} has {observed} columns; the configured limit is {limit}.",
            limit=limit,
            observed=observed,
        )


def enforce_byte_limit(
    content: str | bytes,
    limit_bytes: int,
    *,
    resource: str,
) -> None:
    observed = len(content if isinstance(content, bytes) else content.encode("utf-8"))
    if observed > limit_bytes:
        raise ResourceLimitExceeded(
            code="BYTE_LIMIT_EXCEEDED",
            message=(
                f"{resource} is {observed} bytes; the configured limit is {limit_bytes} bytes."
            ),
            limit=limit_bytes,
            observed=observed,
        )


def limit_error_payload(exc: ResourceLimitExceeded, *, hint: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
        "limit": exc.limit,
        "observed": exc.observed,
    }
    if hint:
        payload["hint"] = hint
    return payload
