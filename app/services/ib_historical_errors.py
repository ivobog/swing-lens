from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class IBHistoricalErrorCategory(StrEnum):
    HISTORICAL_DATA_UNAVAILABLE = "HISTORICAL_DATA_UNAVAILABLE"
    HISTORICAL_PACING = "HISTORICAL_PACING"
    HISTORICAL_TRANSIENT = "HISTORICAL_TRANSIENT"
    HISTORICAL_UNKNOWN_162 = "HISTORICAL_UNKNOWN_162"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    PROVIDER_ERROR = "PROVIDER_ERROR"


@dataclass(frozen=True)
class IBHistoricalErrorClassification:
    category: IBHistoricalErrorCategory
    retryable: bool
    systemic: bool = False


_EODCHART_UNAVAILABLE = re.compile(
    r"\bno\s+data\s+of\s+type\s+eodchart\s+is\s+available\b",
    re.IGNORECASE,
)
_PACING_MESSAGES = (
    "historical data request pacing violation",
    "historical market data pacing violation",
    "making identical historical data requests within 15 seconds",
    "making six or more historical data requests for the same contract",
)
_TRANSIENT_MESSAGES = (
    "historical market data service is currently unavailable",
    "historical data service is currently unavailable",
    "hmds data farm connection is broken",
    "hmds data farm is disconnected",
    "api historical data query cancelled",
)


def normalize_ib_message(message: str) -> str:
    """Normalize provider prose without broadening category matches."""

    return " ".join(str(message).casefold().split())


def classify_ib_historical_error(
    code: int,
    message: str,
) -> IBHistoricalErrorClassification:
    """Classify an IB historical callback using both its code and message."""

    numeric_code = int(code)
    normalized = normalize_ib_message(message)
    if numeric_code == 321:
        return IBHistoricalErrorClassification(
            IBHistoricalErrorCategory.PROVIDER_REJECTED,
            retryable=False,
        )
    if numeric_code != 162:
        return IBHistoricalErrorClassification(
            IBHistoricalErrorCategory.PROVIDER_ERROR,
            retryable=True,
        )
    if _EODCHART_UNAVAILABLE.search(normalized):
        return IBHistoricalErrorClassification(
            IBHistoricalErrorCategory.HISTORICAL_DATA_UNAVAILABLE,
            retryable=False,
            systemic=True,
        )
    if any(marker in normalized for marker in _PACING_MESSAGES):
        return IBHistoricalErrorClassification(
            IBHistoricalErrorCategory.HISTORICAL_PACING,
            retryable=True,
        )
    if any(marker in normalized for marker in _TRANSIENT_MESSAGES):
        return IBHistoricalErrorClassification(
            IBHistoricalErrorCategory.HISTORICAL_TRANSIENT,
            retryable=True,
        )
    return IBHistoricalErrorClassification(
        IBHistoricalErrorCategory.HISTORICAL_UNKNOWN_162,
        retryable=True,
    )


SYSTEMIC_CIRCUIT_CATEGORIES = frozenset(
    {IBHistoricalErrorCategory.HISTORICAL_DATA_UNAVAILABLE.value}
)
