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
    comparison_mode: str | None = None


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
        period_type: str | None = None,
        fiscal_period_end: date | None = None,
        mode: HistoricalViewMode = HistoricalViewMode.AS_KNOWN,
    ) -> list[CeriEstimateSnapshot]:
        snapshots = [
            snapshot
            for snapshot in self._load_snapshots(db, company_id=company_id, metric=metric)
            if snapshot.company_id == company_id
            and snapshot.metric == metric
            and (period_type is None or snapshot.period_type == period_type)
            and (
                fiscal_period_end is None
                or snapshot.fiscal_period_end == fiscal_period_end
            )
            and _current_candidate(snapshot)
            and _is_current_observation(snapshot)
        ]
        if mode is HistoricalViewMode.AS_KNOWN:
            return sorted(
                [
                    snapshot
                    for snapshot in snapshots
                    if _effective_at(snapshot) <= cutoff_at and _known_at(snapshot) <= cutoff_at
                ],
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
        period_slot: str | None = None,
        fiscal_period_end: date | None = None,
        mode: HistoricalViewMode = HistoricalViewMode.AS_KNOWN,
    ) -> CeriEstimateSnapshot | None:
        eligible = self.eligible_estimates(
            db,
            company_id=company_id,
            metric=metric,
            cutoff_at=cutoff_at,
            fiscal_period_end=fiscal_period_end,
            mode=mode,
        )
        if metric == "REVENUE":
            eligible = [
                snapshot
                for snapshot in eligible
                if snapshot.trend_baseline_window_days is None
                and snapshot.baseline_origin != "PROVIDER_RETROSPECTIVE_WINDOW"
            ]
        if period_slot is not None and fiscal_period_end is None:
            slot_candidates = [
                snapshot
                for snapshot in eligible
                if snapshot.canonical_period_slot == period_slot
                or (
                    snapshot.canonical_period_slot is None
                    and snapshot.period_type == period_slot
                )
            ]
            if slot_candidates:
                # Provider-defined slots remain current until the result is
                # reported, even when the fiscal end precedes the cutoff.
                # Duplicate slot rows are resolved to the latest fiscal end.
                target_end = max(row.fiscal_period_end for row in slot_candidates)
                eligible = [
                    row for row in slot_candidates if row.fiscal_period_end == target_end
                ]
            else:
                eligible = []
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
        period_slot: str | None = None,
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
        semantic_candidates = [
            snapshot
            for snapshot in self._load_snapshots(db, company_id=company_id, metric=metric)
            if metric == "EPS_DILUTED"
            and snapshot.company_id == company_id
            and snapshot.metric == metric
            and snapshot is not current
            and snapshot.trend_baseline_window_days == window_days
            and snapshot.current_observation_reference
            == current.current_observation_reference
            and _known_at(snapshot) <= cutoff_at
        ]
        semantic_rejections = [
            (
                snapshot,
                _same_provider_relative_rejection_reason(
                    db,
                    current=current,
                    baseline=snapshot,
                    source_records=self._source_records,
                ),
            )
            for snapshot in semantic_candidates
        ]
        semantic_baselines = [
            snapshot for snapshot, rejection in semantic_rejections if rejection is None
        ]
        if semantic_baselines:
            baseline = max(semantic_baselines, key=_snapshot_sort)
            return BaselineSelection(
                current=current,
                baseline=baseline,
                target_baseline_date=target_date,
                actual_elapsed_days=window_days,
                comparison_mode="SAME_PROVIDER_RELATIVE",
            )
        canonical_semantic_baselines = [
            snapshot
            for snapshot in semantic_candidates
            if _absolute_comparable(snapshot)
            and _absolute_comparable(current)
            and canonical_estimate_key(snapshot) == key
        ]
        if canonical_semantic_baselines:
            baseline = max(canonical_semantic_baselines, key=_snapshot_sort)
            return BaselineSelection(
                current=current,
                baseline=baseline,
                target_baseline_date=target_date,
                actual_elapsed_days=window_days,
                comparison_mode="ABSOLUTE_CANONICAL",
            )
        eligible = [
            snapshot
            for snapshot in self.eligible_estimates(
                db,
                company_id=company_id,
                metric=metric,
                cutoff_at=cutoff_at,
                fiscal_period_end=current.fiscal_period_end,
                mode=mode,
            )
            if snapshot is not current
            and not (
                metric == "REVENUE"
                and (
                    snapshot.trend_baseline_window_days is not None
                    or snapshot.baseline_origin == "PROVIDER_RETROSPECTIVE_WINDOW"
                )
            )
            and _absolute_comparable(snapshot)
            and _absolute_comparable(current)
            and canonical_estimate_key(snapshot) == key
        ]
        baseline = _select_baseline_candidate(
            eligible,
            target_date,
            self.config.revision.baseline_tolerance_days,
        )
        if baseline is None:
            semantic_reason = next(
                (reason for _snapshot, reason in semantic_rejections if reason),
                None,
            )
            absolute_currency_required = any(
                snapshot is not current
                and snapshot.company_id == current.company_id
                and snapshot.metric == current.metric
                and snapshot.period_type == current.period_type
                and snapshot.fiscal_period_end == current.fiscal_period_end
                and snapshot.canonical_scale == current.canonical_scale
                and (
                    snapshot.canonical_currency is None
                    or current.canonical_currency is None
                )
                and _known_at(snapshot) <= cutoff_at
                for snapshot in self._load_snapshots(
                    db, company_id=company_id, metric=metric
                )
            )
            return BaselineSelection(
                current=current,
                baseline=None,
                target_baseline_date=target_date,
                actual_elapsed_days=None,
                unavailable_reason=(
                    "UNAVAILABLE_BASELINE_NOT_ACCUMULATED"
                    if metric == "REVENUE"
                    else semantic_reason
                    or (
                        "ABSOLUTE_COMPARISON_CURRENCY_REQUIRED"
                        if absolute_currency_required
                        else "baseline_unavailable"
                    )
                ),
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
            comparison_mode=(
                "HISTORICAL_OBSERVATION"
                if metric == "REVENUE"
                else "ABSOLUTE_CANONICAL"
            ),
        )

    def source_record(self, db: Session, source_record_id: int) -> CeriSourceRecord | None:
        if source_record_id in self._source_records:
            return self._source_records[source_record_id]
        get = getattr(db, "get", None)
        if callable(get):
            return get(CeriSourceRecord, source_record_id)
        return None

    def _load_snapshots(
        self,
        db: Session,
        *,
        company_id: int | None = None,
        metric: str | None = None,
    ) -> list[CeriEstimateSnapshot]:
        if self._snapshots is not None:
            return [
                snapshot
                for snapshot in self._snapshots
                if (company_id is None or snapshot.company_id == company_id)
                and (metric is None or snapshot.metric == metric)
            ]
        scalars = getattr(db, "scalars", None)
        if not callable(scalars):
            return []
        statement = select(CeriEstimateSnapshot)
        if company_id is not None:
            statement = statement.where(CeriEstimateSnapshot.company_id == company_id)
        if metric is not None:
            statement = statement.where(CeriEstimateSnapshot.metric == metric)
        result = scalars(statement)
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


