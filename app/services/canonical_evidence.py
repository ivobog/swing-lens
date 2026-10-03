from __future__ import annotations

import base64
import hashlib
import json
import math
from collections.abc import Iterator, Mapping, MutableMapping
from datetime import UTC, date, datetime, time, timezone, tzinfo
from decimal import Decimal
from enum import Enum
from time import perf_counter_ns, process_time_ns
from typing import Any
from uuid import UUID

_PRECOMPUTED_MISSING = object()


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
        _precomputed: Mapping[int, Any] | None = None,
    ) -> Any:
        if _precomputed is not None:
            cached = _precomputed.get(id(value), _PRECOMPUTED_MISSING)
            if cached is not _PRECOMPUTED_MISSING:
                return cached
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
            return cls.canonicalize(
                value.value,
                field_name=field_name,
                _path=_path,
                _precomputed=_precomputed,
            )
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
                            _precomputed=_precomputed,
                        ),
                    )
                )
            return dict(sorted(items))
        if isinstance(value, (list, tuple, set, frozenset)):
            normalized = [
                cls.canonicalize(
                    item,
                    _path=f"{_path}[{index}]",
                    _precomputed=_precomputed,
                )
                for index, item in enumerate(value)
            ]
            if isinstance(value, (set, frozenset)) or cls._is_unordered_field(field_name):
                normalized.sort(key=cls._sort_key)
            return normalized
        raise TypeError(f"unsupported evidence value at {_path}: {type(value).__name__}")

    @classmethod
    def dumps(cls, value: Any) -> str:
        return json.dumps(
            value if cls._already_canonical_json(value) else cls.canonicalize(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    @classmethod
    def _already_canonical_json(cls, value: Any, field_name: str | None = None) -> bool:
        """Check fresh JSON without allocating another complete evidence tree.

        JSON key ordering is supplied by dumps. Other canonical transformations
        still use canonicalize, including typed values and sequence reordering.
        No validation result or fingerprint is cached for mutable input.
        """
        kind = type(value)
        if value is None or kind in (bool, int, str):
            return True
        if kind is float:
            return math.isfinite(value) and not (value == 0 and math.copysign(1, value) < 0)
        if kind is dict:
            return all(
                type(key) is str and cls._already_canonical_json(item, key)
                for key, item in value.items()
            )
        if kind is list:
            if not all(cls._already_canonical_json(item) for item in value):
                return False
            if cls._is_unordered_field(field_name):
                keys = [cls._sort_key(item) for item in value]
                return all(left <= right for left, right in zip(keys, keys[1:], strict=False))
            return True
        return False

    @classmethod
    def bytes(
        cls,
        value: Any,
        *,
        precomputed: Mapping[int, Any] | None = None,
    ) -> bytes:
        if precomputed is not None:
            return json.dumps(
                cls.canonicalize(value, _precomputed=precomputed),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        return cls.dumps(value).encode("utf-8")

    @classmethod
    def iter_bytes(
        cls,
        value: Any,
        *,
        precomputed: Mapping[int, Any] | None = None,
        fragments: MutableMapping[int, bytes] | None = None,
        telemetry: dict[str, int | float] | None = None,
    ) -> Iterator[bytes]:
        """Emit the exact canonical byte stream without building one giant value.

        ``precomputed`` retains the P1 semantic-subtree contract. ``fragments``
        is an optional caller-owned cache for the exact JSON bytes of those same
        immutable subtrees; it is deliberately not global and carries no authority
        beyond its owner's Session/transaction scope.
        """

        stats = telemetry if telemetry is not None else {}
        stats.setdefault("canonical_chunks_emitted", 0)
        stats.setdefault("stable_fragment_hits", 0)
        stats.setdefault("stable_fragment_misses", 0)
        stats.setdefault("stable_fragment_byte_reuse", 0)
        for chunk in cls._iter_bytes(
            value,
            field_name=None,
            path="$",
            precomputed=precomputed,
            fragments=fragments,
            telemetry=stats,
        ):
            stats["canonical_chunks_emitted"] += 1
            yield chunk

    @classmethod
    def streaming_bytes(
        cls,
        value: Any,
        *,
        precomputed: Mapping[int, Any] | None = None,
        fragments: MutableMapping[int, bytes] | None = None,
        telemetry: dict[str, int | float] | None = None,
    ) -> bytes:
        """Materialize the candidate stream for byte-equivalence diagnostics."""

        document = b"".join(
            cls.iter_bytes(
                value,
                precomputed=precomputed,
                fragments=fragments,
                telemetry=telemetry,
            )
        )
        if telemetry is not None:
            telemetry["canonical_byte_count"] = len(document)
            telemetry["largest_document_bytes"] = max(
                int(telemetry.get("largest_document_bytes", 0)), len(document)
            )
        return document

    @classmethod
    def fingerprint_streaming(
        cls,
        value: Any,
        *,
        precomputed: Mapping[int, Any] | None = None,
        fragments: MutableMapping[int, bytes] | None = None,
        telemetry: dict[str, int | float] | None = None,
        buffer_size: int = 256 * 1024,
    ) -> str:
        """SHA-256 the exact canonical stream with bounded temporary storage."""

        if buffer_size < 1:
            raise ValueError("canonical streaming buffer_size must be positive")
        stats = telemetry if telemetry is not None else {}
        started_wall = perf_counter_ns()
        started_cpu = process_time_ns()
        hashing_wall_ns = 0
        hashing_cpu_ns = 0
        byte_count = 0
        hash_updates = 0
        pending = bytearray()
        hasher = hashlib.sha256()

        def update(chunk: bytes | bytearray) -> None:
            nonlocal hashing_wall_ns, hashing_cpu_ns, hash_updates
            hash_wall_started = perf_counter_ns()
            hash_cpu_started = process_time_ns()
            hasher.update(chunk)
            hashing_wall_ns += perf_counter_ns() - hash_wall_started
            hashing_cpu_ns += process_time_ns() - hash_cpu_started
            hash_updates += 1

        for chunk in cls.iter_bytes(
            value,
            precomputed=precomputed,
            fragments=fragments,
            telemetry=stats,
        ):
            byte_count += len(chunk)
            if len(chunk) >= buffer_size:
                if pending:
                    update(pending)
                    pending.clear()
                update(chunk)
            else:
                if len(pending) + len(chunk) > buffer_size:
                    update(pending)
                    pending.clear()
                pending.extend(chunk)
        if pending:
            update(pending)

        total_wall_ns = perf_counter_ns() - started_wall
        total_cpu_ns = process_time_ns() - started_cpu
        stats["canonical_byte_count"] = byte_count
        stats["largest_document_bytes"] = max(
            int(stats.get("largest_document_bytes", 0)), byte_count
        )
        stats["hash_update_calls"] = hash_updates
        stats["hashing_wall_ms"] = hashing_wall_ns / 1_000_000
        stats["hashing_cpu_ms"] = hashing_cpu_ns / 1_000_000
        stats["serialization_wall_ms"] = max(0, total_wall_ns - hashing_wall_ns) / 1_000_000
        stats["serialization_cpu_ms"] = max(0, total_cpu_ns - hashing_cpu_ns) / 1_000_000
        return hasher.hexdigest()

    @classmethod
    def fingerprint(cls, value: Any) -> str:
        return hashlib.sha256(cls.bytes(value)).hexdigest()

    @classmethod
    def _iter_bytes(
        cls,
        value: Any,
        *,
        field_name: str | None,
        path: str,
        precomputed: Mapping[int, Any] | None,
        fragments: MutableMapping[int, bytes] | None,
        telemetry: dict[str, int | float],
    ) -> Iterator[bytes]:
        if precomputed is not None:
            cached = precomputed.get(id(value), _PRECOMPUTED_MISSING)
            if cached is not _PRECOMPUTED_MISSING:
                fragment = (
                    fragments.get(id(value), _PRECOMPUTED_MISSING)
                    if fragments is not None
                    else _PRECOMPUTED_MISSING
                )
                if fragment is _PRECOMPUTED_MISSING:
                    fragment = cls._json_bytes(cached)
                    if fragments is not None:
                        fragments[id(value)] = fragment
                    telemetry["stable_fragment_misses"] += 1
                else:
                    telemetry["stable_fragment_hits"] += 1
                    telemetry["stable_fragment_byte_reuse"] += len(fragment)
                yield fragment
                return

        if isinstance(value, Mapping):
            items: list[tuple[str, Any]] = []
            seen: set[str] = set()
            for key, item in value.items():
                canonical_key = str(key)
                if canonical_key in seen:
                    raise ValueError(
                        f"evidence mapping at {path} has colliding key {canonical_key!r}"
                    )
                seen.add(canonical_key)
                items.append((canonical_key, item))
            items.sort(key=lambda item: item[0])
            yield b"{"
            for index, (canonical_key, item) in enumerate(items):
                if index:
                    yield b","
                yield cls._json_bytes(canonical_key)
                yield b":"
                yield from cls._iter_bytes(
                    item,
                    field_name=canonical_key,
                    path=cls._mapping_path(path, canonical_key),
                    precomputed=precomputed,
                    fragments=fragments,
                    telemetry=telemetry,
                )
            yield b"}"
            return

        if isinstance(value, (list, tuple, set, frozenset)):
            values: Any = value
            if isinstance(value, (set, frozenset)) or cls._is_unordered_field(field_name):
                values = [
                    cls.canonicalize(
                        item,
                        _path=f"{path}[{index}]",
                        _precomputed=precomputed,
                    )
                    for index, item in enumerate(value)
                ]
                values.sort(key=cls._sort_key)
                precomputed = None
                fragments = None
            yield b"["
            for index, item in enumerate(values):
                if index:
                    yield b","
                yield from cls._iter_bytes(
                    item,
                    field_name=None,
                    path=f"{path}[{index}]",
                    precomputed=precomputed,
                    fragments=fragments,
                    telemetry=telemetry,
                )
            yield b"]"
            return

        canonical = cls.canonicalize(
            value,
            field_name=field_name,
            _path=path,
            _precomputed=precomputed,
        )
        yield cls._json_bytes(canonical)

    @staticmethod
    def _json_bytes(value: Any) -> bytes:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

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
