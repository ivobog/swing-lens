from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriIngestionRun, CeriSourceRecord
from app.services.ceri.constants import CERI_DAILY_CUTOFF_TIMEZONE


class FreshnessTimestampError(ValueError):
    """Raised when a source-health timestamp is after its reference cutoff."""


@dataclass(frozen=True)
class FeedFreshness:
    provider: str | None
    dataset: str
    last_successful_check_at: datetime | None
    age_days: int | None
    max_stale_days: int
    status: str
    scope: str


@dataclass(frozen=True)
class EvidenceTimestamp:
    value: datetime
    field_name: str
    quality: str
    ignored_future_fields: tuple[str, ...] = ()


def ticker_feed_freshness_from_runs(
    runs: Iterable[CeriIngestionRun],
    *,
    ticker: str,
    cutoff_at: datetime,
    max_stale_days: Mapping[str, int],
    timezone_name: str = CERI_DAILY_CUTOFF_TIMEZONE,
) -> dict[str, FeedFreshness]:
    normalized_ticker = ticker.strip().upper()
    latest: dict[str, CeriIngestionRun] = {}
    for run in runs:
        if not _successful(run):
            continue
        scope_ticker = str((run.scope_json or {}).get("ticker") or "").strip().upper()
        if scope_ticker != normalized_ticker:
            continue
        current = latest.get(run.dataset)
        if current is None or run.completed_at > current.completed_at:
            latest[run.dataset] = run
    return {
        dataset: _feed_state(
            latest.get(dataset),
            dataset=dataset,
            cutoff_at=cutoff_at,
            max_stale_days=threshold,
            timezone_name=timezone_name,
            scope="TICKER",
        )
        for dataset, threshold in max_stale_days.items()
    }


def global_feed_freshness_from_runs(
    runs: Iterable[CeriIngestionRun],
    *,
    cutoff_at: datetime,
    max_stale_days: Mapping[str, int],
    timezone_name: str = CERI_DAILY_CUTOFF_TIMEZONE,
) -> dict[tuple[str, str], FeedFreshness]:
    latest: dict[tuple[str, str], CeriIngestionRun] = {}
    for run in runs:
        if not _successful(run) or run.dataset not in max_stale_days:
            continue
        key = (run.provider, run.dataset)
        current = latest.get(key)
        if current is None or run.completed_at > current.completed_at:
            latest[key] = run
    return {
        key: _feed_state(
            run,
            dataset=key[1],
            cutoff_at=cutoff_at,
            max_stale_days=max_stale_days[key[1]],
            timezone_name=timezone_name,
            scope="PROVIDER_GLOBAL",
        )
        for key, run in sorted(latest.items())
    }


def ticker_feed_coverage_from_runs(
    runs: Iterable[CeriIngestionRun],
    *,
    tickers: set[str],
    provider: str,
    dataset: str,
    cutoff_at: datetime,
    max_stale_days: int,
    timezone_name: str = CERI_DAILY_CUTOFF_TIMEZONE,
) -> dict[str, int]:
    normalized = {ticker.strip().upper() for ticker in tickers}
    latest: dict[str, CeriIngestionRun] = {}
    for run in runs:
        scope_ticker = str((run.scope_json or {}).get("ticker") or "").strip().upper()
        if (
            not _successful(run)
            or run.provider != provider
            or run.dataset != dataset
            or scope_ticker not in normalized
        ):
            continue
        current = latest.get(scope_ticker)
        if current is None or run.completed_at > current.completed_at:
            latest[scope_ticker] = run
    fresh = stale = 0
    for run in latest.values():
        age = freshness_age_days(
            cutoff_at,
            run.completed_at,
            timezone_name=timezone_name,
        )
        if age <= max_stale_days:
            fresh += 1
        else:
            stale += 1
    return {
        "total": len(normalized),
        "fresh": fresh,
        "stale": stale,
        "missing": len(normalized) - fresh - stale,
    }


def evidence_observation_timestamp(
    source: CeriSourceRecord,
    *,
    reference_at: datetime,
) -> EvidenceTimestamp:
    """Select observation semantics without allowing future event dates to win."""
    ignored: list[str] = []
    for field_name, quality in (
        ("source_timestamp", "PROVIDER_TIMESTAMP"),
        ("observed_at", "PROVIDER_TIMESTAMP"),
        ("published_at", "PUBLICATION_TIMESTAMP"),
    ):
        value = getattr(source, field_name, None)
        if value is None:
            continue
        if _as_utc(value) > _as_utc(reference_at):
            ignored.append(field_name)
            continue
        return EvidenceTimestamp(
            value=value,
            field_name=field_name,
            quality=quality,
            ignored_future_fields=tuple(ignored),
        )
    for field_name in ("retrieved_at", "ingested_at"):
        value = getattr(source, field_name, None)
        if value is not None:
            if _as_utc(value) > _as_utc(reference_at):
                raise FreshnessTimestampError(
                    f"{field_name} {value.isoformat()} is after cutoff {reference_at.isoformat()}"
                )
            return EvidenceTimestamp(
                value=value,
                field_name=field_name,
                quality="RETRIEVAL_ONLY",
                ignored_future_fields=tuple(ignored),
            )
    raise FreshnessTimestampError(f"source record {source.id} has no usable timestamp")


def freshness_age_days(
    reference_at: datetime,
    source_at: datetime,
    *,
    timezone_name: str = CERI_DAILY_CUTOFF_TIMEZONE,
) -> int:
    if _as_utc(source_at) > _as_utc(reference_at):
        raise FreshnessTimestampError(
            f"freshness timestamp {source_at.isoformat()} is after cutoff "
            f"{reference_at.isoformat()}"
        )
    timezone = ZoneInfo(timezone_name)
    age = (
        _as_utc(reference_at).astimezone(timezone).date()
        - _as_utc(source_at).astimezone(timezone).date()
    ).days
    if age < 0:
        raise FreshnessTimestampError("freshness age cannot be negative")
    return age


def _feed_state(
    run: CeriIngestionRun | None,
    *,
    dataset: str,
    cutoff_at: datetime,
    max_stale_days: int,
    timezone_name: str,
    scope: str,
) -> FeedFreshness:
    if run is None or run.completed_at is None:
        return FeedFreshness(
            provider=None,
            dataset=dataset,
            last_successful_check_at=None,
            age_days=None,
            max_stale_days=max_stale_days,
            status="UNAVAILABLE",
            scope=scope,
        )
    age = freshness_age_days(
        cutoff_at,
        run.completed_at,
        timezone_name=timezone_name,
    )
    return FeedFreshness(
        provider=run.provider,
        dataset=dataset,
        last_successful_check_at=run.completed_at,
        age_days=age,
        max_stale_days=max_stale_days,
        status="FRESH" if age <= max_stale_days else "STALE",
        scope=scope,
    )


def _successful(run: CeriIngestionRun) -> bool:
    return run.status == "COMPLETED" and run.completed_at is not None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
