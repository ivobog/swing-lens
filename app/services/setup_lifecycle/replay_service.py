from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select

from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
)
from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.enums import EvaluationStatus
from app.services.setup_lifecycle.episode_service import normalized_snapshot_from_row
from app.services.setup_lifecycle.lifecycle_engine import evaluate_lifecycle
from app.services.setup_lifecycle.repository import SetupLifecycleRepository


@dataclass(frozen=True)
class SetupLifecycleReplayRequest:
    ticker: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    persist: bool = False
    requester: str | None = None
    reason: str | None = None
    requested_config: dict[str, Any] = field(default_factory=dict)


class SetupLifecycleReplayService:
    def __init__(
        self,
        *,
        repository: SetupLifecycleRepository | None = None,
        config: SetupLifecycleConfig | None = None,
    ) -> None:
        self.repository = repository or SetupLifecycleRepository()
        self.config = config or load_setup_lifecycle_config()

    def replay(self, db, request: SetupLifecycleReplayRequest) -> dict[str, Any]:
        snapshots = self._snapshots(db, request)
        proposed = [
            {
                "snapshot_id": snapshot.id,
                "ticker": snapshot.ticker,
                "data_as_of_date": snapshot.data_as_of_date.isoformat(),
                "decision": _decision_payload(
                    evaluate_lifecycle(normalized_snapshot_from_row(snapshot))
                ),
            }
            for snapshot in snapshots
        ]
        evaluation_run = None
        if request.persist:
            evaluation_run = self.repository.create_evaluation_run(
                db,
                mode="REPLAY",
                status=EvaluationStatus.RUNNING.value,
                engine_version=self.config.engine.version,
                config_version=self.config.engine.config_version,
                config_hash=self.config.config_hash,
                output_evaluation_version=(
                    f"{self.config.engine.version}:replay:{self.config.config_hash[:12]}"
                ),
                date_from=request.date_from,
                date_to=request.date_to,
                ticker_scope=[request.ticker] if request.ticker else [],
                requested_config=request.requested_config,
                dry_run=False,
                requester=request.requester,
            )
            self.repository.complete_evaluation_run(
                db,
                evaluation_run,
                status=EvaluationStatus.COMPLETED.value,
                current_phase="replay_completed",
                counts={
                    "read": len(snapshots),
                    "captured": 0,
                    "canonical": len(snapshots),
                    "transitioned": len(proposed),
                    "alerted": 0,
                },
            )
        return {
            "mode": "PERSISTED_REPLAY" if request.persist else "DRY_RUN_REPLAY",
            "persisted": request.persist,
            "evaluation_run_id": evaluation_run.id if evaluation_run is not None else None,
            "snapshot_count": len(snapshots),
            "proposed": proposed,
            "comparison": self.compare(db, proposed=proposed, request=request),
        }

    def compare(
        self,
        db,
        *,
        proposed: list[dict[str, Any]],
        request: SetupLifecycleReplayRequest,
    ) -> dict[str, Any]:
        current_events = self._current_events(db, request)
        current_by_key = {_comparison_key_from_event(event): event for event in current_events}
        proposed_by_key = {_comparison_key_from_proposed(row): row for row in proposed}
        added_keys = sorted(set(proposed_by_key) - set(current_by_key))
        removed_keys = sorted(set(current_by_key) - set(proposed_by_key))
        common_keys = sorted(set(current_by_key) & set(proposed_by_key))
        changed_state_dates = [
            {
                "ticker": key[0],
                "effective_date": key[2],
                "current_state": current_by_key[key].to_state,
                "proposed_state": proposed_by_key[key]["decision"]["proposed_state"],
            }
            for key in common_keys
            if current_by_key[key].to_state != proposed_by_key[key]["decision"]["proposed_state"]
        ]
        alert_differences = self._alert_differences(db, request)
        return {
            "changed_state_dates": changed_state_dates,
            "added_events": [_proposed_summary(proposed_by_key[key]) for key in added_keys],
            "removed_events": [_event_summary(current_by_key[key]) for key in removed_keys],
            "alert_differences": alert_differences,
            "changed_primary_episode": self._changed_primary_episode(db, request, proposed),
        }

    def _snapshots(self, db, request: SetupLifecycleReplayRequest):
        statement = select(SetupSignalSnapshot).where(SetupSignalSnapshot.is_canonical.is_(True))
        if request.ticker:
            statement = statement.where(
                SetupSignalSnapshot.ticker == self.repository.normalize_ticker(request.ticker)
            )
        if request.date_from is not None:
            statement = statement.where(SetupSignalSnapshot.data_as_of_date >= request.date_from)
        if request.date_to is not None:
            statement = statement.where(SetupSignalSnapshot.data_as_of_date <= request.date_to)
        return list(
            db.scalars(
                statement.order_by(
                    SetupSignalSnapshot.ticker,
                    SetupSignalSnapshot.data_as_of_date,
                    SetupSignalSnapshot.id,
                )
            )
        )

    def _current_events(self, db, request: SetupLifecycleReplayRequest):
        statement = select(SetupLifecycleEvent).where(
            SetupLifecycleEvent.is_current_version.is_(True)
        )
        if request.ticker:
            statement = statement.where(
                SetupLifecycleEvent.ticker == self.repository.normalize_ticker(request.ticker)
            )
        if request.date_from is not None:
            statement = statement.where(SetupLifecycleEvent.effective_date >= request.date_from)
        if request.date_to is not None:
            statement = statement.where(SetupLifecycleEvent.effective_date <= request.date_to)
        return list(
            db.scalars(
                statement.order_by(
                    SetupLifecycleEvent.ticker,
                    SetupLifecycleEvent.setup_family,
                    SetupLifecycleEvent.effective_date,
                    SetupLifecycleEvent.id,
                )
            )
        )

    def _alert_differences(self, db, request: SetupLifecycleReplayRequest) -> dict[str, int]:
        statement = select(SignalAlertEvent)
        if request.ticker:
            statement = statement.where(
                SignalAlertEvent.ticker == self.repository.normalize_ticker(request.ticker)
            )
        if request.date_from is not None:
            statement = statement.where(SignalAlertEvent.effective_date >= request.date_from)
        if request.date_to is not None:
            statement = statement.where(SignalAlertEvent.effective_date <= request.date_to)
        alerts = list(db.scalars(statement))
        proposed_actionable = sum(
            1
            for row in self._snapshots(db, request)
            if evaluate_lifecycle(
                normalized_snapshot_from_row(row)
            ).actionability_candidate.value
            == "ACTIONABLE"
        )
        return {
            "current_alerts": len(alerts),
            "proposed_actionable_decisions": proposed_actionable,
            "delta": proposed_actionable - len(alerts),
        }

    def _changed_primary_episode(
        self,
        db,
        request: SetupLifecycleReplayRequest,
        proposed: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not proposed:
            return None
        first = proposed[0]
        statement = select(SetupLifecycleEpisode).where(
            SetupLifecycleEpisode.ticker == first["ticker"]
        )
        if request.ticker:
            statement = statement.where(
                SetupLifecycleEpisode.ticker == self.repository.normalize_ticker(request.ticker)
            )
        current = db.scalar(
            statement.where(SetupLifecycleEpisode.is_primary.is_(True))
            .order_by(SetupLifecycleEpisode.id.desc())
            .limit(1)
        )
        proposed_family = first["decision"]["setup_family"]
        if current is None or current.setup_family == proposed_family:
            return None
        return {
            "ticker": first["ticker"],
            "current_episode_id": current.id,
            "current_family": current.setup_family,
            "proposed_family": proposed_family,
        }


def _decision_payload(decision) -> dict[str, Any]:
    return {
        "setup_family": decision.setup_family.value,
        "phase_code": decision.phase_code,
        "proposed_state": decision.proposed_state.value,
        "actionability_candidate": decision.actionability_candidate.value,
        "confidence_score": decision.confidence_score,
        "confidence_label": decision.confidence_label.value,
        "reason_codes": list(decision.reason_codes),
        "evidence": decision.evidence,
    }


def _comparison_key_from_event(event: SetupLifecycleEvent) -> tuple[str, str, str]:
    return (event.ticker, event.setup_family, event.effective_date.isoformat())


def _comparison_key_from_proposed(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        row["ticker"],
        row["decision"]["setup_family"],
        row["data_as_of_date"],
    )


def _event_summary(event: SetupLifecycleEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "ticker": event.ticker,
        "setup_family": event.setup_family,
        "effective_date": event.effective_date.isoformat(),
        "state": event.to_state,
        "source_event_key": event.source_event_key,
    }


def _proposed_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": row["snapshot_id"],
        "ticker": row["ticker"],
        "setup_family": row["decision"]["setup_family"],
        "effective_date": row["data_as_of_date"],
        "state": row["decision"]["proposed_state"],
    }
