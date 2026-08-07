from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import HistoricalViewMode


@dataclass(frozen=True)
class BaselineSelection:
    current: CeriEstimateSnapshot | None
    baseline: CeriEstimateSnapshot | None
    target_baseline_date: date
    actual_elapsed_days: int | None
    unavailable_reason: str | None = None


class CeriPointInTimeQuery:
    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        snapshots: list[CeriEstimateSnapshot] | None = None,
        source_records: dict[int, CeriSourceRecord] | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        self._snapshots = snapshots
        self._source_records = source_records or {}

    def eligible_estimates(
        self,
        db: Session,
        *,
        company_id: int,
        metric: str,
        cutoff_at: datetime,
        mode: HistoricalViewMode = HistoricalViewMode.AS_KNOWN,
    ) -> list[CeriEstimateSnapshot]:
        snapshots = [
            snapshot
            for snapshot in self._load_snapshots(db)
            if snapshot.company_id == company_id
            and snapshot.metric == metric
            and _comparable(snapshot)
            and _is_current_observation(snapshot)
        ]
        if mode is HistoricalViewMode.AS_KNOWN:
            return sorted(
                [snapshot for snapshot in snapshots if _effective_at(snapshot) <= cutoff_at],
                key=_snapshot_sort,
            )
        if mode is HistoricalViewMode.LATEST_CORRECTED:
            return sorted(
                self._latest_corrected_estimates(snapshots, cutoff_at),
                key=_snapshot_sort,
            )
        raise ValueError(f"Unsupported historical view mode: {mode}")

    def current_snapshot(
        self,
        db: Session,
        *,
        company_id: int,
        metric: str,
        cutoff_at: datetime,
        mode: HistoricalViewMode = HistoricalViewMode.AS_KNOWN,
    ) -> CeriEstimateSnapshot | None:
        eligible = self.eligible_estimates(
            db,
            company_id=company_id,
            metric=metric,
            cutoff_at=cutoff_at,
            mode=mode,
        )
        return eligible[-1] if eligible else None

    def select_baseline(
        self,
        db: Session,
        *,
        current: CeriEstimateSnapshot | None,
        company_id: int,
        metric: str,
        cutoff_at: datetime,
        window_days: int,
        mode: HistoricalViewMode = HistoricalViewMode.AS_KNOWN,
    ) -> BaselineSelection:
        target_date = cutoff_at.date() - timedelta(days=window_days)
        if current is None:
            return BaselineSelection(
                current=None,
                baseline=None,
                target_baseline_date=target_date,
                actual_elapsed_days=None,
                unavailable_reason="current_snapshot_unavailable",
            )
        key = canonical_estimate_key(current)
        semantic_baselines = [
            snapshot
            for snapshot in self._load_snapshots(db)
            if snapshot.company_id == company_id
            and snapshot.metric == metric
            and snapshot is not current
            and snapshot.trend_baseline_window_days == window_days
            and snapshot.current_observation_reference
            == current.current_observation_reference
            and _comparable(snapshot)
        ]
        if semantic_baselines:
            baseline = max(semantic_baselines, key=_snapshot_sort)
            return BaselineSelection(
                current=current,
                baseline=baseline,
                target_baseline_date=target_date,
                actual_elapsed_days=window_days,
            )
        eligible = [
            snapshot
            for snapshot in self.eligible_estimates(
                db,
                company_id=company_id,
                metric=metric,
                cutoff_at=cutoff_at,
                mode=mode,
            )
            if snapshot is not current and canonical_estimate_key(snapshot) == key
        ]
        baseline = _select_baseline_candidate(
            eligible,
            target_date,
            self.config.revision.baseline_tolerance_days,
        )
        if baseline is None:
            return BaselineSelection(
                current=current,
                baseline=None,
                target_baseline_date=target_date,
                actual_elapsed_days=None,
                unavailable_reason="baseline_unavailable",
            )
        elapsed = (
            (current.effective_session - baseline.effective_session).days
            if current.effective_session is not None and baseline.effective_session is not None
            else window_days
        )
        return BaselineSelection(
            current=current,
            baseline=baseline,
            target_baseline_date=target_date,
            actual_elapsed_days=elapsed,
        )

    def source_record(self, db: Session, source_record_id: int) -> CeriSourceRecord | None:
        if source_record_id in self._source_records:
            return self._source_records[source_record_id]
        get = getattr(db, "get", None)
        if callable(get):
            return get(CeriSourceRecord, source_record_id)
        return None

    def _load_snapshots(self, db: Session) -> list[CeriEstimateSnapshot]:
        if self._snapshots is not None:
            return self._snapshots
        scalars = getattr(db, "scalars", None)
        if not callable(scalars):
            return []
        result = scalars(select(CeriEstimateSnapshot))
        return list(result.all() if hasattr(result, "all") else result)

    def _latest_corrected_estimates(
        self,
        snapshots: list[CeriEstimateSnapshot],
        cutoff_at: datetime,
    ) -> list[CeriEstimateSnapshot]:
        as_known = [snapshot for snapshot in snapshots if _effective_at(snapshot) <= cutoff_at]
        eligible_source_ids = {snapshot.source_record_id for snapshot in as_known}
        correction_snapshots = []
        for snapshot in snapshots:
            source = self._source_records.get(snapshot.source_record_id)
            if source is None or source.supersedes_id is None:
                continue
            if source.supersedes_id in eligible_source_ids:
                correction_snapshots.append(snapshot)

        superseded = {
            self._source_records[snapshot.source_record_id].supersedes_id
            for snapshot in correction_snapshots
            if snapshot.source_record_id in self._source_records
        }
        return [
            *[snapshot for snapshot in as_known if snapshot.source_record_id not in superseded],
            *correction_snapshots,
        ]


