import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.sector_rotation_repository import SectorRotationRepository
from app.services.sector_serialization import canonical_sector_payload, sector_dataclass_payload


@dataclass
class _Row:
    sector: str
    debug: dict


@dataclass
class _Snapshot:
    run_id: int
    rows: list[_Row]
    debug: dict


def test_sector_scoped_copy_preserves_historical_bytes_and_independent_shared_values():
    shared = {"financial_inputs": [{"evidence_id": 3}, {"evidence_id": 1}],
              "text": "Industrials — ü", "amount": Decimal("1.2300"),
              "observed": datetime(2026, 11, 9, 21, tzinfo=UTC)}
    snapshot = _Snapshot(7, [_Row("A", shared), _Row("B", shared)], {"inputs": shared})
    legacy = asdict(snapshot)
    view = sector_dataclass_payload(snapshot)
    assert view == legacy
    assert view["rows"][0]["debug"] is view["rows"][1]["debug"] is view["debug"]["inputs"]
    assert view["debug"]["inputs"] is not shared
    expected_hash = hashlib.sha256(json.dumps(
        legacy, sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()
    assert SectorRotationRepository.snapshot_evidence_hash(snapshot) == expected_hash
    sealed = canonical_sector_payload(view)
    assert Canonical.bytes(sealed) == Canonical.bytes(legacy)
    assert Canonical.fingerprint_streaming(sealed) == Canonical.fingerprint(legacy)
    assert sealed["rows"][0]["debug"] is sealed["rows"][1]["debug"]
    before = Canonical.bytes(sealed)
    shared["financial_inputs"][0]["evidence_id"] = 999
    view["rows"][0]["debug"]["financial_inputs"][1]["evidence_id"] = 888
    assert Canonical.bytes(sealed) == before
    independently_sealed = canonical_sector_payload(legacy)
    sealed["rows"][0]["debug"]["text"] = "changed"
    assert independently_sealed["rows"][0]["debug"]["text"] != "changed"


def test_sector_copy_preserves_field_specific_unordered_semantics_and_rejects_cycles():
    sequence = [{"id": 3}, {"id": 1}]
    payload = {"values": sequence, "reason_codes": sequence}
    assert Canonical.bytes(canonical_sector_payload(payload)) == Canonical.bytes(payload)
    copied = canonical_sector_payload(payload)
    assert copied["values"] != copied["reason_codes"]
    with pytest.raises(ValueError, match="COLLIDING_MAPPING_KEY"):
        canonical_sector_payload({1: "one", "1": "another"})
    cyclic = {}
    cyclic["self"] = cyclic
    for copy in (canonical_sector_payload, sector_dataclass_payload):
        with pytest.raises(ValueError, match="CYCLIC_PAYLOAD"):
            copy(cyclic)


def test_streamed_hash_progress_counts_actual_bytes_and_preserves_cancellation():
    payload = {"values": list(range(100)), "text": "é"}
    progress = []
    digest = Canonical.fingerprint_streaming(
        payload, buffer_size=16, progress_interval_seconds=0,
        progress_callback=lambda **values: progress.append(values),
    )
    assert digest == Canonical.fingerprint(payload)
    assert progress[-1] == {"processed_bytes": len(Canonical.bytes(payload)), "complete": True}
    assert all(left["processed_bytes"] <= right["processed_bytes"]
               for left, right in zip(progress, progress[1:], strict=False))

    def cancelled(**_values):
        raise RuntimeError("hash cancelled")

    with pytest.raises(RuntimeError, match="hash cancelled"):
        Canonical.fingerprint_streaming(
            payload, buffer_size=16, progress_interval_seconds=0, progress_callback=cancelled,
        )