def _current_candidate(snapshot: CeriEstimateSnapshot) -> bool:
    return snapshot.canonical_scale is not None


def _absolute_comparable(snapshot: CeriEstimateSnapshot) -> bool:
    return snapshot.canonical_currency is not None and snapshot.canonical_scale is not None


def _same_provider_relative_rejection_reason(
    db: Session,
    *,
    current: CeriEstimateSnapshot,
    baseline: CeriEstimateSnapshot,
    source_records: dict[int, CeriSourceRecord],
) -> str | None:
    if current.metric != "EPS_DILUTED" or baseline.metric != current.metric:
        return "SAME_PROVIDER_METRIC_MISMATCH"
    if baseline.baseline_origin != "PROVIDER_RETROSPECTIVE_WINDOW":
        return "BASELINE_ORIGIN_INELIGIBLE"
    if not current.current_observation_reference or (
        baseline.current_observation_reference != current.current_observation_reference
    ):
        return "OBSERVATION_REFERENCE_MISMATCH"
    if (
        baseline.company_id != current.company_id
        or baseline.period_type != current.period_type
        or baseline.canonical_period_slot != current.canonical_period_slot
        or baseline.fiscal_period_end != current.fiscal_period_end
    ):
        return "SAME_PROVIDER_PERIOD_MISMATCH"
    if (
        baseline.source_scale != current.source_scale
        or baseline.canonical_scale != current.canonical_scale
    ):
        return "SAME_PROVIDER_SCALE_MISMATCH"
    if (
        baseline.source_currency is not None
        and current.source_currency is not None
        and baseline.source_currency != current.source_currency
    ):
        return "SAME_PROVIDER_CURRENCY_SEMANTICS_MISMATCH"
    current_source = source_records.get(current.source_record_id)
    baseline_source = source_records.get(baseline.source_record_id)
    if current_source is None:
        get = getattr(db, "get", None)
        current_source = get(CeriSourceRecord, current.source_record_id) if callable(get) else None
    if baseline_source is None:
        get = getattr(db, "get", None)
        baseline_source = (
            get(CeriSourceRecord, baseline.source_record_id) if callable(get) else None
        )
    if current_source is not None or baseline_source is not None:
        same_provider = bool(
            current_source is not None
            and baseline_source is not None
            and current_source.provider == baseline_source.provider
        )
    else:
        same_provider = bool(
            current.source_provider
            and baseline.source_provider
            and current.source_provider == baseline.source_provider
        )
    if not same_provider:
        if current.canonical_currency is None or baseline.canonical_currency is None:
            return "CROSS_PROVIDER_CURRENCY_REQUIRED"
        return "CROSS_PROVIDER_COMPARISON_REJECTED"
    return None


