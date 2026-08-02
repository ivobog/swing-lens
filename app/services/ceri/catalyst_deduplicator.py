from __future__ import annotations

from dataclasses import dataclass

from app.services.ceri.dtos import NormalizedCatalystRecord


@dataclass(frozen=True)
class CatalystCluster:
    canonical: NormalizedCatalystRecord
    sources: tuple[NormalizedCatalystRecord, ...]
    conflict_flags: tuple[str, ...]


class CeriCatalystDeduplicator:
    def cluster(self, records: list[NormalizedCatalystRecord]) -> list[CatalystCluster]:
        groups: dict[tuple, list[NormalizedCatalystRecord]] = {}
        for record in records:
            groups.setdefault(_cluster_key(record), []).append(record)
        return [_cluster(rows) for rows in groups.values()]


def _cluster_key(record: NormalizedCatalystRecord) -> tuple:
    return (
        record.company_id,
        record.category.value,
        record.subtype,
        record.subject_key,
    )


def _cluster(records: list[NormalizedCatalystRecord]) -> CatalystCluster:
    canonical = sorted(
        records,
        key=lambda row: (
            row.announced_at is None,
            row.announced_at,
            row.source_record_id,
        ),
    )[0]
    expected_dates = {row.expected_date for row in records if row.expected_date is not None}
    statuses = {row.status for row in records}
    flags = set().union(*(set(row.conflict_flags) for row in records))
    if len(expected_dates) > 1:
        flags.add("conflicting_event_dates")
    if "CANCELLED" in {status.value for status in statuses} and len(statuses) > 1:
        flags.add("mutually_exclusive_statuses")
    return CatalystCluster(
        canonical=canonical,
        sources=tuple(records),
        conflict_flags=tuple(sorted(flags)),
    )
