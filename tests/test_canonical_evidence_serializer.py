from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.models.tables import TechnicalScore
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.market_calculation_context_service import (
    market_calculation_context_fingerprint,
)
from app.services.market_clock_service import MarketClockService
from app.services.setup_lifecycle.transition_candidate_service import (
    _stable_hash,
    _technical_reconstruction_fingerprint,
)


def test_json_encoding_matches_full_canonicalization_for_nested_evidence() -> None:
    serializer = CanonicalEvidenceSerializer
    rng = random.Random(1403)

    def reference(value):
        return json.dumps(
            serializer.canonicalize(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    for _ in range(200):
        payload = {
            "bars": [{"close": rng.random(), "session": str(i)} for i in range(20)],
            "nested": {"reason_codes": rng.sample(["Ω", "A", "Z", "B"], 4)},
            "ordered": [None, True, -0.0, 0.0, 12, "Unicode Ω"],
        }
        assert serializer.dumps(payload) == reference(payload)
        payload["ordered"][2] = 0.0
        payload["nested"]["reason_codes"].sort()
        assert serializer.dumps(payload) == reference(payload)
        original = serializer.fingerprint(payload)
        payload["bars"][0]["close"] += 1
        assert serializer.dumps(payload) == reference(payload)
        assert serializer.fingerprint(payload) != original
    for payload in (
        {"reason_codes": [2, 10, 1]},
        {"reason_codes": [{"z": 1, "a": 2}, {"a": 1}]},
        {"typed": Decimal("1.200"), "timestamp": datetime(2026, 9, 16, tzinfo=UTC)},
    ):
        assert serializer.dumps(payload) == reference(payload)


def test_exact_latest_canary_timezone_regression_is_canonical() -> None:
    utc_value = datetime.fromisoformat("2026-09-09T19:55:33.682959+00:00")
    zurich_value = datetime.fromisoformat("2026-09-09T21:55:33.682959+02:00")

    assert utc_value == zurich_value
    assert CanonicalEvidenceSerializer.bytes(utc_value) == CanonicalEvidenceSerializer.bytes(
        zurich_value
    )
    left = MarketClockService().cutoff_for(utc_value, reason="CERTIFICATION")
    right = MarketClockService().cutoff_for(zurich_value, reason="CERTIFICATION")
    assert market_calculation_context_fingerprint(left) == market_calculation_context_fingerprint(
        right
    )

    left_technical = TechnicalScore(ticker="TBLA", calculation_cutoff_at=utc_value)
    right_technical = TechnicalScore(ticker="TBLA", calculation_cutoff_at=zurich_value)
    assert _technical_reconstruction_fingerprint(
        left_technical
    ) == _technical_reconstruction_fingerprint(right_technical)
    assert _stable_hash({"cutoff_at": utc_value}) == _stable_hash({"cutoff_at": zurich_value})


def test_timezone_metamorphism_generated_aware_datetimes() -> None:
    generator = random.Random(20260909)
    zones = tuple(
        ZoneInfo(name) for name in ("UTC", "Europe/Zurich", "America/New_York", "Asia/Tokyo")
    )
    anchors = (
        datetime(2026, 3, 8, 6, 55, tzinfo=UTC),
        datetime(2026, 3, 29, 0, 55, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 55, tzinfo=UTC),
        datetime(2026, 11, 1, 5, 55, tzinfo=UTC),
    )
    values = list(anchors)
    values.extend(
        datetime(2020, 1, 1, tzinfo=UTC)
        + timedelta(
            days=generator.randrange(0, 3650),
            seconds=generator.randrange(0, 86_400),
            microseconds=generator.randrange(0, 1_000_000),
        )
        for _ in range(256)
    )
    for value in values:
        expected = CanonicalEvidenceSerializer.fingerprint(value)
        assert {
            CanonicalEvidenceSerializer.fingerprint(value.astimezone(zone)) for zone in zones
        } == {expected}


def test_scalar_container_json_and_ordering_contract() -> None:
    class Status(Enum):
        READY = "READY"

    value = {
        "timestamp": datetime(2026, 9, 9, 19, 55, 33, 682959, tzinfo=UTC),
        "date": date(2026, 9, 9),
        "time": time(19, 55, 33, 682959, tzinfo=UTC),
        "timezone": timezone(timedelta(hours=2)),
        "decimal": Decimal("1.2300"),
        "negative_zero": -0.0,
        "uuid": UUID("58C34B4A-8D7D-4CD1-BD2C-5319C36E7A2C"),
        "enum": Status.READY,
        "bytes": b"\x00evidence",
        "set": {"b", "a"},
        "tuple": (True, None, 3),
    }
    canonical = CanonicalEvidenceSerializer.canonicalize(value)
    assert canonical["timestamp"] == "2026-09-09T19:55:33.682959Z"
    assert canonical["date"] == "2026-09-09"
    assert canonical["decimal"] == "1.23"
    assert canonical["negative_zero"] == 0.0
    assert canonical["set"] == ["a", "b"]
    assert CanonicalEvidenceSerializer.fingerprint(
        value
    ) == CanonicalEvidenceSerializer.fingerprint(dict(reversed(list(value.items()))))
    encoded = CanonicalEvidenceSerializer.dumps(value)
    assert encoded == CanonicalEvidenceSerializer.dumps(json.loads(encoded))


@pytest.mark.parametrize("value", [datetime(2026, 9, 9), Decimal("NaN"), float("inf")])
def test_unsafe_scalar_representation_fails_closed(value: object) -> None:
    with pytest.raises(ValueError):
        CanonicalEvidenceSerializer.bytes(value)


def test_mapping_key_collision_fails_closed() -> None:
    with pytest.raises(ValueError, match="colliding key"):
        CanonicalEvidenceSerializer.bytes({1: "integer", "1": "string"})


def test_precomputed_canonical_subtree_is_exactly_byte_equivalent() -> None:
    row = {
        "at": datetime(2026, 10, 2, 12, 30, 58, 244864, tzinfo=UTC),
        "amount": Decimal("123.4500"),
        "reason_codes": ["Z", "A"],
    }
    manifest = {"writer": "fixture", "native_source": {"rows": [row, row]}}
    canonical_row = CanonicalEvidenceSerializer.canonicalize(row)

    reference = CanonicalEvidenceSerializer.bytes(manifest)
    optimized = CanonicalEvidenceSerializer.bytes(
        manifest,
        precomputed={id(row): canonical_row},
    )

    assert optimized == reference
    assert CanonicalEvidenceSerializer.fingerprint(manifest) == hashlib.sha256(
        optimized
    ).hexdigest()


def test_precomputed_subtree_does_not_hide_changes_outside_cached_identity() -> None:
    cached = {"amount": Decimal("1.00")}
    mutable = {"amount": Decimal("2.00")}
    manifest = {"cached": cached, "mutable": mutable}
    precomputed = {id(cached): CanonicalEvidenceSerializer.canonicalize(cached)}
    before = CanonicalEvidenceSerializer.bytes(manifest, precomputed=precomputed)

    mutable["amount"] = Decimal("3.00")

    assert CanonicalEvidenceSerializer.bytes(manifest, precomputed=precomputed) != before


def test_streaming_bytes_and_fingerprint_match_reference_torture_suite() -> None:
    class Status(Enum):
        READY = "READY"

    values = [
        None,
        True,
        False,
        0,
        -123,
        1.25,
        -9.5,
        -0.0,
        Decimal("123.4500"),
        datetime(2026, 10, 3, 12, 34, 56, 789, tzinfo=UTC),
        date(2026, 10, 3),
        time(12, 34, 56, 789, tzinfo=timezone(timedelta(hours=2))),
        timezone(timedelta(hours=-5, minutes=-30)),
        ZoneInfo("Europe/Zurich"),
        UUID("58C34B4A-8D7D-4CD1-BD2C-5319C36E7A2C"),
        Status.READY,
        b"\x00evidence\xff",
        {"z": 1, "a": ["Ω", 'quote"', "line\n", "slash\\"]},
        [1, (2, 3), {"nested": frozenset({"b", "a"})}],
        {"reason_codes": ["Z", "A", "B"]},
        {"nested_reason_codes_json": [{"z": 1}, {"a": 2}]},
        "x" * 100_000,
        {},
        [],
    ]
    for value in values:
        telemetry: dict[str, int | float] = {}
        expected = CanonicalEvidenceSerializer.bytes(value)
        actual = CanonicalEvidenceSerializer.streaming_bytes(value)
        digest = CanonicalEvidenceSerializer.fingerprint_streaming(
            value, telemetry=telemetry
        )
        assert actual == expected
        assert digest == hashlib.sha256(expected).hexdigest()
        assert telemetry["canonical_byte_count"] == len(expected)


@pytest.mark.parametrize(
    "value",
    [
        {1: "integer", "1": "string"},
        object(),
        datetime(2026, 10, 3),
        Decimal("NaN"),
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_streaming_failure_matches_reference_exception(value: object) -> None:
    with pytest.raises(Exception) as reference:
        CanonicalEvidenceSerializer.bytes(value)
    with pytest.raises(Exception) as candidate:
        CanonicalEvidenceSerializer.streaming_bytes(value)
    assert type(candidate.value) is type(reference.value)
    assert str(candidate.value) == str(reference.value)


def test_streaming_precomputed_fragments_are_exact_and_scoped() -> None:
    row = {
        "at": datetime(2026, 10, 2, 12, 30, 58, 244864, tzinfo=UTC),
        "amount": Decimal("123.4500"),
        "reason_codes": ["Z", "A"],
    }
    manifest = {"writer": "fixture", "native_source": {"rows": [row, row]}}
    precomputed = {id(row): CanonicalEvidenceSerializer.canonicalize(row)}
    fragments: dict[int, bytes] = {}
    telemetry: dict[str, int | float] = {}

    actual = CanonicalEvidenceSerializer.streaming_bytes(
        manifest,
        precomputed=precomputed,
        fragments=fragments,
        telemetry=telemetry,
    )

    assert actual == CanonicalEvidenceSerializer.bytes(manifest, precomputed=precomputed)
    assert telemetry["stable_fragment_misses"] == 1
    assert telemetry["stable_fragment_hits"] == 1
    assert telemetry["stable_fragment_byte_reuse"] == len(fragments[id(row)])
