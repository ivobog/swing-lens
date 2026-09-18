from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
)
from app.services.configuration_delivery import anchored_decision_calculator
from app.services.core_mutation_authority import core_writer_member, core_writer_transaction
from app.services.setup_lifecycle.actionability_policy import SetupLifecycleActionabilityPolicy
from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.decision_evidence import (
    get_setup_evidence,
    persist_lifecycle_evaluation_evidence,
    persist_lifecycle_transition_evidence,
    persist_observation_gap_evaluation_evidence,
)
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
from app.services.technical_consumer_eligibility import setup_technical_blocked
from app.services.us_market_calendar import us_trading_sessions_between


@dataclass(frozen=True)
class EpisodeEvaluationResult:
    episode: SetupLifecycleEpisode | None
    decision: LifecycleDecision
    actionability: ActionabilityDecision
    lifecycle_event: SetupLifecycleEvent | None = None
    lifecycle_evaluation_evidence: SetupLifecycleEvaluationEvidence | None = None
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
        from app.services.decision_effective_configuration import resolve_lifecycle_configuration

        self.effective_configuration = resolve_lifecycle_configuration(config)
        self.config = self.effective_configuration.setup_config()
        self.repository = repository or SetupLifecycleRepository()
        self.lifecycle_engine = lifecycle_engine or SetupLifecycleEngine(config=self.config)
        self.actionability_policy = actionability_policy or SetupLifecycleActionabilityPolicy(
            self.config
        )

    @anchored_decision_calculator
    @core_writer_transaction
    def apply_snapshot(
        self,
        db,
        snapshot: SetupSignalSnapshot,
        *,
        evaluation_run_id: int | None = None,
        completed_observation_sessions: int = 1,
        prior_snapshots: tuple[NormalizedSnapshot, ...] = (),
        preloaded_episodes: tuple[SetupLifecycleEpisode, ...] | None = None,
        refresh_primary: bool = True,
    ) -> EpisodeEvaluationResult:
        from sqlalchemy.orm import Session

        from app.services.decision_mutation_authority import (
            lock_decision_scope,
            validate_episode_projection,
        )

        if isinstance(db, Session):
            lock_decision_scope(db, ("lifecycle", snapshot.ticker, snapshot.timeframe))
            if evaluation_run_id is not None:
                from app.models.tables import SetupLifecycleEvaluationRun

                operation = db.get(SetupLifecycleEvaluationRun, evaluation_run_id)
                if operation is None or operation.mode not in {"LIVE", "REPAIR"}:
                    raise ValueError("MUTATION_LIFECYCLE_CURRENT_EPISODE_MODE_REJECTED")
            if preloaded_episodes is not None:
                for episode in preloaded_episodes:
                    db.refresh(episode, with_for_update=True)
                    validate_episode_projection(db, episode)
        normalized = normalized_snapshot_from_row(snapshot)
        if snapshot.evidence_id is not None:
            setup = get_setup_evidence(db, snapshot.evidence_id)
            if setup.run_id != snapshot.run_id or setup.ticker != snapshot.ticker.upper():
                raise ValueError("EVIDENCE_UNAVAILABLE: Lifecycle Setup scope mismatch")
            normalized = replace(
                normalized, source_lineage=dict(setup.payload_json.get("source_lineage_json") or {})
            )
        first_pass = self.lifecycle_engine.evaluate(
            _request(
                normalized,
                previous_snapshots=prior_snapshots,
                state_age_sessions=0,
                missing_observation_sessions=0,
            )
        )
        lookup_family = first_pass.setup_family.value
        if setup_technical_blocked(normalized):
            episodes = preloaded_episodes
            if episodes is None:
                episodes = tuple(
                    self.repository.active_episodes_for_ticker(
                        db,
                        ticker=snapshot.ticker,
                        timeframe=snapshot.timeframe,
                    )
                )
            eligible = [episode for episode in episodes if episode.status == "ACTIVE"]
            if eligible:
                primary = select_primary_episodes(eligible, config=self.config)[0]
                lookup_family = primary.setup_family
                first_pass = replace(first_pass, setup_family=SetupFamily(lookup_family))
        active = (
            _active_episode_from_preloaded(
                preloaded_episodes,
                setup_family=lookup_family,
                as_of_date=snapshot.data_as_of_date,
            )
            if preloaded_episodes is not None
            else self.repository.active_episode_for_update(
                db,
                ticker=snapshot.ticker,
                timeframe=snapshot.timeframe,
                setup_family=lookup_family,
                as_of_date=snapshot.data_as_of_date,
            )
        )
        decision = first_pass
        if active is not None:
            if isinstance(db, Session):
                validate_episode_projection(db, active)
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
            if setup_technical_blocked(normalized):
                decision = replace(decision, setup_family=SetupFamily(active.setup_family))
        actionability = self.actionability_policy.evaluate(decision, normalized)
        self._apply_snapshot_denormalization(snapshot, decision, actionability)
        # The evidence writer rechecks the sealed Setup projection in this
        # transaction before retaining any evaluation or advancing an episode.
        effective_observation_sessions = (
            0
            if active is not None and snapshot.data_as_of_date <= active.last_observed_on
            else completed_observation_sessions
        )
        evaluation_evidence = persist_lifecycle_evaluation_evidence(
            db,
            snapshot=snapshot,
            episode=active,
            decision=decision,
            actionability=actionability,
            evaluation_run_id=evaluation_run_id,
            transition_eligible=(
                _opens_episode(decision)
                if active is None
                else active.current_state != decision.proposed_state.value
                or active.current_phase != decision.phase_code
            ),
            effective_configuration=self.lifecycle_engine.effective_configuration,
            prior_snapshots=prior_snapshots,
            completed_observation_sessions=effective_observation_sessions,
        )
        if (
            active is not None
            and evaluation_evidence is not None
            and evaluation_evidence.id == active.latest_evaluation_evidence_id
        ):
            # A certified duplicate retains its original projection, including
            # opening reason codes. The fresh no-change decision is not new evidence.
            self._certify_episode_projection(db, active)
            if refresh_primary:
                self.refresh_primary_status(
                    db,
                    ticker=snapshot.ticker,
                    timeframe=snapshot.timeframe,
                    market_cutoff=self._snapshot_cutoff(db, snapshot),
                )
            return EpisodeEvaluationResult(
                episode=active,
                decision=decision,
                actionability=actionability,
                lifecycle_event=None,
                lifecycle_evaluation_evidence=evaluation_evidence,
                actionability_before=active.current_actionability,
                updated=False,
            )

        if active is None:
            return self._maybe_open_episode(
                db,
                snapshot,
                decision,
                actionability,
                evaluation_run_id=evaluation_run_id,
                evaluation_evidence=evaluation_evidence,
                preloaded_episodes=preloaded_episodes,
                refresh_primary=refresh_primary,
            )

        return self._update_episode(
            db,
            active,
            snapshot,
            decision,
            actionability,
            evaluation_run_id=evaluation_run_id,
            evaluation_evidence=evaluation_evidence,
            completed_observation_sessions=effective_observation_sessions,
            refresh_primary=refresh_primary,
        )

    @anchored_decision_calculator
    @core_writer_transaction
    def apply_observation_gap(
        self,
        db,
        *,
        ticker: str,
        timeframe: str,
        setup_family: SetupFamily,
        observed_on: date,
        evaluation_run_id: int | None = None,
        market_cutoff=None,
    ) -> EpisodeApplyResult:
        episode = self.repository.active_episode_for_update(
            db,
            ticker=ticker,
            timeframe=timeframe,
            setup_family=setup_family.value,
            as_of_date=observed_on,
        )
        if episode is None:
            return EpisodeApplyResult(episode_id=None)

        from sqlalchemy.orm import Session

        if isinstance(db, Session):
            from app.services.decision_mutation_authority import validate_episode_projection

            if market_cutoff is None or market_cutoff.latest_completed_session != observed_on:
                raise ValueError("MUTATION_LIFECYCLE_GAP_TEMPORAL_AUTHORITY_REQUIRED")
            validate_episode_projection(db, episode)

        missing_sessions = trading_sessions_between(episode.last_observed_on, observed_on)
        if missing_sessions <= 0:
            return EpisodeApplyResult(episode_id=episode.id, updated=False)

        missing_observation_sessions = max(
            episode.missing_observation_sessions,
            missing_sessions,
        )
        threshold = self.config.families.policies[setup_family].observation_gap_sessions
        evaluation_evidence = persist_observation_gap_evaluation_evidence(
            db,
            episode=episode,
            observed_on=observed_on,
            missing_observation_sessions=missing_observation_sessions,
            threshold=threshold,
            evaluation_run_id=evaluation_run_id,
            effective_configuration=self.lifecycle_engine.effective_configuration,
            market_cutoff=market_cutoff,
        )
        if evaluation_evidence is not None:
            episode.latest_evaluation_evidence_id = evaluation_evidence.id
        episode.missing_observation_sessions = missing_observation_sessions
        episode.current_as_of_date = observed_on
        if episode.missing_observation_sessions <= threshold:
            self._certify_episode_projection(db, episode)
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
            from_state=LifecycleState(episode.current_state),
            from_phase=episode.current_phase,
            actionability_before=episode.current_actionability,
            state_age_before=episode.state_age_sessions,
            evaluation_evidence=evaluation_evidence,
        )
        self._close_episode(
            episode,
            closed_on=observed_on,
            state=LifecycleState.EXPIRED,
            phase="OBSERVATION_GAP_EXPIRED",
            terminal_reason="OBSERVATION_GAP",
            evaluation_run_id=evaluation_run_id,
        )
        self._certify_episode_projection(db, episode)
        return EpisodeApplyResult(
            episode_id=episode.id,
            updated=True,
            closed=True,
            lifecycle_event_id=event.id,
        )

    @core_writer_transaction
    def refresh_primary_status(
        self, db, *, ticker: str, timeframe: str, market_cutoff=None
    ) -> None:
        episodes = self.repository.active_episodes_for_ticker(
            db,
            ticker=ticker,
            timeframe=timeframe,
        )
        self._validate_primary_refresh(
            db, episodes, ticker=ticker, timeframe=timeframe, market_cutoff=market_cutoff
        )
        for index, episode in enumerate(select_primary_episodes(episodes, config=self.config)):
            episode.is_primary = index == 0
            episode.primary_rank = index + 1

    @core_writer_transaction
    def refresh_primary_statuses(
        self, db, *, keys: set[tuple[str, str]], market_cutoffs=None
    ) -> None:
        for ticker, timeframe in sorted(keys):
            self.refresh_primary_status(
                db,
                ticker=ticker,
                timeframe=timeframe,
                market_cutoff=(market_cutoffs or {}).get((ticker, timeframe)),
            )

    def _validate_primary_refresh(self, db, episodes, *, ticker, timeframe, market_cutoff):
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.services.calculation_identity import CalculationIdentity
        from app.services.contextual_calculation_identity import (
            build_contextual_result_identity,
            consumer_context_identity,
        )
        from app.services.core_mutation_authority import identity_cutoff
        from app.services.decision_mutation_authority import (
            decision_authority,
            lock_decision_scope,
            validate_episode_projection,
        )
        from app.services.domain_mutation import MutationDomain, MutationSemanticMode

        if not isinstance(db, Session):
            return
        if market_cutoff is None:
            raise ValueError("MUTATION_LIFECYCLE_PRIMARY_TEMPORAL_AUTHORITY_REQUIRED")
        lock_decision_scope(db, ("lifecycle", ticker.upper(), timeframe))
        # Reload the entire target scope after acquiring its lock. A caller
        # cannot select a smaller population or supply ranking inputs.
        locked = list(
            db.scalars(
                select(SetupLifecycleEpisode)
                .where(
                    SetupLifecycleEpisode.ticker == ticker.upper(),
                    SetupLifecycleEpisode.timeframe == timeframe,
                    SetupLifecycleEpisode.status == "ACTIVE",
                )
                .order_by(SetupLifecycleEpisode.id)
                .with_for_update()
            )
        )
        episodes[:] = locked
        if not episodes:
            return
        manifest = []
        evaluations = []
        for episode in episodes:
            validate_episode_projection(db, episode)
            evaluation = db.get(
                SetupLifecycleEvaluationEvidence, episode.latest_evaluation_evidence_id
            )
            frozen = evaluation.payload_json.get("effective_configuration_at_creation") or {}
            if frozen.get("semantic_hash") != self.effective_configuration.snapshot.semantic_hash:
                raise ValueError("MUTATION_LIFECYCLE_PRIMARY_CONFIGURATION_MISMATCH")
            if episode.current_as_of_date > market_cutoff.latest_completed_session:
                raise ValueError("MUTATION_LIFECYCLE_PRIMARY_SESSION_REGRESSION")
            evaluations.append(evaluation)
            manifest.append(
                {
                    "episode_id": episode.id,
                    "evaluation_id": evaluation.id,
                    "evaluation_key": evaluation.evidence_key,
                }
            )
        base = CalculationIdentity.from_canonical_payload(
            evaluations[0].payload_json["calculation_identity"]
        )
        if identity_cutoff(db, base) != market_cutoff:
            from app.models.tables import BackgroundJob
            from app.services.domain_write_fence import current_domain_write_ownership

            ownership = current_domain_write_ownership()
            if ownership is not None:
                job = db.get(BackgroundJob, ownership.job_id)
                if (
                    job.job_type != "SETUP_LIFECYCLE_DAILY_MAINTENANCE"
                    or job.related_run_id is not None
                ):
                    raise ValueError("MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH")
                from datetime import datetime, time
                from zoneinfo import ZoneInfo

                from app.services.market_clock_service import EXCHANGE_TIMEZONE, MarketClockService

                maintenance_day = date.fromisoformat(str(job.payload_json["as_of_date"]))
                expected_cutoff = MarketClockService().cutoff_for(
                    datetime.combine(maintenance_day, time.max, tzinfo=ZoneInfo(EXCHANGE_TIMEZONE)),
                    reason="LIFECYCLE_CURRENT_STATE_MAINTENANCE_AS_OF_DAY",
                )
                if job.payload_json.get("market_session_completed", True) is not True or any(
                    getattr(market_cutoff, field) != getattr(expected_cutoff, field)
                    for field in (
                        "cutoff_at",
                        "latest_completed_session",
                        "exchange_timezone",
                        "calendar_version",
                        "bar_readiness_version",
                        "context_id",
                    )
                ):
                    raise ValueError("MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH")
            base = consumer_context_identity(
                market_cutoff=market_cutoff, run_id=None, pipeline_id=None, ticker=ticker
            )
        identity = self.effective_configuration.bind(
            build_contextual_result_identity(
                base=base,
                namespace="lifecycle-primary-projection",
                config_hash=self.effective_configuration.snapshot.semantic_hash,
                calculation_version=self.config.engine.version,
                engine_version=self.config.engine.version,
                source_artifacts=(),
                source_payload={"episodes": manifest, "timeframe": timeframe},
            )
        )
        decision_authority(
            db,
            domain=MutationDomain.CURRENT_PROJECTION,
            writer="refresh_primary_status",
            identity=identity,
            configuration=self.effective_configuration,
            records={"target_evidence": evaluations[0]},
            manifests={"projection_scope": {"episodes": manifest, "timeframe": timeframe}},
            semantic_mode=MutationSemanticMode.CURRENT_PROJECTION_ADVANCE,
        )

    @core_writer_member(
        (
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_snapshot",
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_observation_gap",
        )
    )
    def _maybe_open_episode(
        self,
        db,
        snapshot: SetupSignalSnapshot,
        decision: LifecycleDecision,
        actionability: ActionabilityDecision,
        *,
        evaluation_run_id: int | None,
        evaluation_evidence: SetupLifecycleEvaluationEvidence | None,
        preloaded_episodes: tuple[SetupLifecycleEpisode, ...] | None,
        refresh_primary: bool,
    ) -> EpisodeEvaluationResult:
        if not _opens_episode(decision):
            return EpisodeEvaluationResult(
                episode=None,
                decision=decision,
                actionability=actionability,
                lifecycle_evaluation_evidence=evaluation_evidence,
                warning_codes=("NOT_TRACKABLE_FOR_EPISODE",),
            )

        cooldown_warning = self._cooldown_warning(
            db,
            snapshot,
            decision,
            preloaded_episodes=preloaded_episodes,
        )
        if cooldown_warning is not None:
            return EpisodeEvaluationResult(
                episode=None,
                decision=decision,
                actionability=actionability,
                lifecycle_evaluation_evidence=evaluation_evidence,
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
            latest_evaluation_evidence_id=(
                evaluation_evidence.id if evaluation_evidence is not None else None
            ),
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
            evaluation_evidence=evaluation_evidence,
            new_episode=True,
        )
        if refresh_primary:
            self.refresh_primary_status(
                db,
                ticker=snapshot.ticker,
                timeframe=snapshot.timeframe,
                market_cutoff=self._snapshot_cutoff(db, snapshot),
            )
        self._certify_episode_projection(db, episode)
        return EpisodeEvaluationResult(
            episode=episode,
            decision=decision,
            actionability=actionability,
            lifecycle_event=event,
            lifecycle_evaluation_evidence=evaluation_evidence,
            actionability_before=None,
            opened=True,
            updated=True,
        )

    @core_writer_member(
        (
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_snapshot",
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_observation_gap",
        )
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
        evaluation_evidence: SetupLifecycleEvaluationEvidence | None,
        completed_observation_sessions: int,
        refresh_primary: bool,
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
        if evaluation_evidence is not None:
            episode.latest_evaluation_evidence_id = evaluation_evidence.id

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
                evaluation_evidence=evaluation_evidence,
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

        if refresh_primary:
            self.refresh_primary_status(
                db,
                ticker=snapshot.ticker,
                timeframe=snapshot.timeframe,
                market_cutoff=self._snapshot_cutoff(db, snapshot),
            )
        self._certify_episode_projection(db, episode)
        return EpisodeEvaluationResult(
            episode=episode,
            decision=decision,
            actionability=actionability,
            lifecycle_event=event,
            lifecycle_evaluation_evidence=evaluation_evidence,
            actionability_before=previous_actionability,
            updated=True,
            closed=closed,
        )

    @core_writer_member(
        (
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_snapshot",
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_observation_gap",
        )
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
        new_episode: bool = False,
        from_state: LifecycleState | None = None,
        from_phase: str | None = None,
        actionability_before: str | None = None,
        state_age_before: int | None = None,
        evaluation_evidence: SetupLifecycleEvaluationEvidence | None = None,
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
        if new_episode:
            add_new = getattr(self.repository, "add_new_lifecycle_event", None)
            event = (
                add_new(db, event)
                if add_new is not None
                else self.repository.add_lifecycle_event(db, event)
            )
        else:
            event = self.repository.add_lifecycle_event(db, event)
            self.repository.supersede_prior_current_events(db, event)
        transition_evidence = persist_lifecycle_transition_evidence(
            db,
            event=event,
            evaluation=evaluation_evidence,
            prior_transition_evidence_id=episode.latest_transition_evidence_id,
        )
        if transition_evidence is not None:
            event.transition_evidence_id = transition_evidence.id
            episode.latest_transition_evidence_id = transition_evidence.id
            db.flush()
        return event

    @core_writer_member(
        (
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_snapshot",
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_observation_gap",
        )
    )
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

    def _certify_episode_projection(self, db, episode):
        from sqlalchemy.orm import Session

        from app.services.decision_mutation_authority import validate_episode_projection

        if isinstance(db, Session):
            validate_episode_projection(db, episode)

    def _snapshot_cutoff(self, db, snapshot):
        from sqlalchemy.orm import Session

        from app.services.combined_ranking_identity import calculation_identity_from_debug
        from app.services.core_mutation_authority import identity_cutoff

        return (
            identity_cutoff(db, calculation_identity_from_debug(snapshot.source_lineage_json))
            if isinstance(db, Session)
            else None
        )

    def _cooldown_warning(
        self,
        db,
        snapshot: SetupSignalSnapshot,
        decision: LifecycleDecision,
        *,
        preloaded_episodes: tuple[SetupLifecycleEpisode, ...] | None = None,
    ) -> str | None:
        closed = (
            _latest_closed_episode_from_preloaded(
                preloaded_episodes,
                setup_family=decision.setup_family.value,
                as_of_date=snapshot.data_as_of_date,
            )
            if preloaded_episodes is not None
            else self.repository.latest_closed_episode(
                db,
                ticker=snapshot.ticker,
                timeframe=snapshot.timeframe,
                setup_family=decision.setup_family.value,
                as_of_date=snapshot.data_as_of_date,
            )
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
    @core_writer_member(
        (
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_snapshot",
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_observation_gap",
        )
    )
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


def _active_episode_from_preloaded(
    episodes: tuple[SetupLifecycleEpisode, ...],
    *,
    setup_family: str,
    as_of_date: date,
) -> SetupLifecycleEpisode | None:
    family = [
        episode
        for episode in episodes
        if episode.status == "ACTIVE" and episode.setup_family == setup_family
    ]
    eligible = [
        episode
        for episode in family
        if episode.opened_on <= as_of_date
        and episode.current_as_of_date <= as_of_date
        and episode.last_observed_on <= as_of_date
    ]
    if eligible:
        return max(
            eligible,
            key=lambda episode: (
                episode.current_as_of_date,
                episode.opened_on,
                episode.id or 0,
            ),
        )
    if family:
        raise ValueError(
            "historical lifecycle state is unavailable because a newer active episode exists"
        )
    return None


def _latest_closed_episode_from_preloaded(
    episodes: tuple[SetupLifecycleEpisode, ...],
    *,
    setup_family: str,
    as_of_date: date,
) -> SetupLifecycleEpisode | None:
    eligible = [
        episode
        for episode in episodes
        if episode.status == "CLOSED"
        and episode.setup_family == setup_family
        and episode.closed_on is not None
        and episode.closed_on <= as_of_date
    ]
    return max(eligible, key=lambda episode: (episode.closed_on, episode.id or 0), default=None)


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
            "raw_row_id": snapshot.raw_row_id,
            "fundamental_score_id": snapshot.fundamental_score_id,
            "technical_score_id": snapshot.technical_score_id,
            "combined_result_id": snapshot.combined_result_id,
            "ranking_result_id": snapshot.ranking_result_id,
            "market_regime_snapshot_id": snapshot.market_regime_snapshot_id,
            "sector_rotation_snapshot_id": snapshot.sector_rotation_snapshot_id,
        },
        source_lineage=dict(snapshot.source_lineage_json or {}),
        engine_version=snapshot.engine_version,
        config_version=snapshot.config_version,
        schema_version=snapshot.schema_version,
        config_hash=snapshot.config_hash,
        source_data_hash=snapshot.source_data_hash,
        origin_type=snapshot.origin_type,
        is_canonical=snapshot.is_canonical,
        superseded_by_snapshot_id=snapshot.superseded_by_snapshot_id,
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
        "technical_score": snapshot.dual_score,
        "trend_score": snapshot.trend_score,
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
    if isinstance(value, (list, tuple, set)):
        return SignalValueType.SET
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
