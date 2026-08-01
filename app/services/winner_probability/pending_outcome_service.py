from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.tables import (
    OutcomeStatus,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
)
from app.services.us_market_calendar import nth_us_trading_day_from_entry
from app.services.winner_probability.config import (
    ENTRY_MODEL_SIGNAL_CLOSE_DIAGNOSTIC,
    WinnerProbabilityConfig,
)
from app.services.winner_probability.repository import WinnerProbabilityRepository


@dataclass(frozen=True)
class PendingOutcomeMaterializationResult:
    forward_outcome_count: int
    target_stop_outcome_count: int


class PendingOutcomeService:
    def __init__(self, repository: WinnerProbabilityRepository | None = None) -> None:
        self.repository = repository or WinnerProbabilityRepository()

    def materialize_pending_outcomes(
        self,
        db: Session,
        prediction: WinnerPredictionSnapshot,
        config: WinnerProbabilityConfig,
    ) -> PendingOutcomeMaterializationResult:
        forward_count = 0
        target_stop_count = 0
        definitions = [
            self._ensure_outcome_definition(db, raw_definition, config)
            for raw_definition in config.outcome_definitions
        ]
        entry_models = (config.entry_models.production, *config.entry_models.diagnostics)
        for entry_model in entry_models:
            for horizon in config.horizon.sessions:
                _, created = self._ensure_forward_outcome(
                    db, prediction, entry_model, horizon
                )
                if created:
                    forward_count += 1

        for raw_definition, definition in zip(config.outcome_definitions, definitions, strict=True):
            forward = self.repository.get_forward_outcome(
                db,
                prediction_id=prediction.id,
                entry_model=raw_definition.entry_model,
                horizon_sessions=raw_definition.horizon_sessions,
            )
            _, created = self._ensure_target_stop_outcome(
                db,
                prediction,
                definition,
                forward,
                raw_definition.target_pct,
                raw_definition.stop_pct,
            )
            if created:
                target_stop_count += 1

        return PendingOutcomeMaterializationResult(
            forward_outcome_count=forward_count,
            target_stop_outcome_count=target_stop_count,
        )

    def _ensure_outcome_definition(
        self,
        db: Session,
        raw_definition,
        config: WinnerProbabilityConfig,
    ) -> WinnerOutcomeDefinition:
        existing = self.repository.get_outcome_definition(
            db,
            definition_id=raw_definition.id,
            calculation_version=config.engine.calculation_version,
        )
        if existing is not None:
            return existing
        row = WinnerOutcomeDefinition(
            definition_id=raw_definition.id,
            label=raw_definition.label,
            entry_model=raw_definition.entry_model,
            horizon_sessions=raw_definition.horizon_sessions,
            target_pct=raw_definition.target_pct,
            stop_pct=raw_definition.stop_pct,
            same_bar_conflict_policy=raw_definition.same_bar_conflict_policy,
            calculation_version=config.engine.calculation_version,
            config_hash=config.config_hash,
            is_primary=raw_definition.primary,
            is_active=True,
            metadata_json={"phase": "phase_3"},
        )
        return self.repository.add(db, row)

    def _ensure_forward_outcome(
        self,
        db: Session,
        prediction: WinnerPredictionSnapshot,
        entry_model: str,
        horizon_sessions: int,
    ) -> tuple[WinnerForwardOutcome, bool]:
        existing = self.repository.get_forward_outcome(
            db,
            prediction_id=prediction.id,
            entry_model=entry_model,
            horizon_sessions=horizon_sessions,
        )
        if existing is not None:
            return existing, False

        entry_session = prediction.planned_entry_session
        if entry_model == ENTRY_MODEL_SIGNAL_CLOSE_DIAGNOSTIC:
            entry_session = prediction.prediction_as_of_date
        due_session = (
            nth_us_trading_day_from_entry(entry_session, horizon_sessions)
            if entry_session is not None
            else None
        )
        row = WinnerForwardOutcome(
            prediction_id=prediction.id,
            entry_model=entry_model,
            horizon_sessions=horizon_sessions,
            entry_session=entry_session,
            due_session=due_session,
            status=OutcomeStatus.PENDING,
            revision=1,
            is_current_revision=True,
            metadata_json={"materialized_at_capture": True},
        )
        return self.repository.add(db, row), True

    def _ensure_target_stop_outcome(
        self,
        db: Session,
        prediction: WinnerPredictionSnapshot,
        definition: WinnerOutcomeDefinition,
        forward: WinnerForwardOutcome | None,
        target_pct: float,
        stop_pct: float,
    ) -> tuple[WinnerTargetStopOutcome, bool]:
        existing = self.repository.get_target_stop_outcome(
            db,
            prediction_id=prediction.id,
            outcome_definition_id=definition.id,
        )
        if existing is not None:
            return existing, False
        row = WinnerTargetStopOutcome(
            prediction_id=prediction.id,
            outcome_definition_id=definition.id,
            forward_outcome_id=getattr(forward, "id", None),
            entry_model=definition.entry_model,
            horizon_sessions=definition.horizon_sessions,
            status=OutcomeStatus.PENDING,
            revision=1,
            is_current_revision=True,
            target_pct=target_pct,
            stop_pct=stop_pct,
            metadata_json={"materialized_at_capture": True},
        )
        return self.repository.add(db, row), True
