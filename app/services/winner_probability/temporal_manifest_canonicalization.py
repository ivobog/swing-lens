from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def canonicalize_manifest_value(value: Any, *, field_name: str | None = None) -> Any:
    """Convert a temporal manifest value to its deterministic JSON representation.

    Timestamp values retain exact microseconds but are represented at UTC. Date-only
    values never pass through timezone conversion. Lists retain domain order except
    for explicitly unordered reason-code collections.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("manifest timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return canonicalize_manifest_value(value.value, field_name=field_name)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("manifest decimals must be finite")
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("manifest floats must be finite")
        return value
    if isinstance(value, dict):
        return {
            str(key): canonicalize_manifest_value(item, field_name=str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        normalized = [canonicalize_manifest_value(item) for item in value]
        if isinstance(value, (set, frozenset)) or _is_unordered_reason_field(field_name):
            normalized.sort(key=_canonical_sort_key)
        return normalized
    raise TypeError(f"unsupported manifest value type: {type(value).__name__}")


def canonical_manifest_bytes(value: Any) -> bytes:
    canonical = canonicalize_manifest_value(value)
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _is_unordered_reason_field(field_name: str | None) -> bool:
    if field_name is None:
        return False
    normalized = field_name.lower()
    return (
        normalized == "reason_codes"
        or normalized.endswith("_reason_codes")
        or normalized.endswith("_reason_codes_json")
    )


def _canonical_sort_key(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
