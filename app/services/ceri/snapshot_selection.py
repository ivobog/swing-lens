from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime

from app.models.ceri_tables import CeriScoreSnapshot


def snapshot_recency_key(snapshot: CeriScoreSnapshot) -> tuple[datetime, date, int]:
    return (
        snapshot.cutoff_at or datetime.min.replace(tzinfo=UTC),
        snapshot.as_of_session or date.min,
        snapshot.id or 0,
    )


def snapshot_recency_order(snapshot=CeriScoreSnapshot) -> tuple:
    return (
        snapshot.cutoff_at.desc().nullslast(),
        snapshot.as_of_session.desc().nullslast(),
        snapshot.id.desc(),
    )


def latest_eligible_by_ticker(
    snapshots: Iterable[CeriScoreSnapshot],
) -> dict[str, CeriScoreSnapshot]:
    latest: dict[str, CeriScoreSnapshot] = {}
    for snapshot in snapshots:
        ticker = snapshot.ticker.upper()
        previous = latest.get(ticker)
        if previous is None or snapshot_recency_key(snapshot) > snapshot_recency_key(previous):
            latest[ticker] = snapshot
    return latest
