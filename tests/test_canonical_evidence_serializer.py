from __future__ import annotations

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
