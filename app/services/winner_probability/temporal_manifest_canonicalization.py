from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.canonical_evidence import CanonicalEvidenceSerializer


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
    return CanonicalEvidenceSerializer.canonicalize(
        value,
        field_name=field_name,
        _path=_path,
    )


def canonicalize_temporal_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return JSON-safe metadata using the same primitive rules as manifest hashing."""
    canonical = canonicalize_manifest_value(dict(metadata), _path="metadata")
    if not isinstance(canonical, dict):  # pragma: no cover - defensive type narrowing
        raise TypeError("temporal metadata must canonicalize to an object")
    return canonical


def canonical_manifest_bytes(value: Any) -> bytes:
    return CanonicalEvidenceSerializer.bytes(value)
