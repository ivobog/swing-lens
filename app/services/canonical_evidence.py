from __future__ import annotations

import base64
import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timezone, tzinfo
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


class CanonicalEvidenceSerializer:
    """Canonical JSON encoding for durable evidence and safety-critical hashes.

    Datetimes are aware, UTC-normalized, and always retain six fractional digits.
    Date and time values use disjoint ISO profiles (date-only versus time-only),
    decimals use a normalized fixed-point form, and unordered containers are
    ordered by their canonical JSON bytes. Unsupported runtime objects fail closed.
    """

    @classmethod
    def canonicalize(
        cls,
        value: Any,
        *,
        field_name: str | None = None,
        _path: str = "$",
    ) -> Any:
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"evidence timestamp at {_path} must be timezone-aware")
            return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, time):
            if value.tzinfo is not None and value.utcoffset() is None:
                raise ValueError(f"evidence time at {_path} has an invalid timezone")
            return value.isoformat(timespec="microseconds")
        if isinstance(value, timezone):
            return cls._timezone_text(value, _path)
        if isinstance(value, tzinfo):
            key = getattr(value, "key", None)
            if key:
                return str(key)
            raise TypeError(f"unsupported evidence timezone at {_path}: {value!r}")
        if isinstance(value, UUID):
            return str(value).lower()
        if isinstance(value, Enum):
            return cls.canonicalize(value.value, field_name=field_name, _path=_path)
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError(f"evidence decimal at {_path} must be finite")
            normalized = value.normalize()
            if normalized == 0:
                return "0"
            return format(normalized, "f")
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(f"evidence float at {_path} must be finite")
            return 0.0 if value == 0 else value
        if isinstance(value, bytes):
            return {"$bytes_base64": base64.b64encode(value).decode("ascii")}
        if isinstance(value, Mapping):
            items: list[tuple[str, Any]] = []
            seen: set[str] = set()
            for key, item in value.items():
                canonical_key = str(key)
                if canonical_key in seen:
                    raise ValueError(
                        f"evidence mapping at {_path} has colliding key {canonical_key!r}"
                    )
                seen.add(canonical_key)
                items.append(
                    (
                        canonical_key,
                        cls.canonicalize(
                            item,
                            field_name=canonical_key,
                            _path=cls._mapping_path(_path, canonical_key),
                        ),
                    )
                )
            return dict(sorted(items))
        if isinstance(value, (list, tuple, set, frozenset)):
            normalized = [
                cls.canonicalize(item, _path=f"{_path}[{index}]")
                for index, item in enumerate(value)
            ]
            if isinstance(value, (set, frozenset)) or cls._is_unordered_field(field_name):
                normalized.sort(key=cls._sort_key)
            return normalized
        raise TypeError(f"unsupported evidence value at {_path}: {type(value).__name__}")

    @classmethod
    def dumps(cls, value: Any) -> str:
        return json.dumps(
            cls.canonicalize(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    @classmethod
    def bytes(cls, value: Any) -> bytes:
        return cls.dumps(value).encode("utf-8")

    @classmethod
    def fingerprint(cls, value: Any) -> str:
        return hashlib.sha256(cls.bytes(value)).hexdigest()

    @staticmethod
    def _timezone_text(value: timezone, path: str) -> str:
        offset = value.utcoffset(None)
        if offset is None:
            raise ValueError(f"evidence timezone at {path} has no fixed offset")
        total_seconds = int(offset.total_seconds())
        sign = "+" if total_seconds >= 0 else "-"
        total_seconds = abs(total_seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        suffix = f":{seconds:02d}" if seconds else ""
        return f"UTC{sign}{hours:02d}:{minutes:02d}{suffix}"

    @staticmethod
    def _is_unordered_field(field_name: str | None) -> bool:
        if field_name is None:
            return False
        normalized = field_name.lower()
        return (
            normalized == "reason_codes"
            or normalized.endswith("_reason_codes")
            or normalized.endswith("_reason_codes_json")
        )

    @classmethod
    def _sort_key(cls, value: Any) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    @staticmethod
    def _mapping_path(parent: str, key: str) -> str:
        return f"{parent}.{key}" if key.isidentifier() else f"{parent}[{key!r}]"


canonical_evidence_bytes = CanonicalEvidenceSerializer.bytes
canonical_evidence_fingerprint = CanonicalEvidenceSerializer.fingerprint
canonicalize_evidence_value = CanonicalEvidenceSerializer.canonicalize
