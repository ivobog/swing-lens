from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from sqlalchemy import func

from app.models.ceri_tables import CeriSourceRecord
from app.models.tables import PriceBar


def source_record_known_at(record: CeriSourceRecord) -> datetime | None:
    """Return the earliest persisted proof that SwingLens possessed a source record.

    Provider publication, event, observation, and revision timestamps describe the
    provider's world.  They do not prove when SwingLens received the payload.  A
    stored retrieval timestamp is authoritative; the persistence timestamp is the
    conservative legacy fallback.  If neither exists, point-in-time use fails closed.
    """

    return _aware(record.retrieved_at) or _aware(record.ingested_at)


def source_record_is_eligible(record: CeriSourceRecord, cutoff_at: datetime) -> bool:
    known_at = source_record_known_at(record)
    return known_at is not None and known_at <= _required_aware(cutoff_at)


def eligible_source_record_ids(
    records: Iterable[CeriSourceRecord], cutoff_at: datetime
) -> set[int]:
    return {
        int(record.id)
        for record in records
        if record.id is not None and source_record_is_eligible(record, cutoff_at)
    }


def source_record_knowledge_predicate(cutoff_at: datetime):
    """SQL predicate matching :func:`source_record_known_at`."""

    return func.coalesce(CeriSourceRecord.retrieved_at, CeriSourceRecord.ingested_at) <= cutoff_at


def referenced_sources_are_eligible(
    row: Any,
    eligible_ids: set[int],
    *,
    scalar_fields: tuple[str, ...] = ("source_record_id",),
    collection_fields: tuple[str, ...] = (),
    allow_unreferenced: bool = False,
) -> bool:
    source_ids: list[int] = []
    for field in scalar_fields:
        value = getattr(row, field, None)
        if value is not None:
            source_ids.append(int(value))
    for field in collection_fields:
        source_ids.extend(int(value) for value in (getattr(row, field, None) or []))
    if not source_ids:
        return allow_unreferenced
    return all(source_id in eligible_ids for source_id in source_ids)


def price_bar_known_at(bar: PriceBar) -> datetime | None:
    """Knowledge time of the current values stored on a price-bar row."""

    return _aware(bar.revised_at) or _aware(bar.first_seen_at)


def price_bar_is_eligible(
    bar: PriceBar,
    *,
    latest_completed_session: date,
    cutoff_at: datetime,
) -> bool:
    """Apply both the market-session and current-value knowledge gates.

    PriceBar rows are mutable current projections.  When ``revised_at`` is after
    the cutoff, their current values cannot be used even if revision audit rows
    retain previous JSON; callers must either reconstruct a certified versioned
    row explicitly or fail closed.
    """

    first_seen_at = _aware(bar.first_seen_at)
    revised_at = _aware(bar.revised_at)
    cutoff = _required_aware(cutoff_at)
    return (
        bar.bar_date <= latest_completed_session
        and first_seen_at is not None
        and first_seen_at <= cutoff
        and (revised_at is None or revised_at <= cutoff)
    )


def price_bar_knowledge_predicates(*, latest_completed_session: date, cutoff_at: datetime):
    """SQL predicates matching :func:`price_bar_is_eligible`."""

    return (
        PriceBar.bar_date <= latest_completed_session,
        PriceBar.first_seen_at <= cutoff_at,
        (PriceBar.revised_at.is_(None)) | (PriceBar.revised_at <= cutoff_at),
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value


def _required_aware(value: datetime) -> datetime:
    aware = _aware(value)
    if aware is None:
        raise ValueError("cutoff_at must be timezone-aware")
    return aware
