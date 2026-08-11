from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.models.tables import SetupLifecycleEpisode, SetupLifecycleEvent, SetupSignalSnapshot
from app.services.setup_lifecycle.actionability_policy import SetupLifecycleActionabilityPolicy
from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import (
    ActionabilityDecision,
    EpisodeApplyResult,
    LifecycleDecision,
    NormalizedSnapshot,
    SignalValue,
)
from app.services.setup_lifecycle.enums import (
    Actionability,
    DataQualityLabel,
    EventSeverity,
    LifecycleState,
    SetupFamily,
    SignalValueType,
)
from app.services.setup_lifecycle.lifecycle_engine import SetupLifecycleEngine
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.us_market_calendar import us_trading_sessions_between


@dataclass(frozen=True)
class EpisodeEvaluationResult:
    episode: SetupLifecycleEpisode | None
    decision: LifecycleDecision
    actionability: ActionabilityDecision
    lifecycle_event: SetupLifecycleEvent | None = None
    actionability_before: str | None = None
    opened: bool = False
    updated: bool = False
    closed: bool = False
    warning_codes: tuple[str, ...] = ()


class SetupLifecycleEpisodeService:
    def __init__(
        self,
        *,
        repository: SetupLifecycleRepository | None = None,
        lifecycle_engine: SetupLifecycleEngine | None = None,
        actionability_policy: SetupLifecycleActionabilityPolicy | None = None,
        config: SetupLifecycleConfig | None = None,
    ) -> None:
        self.config = config or load_setup_lifecycle_config()
        self.repository = repository or SetupLifecycleRepository()
        self.lifecycle_engine = lifecycle_engine or SetupLifecycleEngine(config=self.config)
        self.actionability_policy = actionability_policy or SetupLifecycleActionabilityPolicy(
            self.config
        )

    def apply_snapshot(
        self,
        db,
        snapshot: SetupSignalSnapshot,
        *,
        evaluation_run_id: int | None = None,
        completed_observation_sessions: int = 1,
        prior_snapshots: tuple[NormalizedSnapshot, ...] = (),
    ) -> EpisodeEvaluationResult:
        normalized = normalized_snapshot_from_row(snapshot)
        first_pass = self.lifecycle_engine.evaluate(
            _request(
                normalized,
                previous_snapshots=prior_snapshots,
                state_age_sessions=0,
                missing_observation_sessions=0,
            )
        )
        active = self.repository.active_episode_for_update(
            db,
            ticker=snapshot.ticker,
            timeframe=snapshot.timeframe,
            setup_family=first_pass.setup_family.value,
        )
        decision = first_pass
        if active is not None:
            decision = self.lifecycle_engine.evaluate(
                _request(
                    normalized,
                    previous_snapshots=prior_snapshots,
                    previous_state=LifecycleState(active.current_state),
                    previous_phase=active.current_phase,
                    previous_confidence_score=active.confidence_score,
                    state_age_sessions=active.state_age_sessions,
                    persistence_sessions=_persistence_sessions(active),
                    missing_observation_sessions=active.missing_observation_sessions,
                )
            )
        actionability = self.actionability_policy.evaluate(decision, normalized)
        self._apply_snapshot_denormalization(snapshot, decision, actionability)

        if active is None:
            return self._maybe_open_episode(
                db,
                snapshot,
                decision,
                actionability,
                evaluation_run_id=evaluation_run_id,
            )

        return self._update_episode(
            db,
            active,
            snapshot,
            decision,
            actionability,
            evaluation_run_id=evaluation_run_id,
            completed_observation_sessions=completed_observation_sessions,
        )

    def apply_observation_gap(
        self,
        db,
        *,
        ticker: str,
        timeframe: str,
        setup_family: SetupFamily,
        observed_on: date,
        evaluation_run_id: int | None = None,
    ) -> EpisodeApplyResult:
        episode = self.repository.active_episode_for_update(
            db,
            ticker=ticker,
            timeframe=timeframe,
            setup_family=setup_family.value,
        )
        if episode is None:
            return EpisodeApplyResult(episode_id=None)

        missing_sessions = trading_sessions_between(episode.last_observed_on, observed_on)
        if missing_sessions <= 0:
            return EpisodeApplyResult(episode_id=episode.id, updated=False)

        episode.missing_observation_sessions += missing_sessions
        episode.current_as_of_date = observed_on
        threshold = self.config.families.policies[setup_family].observation_gap_sessions
        if episode.missing_observation_sessions <= threshold:
            return EpisodeApplyResult(episode_id=episode.id, updated=True)

        event = self._create_event(
            db,
            episode,
            snapshot=None,
            evaluation_run_id=evaluation_run_id,
            to_state=LifecycleState.EXPIRED,
            to_phase="OBSERVATION_GAP_EXPIRED",
            actionability_after=Actionability.WATCH_ONLY,
            confidence_score=episode.confidence_score,
            confidence_label=episode.confidence_label,
            reason_codes=("OBSERVATION_GAP_EXPIRED",),
            evidence={
                "missing_observation_sessions": episode.missing_observation_sessions,
                "observation_gap_threshold": threshold,
            },
            event_type="STATE_TRANSITION",
            immediate_transition=False,
        )
        self._close_episode(
            episode,
            closed_on=observed_on,
            state=LifecycleState.EXPIRED,
            phase="OBSERVATION_GAP_EXPIRED",
            terminal_reason="OBSERVATION_GAP",
            evaluation_run_id=evaluation_run_id,
        )
        return EpisodeApplyResult(
            episode_id=episode.id,
            updated=True,
            closed=True,
            lifecycle_event_id=event.id,
        )

    def refresh_primary_status(self, db, *, ticker: str, timeframe: str) -> None:
        episodes = self.repository.active_episodes_for_ticker(
            db,
            ticker=ticker,
            timeframe=timeframe,
        )
        for index, episode in enumerate(select_primary_episodes(episodes, config=self.config)):
            episode.is_primary = index == 0
            episode.primary_rank = index + 1

    def _maybe_open_episode(
        self,
        db,
        snapshot: SetupSignalSnapshot,
        decision: LifecycleDecision,
        actionability: ActionabilityDecision,
        *,
        evaluation_run_id: int | None,
    ) -> EpisodeEvaluationResult:
        if not _opens_episode(decision):
            return EpisodeEvaluationResult(
                episode=None,
                decision=decision,
                actionability=actionability,
                warning_codes=("NOT_TRACKABLE_FOR_EPISODE",),
            )

        cooldown_warning = self._cooldown_warning(db, snapshot, decision)
        if cooldown_warning is not None:
            return EpisodeEvaluationResult(
                episode=None,
                decision=decision,
                actionability=actionability,
                warning_codes=(cooldown_warning,),
            )

        episode = SetupLifecycleEpisode(
            ticker=self.repository.normalize_ticker(snapshot.ticker),
            timeframe=snapshot.timeframe,
            setup_family=decision.setup_family.value,
            status="ACTIVE",
            opened_on=snapshot.data_as_of_date,
            current_as_of_date=snapshot.data_as_of_date,
            last_observed_on=snapshot.data_as_of_date,
            missing_observation_sessions=0,
            current_state=decision.proposed_state.value,
            current_phase=decision.phase_code,
            state_entered_on=snapshot.data_as_of_date,
            state_age_sessions=0,
            current_actionability=actionability.actionability.value,
            confidence_score=decision.confidence_score,
            confidence_label=decision.confidence_label.value,
            opening_snapshot_id=snapshot.id,
            current_snapshot_id=snapshot.id,
            opening_evaluation_id=evaluation_run_id,
            engine_version=snapshot.engine_version,
            config_version=snapshot.config_version,
            config_hash=snapshot.config_hash,
            metadata_json=_episode_metadata(snapshot, decision, actionability),
        )
        episode = self.repository.add(db, episode)
        event = self._create_event(
            db,
            episode,
            snapshot=snapshot,
            evaluation_run_id=evaluation_run_id,
            to_state=decision.proposed_state,
            to_phase=decision.phase_code,
            actionability_after=actionability.actionability,
            confidence_score=decision.confidence_score,
            confidence_label=decision.confidence_label.value,
            reason_codes=_opening_reasons(decision),
            evidence=_decision_evidence(decision, actionability),
            event_type="EPISODE_OPENED",
            immediate_transition=decision.immediate_transition,
        )
        self.refresh_primary_status(db, ticker=snapshot.ticker, timeframe=snapshot.timeframe)
        return EpisodeEvaluationResult(
            episode=episode,
            decision=decision,
            actionability=actionability,
            lifecycle_event=event,
            actionability_before=None,
            opened=True,
            updated=True,
        )

    def _update_episode(
        self,
        db,
        episode: SetupLifecycleEpisode,
        snapshot: SetupSignalSnapshot,
        decision: LifecycleDecision,
        actionability: ActionabilityDecision,
        *,
        evaluation_run_id: int | None,
        completed_observation_sessions: int,
    ) -> EpisodeEvaluationResult:
        changed = (
            episode.current_state != decision.proposed_state.value
            or episode.current_phase != decision.phase_code
        )
        previous_state = LifecycleState(episode.current_state)
        previous_phase = episode.current_phase
        previous_actionability = episode.current_actionability
        state_age_before = episode.state_age_sessions

        episode.current_snapshot_id = snapshot.id
        episode.current_as_of_date = snapshot.data_as_of_date
        episode.last_observed_on = snapshot.data_as_of_date
        episode.missing_observation_sessions = 0
        episode.current_actionability = actionability.actionability.value
        episode.confidence_score = decision.confidence_score
        episode.confidence_label = decision.confidence_label.value
        episode.metadata_json = _episode_metadata(snapshot, decision, actionability)
        if changed:
            episode.current_state = decision.proposed_state.value
            episode.current_phase = decision.phase_code
            if previous_state is decision.proposed_state:
                episode.state_age_sessions += completed_observation_sessions
            else:
                episode.state_entered_on = snapshot.data_as_of_date
                episode.state_age_sessions = 0
        else:
            episode.state_age_sessions += completed_observation_sessions

        event = None
        closed = False
        if changed:
            event = self._create_event(
                db,
                episode,
                snapshot=snapshot,
                evaluation_run_id=evaluation_run_id,
                to_state=decision.proposed_state,
                to_phase=decision.phase_code,
                actionability_after=actionability.actionability,
                confidence_score=decision.confidence_score,
                confidence_label=decision.confidence_label.value,
                reason_codes=decision.reason_codes,
                evidence=_decision_evidence(decision, actionability),
                event_type="STATE_TRANSITION"
                if previous_state is not decision.proposed_state
                else "PHASE_TRANSITION",
                immediate_transition=decision.immediate_transition,
                from_state=previous_state,
                from_phase=previous_phase,
                actionability_before=previous_actionability,
                state_age_before=state_age_before,
            )
        if decision.proposed_state in {LifecycleState.FAILED, LifecycleState.EXPIRED}:
            self._close_episode(
                episode,
                closed_on=snapshot.data_as_of_date,
                state=decision.proposed_state,
                phase=decision.phase_code,
                terminal_reason=decision.terminal_reason or decision.proposed_state.value,
                snapshot_id=snapshot.id,
                evaluation_run_id=evaluation_run_id,
            )
            closed = True

        self.refresh_primary_status(db, ticker=snapshot.ticker, timeframe=snapshot.timeframe)
        return EpisodeEvaluationResult(
            episode=episode,
            decision=decision,
            actionability=actionability,
            lifecycle_event=event,
            actionability_before=previous_actionability,
            updated=True,
            closed=closed,
        )

    def _create_event(
        self,
        db,
        episode: SetupLifecycleEpisode,
        *,
        snapshot: SetupSignalSnapshot | None,
        evaluation_run_id: int | None,
        to_state: LifecycleState,
        to_phase: str,
        actionability_after: Actionability,
        confidence_score: int,
        confidence_label: str,
        reason_codes: tuple[str, ...],
        evidence: dict[str, Any],
        event_type: str,
        immediate_transition: bool,
        from_state: LifecycleState | None = None,
        from_phase: str | None = None,
        actionability_before: str | None = None,
        state_age_before: int | None = None,
    ) -> SetupLifecycleEvent:
        effective_date = (
            snapshot.data_as_of_date if snapshot is not None else episode.current_as_of_date
        )
        key = self.repository.stable_key(
            "episode_event",
            str(evaluation_run_id or ""),
            str(episode.id or ""),
            event_type,
            episode.ticker,
            episode.timeframe,
            episode.setup_family,
            effective_date.isoformat(),
            from_state.value if from_state is not None else "",
            to_state.value,
            from_phase or "",
            to_phase,
            episode.config_hash,
        )
        event = SetupLifecycleEvent(
            episode_id=episode.id,
            evaluation_run_id=evaluation_run_id,
            snapshot_id=snapshot.id if snapshot is not None else None,
            ticker=episode.ticker,
            timeframe=episode.timeframe,
            setup_family=episode.setup_family,
            effective_date=effective_date,
            event_type=event_type,
            from_state=from_state.value if from_state is not None else None,
            to_state=to_state.value,
            from_phase=from_phase,
            to_phase=to_phase,
            state_age_before=state_age_before,
            immediate_transition=immediate_transition,
            actionability_before=actionability_before,
            actionability_after=actionability_after.value,
            confidence_score=confidence_score,
            confidence_label=confidence_label,
            severity=_severity(to_state).value,
            source_event_key=key,
            engine_version=episode.engine_version,
            config_version=episode.config_version,
            config_hash=episode.config_hash,
            reason_codes_json=list(reason_codes),
            evidence_json=dict(evidence),
            warning_flags_json=list(snapshot.warning_flags_json or []) if snapshot else [],
        )
        event = self.repository.add_lifecycle_event(db, event)
        self.repository.supersede_prior_current_events(db, event)
        return event

    def _close_episode(
        self,
        episode: SetupLifecycleEpisode,
        *,
        closed_on: date,
        state: LifecycleState,
        phase: str,
        terminal_reason: str,
        snapshot_id: int | None = None,
        evaluation_run_id: int | None = None,
    ) -> None:
        episode.status = "CLOSED"
        episode.closed_on = closed_on
        episode.current_state = state.value
        episode.current_phase = phase
        episode.terminal_state = state.value
        episode.terminal_reason_code = terminal_reason
        episode.closing_snapshot_id = snapshot_id
        episode.closing_evaluation_id = evaluation_run_id
        episode.is_primary = False
        episode.primary_rank = None

    def _cooldown_warning(
        self,
        db,
        snapshot: SetupSignalSnapshot,
        decision: LifecycleDecision,
    ) -> str | None:
        closed = self.repository.latest_closed_episode(
            db,
            ticker=snapshot.ticker,
            timeframe=snapshot.timeframe,
            setup_family=decision.setup_family.value,
        )
        if closed is None or closed.closed_on is None:
            return None
        cooldown = self.config.families.policies[
            decision.setup_family
        ].failed_rearm_cooldown_sessions
        sessions_since_close = trading_sessions_between(closed.closed_on, snapshot.data_as_of_date)
        fresh_setup = decision.proposed_state in {
            LifecycleState.READY,
            LifecycleState.TRIGGERED,
            LifecycleState.CONFIRMED,
        }
        if sessions_since_close < cooldown and not fresh_setup:
            return "REARM_COOLDOWN_ACTIVE"
        return None

    @staticmethod
    def _apply_snapshot_denormalization(
        snapshot: SetupSignalSnapshot,
        decision: LifecycleDecision,
        actionability: ActionabilityDecision,
    ) -> None:
        snapshot.primary_setup_family = decision.setup_family.value
        snapshot.primary_phase = decision.phase_code
        snapshot.lifecycle_state_candidate = decision.proposed_state.value
        snapshot.actionability_candidate = actionability.actionability.value
        snapshot.confidence_score = decision.confidence_score
        snapshot.confidence_label = decision.confidence_label.value