def canonical_estimate_key(snapshot: CeriEstimateSnapshot) -> str:
    return ":".join(
        [
            str(snapshot.company_id),
            snapshot.metric,
            snapshot.period_type,
            snapshot.fiscal_period_end.isoformat(),
            snapshot.canonical_currency or "UNVERIFIED",
            str((snapshot.canonical_scale or Decimal("1")).normalize()),
        ]
    )


def _select_baseline_candidate(
    snapshots: list[CeriEstimateSnapshot],
    target_date: date,
    tolerance_days: int,
) -> CeriEstimateSnapshot | None:
    before_target = [
        snapshot
        for snapshot in snapshots
        if snapshot.effective_session is not None and snapshot.effective_session <= target_date
    ]
    if before_target:
        return max(before_target, key=lambda snapshot: snapshot.effective_session)

    tolerance_end = target_date + timedelta(days=tolerance_days)
    within_tolerance = [
        snapshot
        for snapshot in snapshots
        if snapshot.effective_session is not None
        and target_date < snapshot.effective_session <= tolerance_end
    ]
    if within_tolerance:
        return min(within_tolerance, key=lambda snapshot: snapshot.effective_session)
    return None


def _comparable(snapshot: CeriEstimateSnapshot) -> bool:
    return snapshot.canonical_currency is not None and snapshot.canonical_scale is not None


def _is_current_observation(snapshot: CeriEstimateSnapshot) -> bool:
    return snapshot.trend_baseline_window_days is None


def _effective_at(snapshot: CeriEstimateSnapshot) -> datetime:
    if snapshot.effective_at is None:
        if snapshot.effective_session is None:
            return datetime.min.replace(tzinfo=UTC)
        return datetime.combine(snapshot.effective_session, datetime.min.time(), tzinfo=UTC)
    return snapshot.effective_at


def _snapshot_sort(snapshot: CeriEstimateSnapshot) -> tuple[Any, ...]:
    return (
        snapshot.effective_session or date.min,
        _effective_at(snapshot),
        snapshot.id or 0,
        snapshot.source_record_id,
    )
