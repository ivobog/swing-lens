from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def canonicalize_manifest_value(
    value: Any,
    *,
    field_name: str | None = None,
    _path: str = "$",
) -> Any:
    """Convert a temporal manifest value to its deterministic JSON representation.

    Timestamp values retain exact microseconds but are represented at UTC. Date-only
    values never pass through timezone conversion. Lists retain domain order except
    for explicitly unordered reason-code collections.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"manifest timestamp at {_path} must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return canonicalize_manifest_value(
            value.value,
            field_name=field_name,
            _path=_path,
        )
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"manifest decimal at {_path} must be finite")
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"manifest float at {_path} must be finite")
        return value
    if isinstance(value, dict):
        return {
            str(key): canonicalize_manifest_value(
                item,
                field_name=str(key),
                _path=_mapping_path(_path, str(key)),
            )
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        normalized = [
            canonicalize_manifest_value(item, _path=f"{_path}[{index}]")
            for index, item in enumerate(value)
        ]
        if isinstance(value, (set, frozenset)) or _is_unordered_reason_field(field_name):
            normalized.sort(key=_canonical_sort_key)
        return normalized
    raise TypeError(f"unsupported manifest value at {_path}: {type(value).__name__}")


def canonicalize_temporal_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return JSON-safe metadata using the same primitive rules as manifest hashing."""
    canonical = canonicalize_manifest_value(dict(metadata), _path="metadata")
    if not isinstance(canonical, dict):  # pragma: no cover - defensive type narrowing
        raise TypeError("temporal metadata must canonicalize to an object")
    return canonical


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


def _mapping_path(parent: str, key: str) -> str:
    if key.isidentifier():
        return f"{parent}.{key}"
    return f"{parent}[{key!r}]"
