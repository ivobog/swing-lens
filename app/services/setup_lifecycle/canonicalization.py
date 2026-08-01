from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.models.tables import SetupLifecycleEvent, SetupSignalSnapshot
from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.repository import SetupLifecycleRepository


@dataclass(frozen=True)
class CanonicalizationResult:
    selected_snapshot_ids: tuple[int, ...] = ()
    changed_snapshot_ids: tuple[int, ...] = ()
    unchanged_snapshot_ids: tuple[int, ...] = ()
    audit_event_ids: tuple[int, ...] = ()
    groups_evaluated: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def selected_count(self) -> int:
        return len(self.selected_snapshot_ids)

    @property
    def changed_count(self) -> int:
        return len(self.changed_snapshot_ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_snapshots": self.selected_count,
            "canonical_changed": self.changed_count,
            "canonical_audit_events": len(self.audit_event_ids),
            "groups_evaluated": self.groups_evaluated,
            "warnings": len(self.warnings),
        }


class SetupLifecycleCanonicalizer:
    def __init__(
        self,
        *,
        repository: SetupLifecycleRepository | None = None,
        config: SetupLifecycleConfig | None = None,
    ) -> None:
        self.repository = repository or SetupLifecycleRepository()
        self.config = config or load_setup_lifecycle_config()

    def canonicalize_run(
        self,
        db,
        *,
        run_id: int,
        evaluation_run_id: int | None = None,
    ) -> CanonicalizationResult:
        affected = self.repository.load_snapshots_for_run(
            db,
            run_id=run_id,
            config_hash=self.config.config_hash,
        )
        candidates = self.repository.load_canonicalization_candidates(
            db,
            affected,
            lock=True,
        )
        return self.canonicalize_snapshots(
            db,
            candidates,
            evaluation_run_id=evaluation_run_id,
        )

    def canonicalize_snapshots(
        self,
        db,
        snapshots: list[SetupSignalSnapshot] | tuple[SetupSignalSnapshot, ...],
        *,
        evaluation_run_id: int | None = None,
    ) -> CanonicalizationResult:
        selected_ids: list[int] = []
        changed_ids: list[int] = []
        unchanged_ids: list[int] = []
        audit_ids: list[int] = []

        for group in _groups(snapshots).values():
            selected = select_canonical_snapshot(group)
            previous = next((snapshot for snapshot in group if snapshot.is_canonical), None)
            selected_ids.append(selected.id)
            changed = previous is None or previous.id != selected.id
            if changed:
                changed_ids.append(selected.id)
                event = self._canonical_revision_event(
                    selected,
                    previous,
                    evaluation_run_id=evaluation_run_id,
                )
                audit_event = self.repository.add_lifecycle_event(db, event)
                audit_ids.append(audit_event.id)
            else:
                unchanged_ids.append(selected.id)
            self.repository.promote_canonical_snapshot(
                db,
                selected,
                reason="phase_4_canonical_precedence",
                decision={
                    "changed": changed,
                    "previous_snapshot_id": previous.id if previous is not None else None,
                    "selected_snapshot_id": selected.id,
                    "precedence": list(self.config.canonicalization.precedence),
                    "score": list(_canonical_sort_key(selected)),
                },
            )

        return CanonicalizationResult(
            selected_snapshot_ids=tuple(selected_ids),
            changed_snapshot_ids=tuple(changed_ids),
            unchanged_snapshot_ids=tuple(unchanged_ids),
            audit_event_ids=tuple(audit_ids),
            groups_evaluated=len(_groups(snapshots)),
        )

    def _canonical_revision_event(
        self,
        selected: SetupSignalSnapshot,
        previous: SetupSignalSnapshot | None,
        *,
        evaluation_run_id: int | None,
    ) -> SetupLifecycleEvent:
        key = self.repository.stable_key(
            "canonical_revision",
            str(evaluation_run_id or ""),
            selected.ticker,
            selected.timeframe,
            selected.data_as_of_date.isoformat(),
            str(previous.id if previous is not None else ""),
            str(selected.id),
            selected.config_hash,
        )
        return SetupLifecycleEvent(
            episode_id=None,
            evaluation_run_id=evaluation_run_id,
            snapshot_id=selected.id,
            ticker=selected.ticker,
            timeframe=selected.timeframe,
            setup_family=selected.primary_setup_family or "GENERIC",
            effective_date=selected.data_as_of_date,
            event_type="CANONICAL_REVISION",
            from_state="CANONICAL" if previous is not None else None,
            to_state="CANONICAL",
            from_phase=previous.primary_phase if previous is not None else None,
            to_phase=selected.primary_phase or "CANDIDATE",
            state_age_before=None,
            immediate_transition=True,
            actionability_before=None,
            actionability_after=selected.actionability_candidate or "WATCH_ONLY",
            confidence_score=selected.confidence_score or 0,
            confidence_label=selected.confidence_label or "INSUFFICIENT",
            severity="INFO",
            source_event_key=key,
            engine_version=selected.engine_version,
            config_version=selected.config_version,
            config_hash=selected.config_hash,
            reason_codes_json=["CANONICAL_SELECTION_CHANGED"],
            evidence_json={
                "previous_snapshot_id": previous.id if previous is not None else None,
                "selected_snapshot_id": selected.id,
                "canonical_score": list(_canonical_sort_key(selected)),
            },
            warning_flags_json=[],
        )


def select_canonical_snapshot(snapshots: list[SetupSignalSnapshot]) -> SetupSignalSnapshot:
    if not snapshots:
        raise ValueError("at least one snapshot is required")
    return max(snapshots, key=_canonical_sort_key)


def _groups(
    snapshots: list[SetupSignalSnapshot] | tuple[SetupSignalSnapshot, ...],
) -> dict[tuple[str, str, date], list[SetupSignalSnapshot]]:
    grouped: dict[tuple[str, str, date], list[SetupSignalSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        grouped[(snapshot.ticker, snapshot.timeframe, snapshot.data_as_of_date)].append(snapshot)
    return grouped


def _canonical_sort_key(snapshot: SetupSignalSnapshot) -> tuple[Any, ...]:
    return (
        int(_has_completed_daily_bar(snapshot)),
        int(_source_pipeline_successful(snapshot)),
        _coverage(snapshot),
        int(_context_complete(snapshot)),
        snapshot.calculated_at,
        snapshot.id or 0,
    )


def _has_completed_daily_bar(snapshot: SetupSignalSnapshot) -> bool:
    latest_bar = (snapshot.source_lineage_json or {}).get("latest_bar")
    if not isinstance(latest_bar, dict):
        return False
    return latest_bar.get("bar_date") == snapshot.data_as_of_date.isoformat()


def _source_pipeline_successful(snapshot: SetupSignalSnapshot) -> bool:
    warnings = set(snapshot.warning_flags_json or [])
    fatal_warnings = {
        "MISSING_REQUIRED_TECHNICAL_SCORE",
        "MISSING_REQUIRED_SETUP_SCORE",
        "MISSING_REQUIRED_CLASSIFICATION",
        "MISSING_REQUIRED_CLOSE_PRICE",
        "NO_COMPLETED_DAILY_BAR",
        "FUTURE_DATED_SOURCE_CONTEXT",
    }
    return not warnings.intersection(fatal_warnings)


def _coverage(snapshot: SetupSignalSnapshot) -> Decimal:
    value = snapshot.required_feature_coverage
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _context_complete(snapshot: SetupSignalSnapshot) -> bool:
    return bool(snapshot.market_regime_snapshot_id and snapshot.sector_rotation_snapshot_id)