def _same_provider_relative_comparable(
    db: Session,
    *,
    current: CeriEstimateSnapshot,
    baseline: CeriEstimateSnapshot,
    source_records: dict[int, CeriSourceRecord],
) -> bool:
    return (
        _same_provider_relative_rejection_reason(
            db,
            current=current,
            baseline=baseline,
            source_records=source_records,
        )
        is None
    )


def _is_current_observation(snapshot: CeriEstimateSnapshot) -> bool:
    return snapshot.trend_baseline_window_days is None


def _effective_at(snapshot: CeriEstimateSnapshot) -> datetime:
    if snapshot.effective_at is None:
        if snapshot.effective_session is None:
            return datetime.min.replace(tzinfo=UTC)
        return datetime.combine(snapshot.effective_session, datetime.min.time(), tzinfo=UTC)
    return snapshot.effective_at


def _known_at(snapshot: CeriEstimateSnapshot) -> datetime:
    value = (
        snapshot.known_at
        or snapshot.provider_observed_at
        or snapshot.source_timestamp
        or snapshot.retrieved_at
        or snapshot.effective_at
    )
    if value is None:
        return datetime.max.replace(tzinfo=UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _target_period_end(
    snapshots: list[CeriEstimateSnapshot],
    period_slot: str,
    as_of: date,
) -> date | None:
    quarterly = {"CURRENT_QUARTER", "NEXT_QUARTER", "QUARTERLY"}
    annual = {"CURRENT_FISCAL_YEAR", "NEXT_FISCAL_YEAR", "ANNUAL"}
    if period_slot in {"CURRENT_QUARTER", "NEXT_QUARTER"}:
        candidates = sorted(
            {
                snapshot.fiscal_period_end
                for snapshot in snapshots
                if snapshot.period_type in quarterly and snapshot.fiscal_period_end >= as_of
            }
        )
        index = 0 if period_slot == "CURRENT_QUARTER" else 1
    elif period_slot in {"CURRENT_FISCAL_YEAR", "NEXT_FISCAL_YEAR"}:
        candidates = sorted(
            {
                snapshot.fiscal_period_end
                for snapshot in snapshots
                if snapshot.period_type in annual and snapshot.fiscal_period_end >= as_of
            }
        )
        index = 0 if period_slot == "CURRENT_FISCAL_YEAR" else 1
    else:
        candidates = sorted({snapshot.fiscal_period_end for snapshot in snapshots})
        index = 0
    return candidates[index] if len(candidates) > index else None


def _snapshot_sort(snapshot: CeriEstimateSnapshot) -> tuple[Any, ...]:
    return (
        snapshot.effective_session or date.min,
        _effective_at(snapshot),
        snapshot.id or 0,
        snapshot.source_record_id,
    )