def normalized_snapshot_from_row(snapshot: SetupSignalSnapshot) -> NormalizedSnapshot:
    signals = {
        key: SignalValue(
            key=key,
            value_type=_value_type(raw),
            raw_value=raw,
            normalized_value=raw,
        )
        for key, raw in _signal_values(snapshot).items()
    }
    return NormalizedSnapshot(
        ticker=snapshot.ticker,
        timeframe=snapshot.timeframe,
        data_as_of_date=snapshot.data_as_of_date,
        calculated_at=snapshot.calculated_at,
        signals=signals,
        data_quality_label=_data_quality(snapshot.data_quality_label),
        required_feature_coverage=_number(snapshot.required_feature_coverage),
        freshness_status=snapshot.freshness_status,
        warning_flags=tuple(snapshot.warning_flags_json or ()),
        source_ids={
            "snapshot_id": snapshot.id,
            "run_id": snapshot.run_id,
        },
        source_lineage=dict(snapshot.source_lineage_json or {}),
    )


def select_primary_episodes(
    episodes: list[SetupLifecycleEpisode] | tuple[SetupLifecycleEpisode, ...],
    *,
    config: SetupLifecycleConfig | None = None,
) -> list[SetupLifecycleEpisode]:
    config = config or load_setup_lifecycle_config()
    return sorted(episodes, key=lambda episode: _primary_sort_key(episode, config), reverse=True)


