"""Fresh Sector evidence copies that preserve shared JSON without changing bytes."""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical

HASH_PROGRESS_MIN_BYTES = 1024 * 1024


def sector_hash_progress(checkpoint_callback, label, *, phase="PERSISTENCE_CANONICAL_HASH"):
    if checkpoint_callback is None:
        return None

    def progress(*, processed_bytes, complete):
        if processed_bytes < HASH_PROGRESS_MIN_BYTES:
            return
        item = f"{label}:canonical-bytes:{processed_bytes}"
        checkpoint_callback(
            phase=phase, processed=0, total=1,
            current_item=item, last_completed_item=item if complete else None,
            metrics={"canonical_bytes_hashed": processed_bytes, "hash_complete": int(complete)},
        )

    return progress


def sector_dataclass_payload(value):
    """Match asdict values while keeping repeated subtrees shared in this fresh view."""
    memo = {}
    active = set()

    def copy(node):
        structured = is_dataclass(node) or isinstance(node, (Mapping, list, tuple))
        if not structured:
            return node
        address = id(node)
        if address in active:
            raise ValueError("SECTOR_EVIDENCE_CYCLIC_PAYLOAD")
        if address in memo:
            return memo[address]
        active.add(address)
        try:
            if is_dataclass(node):
                result = {field.name: copy(getattr(node, field.name)) for field in fields(node)}
            elif isinstance(node, Mapping):
                result = {key: copy(item) for key, item in node.items()}
            elif isinstance(node, tuple):
                result = tuple(copy(item) for item in node)
            else:
                result = [copy(item) for item in node]
            memo[address] = result
            return result
        finally:
            active.remove(address)

    return copy(value)


def canonical_sector_payload(value):
    """Independent canonical values with a fresh, field-aware structural memo.

    This shares copies, never caller-owned mutable objects, validation decisions,
    hashes, or state across calls. Unordered-field normalization remains exact.
    """
    memo = {}
    active = set()

    def copy(node, field_name=None):
        if not isinstance(node, (Mapping, list, tuple, set, frozenset)):
            return Canonical.canonicalize(node, field_name=field_name)
        address = id(node)
        unordered = isinstance(node, (set, frozenset)) or (
            not isinstance(node, Mapping) and Canonical._is_unordered_field(field_name)
        )
        key = (address, unordered)
        if address in active:
            raise ValueError("SECTOR_EVIDENCE_CYCLIC_PAYLOAD")
        if key in memo:
            return memo[key]
        active.add(address)
        try:
            if isinstance(node, Mapping):
                result = {}
                for name, item in node.items():
                    name = str(name)
                    if name in result:
                        raise ValueError("SECTOR_EVIDENCE_COLLIDING_MAPPING_KEY")
                    result[name] = copy(item, name)
                result = dict(sorted(result.items()))
            else:
                result = [copy(item) for item in node]
                if unordered:
                    result.sort(key=Canonical._sort_key)
            memo[key] = result
            return result
        finally:
            active.remove(address)

    return copy(value)
