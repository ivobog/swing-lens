from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select

from app.models.tables import SetupSignalSnapshot
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