def trading_sessions_between(start_exclusive: date, end_inclusive: date) -> int:
    return us_trading_sessions_between(start_exclusive, end_inclusive)


def _request(
    snapshot: NormalizedSnapshot,
    *,
    previous_snapshots: tuple[NormalizedSnapshot, ...] = (),
    previous_state: LifecycleState | None = None,
    previous_phase: str | None = None,
    previous_confidence_score: int | None = None,
    state_age_sessions: int = 0,
    persistence_sessions: int = 0,
    missing_observation_sessions: int = 0,
):
    from app.services.setup_lifecycle.lifecycle_engine import LifecycleEvaluationInput

    return LifecycleEvaluationInput(
        snapshot=snapshot,
        previous_snapshots=previous_snapshots,
        previous_state=previous_state,
        previous_phase=previous_phase,
        previous_confidence_score=previous_confidence_score,
        state_age_sessions=state_age_sessions,
        persistence_sessions=persistence_sessions,
        missing_observation_sessions=missing_observation_sessions,
    )


def _signal_values(snapshot: SetupSignalSnapshot) -> dict[str, Any]:
    signals: dict[str, Any] = {}
    for key, raw in (snapshot.signals_json or {}).items():
        if isinstance(raw, dict) and "value" in raw:
            signals[key] = raw["value"]
        else:
            signals[key] = raw
    for key, value in {
        "setup_score": snapshot.setup_score,
        "technical_score": snapshot.trend_score or snapshot.dual_score,
        "classification": snapshot.technical_classification,
        "stage": snapshot.stage,
        "distance_to_pivot_pct": snapshot.distance_to_pivot_pct,
        "close_trigger_cross": snapshot.close_above_trigger,
        "market_regime": (snapshot.signals_json or {}).get("market_regime"),
        "earnings_risk": (snapshot.signals_json or {}).get("earnings_risk"),
        "liquidity": (snapshot.signals_json or {}).get("liquidity"),
    }.items():
        signals.setdefault(key, _json_scalar(value))
    return signals


