"""Cache keys preserve the canonical typed evidence, including DST folds."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services.price_bar_evidence import (
    PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS,
    price_bar_immutable_evidence_hash,
)
from app.services.price_bar_repository import _immutable_fingerprint, price_bar_pit_manifest_hash


def row():
    return SimpleNamespace(
        ticker="SPY",
        bar_date=date(2026, 10, 30),
        timeframe="1 day",
        open=Decimal("100"),
        high=Decimal("103"),
        low=Decimal("98"),
        close=Decimal("101"),
        volume=Decimal("10000"),
        source="IB",
        what_to_show="TRADES",
        adjustment_type="raw",
        first_fetch_run_id=7,
        first_fetch_item_id=9,
        created_at=datetime(2026, 10, 30, 22, tzinfo=UTC),
        first_seen_at=datetime(2026, 10, 30, 22, tzinfo=UTC),
        revised_at=None,
        revision_count=0,
        data_hash="abc",
    )


@pytest.fixture(autouse=True)
def clear_cache():
    _immutable_fingerprint.cache_clear()
    yield
    _immutable_fingerprint.cache_clear()


@pytest.mark.parametrize("field", PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS)
def test_every_evidence_field_is_in_the_key(field):
    source = row()
    before = price_bar_pit_manifest_hash(None, source)
    value = getattr(source, field)
    changed = (
        datetime(2026, 10, 31, 22, tzinfo=UTC)
        if value is None
        else value + timedelta(days=1)
        if isinstance(value, (date, datetime))
        else value + 1
        if isinstance(value, (int, Decimal))
        else value + "-changed"
    )
    setattr(source, field, changed)
    after = price_bar_pit_manifest_hash(None, source)
    assert after == price_bar_immutable_evidence_hash(source)
    assert before != after
    assert _immutable_fingerprint.cache_info().misses == 2


def test_equal_numeric_values_keep_their_canonical_types():
    source = row()
    digests = []
    for value in (True, 1, 1.0, Decimal("1")):
        source.open = value
        digests.append(price_bar_pit_manifest_hash(None, source))
        assert digests[-1] == price_bar_immutable_evidence_hash(source)
    assert len(set(digests)) == 4
    source.open = Decimal("1.0000")
    assert price_bar_pit_manifest_hash(None, source) == digests[-1]
    assert _immutable_fingerprint.cache_info().hits == 1


def test_distinct_dst_folds_cannot_alias():
    source = row()
    stamps = [
        datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=fold)
        for fold in (0, 1)
    ]
    assert stamps[0] == stamps[1]  # Python equality alone cannot key this evidence.
    digests = []
    for stamp in stamps:
        source.created_at = stamp
        digests.append(price_bar_pit_manifest_hash(None, source))
        assert digests[-1] == price_bar_immutable_evidence_hash(source)
    assert digests[0] != digests[1]


def test_operational_last_seen_does_not_grow_cache_or_change_evidence():
    source = row()
    before = price_bar_pit_manifest_hash(None, source)
    source.last_seen_at = datetime.now(UTC)
    assert price_bar_pit_manifest_hash(None, source) == before
    assert _immutable_fingerprint.cache_info().hits == 1
    assert _immutable_fingerprint.cache_parameters()["maxsize"] == 32768