def _json_scalar(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    if isinstance(value, Decimal):
        return float(value)
    return value


def _data_quality(value: str) -> DataQualityLabel:
    try:
        return DataQualityLabel(value)
    except ValueError:
        return DataQualityLabel.INSUFFICIENT


def _value_type(value: Any) -> SignalValueType:
    if isinstance(value, bool):
        return SignalValueType.BOOLEAN
    if isinstance(value, int | float | Decimal):
        return SignalValueType.FLOAT
    if value is None:
        return SignalValueType.NULLABILITY
    return SignalValueType.ENUM


def _opens_episode(decision: LifecycleDecision) -> bool:
    if decision.proposed_state in {LifecycleState.FAILED, LifecycleState.EXPIRED}:
        return False
    return (
        decision.confidence_score > 0
        and "INSUFFICIENT_FAMILY_EVIDENCE" not in decision.reason_codes
    )


def _opening_reasons(decision: LifecycleDecision) -> tuple[str, ...]:
    reasons = list(decision.reason_codes)
    if decision.proposed_state in {
        LifecycleState.READY,
        LifecycleState.TRIGGERED,
        LifecycleState.CONFIRMED,
        LifecycleState.EXTENDED,
    }:
        reasons.append("SKIPPED_PRIOR_PROGRESSION")
    return tuple(dict.fromkeys(reasons))


def _persistence_sessions(episode: SetupLifecycleEpisode) -> int:
    if episode.current_state == LifecycleState.TRIGGERED.value:
        return episode.state_age_sessions + 1
    return 0


def _episode_metadata(
    snapshot: SetupSignalSnapshot,
    decision: LifecycleDecision,
    actionability: ActionabilityDecision,
) -> dict[str, Any]:
    return {
        "setup_score": _number(snapshot.setup_score)
        or _number((snapshot.signals_json or {}).get("setup_score")),
        "snapshot_id": snapshot.id,
        "reason_codes": list(decision.reason_codes),
        "actionability_reason_codes": list(actionability.reason_codes),
        "actionability_metadata": dict(actionability.metadata),
        "blockers": list(actionability.blockers),
        "market_regime": _json_scalar((snapshot.signals_json or {}).get("market_regime")),
    }


def _decision_evidence(
    decision: LifecycleDecision,
    actionability: ActionabilityDecision,
) -> dict[str, Any]:
    return {
        **decision.evidence,
        "actionability": {
            "reason_codes": list(actionability.reason_codes),
            "blockers": list(actionability.blockers),
            "metadata": dict(actionability.metadata),
        },
    }


def _number(value: Any) -> float | None:
    value = _json_scalar(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _severity(state: LifecycleState) -> EventSeverity:
    if state is LifecycleState.FAILED or state is LifecycleState.EXTENDED:
        return EventSeverity.RISK
    if state in {LifecycleState.READY, LifecycleState.TRIGGERED, LifecycleState.CONFIRMED}:
        return EventSeverity.ACTIONABLE
    return EventSeverity.INFO


def _primary_sort_key(
    episode: SetupLifecycleEpisode,
    config: SetupLifecycleConfig,
) -> tuple[Any, ...]:
    state_priority = {
        state.value: len(config.states.transition_precedence) - index
        for index, state in enumerate(config.states.transition_precedence)
    }
    family_priority = {
        family.value: len(config.families.precedence) - index
        for index, family in enumerate(config.families.precedence)
    }
    return (
        state_priority.get(episode.current_state, 0),
        episode.confidence_score,
        _number((episode.metadata_json or {}).get("setup_score")) or 0.0,
        episode.current_as_of_date,
        family_priority.get(episode.setup_family, 0),
    )
